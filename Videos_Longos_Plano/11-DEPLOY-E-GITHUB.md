# 11 — Deploy (deixar online) e GitHub

> **Pedido literal:** *"ao terminar ele já deverá deixar tudo online e no github, deverá ser feita
> uma cópia para meu perfil do github também"*.
> Este arquivo é a execução desse pedido. Leia inteiro **antes** do primeiro `git push`.

---

## 1. Higiene de segredos — faça isto ANTES de qualquer commit

O MoneyPrinterTurbo guarda chaves de LLM, Pexels, Pixabay, Azure, ElevenLabs e outras em
`config.toml`. O repositório traz `config.example.toml` como modelo — **o `config.toml` real nunca
pode ir para o GitHub.**

```bash
# 1. Confirme que o .gitignore cobre o que precisa
grep -nE "config\.toml|\.env|storage/|\.venv" .gitignore

# 2. Veja exatamente o que seria enviado
git status
git diff --cached --stat

# 3. Varredura de segredo no que você vai commitar
git diff --cached | grep -inE "api[_-]?key|secret|token|password|sk-[a-z0-9]{20}|Bearer "
```

> 🔴 **Se qualquer uma dessas buscas retornar algo, pare.** Um segredo que chega ao GitHub tem que
> ser considerado vazado e **rotacionado**, mesmo que você apague o commit em seguida.

Confira também que **não** estão indo: `storage/tasks/**` (vídeos gerados, pesados),
`*.mp4`, `*.mp3`, caches de material, e a pasta `.venv`.

## 2. Estratégia de branch

```
main                       ← não commite direto
└── feat/long-video-tab    ← todo o trabalho aqui
```

1. Trabalhe sempre em `feat/long-video-tab`.
2. Commits pequenos e temáticos, na ordem das fases do plano `12` — um commit por fase é um bom
   tamanho.
3. Mensagens em português ou inglês, mas **consistentes** com o histórico do repo (confira
   `git log --oneline -20` antes de escolher).
4. Só faça merge em `main` depois do gate do plano `00` §5 e do QA do plano `09`.

## 3. Os dois remotes

Este repo é um **fork de trabalho** de `harry0703/MoneyPrinterTurbo` (upstream MIT). O pedido é ter
o código no GitHub **e** uma cópia no perfil pessoal.

| Remote | Aponta para | Papel |
|---|---|---|
| `origin` | o remote atual do clone | de onde veio |
| `alvaro` | `github.com/alvaro209890/MoneyPrinterTurbo` | **a cópia pedida** |
| `upstream` | `github.com/harry0703/MoneyPrinterTurbo` | só para puxar atualizações |

```bash
git remote -v                                    # veja o que já existe
gh repo create alvaro209890/MoneyPrinterTurbo --public --source=. --remote=alvaro
git push -u alvaro feat/long-video-tab
```

### 3.1 ✅ DECISÃO: a cópia é **PÚBLICA**

**Autorizado pelo Álvaro em 2026-08-28.** Use `--public`. Não pergunte de novo.

Como o repositório fica visível para qualquer pessoa, três coisas passam a ser obrigatórias:

**a) Licença e atribuição.** Mantenha o `LICENSE` (MIT do upstream) e a atribuição a
`harry0703/MoneyPrinterTurbo` no README. Isso não é cortesia, é a condição da licença.

**b) A varredura de segredos do §1 vira crítica.** Num repo privado, um segredo commitado é um
susto. Num repo público, é um vazamento imediato — bots varrem o GitHub em minutos. Rode a
varredura **antes de cada push**, não só no primeiro.

**c) Sanitização da documentação operacional.** Este diretório de planos cita, hoje:

| O que aparece | Onde |
|---|---|
| `https://video.cursar.space` | `01` §14, `10` §1, `11` §6 |
| Caminhos do `acer` (`/home/acer/Projetos/...`, `~/.hermes/...`) | `01`, `10`, `11` |
| ID do cron (`5e030f4a0c16`) e do canal (`UC3L86...`) | `10` |
| Caminho do Segundo Cérebro no server | `10` §7 |
| Estrutura da frota e nomes de serviços systemd | `01` §14, `11` §6 |

Nada disso é credencial, e o Álvaro autorizou a publicação sabendo o que o repo contém. Mas
`video.cursar.space` é um endpoint **real e alcançável** — publicá-lo transforma "URL que ninguém
conhece" em "URL indexada". Antes do push público, faça **uma** verificação e escolha **uma** saída:

### ✅ DECISÃO (2026-08-28): **Saída A — placeholders**

