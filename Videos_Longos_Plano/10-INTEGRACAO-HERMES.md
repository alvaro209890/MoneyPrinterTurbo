# 10 — Integração com o Hermes (substituir o NotebookLM)

> **Objetivo final de todo este plano:** o vídeo longo diário do canal "Debugando o Mundo" deixa de
> sair do Gemini Notebook e passa a sair do MoneyPrinterTurbo, pelo mesmo caminho dos Shorts.

---

## 1. Como o Hermes usa o MoneyPrinterTurbo hoje

| Item | Valor |
|---|---|
| Agente | `Hermes-acer/videos` (perfil `videos` do Hermes no PC `acer`) |
| Skill | `~/.hermes/skills/media/moneyprinterturbo/` — `SKILL.md` + `scripts/mpt.py` |
| Pasta do projeto | `/home/acer/Projetos/MoneyPrinterTurbo` |
| WebUI | `moneyprinter-webui.service` (porta 8501) |
| URL | `https://video.cursar.space` (`video-tunnel.service`, túnel Cloudflare) |
| Saída | `storage/tasks/<task_id>/final-1.mp4` |
| Cron diário | job `5e030f4a0c16`, 08:30 BRT, 30 execuções, workdir = a pasta do projeto |

O wrapper `mpt.py` já expõe: `--subject`, `--script`, `--terms`, `--aspect`, `--voice`,
`--language`, `--paragraphs`, `--source`, `--materials`, `--bgm-type`, `--bgm-volume`,
`--clip-duration`, `--no-subtitle`, `--stop-at`.

## 2. O que o NotebookLM faz hoje (e que você precisa cobrir)

O fluxo atual do vídeo longo, que vai ser aposentado:

1. o agente abre `notebook.google.com` no perfil Chrome `alvaro` (sessão logada);
2. alimenta o notebook com fontes de pesquisa;
3. gera um vídeo explicativo;
4. baixa o MP4;
5. roda `tratar-notebooklm-video.py` para **remover a marca d'água**
   (`delogo=x=1050:y=655:w=220:h=55`) e **cortar os últimos 3,5 s** de tela de logo;
6. normaliza áudio para −14 LUFS e aplica `+faststart`;
7. sobe no YouTube Studio.

**Vantagens que se perdem** (e que você precisa compensar): pesquisa documental multi-fonte
automática e narração de qualidade de documentário.

**Vantagens que se ganham:** sem navegador, sem marca d'água, sem tela de logo, controle total do
roteiro, reprodutibilidade, custo zero, e o mesmo QA dos Shorts.

> ⚠️ A pesquisa documental **não** é responsabilidade do MoneyPrinterTurbo. Ela continua sendo do
> agente (skill `pesquisa-web-brasil`), que já pesquisa tendências e valida fontes antes de decidir
> a pauta. O que muda é que, em vez de jogar as fontes no NotebookLM, o agente vai **escrever ou
> encomendar o roteiro** e mandar para o MoneyPrinterTurbo.

## 3. Atualizar o wrapper `scripts/mpt.py`

Acrescente as flags que espelham a CLI (plano `08` §3.1):

```
--long                         modo vídeo longo
--duration MINUTES             3 a 35 (default 10 quando --long)
--chapters N                   3 a 14, ou omitir para automático
--outline PATH                 outline pronto em JSON
--no-normalize-loudness        desliga a normalização
```

Requisitos do wrapper:

1. **Saída JSON de uma linha** com `task_id`, `video`, `duration`, `chapters`, `truncated`,
   `warnings` e o resultado da verificação técnica — o agente parseia isso para o relatório.
2. **Bloco de créditos** (plano `05` §8) impresso separadamente ou gravado em
   `storage/tasks/<id>/credits.txt`, para colar na descrição do YouTube.
3. **Job longo roda em background.** O gotcha nº 5 da skill de vídeo do Hermes é explícito:
   `gateway_timeout` é 3600 s, e job longo tem que ser processo em background que avisa por
   `hermes send` ao terminar. Um render de 30 min **não** cabe no turno do agente.
4. **Scripts são arquivos, nunca heredoc.** Gotcha nº 3 da mesma skill: heredoc dispara aprovação
   manual (`approvals.mode: manual`, janela de 60 s), o que é inviável em execução automática.

## 3.1 🔴 GOTCHA CRÍTICO: existem **três** cópias de skill, não uma

Levantado no acer em 28/08. Editar a errada = mudança perdida ou sobrescrita.

