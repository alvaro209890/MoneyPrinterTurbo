# 01 — Arquitetura atual (o que já existe e você vai reusar)

> Todas as referências foram levantadas lendo o código em `C:\GIS\MoneyPrinterTurbo` na versão
> `1.3.5` (`pyproject.toml:10`). **Confirme as linhas antes de editar** — se o repo tiver avançado,
> os nomes de função continuam válidos mesmo que a linha mude.

---

## 1. Visão de 10 mil pés

```
                      ┌──────────────────────────────────────────┐
   WebUI (Streamlit)  │  webui/Main.py  →  _render_application() │
   CLI                │  cli.py                                  │
   API (FastAPI)      │  app/controllers/v1/video.py             │
                      └────────────────────┬─────────────────────┘
                                           │  todos convergem para:
                                           ▼
                         app/services/task.py :: start()  →  _run_pipeline()
                                           │
   ┌───────────┬───────────┬───────────────┼───────────────┬─────────────┬──────────────┐
   ▼           ▼           ▼               ▼               ▼             ▼              ▼
 script      terms       audio         subtitle        materials      combine        final
 llm.py      llm.py      voice.py      subtitle.py     material.py    video.py       video.py
                                       voice.py                       combine_videos generate_video
```

O `stop_at` do `_run_pipeline` permite parar em qualquer etapa — é assim que a UI faz preview de
roteiro sem renderizar. **Você vai reusar exatamente esse contrato.**

## 2. O pipeline, etapa por etapa

Arquivo central: **`app/services/task.py`**

| Etapa | Função | Linha | O que faz | Progresso |
|---|---|---|---:|---|
| — | `_run_pipeline` | 1242 | orquestrador; recebe `stop_at` | 5 |
| 1 | `generate_script` | 288 | chama `llm.generate_script` se `params.video_script` vazio | 10 |
| 2 | `generate_terms` | 309 | chama `llm.generate_terms` (5 termos, ou 8 se `match_materials_to_script`) | — |
| — | `save_script_data` | 351 | grava `script.json` na pasta da task | — |
| 3 | `generate_audio` | 478 | TTS → `audio.mp3` + `sub_maker` + duração | 20→30 |
| 4 | `generate_subtitle` | 567 | SRT via `edge` (sub_maker) ou `whisper` | — |
| 5 | `get_video_materials` | 625 | baixa/gera materiais até cobrir a duração do áudio | 40 |
| 6 | `generate_final_videos` | 795 | `combine_videos` + `generate_video` por cada `video_count` | 50→100 |
| 7 | `_schedule_cross_post` | 1189 | publica em redes (opcional, thread-pool separado) | — |

Estados da task: `app/models/const.py` — `TASK_STATE_FAILED = -1`, `TASK_STATE_COMPLETE = 1`,
`TASK_STATE_PROCESSING = 4`.

Falha estruturada: `_mark_task_failed(task_id, stage, error)` em `task.py:239`. **Use sempre esta
função** — ela preserva o progresso e alimenta `failed_stage` / `error` na resposta da API.

## 3. Geração de roteiro — onde está o teto que te bloqueia

**`app/services/llm.py`**

```python
# llm.py:15-18
MIN_SCRIPT_PARAGRAPH_NUMBER = 1
MAX_SCRIPT_PARAGRAPH_NUMBER = 10      # ← 🔴 ESTE É O BLOQUEIO PRINCIPAL
MAX_SCRIPT_PROMPT_LENGTH = 2000
MAX_SCRIPT_SYSTEM_PROMPT_LENGTH = 8000
```

- `build_script_prompt()` — `llm.py:467` — monta o prompt (system prompt + subject + nº de parágrafos
  + idioma + requisitos extras).
- `generate_script()` — `llm.py:503` — 1 chamada ao LLM, com `_max_retries = 5` (`llm.py:14`),
  limpa markdown e devolve string única.
- `_normalize_script_paragraph_number()` — `llm.py:450` — **clampa** para [1, 10]. Vale também para
  chamadas internas, não só API.
- No schema: `paragraph_number: int = Field(default=1, ge=1, le=10)` — `app/models/schema.py:127`.

> 🔴 **Consequência:** hoje é fisicamente impossível pedir um roteiro de 35 minutos. 10 parágrafos
> dão ~2–4 minutos de narração. O plano `03` resolve isso com geração em capítulos.

## 4. Geração de palavras-chave

`llm.generate_terms()` — `llm.py:599`. Dois modos:

- **Padrão**: `amount=5` termos temáticos, sem ordem.
- **`match_script_order=True`**: `amount=8`, termos **cronológicos**, na ordem da narração.
  Chamado a partir de `task.py:316-321`.