O Álvaro escolheu publicar a documentação **sanitizada**. Como ele também decidiu manter a WebUI
aberta (§3.2), esta sanitização deixa de ser preferência e vira **requisito de segurança**.

**Sanitização é uma etapa DO PUSH, não de agora.** Os arquivos locais em `Videos_Longos_Plano/`
mantêm os valores reais — são a versão de trabalho. Antes do primeiro push, gere a versão pública.

#### Tabela de substituição (aplique em todos os `.md` antes de publicar)

| Valor real | Placeholder |
|---|---|
| `https://video.cursar.space` | `<URL_DA_WEBUI>` |
| `video.cursar.space` | `<URL_DA_WEBUI>` |
| `/home/acer/Projetos/MoneyPrinterTurbo` | `<PASTA_DO_PROJETO>` |
| `/home/acer/`, `~/.hermes/` (caminhos de perfil) | `<HOME_DO_AGENTE>/` |
| `5e030f4a0c16` | `<CRON_JOB_ID>` |
| `UC3L86WQn5VmHoSvRtvvUhoQ` | `<YOUTUBE_CHANNEL_ID>` |
| `/home/server/Downloads/Segundo-Cerebro` | `<VAULT_PATH>` |
| `9133adc7-33a9-480c-ad4f-e7b8f27ac3cc` (túnel) | `<TUNNEL_ID>` |
| `100.102.202.63` e outros IPs do tailnet | `<IP_TAILNET>` |
| `acer`, `server-desktop`, `pcque001imap` | `<HOST_A>`, `<HOST_B>`, `<HOST_C>` |

#### Como aplicar sem perder a versão de trabalho

```bash
# gere a versão pública numa pasta separada; NÃO edite os originais
mkdir -p /tmp/planos-publicos && cp Videos_Longos_Plano/*.md /tmp/planos-publicos/
cd /tmp/planos-publicos
sed -i \
  -e 's#https\?://video\.cursar\.space#<URL_DA_WEBUI>#g' \
  -e 's#video\.cursar\.space#<URL_DA_WEBUI>#g' \
  -e 's#/home/acer/Projetos/MoneyPrinterTurbo#<PASTA_DO_PROJETO>#g' \
  -e 's#5e030f4a0c16#<CRON_JOB_ID>#g' \
  -e 's#UC3L86WQn5VmHoSvRtvvUhoQ#<YOUTUBE_CHANNEL_ID>#g' \
  -e 's#/home/server/Downloads/Segundo-Cerebro#<VAULT_PATH>#g' \
  -e 's#9133adc7-33a9-480c-ad4f-e7b8f27ac3cc#<TUNNEL_ID>#g' \
  *.md
```

#### Verificação obrigatória antes do push

```bash
# nenhuma dessas buscas pode retornar nada no que vai ser commitado
grep -rniE "cursar\.space|/home/acer|/home/server|5e030f4a0c16|UC3L86|9133adc7|100\.10[0-9]\." .
```

> ⚠️ O `sed` acima cobre o que existe **hoje**. Se você acrescentar arquivos ou seções ao plano
> durante a implementação, **releia** procurando identificadores novos antes de publicar. A
> verificação por `grep` acima é a rede de segurança — rode-a sempre, não confie só no `sed`.

#### 🔁 O problema circular deste arquivo

Contagem de ocorrências a sanitizar (medida em 28/08):

| Arquivo | Ocorrências |
|---|---:|
| `11-DEPLOY-E-GITHUB.md` | **31** |
| `10-INTEGRACAO-HERMES.md` | 8 |
| `00-LEIA-PRIMEIRO.md` | 2 |
| `01-ARQUITETURA-ATUAL.md` | 2 |
| `12-CHECKLIST-EXECUCAO.md` | 1 |

**A maioria das 31 ocorrências deste arquivo está na própria tabela de substituição e nos comandos
de `sed`/`grep` acima** — ou seja, o arquivo que ensina a sanitizar é o que mais contém valores
reais, e o `grep` de verificação vai sempre acusá-lo.

Resolva assim, na versão pública:

- **substitua as seções §3.1 (a partir de "Tabela de substituição") e §3.2 inteiras** por um
  parágrafo curto dizendo que a documentação foi sanitizada e que os valores de infra vivem no
  Segundo Cérebro;
- rode o `grep` de verificação **depois** dessa remoção — aí ele precisa vir limpo.

Não tente sanitizar a tabela com `sed`: você acabaria com uma tabela mapeando
`<URL_DA_WEBUI>` → `<URL_DA_WEBUI>`, que é inútil e denuncia o que foi removido.

