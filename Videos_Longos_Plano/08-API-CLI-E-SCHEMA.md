# 08 — Schema, API e CLI

> **Objetivo:** expor o modo longo pelas três portas de entrada com o **mesmo** contrato, sem
> quebrar nenhum cliente existente. O Hermes usa a CLI — então esta parte não é opcional.

---

## 1. Schema (`app/models/schema.py`)

### 1.1 Campos novos em `VideoParams` (classe na linha 66)

```python
video_mode: Literal["short", "long"] = "short"
target_duration_minutes: Optional[float] = Field(default=None, ge=3, le=35)
chapter_count: Optional[int] = Field(default=None, ge=3, le=14)
chapter_outline: Optional[List[ChapterOutlineItem]] = None
narrate_chapter_titles: bool = False
normalize_loudness: bool = True
```

E um modelo auxiliar:

```python
class ChapterOutlineItem(BaseModel):
    title: str = Field(max_length=200)
    brief: str = Field(default="", max_length=1000)
    weight: float = Field(default=0.0, ge=0.0, le=1.0)
```

### 1.2 Regras de compatibilidade — leia com atenção

1. **Todo campo novo tem default.** Um `VideoParams(video_subject="x")` precisa continuar válido.
2. **Não altere defaults existentes.** Em especial `video_aspect` continua `9:16` e
   `video_clip_duration` continua `5`. Os defaults do modo longo são aplicados por
   `apply_long_video_defaults()` (plano `02` §5), **não** mudando o schema.
3. **Não mexa em `paragraph_number`** (`schema.py:127`, `ge=1, le=10`). Ele continua sendo o
   parâmetro do capítulo individual.
4. `le=35` no `target_duration_minutes` dá a validação Pydantic de graça na API — mas **não
   substitui** a checagem em código do plano `02` §3 (tasks antigas, CLI, chamadas internas).

### 1.3 Validador cruzado

```python
@model_validator(mode="after")
def _validate_long_mode(self):
    if self.video_mode == "long":
        if self.target_duration_minutes is None and not self.chapter_outline:
            raise ValueError("long mode requires target_duration_minutes or chapter_outline")
        if self.video_count != 1:
            raise ValueError("long mode supports a single output video")
    return self
```

> ⚠️ Pydantic v2 — o projeto usa `pydantic.dataclasses` e `ConfigDict` (`schema.py:49`). Use
> `@model_validator(mode="after")`, não o `@validator` v1.

## 2. API (`app/controllers/v1/video.py`, roteada em `app/router.py`)

### 2.1 Endpoints existentes continuam iguais

`TaskVideoRequest` herda de `VideoParams` (`schema.py:217`), então **os campos novos entram
automaticamente** em `POST /api/v1/videos`. Nenhum endpoint novo é estritamente necessário.

Um cliente antigo que não mande `video_mode` recebe exatamente o comportamento de hoje. ✅

### 2.2 Endpoints novos recomendados

| Método | Rota | Para quê |
|---|---|---|
| `POST` | `/api/v1/long-videos/outline` | gera só o outline (equivale a `stop_at="script"` no modo longo) |
| `GET` | `/api/v1/tasks/{task_id}/chapters` | devolve a estrutura de capítulos + offsets + status |

O segundo é o que permite ao Hermes relatar "vídeo de 9 capítulos, 14min32s" sem abrir o MP4.

### 2.3 Resposta da task

`TaskStatusData` (`schema.py:251`) já tem `model_config = ConfigDict(extra="allow")` — campos extras
passam direto. Acrescente, no modo longo:

```json
{
  "task_id": "...", "state": 1, "progress": 100,
  "videos": ["/tasks/xxx/final-1.mp4"],
  "long_video": {
    "duration_seconds": 872.4,
    "chapter_count": 7,
    "truncated": false,
    "verification": {"faststart": true, "loudness_lufs": -14.1, "decode_ok": true}
  },
  "warnings": [{"code": "long_video_material_repeated", "message": "..."}]
}
```