Depois, opcionalmente, `twelvelabs.rerank_terms_by_subject()` reordena por semântica
(`task.py:343`) — **pulado** quando `match_materials_to_script` está ligado.

> 💡 Para vídeo longo, o modo `match_script_order` é o que você quer como padrão — mas com
> `amount` muito maior. Ver plano `03` §4.

## 5. Áudio / TTS

**`app/services/voice.py`** (2253 linhas — o maior serviço).

- `tts()` — `voice.py:455` — dispatcher: Edge, Azure v1/v2, SiliconFlow, Gemini, MiMo, MiniMax,
  ElevenLabs, Chatterbox, Fish Audio, ou `no-voice` (silêncio).
- Edge TTS: `stream_edge_tts_chunks()` — `voice.py:786`; timeout em `get_edge_tts_timeout_seconds()`
  — `voice.py:700`.
- `create_subtitle(sub_maker, text, subtitle_file)` — `voice.py:2113` — gera SRT a partir dos cues
  do Edge, casando com as linhas do roteiro (`_match_script_line`, `voice.py:1950`).
- `get_audio_duration()` — `voice.py:2171`.

> 🔴 **Consequência:** hoje o roteiro inteiro vai numa chamada só de TTS. Para 35 min (~50 mil
> caracteres) isso estoura timeout e/ou limite do provedor. Plano `04` resolve.

## 6. Legendas

- Caminho **edge**: reusa o `sub_maker` do TTS (rápido, sem custo).
- Caminho **whisper**: `app/services/subtitle.py:22` `create()` com `faster-whisper`; depois
  `correct()` (`subtitle.py:202`) alinha o texto reconhecido ao roteiro usando **distância de
  Levenshtein** (`subtitle.py:176`).

> ⚠️ `correct()` é O(n·m) sobre o texto. Com um roteiro de 35 min isso fica caro. Plano `06` §5.

## 7. Materiais visuais

**`app/services/material.py`**

- `download_videos()` — `material.py:1145` — entrada única. Roteia por `source`:
  - `pexels` / `pixabay` / `coverr` → busca em catálogo, com cache de 24 h
    (`_search_videos_with_cache`, `material.py:1052`).
  - `wavespeed` / `volcengine_seedance` → **geração por IA, paga, sob demanda** (só gera o que
    precisa; não usa cache).
  - `match_script_order=True` → `_download_videos_by_script_order()` (`material.py:1475`).
- Critério de parada do loop padrão (`material.py` ~1265):
  ```python
  seconds = min(max_clip_duration, item.duration)
  total_duration += seconds
  if total_duration > audio_duration:
      break
  ```
- Filtro de resolução: `is_material_resolution_acceptable()` — `video.py:105` — mínimo 480 px com
  tolerância de 10 px (`video.py:76-81`).

> 🔴 **Consequência:** 35 min ÷ 5 s por clipe = **420 clipes**. Isso estoura rate limit do Pexels,
> esgota resultados únicos e gera repetição visual óbvia. Plano `05` resolve.

## 8. Render

**`app/services/video.py`**

- `combine_videos()` — `video.py:538` — o coração:
  1. lê duração do áudio;
  2. `_get_required_video_duration()` (`video.py:94`) = duração do áudio + margem de **0,1 s**
     (`_VIDEO_DURATION_SAFETY_MARGIN`, `video.py:75`);
  3. fatia cada material em subclipes de `max_clip_duration` (ajustado por `clip_speed`);
  4. `_prioritize_unique_source_clips()` (`video.py:116`) evita repetir a mesma fonte em sequência;
  5. redimensiona/letterboxa para o aspecto alvo;
  6. aplica transição (`VideoTransitionMode`);
  7. escreve clipes processados e concatena.
- `concat_video_clips_with_ffmpeg()` — `video.py:332` — **já existe** um caminho de concatenação
  via FFmpeg `concat` demuxer, com fallback de codec e suporte a `-t max_duration`.
  **Este é o seu caminho preferencial para vídeo longo.**
- `generate_video()` — `video.py:991` — mixa narração + BGM, queima legendas, escreve o MP4 final.
- Codec: `_get_configured_video_codec()` (`video.py:170`) + `_get_effective_video_codec()`
  (`video.py:222`) com detecção de encoder e desativação em runtime se falhar (`video.py:251`).

## 9. Schema / parâmetros

**`app/models/schema.py`** — `class VideoParams` (linha 66). Campos que importam para você:

