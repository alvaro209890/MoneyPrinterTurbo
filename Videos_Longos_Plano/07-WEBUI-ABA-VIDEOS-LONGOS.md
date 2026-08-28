# 07 — WebUI: a aba "Vídeos Longos"

> **Objetivo:** uma aba nova no topo da WebUI, com a **mesma lógica e os mesmos controles** da tela
> de Shorts, acrescida do que o formato longo exige (duração-alvo, capítulos, preview do outline).

---

## 1. O estado atual da UI

`webui/Main.py` é **página única**, 6095 linhas. O corpo é montado em `_render_application()`
(`Main.py:6043`):

```python
with st.container(key="main_settings_grid"):
    panel = st.columns(4)                      # Main.py:6062
left_panel, middle_panel, audio_panel, right_panel = panel

params = VideoParams(video_subject="")
_render_script_settings(left_panel, params)     # Main.py:3635
uploaded_files = _render_video_settings(middle_panel, params)      # Main.py:3802
uploaded_audio, uploaded_bgm, voice_mode = _render_audio_settings(audio_panel, params)  # 4944
_render_subtitle_settings(right_panel, params)  # Main.py:5454
generation_submitted = _render_generation_controls(...)
```

`st.tabs` já é usado no projeto (`Main.py:1114`, painel de tasks), então o padrão existe — mas
**nunca no nível principal**. Você vai criar o primeiro.

## 2. Arquitetura da mudança

```python
def _render_application():
    _render_top_bar()
    ...  # dialogs e restore, sem mudança

    shorts_tab, long_tab = st.tabs([tr("Tab Shorts"), tr("Tab Long Videos")])

    with shorts_tab:
        _render_shorts_workspace()      # ← exatamente o corpo atual, extraído
    with long_tab:
        _render_long_video_workspace()  # ← novo
```

**Passo 1 obrigatório: extrair, não duplicar.** Mova o corpo atual de `_render_application` para
`_render_shorts_workspace()` **sem alterar uma linha de lógica**. Rode a suíte de WebUI
(`test_webui_*.py`, 13 arquivos) e confirme verde. Só então escreva a aba nova.

> 🔴 **O erro que vai te custar caro:** copiar `_render_audio_settings` e `_render_subtitle_settings`
> para a aba nova. Elas têm ~900 linhas somadas, com dezenas de provedores de TTS. **Reuse as
> mesmas funções**, passando `params` diferente e um `key_prefix` diferente.

## 3. O problema das chaves de widget (leia antes de codar)

Streamlit exige `key` único por widget **na página inteira**, não por aba. Se `_render_audio_settings`
for chamada nas duas abas com as mesmas chaves, o app quebra com
`DuplicateWidgetID`.

**Solução:** dê a todas as funções de painel um parâmetro `key_prefix: str = ""`, e prefixe cada
`key=` com ele:

```python
def _render_audio_settings(panel, params, key_prefix=""):
    ...
    st.selectbox(..., key=f"{key_prefix}voice_name")
```

Chame com `key_prefix=""` na aba de Shorts (preserva as chaves atuais → **preserva as configurações
já salvas dos usuários**) e `key_prefix="long_"` na aba nova.

> ⚠️ Os helpers `_saved_ui_choice` / `_saved_ui_number` / `_saved_ui_bool` (`Main.py:244-316`)
> persistem no `config.toml` usando essas chaves. Prefixar cria entradas novas para o modo longo —
> **isso é o comportamento desejado** (os defaults do modo longo são diferentes, plano `02` §5).
> Só garanta que a aba de Shorts continue lendo as chaves antigas, sem prefixo.

## 4. Layout da aba "Vídeos Longos"

Quatro colunas mantendo o paralelo com a aba de Shorts, com a primeira coluna expandida:

```
┌── Roteiro & Estrutura ──┬── Vídeo ──┬── Áudio ──┬── Legendas ──┐
│ Tema                    │ Aspecto   │ Voz       │ Ativar       │
│ Idioma                  │ (16:9 ✓)  │ Velocidade│ Fonte        │
│ ⏱ Duração-alvo (slider) │ Fonte de  │ Volume    │ Tamanho      │
│ 📚 Nº de capítulos      │ material  │ BGM       │ Cor / contorno│
│ [Gerar estrutura]  ◄──── preview    │ ...       │ Posição      │
│ ── outline editável ──  │ Duração   │           │              │
│  1. título  [~2min]     │ do corte  │           │              │
│  2. título  [~3min]     │ Transição │           │              │
│  ...                    │ Concat    │           │              │
│ Requisitos extras       │           │           │              │
│ System prompt (avançado)│           │           │              │
└─────────────────────────┴───────────┴───────────┴──────────────┘
                    [ Gerar vídeo longo ]
              ▸ estimativa: ~12 min de vídeo · ~180 clipes
```

### 4.1 Controles exclusivos do modo longo

| Controle | Widget | Faixa / valor |
|---|---|---|
| Duração-alvo | `st.slider` | 3 a **35** min, passo 1, default 10 |
| Nº de capítulos | `st.number_input` | 3 a 14, com opção "automático" (default) |
| Gerar estrutura | `st.button` | dispara `stop_at="script"` no modo longo |
| Outline editável | `st.data_editor` | títulos + peso, editáveis antes de gerar |
| Narrar títulos | `st.toggle` | default **off** (plano `03` §6) |
| Normalizar áudio −14 LUFS | `st.toggle` | default **on** (plano `04` §8) |

