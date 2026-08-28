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
| 2 | Roteiro em capítulos | ✅ |
| 3 | Narração e legendas | ✅ |
| 4 | Materiais | ✅ |
| 5 | Render | ✅ |
| 6 | Orquestrador | ✅ |
| 7 | CLI e API | ✅ |
| 8 | WebUI | ✅ |
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

### 2026-08-28 — Fase 2 ✅ concluída (commit `0531dfc`)

Roteiro hierárquico: outline → capítulo a capítulo → costura.

- `LONG_FORM_SYSTEM_PROMPT` — registro de documentário, **separado** do
  `DEFAULT_SCRIPT_SYSTEM_PROMPT` dos Shorts (que continua intocado).
- `parse_outline_response` — tolera cerca de markdown e prosa em volta do JSON;
  trata o prefixo `"Error: "` do `_generate_response` como falha, não como conteúdo.
- Continuidade: cada capítulo recebe o outline completo + seu brief + a cauda do
  capítulo anterior, tudo truncado ao limite de 2000 caracteres do `video_script_prompt`.
- `char_start`/`char_end` por capítulo alinham exatamente com `full_script`.
- Termos por capítulo, em ordem narrativa, **sem** o rerank do TwelveLabs.
- Truncagem por capítulo inteiro, nunca no meio de uma frase.

**Invariante que a Fase 3 depende:** `full_script[char_start:char_end] == chapter.script`.
Há teste dedicado (`test_char_offsets_match_the_joined_script`) — se alguém mudar o
separador de costura, ele quebra imediatamente.

Testes: 75 no arquivo. Suíte: **898 passed**.

### 2026-08-28 — Fase 3 ✅ concluída (commit `8801662`)

TTS por capítulo + legendas com offset. `app/services/long_audio.py` (novo).

- Retry isolado por capítulo (3 tentativas), removendo o arquivo parcial entre
  tentativas — senão a checagem de idempotência o confundiria com capítulo pronto.
- Reuso de capítulo já sintetizado → retomar task interrompida é barato.
- `merge_srt_with_offsets` — desloca todos os cues pela duração **medida** dos
  capítulos anteriores, renumera e ordena.
- Teto de 35 min aplicado sobre o áudio real, com warning estruturado.
- `loudnorm` −14 LUFS / −1,5 dBTP + `+faststart`, copiando o stream de vídeo.

🔴 **Decisão crítica: a concatenação de áudio re-encoda, nunca usa `-c copy`.**
Os MP3 do Edge TTS têm cabeçalhos/padding inconsistentes; a cópia de stream acumula
drift, e drift de áudio é exatamente o que dessincroniza legenda no fim de um vídeo
de 35 min. Há teste que falha se alguém "otimizar" isso para `-c copy`.

**Bug encontrado por teste (e a lição):** o teste de CRLF falhava porque o helper
`_write` abria em modo texto — no Windows, `\r\n` do corpo vira `\r\r\n` em disco, e
a leitura normaliza para `\n\n`, que é o separador de blocos SRT. O parser estava
certo; **o teste é que escrevia errado**. Corrigido com `newline=""`. Vale lembrar ao
escrever qualquer fixture de arquivo com fim de linha significativo.

Testes: 28 no arquivo. Suíte: **926 passed**.

### 2026-08-28 — Fase 4 ✅ (commit `c43d13a`) — materiais

`app/services/long_materials.py` (novo). Alocação por capítulo: cada capítulo chama o
`material.download_videos` **inalterado** com seus próprios termos e sua duração medida.

🔴 **Gotcha que teria comido os créditos:** `download_videos` chama
`task_artifacts.patch_script_data(material_sources=[...])`, que **substitui** a chave inteira.
Chamando por capítulo, só as fontes do último capítulo sobreviveriam — e a descrição do YouTube
perderia a atribuição de quase todo o material. Solução: ler e acumular após cada chamada,
regravando a lista completa no fim. Há teste (`test_sources_from_every_chapter_survive`).

