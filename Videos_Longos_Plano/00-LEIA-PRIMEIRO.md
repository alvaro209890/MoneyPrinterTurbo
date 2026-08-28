# 00 — LEIA PRIMEIRO

> **Você é o agente de IA encarregado de implementar a aba "Vídeos Longos" no MoneyPrinterTurbo.**
> Este diretório é o seu plano completo. Leia este arquivo inteiro antes de tocar em qualquer código.

---

## 1. A missão em uma frase

Criar uma **nova aba no MoneyPrinterTurbo** que gera **vídeos longos de até 35 minutos**, usando
**exatamente a mesma lógica dos Shorts/Reels** que já existe (roteiro por LLM → palavras-chave →
narração TTS → materiais de estoque → legendas → render), apenas escalada para durações longas — e,
ao final, deixá-la **em produção e no GitHub**, substituindo o Gemini Notebook / NotebookLM como
motor de vídeos longos do agente `Hermes-acer/videos`.

## 2. Contexto de quem pediu

O dono do sistema (Álvaro) opera o canal YouTube **"Debugando o Mundo"**
(`UC3L86WQn5VmHoSvRtvvUhoQ`) com um agente autônomo (`Hermes-acer/videos`) que publica todo dia:

- **4 Shorts/dia** — já gerados pelo MoneyPrinterTurbo (funciona, é o padrão-ouro a ser copiado).
- **1 Vídeo Longo/dia** — hoje gerado pelo **Gemini Notebook (NotebookLM)**, que é o problema:
  exige navegador logado, deixa marca d'água que precisa ser removida por script, tem tela final com
  logo, e não dá controle sobre roteiro nem sobre trilha visual.

**O objetivo real desta entrega é matar a dependência do NotebookLM.** Quando esta aba estiver
pronta, o vídeo longo diário passa a sair do mesmo pipeline dos Shorts — mesmo controle editorial,
mesmo QA, mesma reprodutibilidade, sem navegador e sem marca d'água.

## 3. Regras de ouro (não negociáveis)

1. **"Exatamente a mesma lógica dos Reels."** Isto foi dito com todas as letras. Você **não** vai
   inventar um pipeline paralelo: vai **estender** o pipeline existente
   (`app/services/task.py::_run_pipeline`). Roteiro por LLM, palavras-chave por LLM, TTS, materiais
   de estoque, legendas sincronizadas, BGM, render — tudo igual, só que em escala longa.
2. **Limite duro de 35 minutos** (2100 s) de vídeo final. Isso é um teto validado em código, não um
   texto de UI. Ver [`02-REQUISITOS-E-DECISOES.md`](02-REQUISITOS-E-DECISOES.md) §3.
3. **Nada de regressão nos Shorts.** O caminho atual de 9:16 curto é produção diária. Toda mudança
   em código compartilhado precisa ser retrocompatível e coberta por teste.
4. **Reuso antes de reescrita.** Antes de escrever uma função nova, procure a equivalente já pronta
   nos serviços. O mapa está em [`01-ARQUITETURA-ATUAL.md`](01-ARQUITETURA-ATUAL.md).
5. **Sem custo novo obrigatório.** O fluxo padrão precisa continuar rodando com Edge TTS (grátis) +
   Pexels/Pixabay (grátis) + LLM já configurado no 9Router. Provedores pagos continuam opcionais.
6. **Toda etapa longa precisa de progresso observável.** Um render de 35 min pode levar dezenas de
   minutos; a UI e o estado da task não podem ficar mudos.

## 3.1 Autorizações já concedidas (2026-08-28)

O Álvaro já respondeu as confirmações que os planos pediam. **Estas três estão fechadas — não
pergunte de novo:**

| Item | Decisão |
|---|---|
| Cópia no perfil `alvaro209890` | **PÚBLICA** (`gh repo create --public`) |
| Reiniciar o `moneyprinter-webui.service` no acer | **AUTORIZADO** |
| Editar o prompt do cron diário `5e030f4a0c16` | **AUTORIZADO** |

⚠️ **Autorização não é dispensa de cuidado.** Duas consequências práticas:

1. **Repo público ⇒ a varredura de segredos é crítica.** Bots varrem o GitHub em minutos; um
   segredo commitado é vazamento imediato, não susto. Ver plano `11` §1 e §3.1.
2. **O cron dispara 08:30 BRT e produz 5 vídeos.** Reiniciar o serviço ou editar o job **em cima de
   uma execução em andamento** custa a produção do dia. Escolha a janela. Ver plano `11` §8.1.

### Duas decisões adicionais, também fechadas

| Item | Decisão | Detalhe |
|---|---|---|
| Front web exposto sem autenticação | **fechar é o alvo** — a condição já está satisfeita | plano `11` §3.2 |
| Detalhe operacional na doc publicada | **Saída A — placeholders** | plano `11` §3.1 |

**Sobre o front web:** o Álvaro autorizou fechar, *desde que o Hermes consiga operar de outra
forma*. Verificado: **o Hermes nunca dependeu da WebUI** — ele usa a CLI local (`mpt.py`), e a
própria skill diz *"CLI primeiro… WebUI para o Álvaro ajustar visualmente"*. Fechar o túnel custa
zero ao agente. O comando e as alternativas de acesso (Tailscale, túnel SSH) estão no `11` §3.2.