| Caminho | Nome da skill | `SKILL.md` md5 | O que é |
|---|---|---|---|
| `~/.hermes/skills/media/moneyprinterturbo/` | `moneyprinterturbo` | `c8cb8441` | skill **global** |
| `~/.hermes/profiles/videos/skills/media/moneyprinterturbo/` | `moneyprinterturbo` | `c8cb8441` | **cópia real** da global (não é symlink) |
| `~/.hermes/profiles/videos/skills/video-criacao/moneyprinterturbo-video/` | `moneyprinterturbo-video` | `8dabaca2` | skill **diferente** — ⚠️ **é esta que o cron referencia** |

Dois fatos que decorrem disso:

1. **O job `5e030f4a0c16` carrega `moneyprinterturbo-video`**, não `moneyprinterturbo`. Confira a
   lista de skills do job antes de editar:
   ```bash
   HERMES_HOME=/home/acer/.hermes/profiles/videos hermes cron list   # veja a linha "Skills:"
   ```
2. **Existe um timer que sincroniza skills entre os corpos da frota:**
   `hermes-skills-sync.timer`, que dispara **00:06** diariamente (e 00:02 no server). As duas
   cópias de `media/moneyprinterturbo` são idênticas justamente por causa dele.
   > 🔴 Se você editar **só a cópia do perfil**, o sync pode sobrescrevê-la na madrugada seguinte e
   > sua mudança desaparece sem aviso. **Edite a global** e deixe o sync propagar — ou edite as
   > duas e confirme no dia seguinte que continuam iguais (`md5sum`).

**Regra prática:** atualize `moneyprinterturbo-video` (a que o cron usa) **e** a global
`media/moneyprinterturbo`, e verifique os md5 no dia seguinte ao sync.

## 4. Atualizar a `SKILL.md`

Acrescente uma seção "Vídeos Longos" com:

- exemplo mínimo:
  ```bash
  ~/.hermes/skills/media/moneyprinterturbo/scripts/mpt.py \
    --long --duration 12 \
    --subject "O bug do Ariane 5: 37 segundos e 370 milhões de dólares" \
    --voice pt-BR-ThalitaMultilingualNeural-Female
  ```
- exemplo com roteiro próprio (o caso mais provável, porque o agente pesquisa e escreve):
  ```bash
  ~/.hermes/skills/media/moneyprinterturbo/scripts/mpt.py \
    --long --duration 15 --script-file /caminho/roteiro.txt --aspect 16:9
  ```
- exemplo em duas etapas (outline → revisão → render), usando `--stop-at script`;
- a tabela de defaults do modo longo (plano `02` §5);
- os gotchas novos que você descobrir durante a implementação.

> ⚠️ `test/services/test_mpt_agent_skill.py` existe no repo e testa a skill. Rode-o.

## 5. Atualizar o prompt do cron diário

> ✅ **Edição autorizada pelo Álvaro em 2026-08-28.** Não precisa perguntar de novo. Mas escolha a
> janela: o job dispara **08:30 BRT**; edite fora desse horário e com o `hermes cron list`
> confirmando que não há execução em andamento.

O job `5e030f4a0c16` vive no perfil `videos`, e a edição **precisa** do `HERMES_HOME` apontado — sem
isso o `hermes cron list` não enxerga o job:

```bash
HERMES_HOME=/home/acer/.hermes/profiles/videos hermes cron list
HERMES_HOME=/home/acer/.hermes/profiles/videos hermes cron edit 5e030f4a0c16 --prompt "$(cat /tmp/novo_prompt.txt)"
```

**Antes de editar: faça backup.**
```bash
cp ~/.hermes/profiles/videos/cron/jobs.json \
   ~/.hermes/profiles/videos/cron/jobs.json.bak-longos-$(date +%Y%m%d_%H%M%S)
```

### 5.1 O que muda no prompt

O prompt atual manda, no passo do vídeo longo:

> *"Produzir o Vídeo Longo do dia pelo Gemini Notebook (pesquisa estruturada, fontes
> históricas/tecnológicas, síntese audiovisual). Normalizar áudio e muxar."*

Substitua por algo como:

> *"Produzir o Vídeo Longo do dia pelo MoneyPrinterTurbo em modo longo
> (`mpt.py --long --duration <N>`), a partir do roteiro que você mesmo escreveu com base na
> pesquisa do passo 2. Duração-alvo entre 8 e 20 minutos conforme a densidade da pauta, teto
> técnico de 35 minutos. A normalização para −14 LUFS e o `+faststart` já saem do próprio
> pipeline — não use mais o `tratar-notebooklm-video.py`, que só serve para vídeos do
> NotebookLM. Conferir o QA técnico retornado pelo wrapper antes de publicar."*

E acrescente ao passo de publicação: *"colar o bloco de créditos de materiais gerado pelo pipeline
na descrição do vídeo, junto das fontes factuais da pauta."*