> ⚠️ O slider **tem que** terminar em 35. Mas lembre: a UI não é a única porta de entrada — a
> validação de verdade é a do plano `02` §3, em código.

### 4.2 O botão "Gerar estrutura" (preview de outline)

Este é o recurso que torna a aba realmente usável, e sai quase de graça porque
`stop_at` já existe:

1. monta `VideoParams` com `video_mode="long"`;
2. chama `webui_task.submit_generation(..., stop_at="script")`;
3. quando a task completa, lê o `script.json` e mostra os capítulos numa tabela editável;
4. o usuário ajusta títulos/pesos e clica em "Gerar vídeo longo", que reenvia **com o outline já
   fixado** em `params.chapter_outline`.

> 💡 Isso é o equivalente ao "Gerar roteiro e palavras-chave com IA" que a aba de Shorts já tem
> (chave de i18n `"Generate Video Script and Keywords"`, presente em `webui/i18n/pt.json`).

### 4.3 Estimativas em tempo real

Abaixo do botão principal, mostre (calculado localmente, sem chamar LLM):

- duração estimada do vídeo;
- número aproximado de clipes = `duração / video_clip_duration`;
- **aviso** se o número de clipes passar de ~250;
- **aviso** se `video_source` for provedor pago, com estimativa de custo por clipe
  (trava do plano `05` §3.4).

## 5. Progresso e a fila

O render longo bloqueia a fila (`max_concurrent_tasks=1`, `webui_task.py:19`). A UI precisa dizer
isso, não deixar o usuário adivinhar.

- Mostre um `st.info` fixo na aba: *"A geração de vídeo longo ocupa a fila. Shorts enviados durante
  a geração ficam aguardando."*
- Reuse o painel de progresso e o log ao vivo que a aba de Shorts já tem
  (`_render_current_generation_task`, e o `TASK_LOG_REFRESH_INTERVAL_SECONDS = 0.5` de
  `webui_task.py:29`).
- Exiba a **etapa nomeada** além do percentual — com o mapa de progresso do plano `06` §7 o
  usuário entende "está no capítulo 4 de 9" em vez de ver 24%.

## 6. Internacionalização

**Toda** string nova passa por `tr()` (`Main.py:553`) e entra nos **12** arquivos de
`webui/i18n/`: `de, en, es, fr, id, it, ko, pt, ru, tr, vi, zh`.

Existe teste que cobre isso: `test/services/test_webui_i18n.py`. Se você adicionar chave só no
`en.json`, ele quebra.

Chaves novas sugeridas (nomes em inglês, é a convenção do projeto):

```
"Tab Shorts", "Tab Long Videos", "Long Video Settings",
"Target Duration Minutes", "Chapter Count", "Chapter Count Auto",
"Generate Outline", "Outline Preview", "Chapter Title", "Chapter Weight",
"Narrate Chapter Titles", "Normalize Loudness",
"Long Video Queue Warning", "Long Video Clip Estimate",
"Long Video Paid Provider Warning", "Long Video Truncated Warning"
```

Traduza pt/en com cuidado (são os idiomas realmente usados aqui); para os demais, tradução direta
é aceitável — mas **nunca deixe a chave faltando**.

## 7. Restauração de task e histórico

`_load_task_restore_payload()` (`Main.py:1180`) e `_apply_restored_params()` (`Main.py:1253`)
restauram uma task antiga nos widgets. Estenda para:

- reconhecer `video_mode == "long"` e **abrir a aba certa**;
- restaurar `target_duration_minutes`, `chapter_count` e o outline.

> ⚠️ Tasks antigas não têm `video_mode`. O `.get("video_mode", "short")` precisa ser o default em
> todo lugar, senão o histórico quebra. `test_webui_task_history.py` vai cobrar isso.

## 8. Testes obrigatórios deste plano

Novos, em `test/services/`:

- `test_webui_long_tab.py::test_tabs_render_without_duplicate_keys` — nenhuma `key` repetida entre
  as duas abas.
- `test_webui_long_tab.py::test_long_defaults_applied` — a aba longa monta `VideoParams` com os
  defaults do plano `02` §5.
- `test_webui_long_tab.py::test_duration_slider_capped_at_35` — o widget não permite > 35.
- `test_webui_long_tab.py::test_outline_preview_uses_stop_at_script`.
- `test_webui_long_tab.py::test_restore_long_task_opens_long_tab`.
- `test_webui_i18n.py` (existente) — deve continuar verde com as chaves novas.

E os existentes que **não podem** quebrar: `test_webui_startup.py`,
`test_webui_generation_defaults.py`, `test_webui_task.py`, `test_webui_task_history.py`,
`test_webui_settings_transfer.py`, `test_webui_tts_settings.py`.

> 💡 Rode `streamlit run webui/Main.py` de verdade uma vez antes de dar a tarefa por concluída.
> Os testes de WebUI do projeto são de importação/estado, não de renderização real — eles não pegam
> `DuplicateWidgetID`.