Também: trava de fonte paga, throttle entre capítulos, empréstimo de termos de capítulo vizinho,
e `build_credits_block()`. Testes: 27. Suíte: **953 passed**.

### 2026-08-28 — Fase 5 ✅ (commit `4acb314`) — render

`app/services/long_render.py` (novo). FFmpeg-first: cada clipe é normalizado por um comando
FFmpeg e escrito em disco, depois um único `concat`. Memória constante independente do número
de clipes.

🔴 **`setsar=1` não é opcional.** SAR inconsistente é a causa nº 1 de "concatenou mas o vídeo
saiu esticado". Todos os clipes saem com resolução, fps, pix_fmt e SAR idênticos.

`verify_long_video()` cobre decode, duração, codec, pix_fmt, stream de áudio e posição do átomo
`moov`. Testes: 39. Suíte: **992 passed**.

### 2026-08-28 — Fase 6 ✅ (commit `08c4b1a`) — orquestrador

Ramos do modo longo dentro do `_run_pipeline` existente (não um orquestrador paralelo), então
`stop_at`, tratamento de falha, estado da task e cross-post continuam valendo.

O teste que mais importa da entrega inteira está em `test_long_pipeline.py::TestShortModeIsUntouched`:
com `video_mode="short"`, o pipeline chama exatamente as funções originais e **nunca** toca em
nenhum módulo `long_*`.

Suíte: **1006 passed**.

### 2026-08-28 — Fase 7 ✅ (commit `11b8e65`) — CLI e API

Flags `--long`, `--duration`, `--chapters`, `--outline`, `--narrate-chapter-titles`,
`--no-normalize-loudness`.

**Detalhe que quase passou batido:** `--video-aspect` tinha `default="9:16"` no argparse, o que
faria `model_fields_set` sempre conter `video_aspect` e o default 16:9 do modo longo **nunca**
se aplicaria. Mudado para `default=None` com resolução posterior — o caminho curto continua
resolvendo para 9:16 exatamente como antes.

**Reuso em vez de duplicação:** a CLI já tinha `--confirm-seedance-charge` para fonte paga.
Cheguei a criar um `--confirm-paid-source` paralelo; o teste apontou o conflito e removi —
o modo longo agora reusa o sinal existente.

A API não precisou de mudança: `TaskVideoRequest` herda de `VideoParams`, então os campos novos
entram automaticamente e um payload antigo continua idêntico.

Suíte: **1025 passed**.

### 2026-08-28 — Fase 8 ✅ (commit `6a859dc`) — a aba

`st.tabs` no nível principal. `_render_shorts_workspace()` é o corpo anterior extraído **sem
alterar uma linha**, mantendo as keys sem prefixo (as configurações salvas do usuário continuam
valendo). A aba longa prefixa tudo com `long_`.

**Decisão de escopo:** a aba longa **não** reusa `_render_audio_settings` /
`_render_subtitle_settings`. Somadas dão ~900 linhas cobrindo uma dúzia de provedores de TTS;
parametrizá-las traria risco de regressão desproporcional ao ganho. A aba longa tem um conjunto
enxuto de controles e herda o resto do que já está salvo.

🔴 **Regressão de 38 testes que eu mesmo causei e o que ensinou:** usei `st.selectbox` com
`format_func` mapeando `""` → "Auto Detect". O harness de teste do Streamlit procura o **valor
cru** entre as **opções formatadas** e estoura `ValueError: '' is not in list`. O projeto já
tinha o helper `stable_selectbox` exatamente para isso. **Use `stable_selectbox`, nunca
`st.selectbox` com `format_func`.**

Verificação que a suíte não faz sozinha (plano `07` §8): renderizei a página real via `AppTest` —
sem exceção, **sem keys duplicadas**, widgets `long_*` presentes.

19 chaves de i18n nos 12 locales. Suíte: **1025 passed**.

### 2026-08-28 — auditoria de retomada ✅

