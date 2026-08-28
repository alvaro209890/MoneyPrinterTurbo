# 12 — Checklist de execução (a ordem exata)

> Trabalhe **em fases**. Cada fase termina com a suíte verde e um commit. Se uma fase não fechar,
> **não avance** — o custo de depurar duas camadas quebradas ao mesmo tempo é muito maior.

---

## Fase 0 — Reconhecimento (não escreva código ainda)

- [ ] Ler `00`, `01`, `02` deste diretório.
- [ ] `git status` e `git log --oneline -20` — entender onde o repo está.
- [ ] Rodar a suíte e **guardar a saída** como linha de base:
      `uv run pytest test/ -q > /tmp/baseline-tests.txt`
- [ ] Anotar quais testes (se algum) já estavam vermelhos **antes** de você mexer.
- [ ] Subir a WebUI uma vez e gerar **um Short real de ponta a ponta**. Você precisa saber como é o
      comportamento correto antes de mudá-lo.
- [ ] Confirmar as linhas citadas no plano `01` (o repo pode ter avançado).
- [ ] Criar a branch: `git checkout -b feat/long-video-tab`

**Saída da fase:** você sabe o estado atual e tem a linha de base.

---

## Fase 1 — Fundação (schema, constantes, defaults)

Planos: `02` §3-§5, `08` §1

- [ ] `LONG_VIDEO_MAX_DURATION_SECONDS` e `LONG_VIDEO_MIN_DURATION_SECONDS` em `app/models/const.py`.
- [ ] Campos novos em `VideoParams` + `ChapterOutlineItem` em `app/models/schema.py`.
- [ ] Validador cruzado do modo longo.
- [ ] `app/services/long_video.py` criado com `apply_long_video_defaults()`.
- [ ] Testes de `test_schema.py` estendidos.
- [ ] **Suíte verde.** ✅ Commit: `feat(schema): parâmetros do modo vídeo longo`

> ⚠️ Ao final desta fase, **nada** de comportamento mudou. Se algum teste quebrou, você mexeu em
> default que não devia.

---

## Fase 2 — Roteiro em capítulos

Plano: `03`

- [ ] `estimate_narration_seconds()` — calibrada medindo com a voz real do canal.
- [ ] `plan_chapters()` (outline) + validação e normalização de pesos.
- [ ] `LONG_FORM_SYSTEM_PROMPT`.
- [ ] `generate_chapter_script()` reusando `llm.generate_script()` **sem alterá-la**.
- [ ] `build_long_script()` com continuidade entre capítulos e `progress_cb`.
- [ ] `collect_terms()` por capítulo, preservando ordem, sem rerank do TwelveLabs.
- [ ] Truncagem por orçamento de duração.
- [ ] `test_long_video.py` — 9 casos.
- [ ] **Suíte verde.** ✅ Commit: `feat(long): roteiro longo em capítulos`

**Teste manual da fase:** `cli.py --long --duration 12 --subject "..." --stop-at script` produz um
roteiro coerente, sem repetição entre capítulos. **Leia o roteiro.** Se estiver ruim, ajuste o
prompt agora — depois vai custar um render inteiro para descobrir.

---

## Fase 3 — Narração e legendas

Plano: `04`

- [ ] `synthesize_long_narration()` — TTS por capítulo, com retry isolado e idempotência.
- [ ] Concatenação de áudio via FFmpeg **com re-encode** (nunca `-c copy`).
- [ ] `merge_srt_with_offsets()` — a função mais crítica desta fase.
- [ ] Aplicação do teto de 35 min sobre a duração real, cortando capítulos inteiros.
- [ ] Caminho Whisper: `correct()` por capítulo.
- [ ] Normalização de loudness (−14 LUFS) como etapa opcional.
- [ ] `test_long_audio.py` — 8 casos.
- [ ] **Suíte verde.** ✅ Commit: `feat(long): narração em blocos e legendas com offset`

**Teste manual da fase:** `--stop-at subtitle` num roteiro de ~8 min. Abra o `.srt` e confira o
**último** cue contra o áudio. Erro de offset só aparece longe do começo.

---

## Fase 4 — Materiais

Plano: `05`