### 3.2 🔴 ACHADO VERIFICADO (2026-08-28): a WebUI **não tem autenticação nenhuma**

Levantado ao vivo, não deduzido. O que foi medido:

| Verificação | Comando | Resultado |
|---|---|---|
| Existe tela de login no código? | `grep -rn "Login Required\|Please login" webui/ app/ --include=*.py` | **vazio** — as chaves em `webui/i18n/*.json` são **órfãs**, resto de versão antiga. Não há login implementado. |
| `api_key` da API está setado? | `grep -E "^api_key" config.toml` (no acer) | **vazio** — `/api/v1/*` e `/tasks/*` sem proteção |
| Streamlit escuta onde? | `ss -tlnp \| grep 8501` | `127.0.0.1:8501` — **bom**, não exposto na LAN |
| O túnel está ativo? | `systemctl --user is-active video-tunnel.service` | **active** (`cloudflared`, túnel `9133adc7-…`) |
| A URL responde de fora? | `curl -o /dev/null -w "%{http_code}" https://video.cursar.space` | **HTTP 200** |

**Portanto: não existe senha para remover.** O que existe é o oposto — uma WebUI **publicamente
alcançável e completamente aberta**.

#### Por que isso importa mais do que parece

A tela de Settings carrega os valores **reais** das chaves de API:

```python
# webui/Main.py:2842
st_llm_api_key = llm_form_panel.text_input(tr("API Key"), value=llm_api_key, type="password", ...)
# webui/Main.py:2633
upload_post_api_key = st.text_input(..., value=config.app.get("upload_post_api_key", ""), type="password", ...)
```

`type="password"` no Streamlit **só mascara visualmente**: o valor é serializado e enviado ao
navegador do cliente. Qualquer visitante de `video.cursar.space` pode abrir Settings e ler, pelo
DevTools, as chaves de LLM (9Router), Pexels, Pixabay, ElevenLabs, Upload-Post e as demais
configuradas — além de gerar vídeos queimando as cotas, e baixar as tasks existentes.

Se o `upload_post` estiver configurado com auto-publish, o alcance chega ao canal do YouTube.

#### A correção certa — e por que ela é gratuita aqui

> **O Hermes não usa a WebUI.** Foi verificado: `~/.hermes/skills/media/moneyprinterturbo/scripts/mpt.py`
> cita `video.cursar.space` **apenas num comentário de docstring** (linha 5) e não faz nenhuma
> requisição HTTP — dispara a geração pela **CLI local**. A própria `SKILL.md` diz: *"Você não
> precisa usar o navegador ou a interface WebUI"*.

Ou seja: **desligar o túnel público não afeta o Hermes em absolutamente nada.** O túnel serve
apenas ao acesso humano por navegador.

#### ✅ DECISÃO do Álvaro (2026-08-28, revisada): **fechar o front web é o alvo**

Decisão inicial foi "manter aberto — risco aceito". **O Álvaro revisou:** podemos fechar o front
web, **desde que o Hermes consiga operar de outra forma**.

> 🟢 **A condição já está satisfeita hoje.** Verificado no acer:
>
> | Evidência | Onde |
> |---|---|
> | `mpt.py` não faz nenhuma requisição HTTP; dispara pela **CLI local** | `scripts/mpt.py` (a URL aparece só num comentário, linha 5) |
> | *"Regra de decisão: **CLI primeiro** … WebUI para o Álvaro ajustar visualmente"* | `moneyprinterturbo-video/SKILL.md` |
> | *"Você **não precisa** usar o navegador ou a interface WebUI"* | `media/moneyprinterturbo/SKILL.md` |
> | O prompt do cron `5e030f4a0c16` não menciona a WebUI nem a URL | `cron/jobs.json` |
>
> **O Hermes nunca dependeu da WebUI.** Quem usa o front web é o Álvaro, pelo navegador.

**Portanto, fechar o túnel não bloqueia nada do agente.** O que se perde é o acesso remoto humano
pelo navegador — e para isso a frota já tem Tailscale.

#### Como fechar (quando o Álvaro der o ok para executar)

```bash
# 1. Garantir que não há geração em andamento
HERMES_HOME=/home/acer/.hermes/profiles/videos hermes cron list

# 2. Fechar o túnel
systemctl --user disable --now video-tunnel.service

# 3. Confirmar que caiu de fora
curl -s -o /dev/null -w "%{http_code}\n" --max-time 15 https://video.cursar.space   # espera-se falha/000

# 4. Confirmar que o Hermes continua funcionando (o teste que importa)
~/.hermes/skills/media/moneyprinterturbo/scripts/mpt.py --subject "teste pos-fechamento" --stop-at script
```

