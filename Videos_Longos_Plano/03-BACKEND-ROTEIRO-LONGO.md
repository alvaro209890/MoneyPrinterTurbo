# 03 — Backend: roteiro longo em capítulos + palavras-chave

> **Objetivo:** produzir um roteiro coerente de até 35 minutos usando o mesmo `llm.generate_script()`
> que os Shorts já usam, sem tocar no teto de 10 parágrafos.

---

## 1. O problema, em números

| Grandeza | Valor |
|---|---|
| Velocidade de narração pt-BR (Edge TTS, rate 1.0) | ~**150 palavras/min** |
| Caracteres por minuto de narração | ~**900** |
| 35 minutos | ~5.250 palavras / ~31.500 caracteres |
| Máximo hoje (`MAX_SCRIPT_PARAGRAPH_NUMBER = 10`) | ~400–600 palavras (~3–4 min) |

> ⚠️ Calibre a constante de palavras/min **medindo** com a voz padrão do canal
> (`pt-BR-ThalitaMultilingualNeural-Female`) em vez de confiar nesta tabela. Ela é ponto de partida.

## 2. Arquitetura escolhida: geração hierárquica

```
   tema + duração-alvo
            │
            ▼
   ┌─────────────────────┐
   │ 1. OUTLINE          │  1 chamada LLM → N capítulos (título + tese + o que cobrir)
   └──────────┬──────────┘
              │  para cada capítulo i:
              ▼
   ┌─────────────────────┐
   │ 2. CAPÍTULO i       │  1 chamada LLM, recebe: outline completo + resumo do capítulo i-1
   │    (reusa           │  → texto de ~alvo_min_do_capítulo
   │  llm.generate_script)│
   └──────────┬──────────┘
              │
              ▼
   ┌─────────────────────┐
   │ 3. COSTURA          │  concatena capítulos, normaliza espaçamento, valida duração estimada
   └──────────┬──────────┘
              │
              ▼
   ┌─────────────────────┐
   │ 4. KEYWORDS         │  termos por capítulo (não globais) — ver §4
   └─────────────────────┘
```

**Por que hierárquico e não uma chamada gigante:**
- mantém cada chamada dentro de limites confortáveis de token e de qualidade;
- permite retry **por capítulo** (não perde 30 min de trabalho por 1 erro);
- permite regerar só um capítulo depois (item A-3 do plano `02`);
- deixa `llm.generate_script()` intacto → zero risco para os Shorts.

## 3. Novo módulo: `app/services/long_video.py`

Crie um módulo dedicado. **Não** enfie isso dentro de `llm.py` (que já tem 980 linhas e é
compartilhado com os Shorts).

### 3.1 Estruturas

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Chapter:
    index: int                       # 1-based
    title: str
    brief: str                       # o que este capítulo precisa cobrir (vem do outline)
    target_seconds: float            # duração-alvo desta seção
    script: str = ""                 # preenchido na etapa 2
    terms: list[str] = field(default_factory=list)   # preenchido na etapa 4

@dataclass
class LongVideoPlan:
    subject: str
    language: str
    target_seconds: float
    chapters: list[Chapter]

    @property
    def full_script(self) -> str: ...
    @property
    def estimated_seconds(self) -> float: ...
```

### 3.2 Funções públicas do módulo

| Função | Responsabilidade |
|---|---|
| `estimate_narration_seconds(text, language) -> float` | estimativa determinística de duração |
| `plan_chapters(subject, target_seconds, language, chapter_count=None, ...) -> LongVideoPlan` | etapa 1 (outline) |
| `generate_chapter_script(plan, index, previous_summary) -> str` | etapa 2 (um capítulo) |
| `build_long_script(plan, progress_cb=None) -> LongVideoPlan` | etapa 2 em loop + etapa 3 |
| `generate_chapter_terms(plan) -> LongVideoPlan` | etapa 4 |
| `apply_long_video_defaults(params) -> VideoParams` | defaults do plano `02` §5 |

`progress_cb` é essencial: o orquestrador vai usá-lo para mover a barra de progresso de 5% → 20%
enquanto os capítulos são escritos (RF-07).

## 4. Etapa 1 — o outline

Uma chamada de LLM via `llm._generate_response()` (`llm.py:142`) com prompt próprio.

**Contrato de saída: JSON estrito.** Reuse `llm._strip_code_fence()` (`llm.py:583`) — os provedores
não-OpenAI envelopam JSON em cerca de markdown mesmo quando você pede para não fazer.

Esqueleto do prompt (adapte, mas mantenha os elementos):

```
# Role: Long-form Video Outline Planner

## Goal
Break the subject into {n} sequential chapters that together form a single {minutes}-minute
narrated documentary-style video.