> ⚠️ **Não apague a menção ao Gemini Notebook do dia para a noite.** Deixe-o registrado como
> **fallback** enquanto o modo longo não acumular pelo menos uma semana de execuções bem-sucedidas.
> Trocar um pipeline de produção sem período de sobreposição é como o incidente do
> `ecogestor-flora-update`: a falha só aparece semanas depois, quando ninguém está olhando.

### 5.2 Skills anexadas ao job

O job já tem: `chrome-agente`, `video-criacao`, `moneyprinterturbo-video`, `video-analise`,
`pesquisa-web-brasil`, `segundo-cerebro`.

Confira se a skill que o job referencia (`moneyprinterturbo-video`) é a mesma que vive em
`~/.hermes/skills/media/moneyprinterturbo/` — os nomes divergem, e vale verificar qual o job
realmente carrega antes de editar o arquivo errado:

```bash
HERMES_HOME=/home/acer/.hermes/profiles/videos hermes skills list | grep -i money
```

## 5.3 🎯 Transferência de conhecimento: o Hermes precisa ficar **completamente** ciente

> **Requisito do Álvaro:** *"além de desenvolver essa nova aba, o Hermes de vídeos já deve ficar
> completamente [ciente] das novas atualizações e de como usá-las."*

Entregar o código e parar por aí **não cumpre a tarefa**. O agente roda sozinho, todo dia, às 08:30,
sem ninguém revisando. Se ele não souber que o modo longo existe e como acioná-lo, ele vai continuar
abrindo o NotebookLM — e a entrega inteira terá sido inútil.

São **cinco** superfícies de conhecimento, e todas precisam ser atualizadas:

| # | Superfície | O que precisa entrar | Se ficar de fora… |
|---|---|---|---|
| 1 | **Skills** (`SKILL.md` das 3 cópias — §3.1) | flags novas, exemplos, defaults, gotchas | o agente não sabe que `--long` existe |
| 2 | **Wrapper** `mpt.py` | as flags de fato implementadas (§3) | o agente tenta e recebe erro de argumento |
| 3 | **Prompt do cron** `5e030f4a0c16` | instrução de usar o modo longo no lugar do NotebookLM (§5.1) | ele continua no fluxo antigo |
| 4 | **Memória do Hermes** (perfil `videos`) | o fato durável de que o longo saiu do NotebookLM | ele "esquece" entre execuções e volta ao antigo |
| 5 | **Segundo Cérebro** (§7) | ficha do projeto + changelog | os outros agentes da frota ficam com informação errada |

### 5.3.1 O conteúdo mínimo que o Hermes precisa saber

Escreva isto **uma vez**, num texto único, e replique nas superfícies 1, 4 e 5 (adaptando o formato):

1. **O que mudou:** o MoneyPrinterTurbo agora gera vídeos longos (até 35 min) com a mesma lógica
   dos Shorts — roteiro em capítulos por LLM, palavras-chave por capítulo, TTS em blocos, materiais
   de estoque casados com a narração, legendas, BGM.
2. **Como acionar:** `mpt.py --long --duration <N>` — com os exemplos do §4.
3. **Quando usar cada caminho:**
   - Short do dia → `mpt.py` normal (inalterado);
   - Vídeo longo do dia → `mpt.py --long`;
   - NotebookLM → **apenas fallback** durante o período de sobreposição (§6).
4. **Defaults do modo longo:** 16:9, `sequential`, materiais casados ao roteiro, clipes de 10 s,
   1 vídeo por execução (tabela do plano `02` §5).
5. **O teto de 35 min é técnico:** pedir mais falha; pedir demais trunca por capítulo e emite
   warning. O agente precisa saber ler esse warning e reportá-lo.
6. **O QA já sai pronto:** −14 LUFS e `+faststart` vêm do próprio pipeline. **Não usar mais o
   `tratar-notebooklm-video.py`** em vídeo gerado pelo modo longo — ele existe só para remover
   marca d'água do NotebookLM e, aplicado aqui, degradaria o vídeo à toa.
7. **Créditos:** o pipeline gera o bloco de créditos dos materiais (plano `05` §8) — colar na
   descrição do YouTube junto das fontes factuais da pauta.
8. **Gotchas novos** que você descobrir durante a implementação — especialmente os de tempo de
   render e de fila (o modo longo ocupa a fila; ver plano `06` §7).

### 5.3.2 Memória do Hermes (superfície 4)

O perfil `videos` tem memória própria em `~/.hermes/profiles/videos/memories/`. A regra da casa do
Álvaro é explícita: *"se valeu gravar na memória do agente, vale refletir no `.md` do projeto"* —
ou seja, memória e vault andam juntos, nunca só um.

Grave um fato durável curto, no formato que o Hermes já usa nesse diretório, cobrindo os itens
1, 2, 3 e 6 do §5.3.1. Não copie o texto inteiro — memória é ponteiro, não manual; o manual é a
`SKILL.md`.