🔴 **Enquanto não estiver fechado**, a URL e os identificadores de infra **não podem** ir para o
repositório público — a sanitização por placeholders é condição do push. Tabela de substituição e
`grep` de verificação no plano `11` §3.1.

## 3.2 A entrega não termina no código

> *"além de desenvolver essa nova aba, o Hermes de vídeos já deve ficar completamente ciente das
> novas atualizações e de como usá-las."*

O agente roda **sozinho, todo dia às 08:30**, sem revisão humana. Código entregue que o agente não
sabe que existe é código morto — ele continuaria abrindo o NotebookLM.

São **cinco superfícies** de conhecimento a atualizar (skills, wrapper, prompt do cron, memória do
agente, Segundo Cérebro), e um **teste de aceite** objetivo: perguntar ao agente, sem dar dica,
como ele gera o vídeo longo. Se a resposta não for `mpt.py --long`, a entrega não está pronta.

Detalhe completo no plano `10` §5.3. ⚠️ Existem **três** cópias de skill do MoneyPrinterTurbo e um
timer de sync que pode desfazer sua edição — leia o `10` §3.1 **antes** de editar qualquer uma.

## 4. Ordem de leitura

| # | Arquivo | O que você tira dele |
|---|---|---|
| 00 | **LEIA-PRIMEIRO** (este) | missão, regras, gate |
| 01 | [ARQUITETURA-ATUAL](01-ARQUITETURA-ATUAL.md) | mapa do código que já existe, com `arquivo:linha` |
| 02 | [REQUISITOS-E-DECISOES](02-REQUISITOS-E-DECISOES.md) | requisitos, limites e decisões já fechadas |
| 03 | [BACKEND-ROTEIRO-LONGO](03-BACKEND-ROTEIRO-LONGO.md) | roteiro em capítulos + palavras-chave |
| 04 | [BACKEND-AUDIO-LONGO](04-BACKEND-AUDIO-LONGO.md) | TTS em blocos e concatenação |
| 05 | [BACKEND-MATERIAIS-LONGO](05-BACKEND-MATERIAIS-LONGO.md) | materiais visuais em escala |
| 06 | [BACKEND-RENDER-LONGO](06-BACKEND-RENDER-LONGO.md) | render FFmpeg-first, memória, legendas |
| 07 | [WEBUI-ABA-VIDEOS-LONGOS](07-WEBUI-ABA-VIDEOS-LONGOS.md) | a aba em si |
| 08 | [API-CLI-E-SCHEMA](08-API-CLI-E-SCHEMA.md) | contratos de schema, API e CLI |
| 09 | [TESTES-E-QA](09-TESTES-E-QA.md) | o que precisa de teste e o QA de saída |
| 10 | [INTEGRACAO-HERMES](10-INTEGRACAO-HERMES.md) | trocar o NotebookLM pelo novo motor |
| 11 | [DEPLOY-E-GITHUB](11-DEPLOY-E-GITHUB.md) | colocar online + GitHub + cópia no perfil |
| 12 | [CHECKLIST-EXECUCAO](12-CHECKLIST-EXECUCAO.md) | a ordem exata de execução, fase a fase |

**Se você só puder ler dois arquivos antes de começar:** leia o `01` e o `12`.

## 5. Definição de pronto (o gate)

A entrega só está pronta quando **todas** as linhas abaixo forem verdadeiras e verificadas:

- [ ] Aba "Vídeos Longos" existe na WebUI e gera um vídeo de ponta a ponta sem intervenção manual.
- [ ] Um vídeo de **≥ 20 min** foi gerado e validado (decode integral, faststart, áudio ≈ −14 LUFS).
- [ ] O teto de **35 min** é aplicado em código e testado (tentativa acima disso falha com erro claro).
- [ ] A aba de Shorts continua idêntica — suíte de testes existente 100% verde.
- [ ] Testes novos cobrem: roteiro em capítulos, TTS em blocos, teto de duração, render longo.
- [ ] CLI e API expõem o modo longo com os mesmos parâmetros da UI.
- [ ] As **cinco superfícies** de conhecimento do Hermes atualizadas (plano `10` §5.3).
- [ ] **Teste do agente passou:** perguntado sem dica, ele responde `mpt.py --long` — e o reteste
      após o `hermes-skills-sync` da madrugada seguinte confirma que nada foi desfeito.
- [ ] Serviço rodando e acessível online (ver `11`).
- [ ] Código no GitHub, **com a cópia pública no perfil `alvaro209890`** (ver `11`).
- [ ] Varredura de segredos limpa antes do push público, e `LICENSE`/atribuição preservados.
- [ ] Documentação atualizada (README + ficha no Segundo Cérebro).

## 6. O que você NÃO deve fazer

- ❌ Não crie um repositório separado. Isto é uma **aba dentro do MoneyPrinterTurbo**.
- ❌ Não substitua MoviePy por outra lib no caminho dos Shorts.
- ❌ Não remova nem "limpe" código chinês de comentário existente — o projeto é upstream chinês e
  esses comentários são a documentação real de várias decisões.
- ❌ Não faça commit de chave, token, cookie ou `config.toml` preenchido.
- ❌ Não invente números de performance. Meça e registre o que mediu.