A retomada não confiou apenas nos estados marcados como concluídos. A comparação do código com
os contratos dos planos `02`, `04`, `07`, `08` e `09` encontrou e corrigiu lacunas que os testes
mockados anteriores não cobriam:

- roteiro próprio no modo longo agora é dividido em capítulos **sem qualquer chamada de LLM**;
- `narrate_chapter_titles` deixou de ser flag morta e participa do roteiro, offsets e legendas;
- `voice_volume` é encaminhado para cada chamada TTS;
- retomada de áudio por capítulo também reaproveita o SRT já existente;
- áudio externo recebe offsets/durações proporcionais por capítulo, então materiais e Whisper
  continuam funcionando;
- Whisper transcreve o áudio concatenado uma vez e corrige fatias por capítulo, evitando o custo
  quadrático sobre o roteiro inteiro;
- a WebUI longa aceita roteiro próprio e preserva escolhas explícitas como 9:16, sem impedir o
  default de threads do serviço;
- endpoints `/api/v1/long-videos/outline` e `/api/v1/tasks/{id}/chapters` implementados;
- estado da task expõe offsets e durações medidos dos capítulos durante a execução;
- normalização final fixa AAC 48 kHz, e o verificador passou a medir H.264 High, loudness e true
  peak, além das verificações já existentes.

Provas locais: bateria focada do modo longo **238 passed**; suíte completa **1036 passed**, com a
mesma única falha de ambiente Windows da linha de base. Smoke real de CLI com roteiro próprio e
`--stop-at script` produziu 3 capítulos e não chamou LLM. As rejeições `--duration 40` e
`--duration` sem `--long` foram confirmadas na CLI antes de qualquer geração.

---

## Decisões tomadas durante a implementação

*(preencher — corresponde à tabela de pontos ABERTOS do plano `02` §10)*

| Ponto | Decisão | Justificativa |
|---|---|---|
| A-1 capítulos por minuto | ~1 capítulo / 2,5 min (`DEFAULT_SECONDS_PER_CHAPTER = 150`), limitado a 3–14 | dá capítulos com corpo suficiente para o LLM desenvolver, sem fragmentar |
| A-2 material por capítulo × tudo antes | **por capítulo** | resolve coesão de graça: o clipe do minuto 30 não aparece no minuto 2 |
| A-3 cache de capítulo | **parcial na v1** — o áudio por capítulo já é idempotente e reusado; regerar só o capítulo N ainda não tem UI | o ganho principal (retomar task interrompida) já está; a UI fica para v2 |
| A-4 imagens + Ken Burns | **não implementado na v1** | o empréstimo de termos entre capítulos vizinhos + a cota por fonte já evitam buracos visuais; Ken Burns fica para v2 |
| A-5 loudness no pipeline | **dentro do pipeline**, `normalize_loudness=True` por padrão no modo longo | tira o pós-processo do Hermes e faz o QA do canal passar direto |
| A-6 paralelismo e fila | **não mexer em `max_concurrent_tasks=1`** | o lock de configuração é por processo; duas gerações simultâneas compartilhariam provider/chaves. A aba longa avisa que ocupa a fila (`Long Video Queue Warning`) |
| RNF-04 tempo de render | ⬜ **pendente — exige rodar um vídeo real** | nenhuma medição foi feita ainda; ver "Próximos passos" |

---

## Arquivos criados/modificados

*(mantenha esta lista atualizada — é o que permite outro agente retomar)*