Documente os campos novos no `json_schema_extra` do `TaskQueryResponse` (`schema.py:332`), seguindo
o padrão que já está lá.

## 3. CLI (`cli.py`)

O Hermes chama a CLI através do wrapper `mpt.py` (plano `10`). Esta é a porta mais importante.

### 3.1 Argumentos novos

No grupo `script and content` (`cli.py:212`):

```
--long                          liga o modo longo (equivale a --video-mode long)
--duration MINUTES              duração-alvo em minutos (3 a 35)
--chapters N                    número de capítulos (3 a 14); omitido = automático
--outline PATH                  JSON com o outline pronto (pula a etapa 1)
--narrate-chapter-titles        narra os títulos (default: não)
--no-normalize-loudness         desliga a normalização −14 LUFS (default: ligada no modo longo)
```

### 3.2 Regras da CLI

1. `--long` sem `--duration` → default **10 min** (não falhe; o Hermes vai usar assim).
2. `--duration` sem `--long` → **erro claro**, não ative o modo longo silenciosamente.
3. `--duration 40` → erro imediato do argparse com mensagem citando o teto de 35.
   Crie um `_long_duration_minutes(value)` no mesmo estilo de `_paragraph_count` (`cli.py:63`).
4. `--stop-at` continua aceitando `("script","terms","audio","subtitle","materials","video")`
   (`_PIPELINE_STAGES`). No modo longo, `script` devolve o outline + roteiro completo.
5. `--aspect` omitido no modo longo → **16:9** (default do plano `02` §5), não 9:16.

### 3.3 Saída da CLI

Imprima, ao final, um JSON de uma linha em stdout (o wrapper do Hermes parseia isso):

```json
{"task_id":"...","video":"/abs/path/final-1.mp4","duration":872.4,"chapters":7,"truncated":false,"warnings":[]}
```

> ⚠️ Mantenha o formato de saída atual para o modo curto. `test_cli.py` cobre a CLI —
> leia-o antes de mexer.

## 4. Modo batch

`cli.py` tem suporte a arquivo de lote (`_BATCH_FILE_MAX_BYTES`, `_BATCH_TASK_MAX_COUNT = 100`).

**Decisão:** no lote, **rejeite** misturar tarefas longas com curtas na mesma execução, ou limite a
no máximo 2 tarefas longas por arquivo. Cem vídeos de 35 min num lote seria ~58 horas de render —
melhor falhar cedo com mensagem explícita do que descobrir depois.

## 5. Checklist de compatibilidade (rode antes de fechar esta parte)

- [ ] `VideoParams(video_subject="x")` continua válido e com todos os defaults antigos.
- [ ] `POST /api/v1/videos` com um payload de Short antigo produz **exatamente** o mesmo resultado.
- [ ] Uma task salva antes da mudança abre no histórico da WebUI sem erro.
- [ ] `python cli.py --subject "teste"` continua funcionando sem nenhuma flag nova.
- [ ] `test_schema.py`, `test_controller_video.py`, `test_cli.py` verdes sem alteração.

## 6. Testes obrigatórios deste plano

- `test_schema.py` (estender): defaults preservados; validador cruzado rejeita
  `video_mode="long"` sem duração; `target_duration_minutes=36` rejeitado; `video_count=2` no modo
  longo rejeitado; `ChapterOutlineItem` valida tamanho.
- `test_controller_video.py` (estender): payload antigo → comportamento antigo; payload longo →
  `video_mode` chega ao `task.start`; `/long-videos/outline` responde com a estrutura esperada.
- `test_cli.py` (estender): `--duration` sem `--long` falha; `--duration 40` falha; `--long` sem
  `--duration` usa 10; `--long` sem `--aspect` usa 16:9; JSON de saída tem as chaves novas;
  invocação antiga inalterada.
