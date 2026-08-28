# PROGRESSO — implementação da aba Vídeos Longos

> Estado vivo da implementação. **Atualize a cada fase concluída.**
> Se você é um agente assumindo este trabalho, leia este arquivo primeiro, depois
> `00-LEIA-PRIMEIRO.md` e `12-CHECKLIST-EXECUCAO.md`.

**Branch:** `feat/long-video-tab`
**Base:** `eb8c237` (`feat(material): add native Seedance provider`), MoneyPrinterTurbo `1.3.5`
**Repo de trabalho:** `C:\GIS\MoneyPrinterTurbo` (Windows) — produção é o `acer`
**Ambiente:** Python 3.12.10 · uv 0.12.7 · ffmpeg 8.1.1

---

## Estado por fase

| Fase | Descrição | Estado |
|---|---|---|
| 0 | Reconhecimento e linha de base | ✅ |
| 1 | Fundação (schema, constantes, defaults) | ✅ |
| 2 | Roteiro em capítulos | ⬜ |
| 3 | Narração e legendas | ⬜ |
| 4 | Materiais | ⬜ |
| 5 | Render | ⬜ |
| 6 | Orquestrador | ⬜ |
| 7 | CLI e API | ⬜ |
| 8 | WebUI | ⬜ |
| 9 | QA completo | ⬜ |
| 10 | Publicação | ⬜ |
| 11 | Hermes (5 superfícies) | ⬜ |
| 12 | Encerramento | ⬜ |

Legenda: ⬜ não iniciada · 🟡 em andamento · ✅ concluída · 🔴 bloqueada

---

## Log de execução

### 2026-08-28 — Fase 0 ✅ concluída

- Branch `feat/long-video-tab` criada a partir de `main` @ `eb8c237`.
- Ambiente: Python 3.12.10, uv 0.12.7, ffmpeg 8.1.1. `uv sync` ok.
- **Linha de base:** `823 passed, 1 failed, 10 skipped, 7241 subtests` em 98 s.

🔴 **A falha da linha de base é PRÉ-EXISTENTE e de ambiente Windows.** Confirmada com
`git stash` na árvore limpa (`eb8c237`), antes de qualquer alteração minha:

```
test_webui_headless_task_actions.py::test_headless_open_folder_shows_host_mapped_path
AttributeError: module 'os' has no attribute 'uname'
  numpy/__init__.py:919 in hugepage_setup
```

É o numpy tentando detectar hugepages do kernel Linux via `os.uname()`, que não existe no
Windows. **Não ocorre no `acer` (Linux, que é a produção).** Não tente consertar: não é do
projeto e some sozinha no ambiente real. Trate `823 passed / 1 failed` como o "verde" desta
máquina.

### 2026-08-28 — Fase 1 ✅ concluída

Fundação pronta: constantes, schema e defaults do modo longo.

- `app/models/const.py` — `VIDEO_MODE_*`, `LONG_VIDEO_MAX_DURATION_SECONDS` (2100),
  `LONG_VIDEO_MIN_DURATION_SECONDS` (180), limites de capítulo, códigos de warning.
- `app/models/schema.py` — `ChapterOutlineItem` + 6 campos novos em `VideoParams`, todos
  opcionais com default, mais o validador cruzado `_validate_long_mode`.
- `app/services/long_video.py` — módulo novo: estimativa de narração, derivação de
  `paragraph_number`, sugestão de nº de capítulos, `resolve_target_seconds` (aplica o teto),
  `apply_long_video_defaults`, dataclasses `Chapter`/`LongVideoPlan`, `normalize_chapter_weights`.
- `test/services/test_long_video.py` — **43 testes**, todos verdes.

**Decisão de implementação relevante:** `apply_long_video_defaults` usa
`params.model_fields_set` (Pydantic v2) para distinguir "campo não informado" de "campo
informado com valor igual ao default". É isso que faz `--long --duration 12` render 16:9
automaticamente, sem sobrescrever quem passou `--aspect 9:16` de propósito. A alternativa
(comparar com o default) seria ambígua e silenciosamente errada.

---

## Decisões tomadas durante a implementação

*(preencher — corresponde à tabela de pontos ABERTOS do plano `02` §10)*

| Ponto | Decisão | Justificativa |
|---|---|---|
| A-1 capítulos por minuto | | |
| A-2 material por capítulo × tudo antes | | |
| A-3 cache de capítulo | | |
| A-4 imagens + Ken Burns | | |
| A-5 loudness no pipeline | | |
| A-6 paralelismo e fila | | |
| RNF-04 tempo de render | | |

---

## Arquivos criados/modificados

*(mantenha esta lista atualizada — é o que permite outro agente retomar)*

| Arquivo | Tipo | Fase | Descrição |
|---|---|---|---|
| `app/models/const.py` | modificado | 1 | constantes do modo longo |
| `app/models/schema.py` | modificado | 1 | `ChapterOutlineItem`, 6 campos, validador |
| `app/services/long_video.py` | **novo** | 1 | módulo central do modo longo |
| `test/services/test_long_video.py` | **novo** | 1 | 43 testes |

---

## Problemas encontrados

*(registre qualquer surpresa: teste que já estava vermelho, dependência quebrada, etc.)*

---

## Medições

| Métrica | 5 min | 15 min | 30 min |
|---|---|---|---|
| Tempo de render | | | |
| Pico de RSS | | | |
| Tamanho do MP4 | | | |
| Tamanho da pasta da task | | | |
