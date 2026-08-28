# 02 — Requisitos e decisões arquiteturais fechadas

> Este arquivo existe para você **não ter que decidir sozinho** as coisas que já foram decididas.
> Onde estiver escrito **DECISÃO**, siga. Onde estiver **ABERTO**, use seu julgamento e registre o
> que escolheu no `12-CHECKLIST-EXECUCAO.md`.

---

## 1. Requisitos funcionais

| ID | Requisito | Origem |
|---|---|---|
| RF-01 | Nova aba "Vídeos Longos" na WebUI, separada da aba de Shorts | pedido direto |
| RF-02 | Mesma lógica dos Reels: roteiro por IA, palavras-chave por IA, TTS, materiais, legendas | pedido direto ("bem a mesma lógica dos reels mesmo") |
| RF-03 | Duração final até **35 minutos**, com teto aplicado em código | pedido direto |
| RF-04 | Usuário pode informar só o **tema** e receber o vídeo pronto | paridade com Shorts |
| RF-05 | Usuário pode informar **roteiro próprio** e pular o LLM | paridade com Shorts |
| RF-06 | Preview do roteiro/capítulos antes de renderizar | `stop_at` já existe |
| RF-07 | Progresso granular durante a geração | render longo é demorado |
| RF-08 | Disponível via **CLI e API**, não só UI | o Hermes usa CLI |
| RF-09 | O agente `Hermes-acer/videos` consegue gerar o vídeo longo diário por este caminho | objetivo final |

## 2. Requisitos não-funcionais

| ID | Requisito | Alvo |
|---|---|---|
| RNF-01 | Não regredir o fluxo de Shorts | suíte atual 100% verde |
| RNF-02 | Não estourar memória em máquina modesta (notebook `acer`) | pico < 4 GB RSS |
| RNF-03 | Custo marginal zero no caminho padrão | Edge TTS + Pexels + LLM já pago |
| RNF-04 | Render de 30 min deve completar em tempo aceitável | **ABERTO** — meça e registre |
| RNF-05 | Falha em qualquer etapa deixa `failed_stage` + `error` legíveis | reusar `_mark_task_failed` |
| RNF-06 | Áudio final normalizado ≈ **−14 LUFS**, true peak ≤ −1,5 dBTP, `+faststart` | padrão do canal |

## 3. DECISÃO — o teto de 35 minutos

**Constante única, um lugar só.** Crie em `app/models/const.py`:

```python
# Teto de duração do modo "vídeo longo", em segundos (35 minutos).
LONG_VIDEO_MAX_DURATION_SECONDS = 35 * 60          # 2100
# Piso: abaixo disso o modo longo não faz sentido; use o fluxo normal.
LONG_VIDEO_MIN_DURATION_SECONDS = 3 * 60           # 180
```

O teto precisa ser verificado em **três** momentos (defesa em profundidade):

1. **Pré-voo** (antes de gastar LLM/TTS/materiais): a duração-alvo pedida não pode exceder o teto.
   Falhe com `_mark_task_failed(task_id, "preflight", ...)`.
2. **Pós-TTS** (a duração real do áudio é a verdade): se o áudio gerado passou do teto, ou trunca
   por capítulo (preferível) ou falha — ver plano `04` §6.
3. **No render**: passe `max_duration` para `concat_video_clips_with_ffmpeg()` (`video.py:332` já
   aceita esse parâmetro) como cinto de segurança final.

> ⚠️ Não confie só na validação da UI. O CLI, a API e o Hermes entram pelo mesmo `task.start()`.

## 4. DECISÃO — como o "modo longo" é representado

**Não crie uma classe nova de params.** Estenda `VideoParams` com campos opcionais que, quando
ausentes, deixam o comportamento atual **idêntico**:

```python
# app/models/schema.py, dentro de VideoParams
video_mode: Literal["short", "long"] = "short"       # discriminador
target_duration_minutes: Optional[float] = None      # alvo do modo longo
chapter_count: Optional[int] = None                  # nº de capítulos (None = automático)
chapter_outline: Optional[List[str]] = None          # títulos, se o usuário quiser controlar
```

Razões:
- `task._run_pipeline` continua sendo o **único** orquestrador (regra de ouro nº 1);
- as tasks antigas em `storage/` continuam desserializando sem erro;
- a API não quebra contrato.

> ⚠️ Ao adicionar campo com default, **não** mexa na ordem dos campos existentes nem nos defaults
> atuais. `test_schema.py` vai te cobrar isso.