**Acesso do Álvaro depois do fechamento** — duas opções, ambas privadas:

- **Tailscale (recomendada):** o acer é `100.102.202.63` no tailnet. Exige o Streamlit escutar
  nesse IP, não só em `127.0.0.1` — ajuste `--server.address` no
  `moneyprinter-webui.service` para o **IP do tailnet**, nunca `0.0.0.0`.
- **Túnel SSH sob demanda:** `ssh -L 8501:127.0.0.1:8501 acer` e abrir `http://localhost:8501`.
  Zero mudança de configuração; o serviço continua só em loopback.

#### Enquanto o front estiver aberto

O fechamento depende de uma janela de execução do Álvaro. Até lá, valem as restrições abaixo —
e a nº 1 continua valendo **mesmo depois** de fechar, porque a sanitização já está decidida:

1. 🔴 **A URL `video.cursar.space` NÃO vai para o repositório público.** Como a instância é aberta,
   publicar a URL seria transformar "endpoint obscuro" em "endpoint indexado". Isto é o que torna a
   Saída A do §3.1 **obrigatória**, não mais uma preferência.
2. **Não configure `upload_post` com auto-publish nesta instância** enquanto ela estiver aberta —
   seria dar acesso de publicação no canal a quem abrir a URL.
3. **Mitigação de custo zero que não muda nada para o Álvaro nem para o Hermes:** preencher
   `api_key` no `config.toml` do acer. Isso fecha `/api/v1/*` e o download de `/tasks/*` atrás de
   um header `x-api-key`, sem tocar na WebUI e sem afetar o `mpt.py` (que usa CLI local, não HTTP).
   É um ganho parcial — **não** protege a tela de Settings — mas é grátis.
   ```toml
   # config.toml no acer
   api_key = "<valor-aleatorio-longo>"
   ```
4. **Se um dia as chaves forem rotacionadas**, lembre que a instância aberta volta a expor as novas.

> ℹ️ Subdomínios com certificado TLS aparecem em **Certificate Transparency logs** públicos
> (`crt.sh`). "Ninguém conhece a URL" é uma premissa frágil — mais uma razão para o fechamento
> acontecer em vez de ficar pendurado indefinidamente.

#### Onde isso entra no cronograma

O fechamento **não bloqueia** o desenvolvimento da aba nem o push do repositório. É uma tarefa
independente, de 5 minutos, que deve acontecer:

- **antes** do primeiro push público, se der para encaixar (elimina a preocupação de uma vez); ou
- **logo depois**, com a sanitização de placeholders (§3.1) cobrindo o intervalo.

Registre no `RELATORIO-QA.md` se foi fechado ou não, e a data. Uma decisão de fechar que nunca é
executada é pior do que a decisão consciente de manter aberto — porque ninguém mais fica vigiando.

## 4. O que commitar

| Caminho | Vai? | Observação |
|---|---|---|
| `app/services/long_video.py` e afins | ✅ | o código novo |
| `app/models/schema.py`, `const.py` | ✅ | campos e constantes |
| `webui/Main.py` | ✅ | a aba |
| `webui/i18n/*.json` | ✅ | **os 12 arquivos** |
| `cli.py`, `app/controllers/v1/video.py` | ✅ | CLI e API |
| `test/services/test_long_*.py` | ✅ | os testes novos |
| `Videos_Longos_Plano/**` | ✅ | os planos + o `RELATORIO-QA.md` |
| `README.md` / `README-en.md` | ✅ | seção do modo longo |
| `config.toml` | ❌ | **nunca** |
| `storage/**` | ❌ | saídas |
| `.venv/`, `__pycache__/` | ❌ | |

## 5. Documentação no repositório

Acrescente ao `README.md` (e ao `README-en.md`) uma seção enxuta:

- o que é o modo longo, em 3 linhas;
- o teto de 35 minutos;
- exemplo de CLI;
- print ou descrição da aba;
- ponteiro para `Videos_Longos_Plano/` para quem quiser o detalhe.

> ⚠️ Não reescreva o README inteiro. Ele é do upstream; acrescente uma seção e pare por aí.

## 6. Deploy — deixar online

A produção é o **`acer`**, não o Windows onde estes planos foram escritos.

### 6.1 Onde e o quê

| Item | Valor |
|---|---|
| Host | `acer` (acessível por `ssh acer` a partir do server e do Windows) |
| Pasta | `/home/acer/Projetos/MoneyPrinterTurbo` |
| Serviço WebUI | `moneyprinter-webui.service` (usuário, porta 8501) |
| Túnel | `video-tunnel.service` → `https://video.cursar.space` |