| Campo | Linha | Default | Observação para vídeo longo |
|---|---:|---|---|
| `video_subject` | 81 | — | tema |
| `video_script` | 82 | `""` | se preenchido, pula o LLM |
| `video_terms` | 83 | `None` | string ou lista |
| `video_aspect` | 84 | `9:16` | **longo deve default 16:9** |
| `video_concat_mode` | 85 | `random` | longo deve usar `sequential` |
| `video_clip_duration` | 87 | `5` (`ge=1`, **sem teto**) | longo precisa de 8–15 s |
| `video_count` | 90 | `1` (`ge=1`) | manter 1 no modo longo |
| `match_materials_to_script` | 89 | `False` | **ligar por padrão no longo** |
| `paragraph_number` | 127 | `1` (`ge=1, le=10`) | 🔴 teto a ser contornado |
| `custom_audio_file` | 97 | `None` | permite narração externa |
| `subtitle_enabled` | 113 | `True` | |
| `bgm_type` / `bgm_volume` | 105/107 | `random` / `0.2` | |
| `n_threads` | 126 | `2` | subir no longo |

## 10. WebUI (onde a aba vai entrar)

**`webui/Main.py`** — 6095 linhas, **página única**, sem abas no nível principal.

```python
# Main.py:6043
def _render_application():
    _render_top_bar()
    ...
    with st.container(key="main_settings_grid"):
        panel = st.columns(4)          # ← Main.py:6062
    left_panel, middle_panel, audio_panel, right_panel = panel

    params = VideoParams(video_subject="")
    _render_script_settings(left_panel, params)      # Main.py:3635
    uploaded_files = _render_video_settings(middle_panel, params)   # Main.py:3802
    ... = _render_audio_settings(audio_panel, params)               # Main.py:4944
    _render_subtitle_settings(right_panel, params)                  # Main.py:5454
    generation_submitted = _render_generation_controls(...)
```

`st.tabs` já é usado no projeto, mas só dentro do painel de tasks (`Main.py:1114`). **Não há
precedente de aba no topo — você vai criar o primeiro.** Ver plano `07`.

Outros pontos úteis do `Main.py`:
- `tr(key)` — `Main.py:553` — tradução; chaves vivem em `webui/i18n/*.json` (12 idiomas, com
  `pt.json`).
- `_saved_ui_*` helpers (`Main.py:244-316`) — persistem escolhas de UI no `config.toml`.
- `_render_task_manager_panel()` — `Main.py:1100` — histórico de tasks.

## 11. Execução em background da WebUI

**`app/services/webui_task.py`**

- `submit_generation()` — linha 122 — registra a task e delega ao `InMemoryTaskManager`.
- ⚠️ **`max_concurrent_tasks=1`** — linha 19-22. A WebUI roda **uma geração por vez**, protegida por
  `config.runtime_config_lock()` (linha 81). Um render de 35 min **bloqueia a fila inteira**.
  Isso é uma decisão consciente do upstream; ver plano `06` §7 sobre o que fazer.
- Logs por task com `deque(maxlen=1000)` e no máximo 20 tasks (linhas 25-26).

## 12. API e CLI

- **API**: `app/controllers/v1/video.py` (508 linhas), registrada em `app/router.py`.
  Endpoints de vídeo, script, terms, metadata social, BGM e materiais.
- **CLI**: `cli.py` — argparse com grupos `script and content`, `materials and pipeline`,
  `video output`, `voiceover and background music`, `subtitles`.
  Estágios válidos: `_PIPELINE_STAGES = ("script","terms","audio","subtitle","materials","video")`.

## 13. Testes existentes

`test/services/` tem **49 arquivos** de teste, incluindo `test_task.py`, `test_video.py`,
`test_llm.py`, `test_voice.py`, `test_material.py`, `test_cli.py`, `test_schema.py`,
`test_webui_*.py` (13 arquivos de WebUI) e `test_mpt_agent_skill.py`.

**Rodar tudo antes e depois de qualquer mudança** — é o seu contrato de não-regressão.

## 14. Onde este sistema roda hoje (produção)

| Item | Valor |
|---|---|
| Máquina | `acer` (notebook Linux da frota) |
| Pasta | `/home/acer/Projetos/MoneyPrinterTurbo` |
| WebUI | `systemctl --user status moneyprinter-webui.service` — porta **8501** |
| URL pública | `https://video.cursar.space` (túnel Cloudflare, `video-tunnel.service`) |
| Saídas | `storage/tasks/<task_id>/final-1.mp4` |
| Skill do agente | `~/.hermes/skills/media/moneyprinterturbo/` (`SKILL.md` + `scripts/mpt.py`) |

> ⚠️ O clone em `C:\GIS\MoneyPrinterTurbo` (Windows) é **cópia de trabalho para planejamento**. A
> produção é o acer. Ver plano `11` para a estratégia de deploy.