| Arquivo | Tipo | Fase | Descrição |
|---|---|---|---|
| `app/models/const.py` | modificado | 1 | constantes do modo longo |
| `app/models/schema.py` | modificado | 1 | `ChapterOutlineItem`, 6 campos, validador |
| `app/services/long_video.py` | **novo** | 1 | módulo central do modo longo |
| `test/services/test_long_video.py` | **novo** | 1–2 | 75 testes |
| `app/services/long_audio.py` | **novo** | 3 | TTS por capítulo, merge SRT, loudnorm |
| `test/services/test_long_audio.py` | **novo** | 3 | 28 testes |
| `app/services/long_materials.py` | **novo** | 4 | materiais por capítulo, créditos |
| `test/services/test_long_materials.py` | **novo** | 4 | 27 testes |
| `app/services/long_render.py` | **novo** | 5 | render FFmpeg-first, verificação |
| `test/services/test_long_render.py` | **novo** | 5 | 39 testes |
| `app/services/task.py` | modificado | 6 | ramos do modo longo no pipeline |
| `test/services/test_long_pipeline.py` | **novo** | 6–7 | 28 testes (inclui não-regressão) |
| `cli.py` | modificado | 7 | flags do modo longo |
| `app/services/webui_task.py` | modificado | 8 | parâmetro `stop_at` |
| `webui/Main.py` | modificado | 8 | abas + workspace longo |
| `webui/i18n/*.json` (12) | modificados | 8 | 19 chaves novas cada |
| `test/services/test_webui_task.py` | modificado | 8 | acompanha a extração |
| `test/services/test_webui_long_tab.py` | **novo** | auditoria | roteiro próprio + defaults explícitos da aba longa |

---

## Problemas encontrados

| # | Problema | Estado |
|---|---|---|
| 1 | `test_headless_open_folder_shows_host_mapped_path` vermelho na linha de base | **pré-existente**, ambiente Windows (`os.uname` no numpy). Não ocorre no acer. Ignorar. |
| 2 | `patch_script_data` sobrescreve `material_sources` a cada chamada | resolvido na Fase 4 (acumulação) |
| 3 | `st.selectbox` + `format_func` quebra 38 testes de WebUI | resolvido usando `stable_selectbox` |
| 4 | `--video-aspect` com default fixo impediria o 16:9 do modo longo | resolvido (`default=None` + resolução posterior) |
| 5 | `--confirm-paid-source` duplicava `--confirm-seedance-charge` | removido, reusa o existente |
| 6 | Fixture de teste escrevia `\r\r\n` em modo texto no Windows | corrigido com `newline=""` |

---

## Próximos passos (para quem assumir)

Fases 1–8 estão **prontas e commitadas**. O modo longo funciona de ponta a ponta por CLI, API e
WebUI, com 1025 testes verdes. O que falta é **exatamente o que não dá para fazer sem rodar um
vídeo real e sem tocar em produção**:

### Fase 9 — QA de ponta a ponta (o bloqueador real)

Nenhum vídeo longo foi gerado de verdade ainda: tudo que existe é teste com LLM, TTS e FFmpeg
mockados. É preciso, **no acer** (Linux, onde a produção roda):

1. `uv run pytest test/ -q` na máquina de produção (a falha nº 1 acima deve sumir lá).
2. Gerar um longo de **5 min** e assistir inteiro.
3. Gerar um de **20 min** e rodar o QA técnico (`long_render.verify_long_video`).
4. **Conferir a legenda no fim do vídeo**, não só no começo — erro de offset só aparece tarde.
5. Testar `--long --duration 40` → deve falhar; e 35 → deve caber.
6. Medir tempo de render, pico de RSS e disco por duração (tabela abaixo) → fecha o RNF-04.
7. Escrever `RELATORIO-QA.md`.

### Fases 10–11 — publicação e Hermes

Ver planos `11` e `10`. Nada disso foi iniciado. Lembretes que valem ouro:
- a sanitização por placeholders é **obrigatória** antes do push público (plano `11` §3.1);
- existem **três** cópias de skill e um timer de sync que pode desfazer a edição (plano `10` §3.1);
- o teste de aceite do Hermes é perguntar a ele, sem dica, como gera o vídeo longo.

---

## Medições

| Métrica | 5 min | 15 min | 30 min |
|---|---|---|---|
| Tempo de render | | | |
| Pico de RSS | | | |
| Tamanho do MP4 | | | |
| Tamanho da pasta da task | | | |