## Constraints
1. Return ONLY a json array. No prose, no markdown fence.
2. Each item: {"title": str, "brief": str, "weight": float}
3. `brief` describes what this chapter must cover, in one or two sentences.
4. `weight` is the share of total runtime (all weights sum to 1.0).
5. Chapters must be sequential and non-overlapping: chapter N+1 continues where N stopped.
6. Chapter 1 must hook the viewer in the first 15 seconds.
7. The final chapter must deliver a payoff/conclusion, not a summary of the previous ones.
8. Language of `title` and `brief`: {language}

## Context
### Subject
{subject}
### Total runtime
{minutes} minutes
```

**Cálculo de `n` (quantidade de capítulos)** quando o usuário não especificar — item A-1 do plano
`02`:

```python
# ~2,5 min por capítulo, com piso e teto de sanidade
n = max(3, min(14, round(target_seconds / 150)))
```

**Validação obrigatória da resposta:**
- é lista não vazia;
- todo item tem `title` e `brief` string não vazias;
- `weight` numérico > 0 — se faltar ou vier inválido, **distribua uniformemente** em vez de falhar;
- normalize os pesos para somar 1,0;
- `target_seconds` de cada capítulo = `weight * target_seconds_total`.

Falha após `_max_retries` → `_mark_task_failed(task_id, "outline", ...)`.

## 5. Etapa 2 — o capítulo

**Reuse `llm.generate_script()` sem modificá-la.** Traduza o capítulo para os parâmetros que ela já
aceita:

```python
paragraph_number = clamp(round(chapter.target_seconds / 45), 1, 10)   # ~45 s por parágrafo
script = llm.generate_script(
    video_subject=f"{plan.subject} — {chapter.title}",
    language=plan.language,
    paragraph_number=paragraph_number,
    video_script_prompt=_build_chapter_requirements(plan, chapter, previous_summary),
    custom_system_prompt=LONG_FORM_SYSTEM_PROMPT,   # ver §5.2
)
```

> ⚠️ `video_script_prompt` é truncado em **2000 caracteres** (`llm.py:476`) e
> `custom_system_prompt` em **8000** (`llm.py:479`). Monte `_build_chapter_requirements` para caber:
> resumo do capítulo anterior **comprimido** (2–3 frases), não o texto inteiro.

### 5.1 Continuidade entre capítulos

O maior risco de qualidade é o capítulo 5 repetir o capítulo 2. Mitigadores, em ordem de custo:

1. **Sempre** injete no `video_script_prompt`: o outline completo (só títulos), o índice do capítulo
   atual, e as **últimas 2 frases** do capítulo anterior (para emenda natural).
2. Instrua explicitamente: *"Do not re-introduce the subject; the viewer has already watched
   chapters 1..N-1. Continue directly."*
3. Se o orçamento de tokens permitir, adicione uma lista de "fatos já ditos" acumulada — mas isso é
   opcional; comece sem.

### 5.2 System prompt do modo longo

Crie `LONG_FORM_SYSTEM_PROMPT` no `long_video.py` (não sobrescreva o
`DEFAULT_SCRIPT_SYSTEM_PROMPT` de `llm.py:29`, que é dos Shorts). Diferenças que ele deve ter:

- ritmo de documentário, não de Short: frases mais longas, menos exclamação;
- proibir "neste vídeo vamos ver..." e outros marcadores de meta-discurso;
- proibir listas numeradas faladas ("primeiro... segundo...") como muleta estrutural;
- exigir transição de saída em cada capítulo que puxe o próximo;
- **sem markdown, sem emoji, sem colchetes** — a saída vai direto para o TTS.

> ⚠️ `llm.generate_script()` já remove `*`, `#`, `[...]` e `(...)` (`llm.py:536-543`). Mas parênteses
> removidos podem comer conteúdo legítimo — instrua o modelo a não usá-los.

### 5.3 Controle de duração por capítulo

Depois de gerar, compare `estimate_narration_seconds(script)` com `chapter.target_seconds`:

- dentro de ±25% → aceita;
- muito curto → 1 retry pedindo expansão (`"expand to approximately X words"`);
- muito longo → **não** peça encurtamento (caro); aceite e deixe o controle global de §7 resolver.

Registre no log a diferença — isso é o dado que vai calibrar sua constante de palavras/min.

## 6. Etapa 3 — costura

```python
full_script = "\n\n".join(ch.script.strip() for ch in chapters if ch.script.strip())
```

Regras:
- separador `\n\n` (é o que `voice.create_subtitle` e o TTS já esperam como quebra de parágrafo);
- normalize espaços múltiplos e linhas em branco triplas;
- **não** insira os títulos dos capítulos no texto narrado (eles são metadados, não fala) — a menos
  que a UI ative a opção "narrar títulos de capítulo";
- guarde o mapa `capítulo → intervalo de caracteres` no `script.json` da task
  (`task.save_script_data`, `task.py:351`) — o plano `04` §5 e a UI vão precisar dele.