- [ ] `collect_materials()` — alocação por capítulo, chamando `material.download_videos()`.
- [ ] `MaterialBudget` + throttle + backoff em 429.
- [ ] Dedupe global por `asset_id` e cota de ~3% por fonte.
- [ ] Trava contra provedor pago sem opt-in explícito.
- [ ] Warning (não falha) quando faltar material.
- [ ] `build_credits_block()`.
- [ ] `test_long_materials.py` — 9 casos.
- [ ] **Suíte verde.** ✅ Commit: `feat(long): materiais alocados por capítulo`

**Teste manual da fase:** `--stop-at materials`. Confira que os clipes do último capítulo casam com
o assunto do último capítulo, não com o do primeiro.

---

## Fase 5 — Render

Plano: `06`

- [ ] Normalização de clipe via filtro FFmpeg único (escala + pad + fps + sar).
- [ ] Processamento em janelas, com `close_clip()` em `finally`.
- [ ] Concatenação por `concat_video_clips_with_ffmpeg()` com `max_duration`.
- [ ] Transição só na fronteira de capítulo.
- [ ] Validação de fonte de legenda sobre o roteiro **inteiro**.
- [ ] `verify_long_video()` — as 7 checagens do plano `06` §8.
- [ ] Mapa de progresso completo (15 pontos).
- [ ] `test_long_render.py` — 10 casos.
- [ ] **Suíte verde.** ✅ Commit: `feat(long): render por janelas com concat FFmpeg`

**Medições obrigatórias desta fase** (registre em `RELATORIO-QA.md`):

| Duração | Tempo de render | Pico de RSS | Tamanho do MP4 | Tamanho da pasta da task |
|---|---|---|---|---|
| 5 min | | | | |
| 15 min | | | | |
| 30 min | | | | |

---

## Fase 6 — Orquestrador

Planos: `03` §9, `05` §9, `06`

- [ ] Ramos do modo longo em `task._run_pipeline()` — script, terms, audio, materials, render.
- [ ] `save_script_data()` gravando o bloco `long_video` no `script.json`.
- [ ] Pré-voo do teto de 35 min **antes** de gastar LLM/TTS.
- [ ] `warnings` propagados até o estado da task.
- [ ] `test_task.py` estendido, incluindo `test_short_mode_pipeline_unchanged`.
- [ ] **Suíte verde.** ✅ Commit: `feat(long): integração no pipeline principal`

**Marco:** neste ponto um vídeo longo já sai **inteiro** pela CLI. É o momento de gerar o primeiro
vídeo de verdade e assistir do começo ao fim.

---

## Fase 7 — CLI e API

Plano: `08` §2-§4

- [ ] Flags novas em `cli.py`, com validação de faixa.
- [ ] Regra "`--duration` sem `--long` é erro".
- [ ] Default 16:9 no modo longo.
- [ ] JSON de saída da CLI.
- [ ] Endpoints `/long-videos/outline` e `/tasks/{id}/chapters`.
- [ ] Campo `long_video` na resposta da task.
- [ ] Regra do modo batch.
- [ ] `test_cli.py` e `test_controller_video.py` estendidos.
- [ ] **Suíte verde.** ✅ Commit: `feat(long): CLI e API do modo longo`

---

## Fase 8 — WebUI

Plano: `07`

- [ ] **Primeiro:** extrair `_render_shorts_workspace()` sem mudar lógica. Suíte verde. Commit
      separado.
- [ ] `key_prefix` em todas as funções de painel.
- [ ] `st.tabs` no nível principal.
- [ ] `_render_long_video_workspace()` reusando os painéis.
- [ ] Controles exclusivos (duração, capítulos, outline editável, toggles).
- [ ] Botão "Gerar estrutura" com `stop_at="script"`.
- [ ] Estimativas e avisos (fila, provedor pago, clipes).
- [ ] Progresso com etapa nomeada.
- [ ] Chaves de i18n nos **12** arquivos.
- [ ] Restauração de task longa abre a aba certa.
- [ ] `test_webui_long_tab.py` — 5 casos; `test_webui_i18n.py` verde.
- [ ] **Rodar a WebUI de verdade** e clicar em tudo nas duas abas.
- [ ] **Suíte verde.** ✅ Commit: `feat(webui): aba de vídeos longos`

---

## Fase 9 — QA completo

Plano: `09`