## 5. DECISÃO — defaults do modo longo

Quando `video_mode == "long"` e o usuário não disser o contrário:

| Campo | Default modo longo | Por quê |
|---|---|---|
| `video_aspect` | `16:9` | YouTube longo é horizontal |
| `video_concat_mode` | `sequential` | narrativa longa não pode ter corte aleatório |
| `match_materials_to_script` | `True` | material precisa seguir a narração |
| `video_clip_duration` | `10` | 5 s em 35 min = 420 cortes, insuportável |
| `video_count` | `1` | render longo é caro; não gere variantes |
| `video_transition_mode` | `FadeIn` | corte seco a cada 10 s cansa |
| `n_threads` | `max(4, os.cpu_count()//2)` | render longo é CPU-bound |
| `subtitle_enabled` | `True` | retenção |
| `bgm_volume` | `0.15` | narração longa precisa de trilha mais discreta |
| `target_duration_minutes` | `10` | ponto de partida seguro |

Implemente isso como uma função pura e testável, ex.
`app/services/long_video.py::apply_long_video_defaults(params) -> VideoParams`, **não** como
`if` espalhado pela UI.

## 6. DECISÃO — geração de roteiro em capítulos

O teto `MAX_SCRIPT_PARAGRAPH_NUMBER = 10` **não deve ser aumentado**. Aumentar significaria pedir
35 minutos de texto numa resposta só — caro, frágil e com queda de qualidade no fim.

**A abordagem é hierárquica:** `outline → capítulo por capítulo → costura`. Detalhe completo no
plano `03`. Cada capítulo individual continua respeitando o limite de 10 parágrafos, então
`llm.generate_script()` é reusado **sem alteração**.

## 7. DECISÃO — TTS em blocos

Uma chamada de TTS por **capítulo**, não pelo roteiro inteiro. Concatenação por FFmpeg e
reconstrução do timeline de legendas com deslocamento acumulado. Detalhe no plano `04`.

## 8. DECISÃO — render FFmpeg-first

Para o modo longo, o caminho de concatenação é `concat_video_clips_with_ffmpeg()` (já existe,
`video.py:332`), **não** o merge em memória do MoviePy. Detalhe no plano `06`.

## 9. DECISÃO — a aba é UI, não um segundo pipeline

A aba nova é **apresentação**. Ela monta um `VideoParams` com `video_mode="long"` e chama o mesmo
`webui_task.submit_generation()`. Se você se pegar duplicando `_render_audio_settings`, pare e
extraia um componente compartilhado.

## 10. Pontos ABERTOS (decida você e registre)

| # | Questão | Dica |
|---|---|---|
| A-1 | Quantos capítulos por minuto-alvo? | ~1 capítulo a cada 2–3 min é um bom começo |
| A-2 | Reprocessar material entre capítulos ou baixar tudo antes? | por capítulo tende a dar melhor coesão |
| A-3 | Cache de capítulo para permitir "regerar só o capítulo 4" | muito desejável, mas pode ficar para v2 |
| A-4 | Imagens + Ken Burns como fallback quando faltar vídeo | ver plano `05` §6 |
| A-5 | Normalização de loudness dentro do pipeline ou pós-processo | ver plano `06` §6 |
| A-6 | Como paralelizar sem quebrar `max_concurrent_tasks=1` | ver plano `06` §7 |

## 11. Riscos conhecidos (e o mitigador)

| Risco | Impacto | Mitigação |
|---|---|---|
| Edge TTS falha/timeout em texto longo | bloqueia tudo | TTS por capítulo + retry por capítulo (plano `04`) |
| Pexels esgota resultados / rate limit | vídeo repetitivo | pool multi-provedor + `video_clip_duration` maior (plano `05`) |
| MoviePy estoura memória em 35 min | OOM no acer | FFmpeg concat + processamento em janelas (plano `06`) |
| Render longo bloqueia a fila da WebUI | usuário acha que travou | progresso granular + aviso na UI (plano `06` §7) |
| LLM perde coerência entre capítulos | vídeo incoerente | outline primeiro + resumo do capítulo anterior no prompt (plano `03`) |
| Legenda dessincroniza após concatenar | vídeo inutilizável | offset acumulado testado unitariamente (plano `04` §5) |
| Regressão silenciosa nos Shorts | quebra produção diária | suíte completa + teste de snapshot de params (plano `09`) |