### 6.2 Sequência de deploy

> ✅ O reinício do serviço está **autorizado** (§8). Antes de executar, confira que não há geração
> em andamento — ver §8.1.

```bash
# no acer
cd /home/acer/Projetos/MoneyPrinterTurbo

# 0. NUNCA faça deploy sobre trabalho não commitado
git status                      # se houver mudança local, entenda antes de qualquer coisa
git stash -u                    # se precisar, com -u para pegar untracked

# 1. Backup do que está rodando (rollback barato)
cp config.toml ~/backups/config.toml.bak-$(date +%Y%m%d_%H%M%S)
git rev-parse HEAD > ~/backups/mpt-commit-anterior.txt

# 2. Traga o código
git fetch alvaro && git checkout feat/long-video-tab

# 3. Dependências (o projeto usa uv + uv.lock)
uv sync

# 4. Testes NA MÁQUINA DE PRODUÇÃO (o ambiente é diferente do seu)
uv run pytest test/ -q

# 5. Reinicie o serviço
systemctl --user restart moneyprinter-webui.service
systemctl --user status  moneyprinter-webui.service --no-pager

# 6. Confirme que subiu
curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:8501
systemctl --user status video-tunnel.service --no-pager
```

### 6.3 Verificação pós-deploy (não pule)

- [ ] `https://video.cursar.space` abre e mostra **as duas abas**.
- [ ] Um Short é gerado normalmente (prova que não houve regressão em produção).
- [ ] Um vídeo longo curto (5 min) é gerado pela aba nova.
- [ ] O `mpt.py --long` funciona pelo caminho do agente.
- [ ] Os logs do serviço não mostram exceção nova:
      `journalctl --user -u moneyprinter-webui.service -n 200 --no-pager`

### 6.4 Rollback

Se algo quebrar em produção:

```bash
git checkout $(cat ~/backups/mpt-commit-anterior.txt)
uv sync
systemctl --user restart moneyprinter-webui.service
```

Registre o que quebrou antes de reverter — um rollback sem diagnóstico só adia o problema.

## 7. Ordem correta das ações finais

Esta ordem importa. **Não** faça deploy antes de testar, nem publique antes de varrer segredos.

```
1. Suíte verde localmente
2. QA E2E completo (plano 09 §5)          ← inclui o vídeo de 20 min
3. RELATORIO-QA.md escrito
4. Varredura de segredos (§1)             ← crítica: o repo vai PÚBLICO
5. Decidir Saída A/B/C da documentação (§3.1) — pergunta única ao Álvaro
6. Commit + push em feat/long-video-tab
7. gh repo create alvaro209890/MoneyPrinterTurbo --public   (§3)
8. Deploy no acer (§6.2) + verificação (§6.3)   ← autorizado; confira a janela (§8.1)
9. Integração do Hermes (plano 10) e um vídeo longo real pelo agente
10. Merge em main
11. Vault atualizado (plano 10 §7)
```

## 8. ✅ Autorizações já concedidas (2026-08-28)

O Álvaro respondeu as três confirmações que este plano pedia. **Não pergunte de novo:**

| Pergunta | Resposta | Onde está detalhado |
|---|---|---|
| Cópia no perfil pessoal: pública ou privada? | **PÚBLICA** | §3.1 |
| Pode reiniciar o `moneyprinter-webui.service`? | **SIM** | §6.2 |
| Pode editar o prompt do cron `5e030f4a0c16`? | **SIM** | plano `10` §5 |

### 8.1 O que continua sendo sua responsabilidade

Autorização não é dispensa de cuidado. Antes de reiniciar o serviço:

```bash
# há geração em andamento? (o cron dispara 08:30 e produz 5 vídeos)
HERMES_HOME=/home/acer/.hermes/profiles/videos hermes cron list
ls -lt /home/acer/Projetos/MoneyPrinterTurbo/storage/tasks/ | head
journalctl --user -u moneyprinter-webui.service -n 50 --no-pager
```

Se houver task rodando, **espere ela terminar**. Reiniciar no meio de um render descarta o trabalho
e, se for a execução do cron, custa os vídeos do dia. A autorização é para reiniciar — não para
reiniciar em cima de uma geração em curso.

Janela segura: fora do horário do cron (08:30 BRT) e sem task ativa.

### 8.2 A única pergunta que ainda está aberta

- [ ] Nível de detalhe operacional nos planos publicados: **Saída A, B ou C** do §3.1.

É uma pergunta de uma linha, e só precisa ser feita uma vez, antes do primeiro push público.