- [ ] Os 10 cenários E2E executados e registrados.
- [ ] QA técnico do vídeo de 20 min passando.
- [ ] QA visual amostrado (folha de contato) num vídeo real.
- [ ] `RELATORIO-QA.md` escrito, com as medições e as decisões dos pontos ABERTOS.
- [ ] Nenhum critério de bloqueio do plano `09` §7 acionado.
- [ ] ✅ Commit: `docs: relatório de QA do modo longo`

---

## Fase 10 — Publicação

Plano: `11`

> ✅ **Autorizações já concedidas (2026-08-28):** cópia **pública**, reinício do serviço **liberado**,
> edição do cron **liberada**. Ver plano `11` §8.

- [ ] Varredura de segredos (§1) — **limpa**. *(crítica: o repo vai público)*
- [ ] 🔴 **Sanitização por placeholders aplicada** (plano `11` §3.1) — obrigatória, porque a WebUI
      é aberta (§3.2). Gere a versão pública numa pasta separada; não edite os originais.
- [ ] 🔴 `grep -rniE "cursar\.space|/home/acer|/home/server|5e030f4a0c16|UC3L86|9133adc7"` no que
      vai ser commitado — **sem nenhum resultado**.
- [ ] Seção do modo longo no `README.md` e `README-en.md`.
- [ ] `LICENSE` e atribuição ao upstream preservados.
- [ ] Push em `feat/long-video-tab`.
- [ ] `gh repo create alvaro209890/MoneyPrinterTurbo --public --source=. --remote=alvaro`.
- [ ] Verificar que não há geração em andamento (plano `11` §8.1) e que está fora da janela 08:30.
- [ ] Deploy no acer, com backup e teste na máquina de produção.
- [ ] Verificação pós-deploy (§6.3) completa.
- [ ] Merge em `main` e push nos dois remotes.

---

## Fase 11 — Hermes

Plano: `10`

- [ ] `mpt.py` com as flags do modo longo, execução em background e saída JSON.
- [ ] `SKILL.md` atualizada; `test_mpt_agent_skill.py` verde.
- [ ] 🔴 Identificar **qual das 3 skills** o cron carrega (plano `10` §3.1) — editar a errada
      perde a mudança, e o `hermes-skills-sync` (00:06) pode sobrescrever a cópia do perfil.
- [ ] Backup do `jobs.json`.
- [ ] Prompt do cron atualizado (✅ autorizado), NotebookLM mantido como fallback declarado.
- [ ] Edição feita fora da janela das 08:30, sem execução em andamento.

**Transferência de conhecimento — as 5 superfícies (plano `10` §5.3):**

- [ ] 1. Skills (`moneyprinterturbo-video` + global `media/moneyprinterturbo`).
- [ ] 2. Wrapper `mpt.py`.
- [ ] 3. Prompt do cron.
- [ ] 4. Memória do perfil `videos`.
- [ ] 5. Segundo Cérebro (ficha + changelog).
- [ ] ✅ **Teste do agente:** perguntado sem dica ("como você gera o vídeo longo hoje?"), ele
      responde `mpt.py --long` — **não** "Gemini Notebook". Se falhar, uma superfície ficou de fora.
- [ ] ✅ **Reteste no dia seguinte**, após o sync das 00:06.
- [ ] **Um vídeo longo gerado pelo caminho do agente**, de ponta a ponta.
- [ ] Vault atualizado: ficha do projeto + changelog, com lock, unlock e commit.

---

## Fase 12 — Encerramento

- [ ] Todos os itens do gate (`00` §5) marcados.
- [ ] Relatório final entregue ao Álvaro contendo:
  - o que foi feito, em uma página;
  - os números medidos;
  - o link do vídeo longo gerado como prova;
  - as limitações conhecidas e o que ficou para uma v2;
  - o que ainda depende de decisão dele.
- [ ] Combinado o período de sobreposição com o NotebookLM (plano `10` §6).

---

## Registro de decisões (preencha enquanto trabalha)

| Ponto ABERTO (plano `02` §10) | Decisão tomada | Por quê |
|---|---|---|
| A-1 capítulos por minuto | | |
| A-2 material por capítulo × tudo antes | | |
| A-3 cache de capítulo | | |
| A-4 imagens + Ken Burns | | |
| A-5 loudness dentro do pipeline | | |
| A-6 paralelismo e fila | | |
| RNF-04 tempo aceitável de render | | |