## 7. Guarda de duração global

Antes de devolver o plano montado:

```python
estimated = plan.estimated_seconds
if estimated > const.LONG_VIDEO_MAX_DURATION_SECONDS * 1.05:   # 5% de tolerância na estimativa
    # descarte capítulos finais inteiros, nunca corte no meio de uma frase
    plan = _truncate_to_budget(plan, const.LONG_VIDEO_MAX_DURATION_SECONDS)
    logger.warning("long video plan truncated to fit the 35-minute cap")
```

> A verdade final é a duração do **áudio real**, não a estimativa. Este guarda é só para não gastar
> TTS à toa. O corte definitivo está no plano `04` §6.

## 8. Etapa 4 — palavras-chave (a parte "mesma lógica dos reels")

Os Shorts geram 5 (ou 8) termos para o roteiro inteiro (`task.py:316`). Para 35 minutos isso não
serve: os termos do minuto 2 não podem ilustrar o minuto 28.

**Gere termos por capítulo**, reusando `llm.generate_terms()` sem alterá-la:

```python
for chapter in plan.chapters:
    chapter.terms = llm.generate_terms(
        video_subject=f"{plan.subject} — {chapter.title}",
        video_script=chapter.script,
        amount=max(3, round(chapter.target_seconds / 30)),   # ~1 termo a cada 30 s
        match_script_order=True,                             # ordem cronológica dentro do capítulo
    )
```

Depois monte a lista global **preservando a ordem narrativa**:

```python
video_terms = [t for ch in plan.chapters for t in ch.terms]
video_terms = _dedupe_preserving_order(video_terms)
```

> ⚠️ **Não** rode `twelvelabs.rerank_terms_by_subject()` no modo longo. Ele reordena por relevância
> temática e destruiria a ordem cronológica — exatamente como `task.py:342` já evita quando
> `match_materials_to_script` está ligado. Siga a mesma regra.

**Cap de termos:** um vídeo de 35 min gera ~70 termos. Cada termo vira ≥ 1 busca em API de estoque.
Ver plano `05` §3 sobre orçamento de requisições.

## 9. Integração no orquestrador

Em `task.py::_run_pipeline` (linha 1242), a mudança é cirúrgica:

```python
# ANTES da etapa 1 atual (task.py:1315-1316)
if params.video_mode == "long":
    plan = long_video.build_long_script(params, progress_cb=_make_progress_cb(task_id, 5, 18))
    video_script = plan.full_script
    long_plan = plan                       # guarde para as etapas seguintes
else:
    video_script = generate_script(task_id, params)   # caminho atual, intocado
```

E na etapa 2:

```python
if params.video_mode == "long":
    video_terms = long_video.collect_terms(long_plan)
else:
    video_terms = generate_terms(task_id, params, video_script)   # caminho atual, intocado
```

> ✅ Repare que o `stop_at == "script"` e `stop_at == "terms"` continuam funcionando de graça — a UI
> ganha preview de roteiro longo sem nenhum código extra.

## 10. Persistência

Estenda `save_script_data()` (`task.py:351`) para gravar, no modo longo, também:

```json
{
  "script": "...",
  "search_terms": ["..."],
  "params": {...},
  "long_video": {
    "subject": "...",
    "target_seconds": 1200,
    "estimated_seconds": 1187.4,
    "chapters": [
      {"index": 1, "title": "...", "brief": "...", "target_seconds": 150,
       "char_start": 0, "char_end": 1420, "terms": ["..."]}
    ]
  }
}
```

Isso é o que permite: reabrir a task na UI, regerar um capítulo, e o Hermes relatar a estrutura do
vídeo no changelog.

## 11. Testes obrigatórios deste plano

Em `test/services/test_long_video.py` (arquivo novo):

- `test_estimate_narration_seconds_is_monotonic` — texto maior → duração maior.
- `test_plan_chapters_normalizes_weights` — pesos somando ≠ 1 são normalizados.
- `test_plan_chapters_falls_back_to_uniform_weights` — `weight` ausente/inválido não quebra.
- `test_plan_chapters_rejects_empty_outline` — resposta vazia do LLM → erro claro.
- `test_chapter_paragraph_number_is_clamped` — nunca pede > 10 parágrafos ao `llm.generate_script`.
- `test_build_long_script_truncates_over_budget` — plano acima de 35 min perde capítulos do fim.
- `test_collect_terms_preserves_chapter_order` — ordem cronológica preservada, sem duplicata.
- `test_long_mode_does_not_call_twelvelabs_rerank` — o rerank não é chamado no modo longo.
- `test_short_mode_pipeline_unchanged` — com `video_mode="short"`, o caminho antigo é chamado
  exatamente como antes (mock em `llm.generate_script` conferindo os argumentos).

Todos com o LLM **mockado**. Nenhum teste deste plano pode fazer chamada de rede.