### 5.3.3 Verificação — como saber que o Hermes realmente entendeu

Não confie em ter escrito os arquivos. **Teste o agente:**

```bash
# pergunte ao agente, no perfil videos, sem dar dica nenhuma
HERMES_HOME=/home/acer/.hermes/profiles/videos hermes -z \
  "Como você gera o vídeo longo diário do canal hoje? Qual comando exato?"
```

**Critério de aceite:** a resposta menciona `mpt.py --long` (ou o modo longo do MoneyPrinterTurbo)
**sem** você ter citado isso na pergunta. Se ele responder "Gemini Notebook", a transferência de
conhecimento falhou — volte e corrija a superfície que ficou de fora.

Repita a verificação **depois** do `hermes-skills-sync` da madrugada seguinte, para confirmar que o
sync não desfez a edição (§3.1).

## 6. Período de sobreposição e critério de corte

| Semana | Vídeo longo sai de | Observação |
|---|---|---|
| 1 | MoneyPrinterTurbo, com NotebookLM como fallback declarado | monitorar diariamente |
| 2 | MoneyPrinterTurbo | só cair para o fallback em falha real |
| 3+ | MoneyPrinterTurbo | remover a menção ao NotebookLM do prompt |

**Critério para declarar a migração concluída:** 7 vídeos longos consecutivos gerados, aprovados no
QA e publicados sem intervenção manual.

## 7. Atualizar o Segundo Cérebro

O vault é a fonte de verdade da frota. Ao terminar, atualize seguindo o protocolo da casa:

```bash
cd /home/server/Downloads/Segundo-Cerebro
VAULT_AUTOR=<seu-nome> ./ferramentas/lock.sh 02-projetos/youtube-debugando-o-mundo.md
# ... patch cirúrgico ...
VAULT_AUTOR=<seu-nome> ./ferramentas/lock.sh -u 02-projetos/youtube-debugando-o-mundo.md
VAULT_AUTOR=<seu-nome> ./ferramentas/lock.sh commit "mensagem"
```

O que registrar:

1. Em `02-projetos/youtube-debugando-o-mundo.md`:
   - seção "Pipeline executável → Vídeos longos" reescrita (sai NotebookLM, entra o modo longo);
   - a tabela "Modelos usados no fluxo" atualizada;
   - os gotchas novos.
2. Em `06-changelog.md`: entrada no topo, com autor e data.
3. Se você criar uma ficha própria para o modo longo, adicione o ponteiro no `INDEX.md`.

Regras da casa: autoria `(autor: X | AAAA-MM-DD)` em toda entrada, patch cirúrgico (nunca reescrever
o arquivo inteiro), nunca comitar segredo, nunca forçar lock ativo de outro agente.

## 8. Checklist desta integração

**Wrapper e skills**
- [ ] `mpt.py` aceita `--long`, `--duration`, `--chapters`, `--outline`.
- [ ] `mpt.py` roda job longo em background e avisa ao terminar.
- [ ] `mpt.py` imprime JSON com `task_id`, caminho, duração, capítulos, warnings e QA.
- [ ] Bloco de créditos gerado e acessível.
- [ ] Identificada qual skill o cron carrega (§3.1) — `hermes cron list`, linha `Skills:`.
- [ ] `moneyprinterturbo-video/SKILL.md` atualizada (a que o cron usa).
- [ ] `media/moneyprinterturbo/SKILL.md` **global** atualizada.
- [ ] `test_mpt_agent_skill.py` verde.

**Cron**
- [ ] Backup do `jobs.json` feito.
- [ ] Prompt do cron atualizado (✅ autorizado), com o NotebookLM mantido como fallback.
- [ ] Edição feita fora da janela das 08:30 e sem execução em andamento.

**Transferência de conhecimento (§5.3) — as 5 superfícies**
- [ ] 1. Skills atualizadas (as três cópias conferidas).
- [ ] 2. Wrapper `mpt.py` com as flags de fato implementadas.
- [ ] 3. Prompt do cron instruindo o modo longo.
- [ ] 4. Memória do perfil `videos` com o fato durável.
- [ ] 5. Segundo Cérebro: ficha + changelog.
- [ ] ✅ **Teste do agente (§5.3.3):** perguntado sem dica, ele responde `mpt.py --long`.
- [ ] ✅ **Reteste após o `hermes-skills-sync` da madrugada** — o sync não desfez nada
      (`md5sum` das cópias iguais).

**Prova final**
- [ ] Um vídeo longo gerado de ponta a ponta **pelo caminho do agente** (não pela WebUI).
- [ ] Vault atualizado (ficha + changelog) com lock, unlock e commit.
