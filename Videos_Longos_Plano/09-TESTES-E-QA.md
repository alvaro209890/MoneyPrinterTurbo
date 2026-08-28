# 09 — Testes e QA

> **Objetivo:** provar que o modo longo funciona **e** que os Shorts continuam intactos. A produção
> diária do canal depende dos Shorts — uma regressão silenciosa aqui derruba a operação.

---

## 1. A suíte que já existe (seu contrato de não-regressão)

`test/services/` tem **49 arquivos**. Os que mais importam para você:

| Arquivo | Por que importa |
|---|---|
| `test_task.py` | o orquestrador que você vai estender |
| `test_video.py` | render, concat, legendas |
| `test_llm.py` | geração de roteiro e termos |
| `test_voice.py` | TTS e SubMaker |
| `test_material.py` | download e cache de materiais |
| `test_schema.py` | defaults e validação de `VideoParams` |
| `test_cli.py` | contrato da CLI |
| `test_controller_video.py` | contrato da API |
| `test_webui_*.py` (13 arquivos) | UI, defaults, histórico, i18n |
| `test_mpt_agent_skill.py` | a skill do agente |

**Regra:** rode a suíte inteira **antes** de começar (para ter a linha de base) e depois de cada
fase do plano `12`. Guarde a saída da primeira execução — se algum teste já estava vermelho antes
de você mexer, isso não é problema seu, mas precisa estar registrado.

```bash
uv run pytest test/ -q
```

## 2. Testes novos, por plano

| Plano | Arquivo novo | Nº mínimo de casos |
|---|---|---|
| 03 — roteiro | `test/services/test_long_video.py` | 9 |
| 04 — áudio | `test/services/test_long_audio.py` | 8 |
| 05 — materiais | `test/services/test_long_materials.py` | 9 |
| 06 — render | `test/services/test_long_render.py` | 10 |
| 07 — WebUI | `test/services/test_webui_long_tab.py` | 5 |
| 08 — schema/API/CLI | estender os existentes | 12 |

A lista exata de cada um está no fim do respectivo arquivo de plano. **Não invente casos além
desses antes de ter esses passando.**

## 3. Regras de teste deste projeto

1. **Zero rede.** `requests`, `openai`, `edge_tts` e afins sempre mockados. Os testes existentes já
   fazem isso — copie o padrão de `test_material.py` e `test_llm.py`.
2. **Zero FFmpeg real** nos testes padrão. Verifique o **comando montado** (lista de argumentos),
   não o resultado. Se precisar de um teste de integração real, marque `@pytest.mark.slow`.
3. **Zero arquivo fora do tmpdir.** Use `tmp_path` do pytest.
4. **Determinismo.** Nada de `random` sem seed nem de `datetime.now()` sem congelar.

## 4. Os quatro testes que mais protegem você

Se o tempo apertar, garanta pelo menos estes:

1. **`test_short_mode_pipeline_unchanged`** (plano `03`) — com `video_mode="short"` os mocks recebem
   exatamente os mesmos argumentos de antes. É a sua rede de segurança contra regressão.
2. **`test_clips_are_closed_after_processing`** (plano `06`) — cada clipe MoviePy aberto é fechado.
   É o que impede o OOM em 35 min.
3. **`test_merge_srt_with_offsets_shifts_timestamps`** (plano `04`) — legenda dessincronizada
   inutiliza o vídeo inteiro e só se descobre assistindo.
4. **`test_concat_receives_duration_cap`** (plano `06`) — o teto de 35 min chega ao FFmpeg.

## 5. Teste de ponta a ponta (manual, mas obrigatório)

Não dá para automatizar isto no CI, mas **tem que ser feito** antes do gate:

| # | Cenário | Critério de aceite |
|---|---|---|
| E2E-1 | Short de 40 s, exatamente como antes | idêntico ao baseline; sem diferença perceptível |
| E2E-2 | Longo de **5 min**, tema real do canal | gera sem erro; assista inteiro |
| E2E-3 | Longo de **20 min** | gera sem erro; QA técnico do plano `06` §8 passa |
| E2E-4 | Longo pedindo **35 min** | gera e fica ≤ 2100 s |
| E2E-5 | Longo pedindo **40 min** | **falha** com mensagem clara, sem consumir TTS |
| E2E-6 | Longo com roteiro próprio (`--script`) | pula o LLM, respeita o teto |
| E2E-7 | Longo com `custom_audio_file` | usa o áudio externo, legenda via Whisper |
| E2E-8 | Longo com provedor de estoque sem resultado | emite warning, não falha |
| E2E-9 | Interromper no meio e reenviar | retoma reusando os capítulos já sintetizados |
| E2E-10 | Short enviado durante um longo | entra na fila, não corrompe nada |

**Registre para E2E-2/3/4:** tempo total de render, pico de RSS, tamanho da pasta da task, tamanho
do MP4. Esses são os números do RNF-04, que está aberto de propósito (plano `02` §2).

## 6. QA de saída — o que o Hermes vai exigir

O canal "Debugando o Mundo" já tem um QA fechado. O vídeo longo gerado precisa passar por ele:

### 6.1 QA técnico (automatizável — plano `06` §8)

- decode integral sem erro;
- duração ≤ 2100 s e coerente com o esperado;
- H.264 High, `yuv420p`, AAC 48 kHz;
- `+faststart` presente;
- loudness ≈ −14 LUFS-I, true peak ≤ −1,5 dBTP;
- legenda presente e dentro da safe area.

### 6.2 QA visual (semântico, via modelo de visão)

O fluxo do canal já usa **Groq `qwen/qwen3.6-27b`** para conferir coerência de frames. Ele já pegou
um erro real: uma cena de solda/hardware num Short sobre **exclusão de software**.

Para 35 min, conferir todos os frames é inviável. Faça **folha de contato amostrada**:
- 1 frame a cada ~30 s (≈ 70 frames em 35 min);
- monte em grades de 4×4;
- pergunte ao modelo, em **uma** chamada por grade (o gotcha nº 4 da skill de vídeo do Hermes:
  `vision_analyze` devolve texto, não pixels — pergunta nova = chamada nova; peça tudo de uma vez);
- procure especificamente: cena que contradiz a narração, texto ilegível, tela preta, contraste
  ruim de legenda.

### 6.3 QA factual

Regra editorial fechada do canal: **toda afirmação factual relevante precisa de duas fontes
confiáveis, com uma primária/oficial quando existir.** Isso é responsabilidade do agente que define
a pauta (o Hermes), não do MoneyPrinterTurbo — mas o pipeline precisa **carregar as fontes** do
roteiro até a descrição do vídeo. Ver plano `10`.

## 7. Critério de bloqueio

**Não publique / não feche a tarefa se:**

- ❌ qualquer teste da suíte existente estiver vermelho por causa da sua mudança;
- ❌ o E2E-1 (Short) tiver qualquer diferença de comportamento;
- ❌ o E2E-5 (40 min) **não** falhar — teto que não bloqueia não é teto;
- ❌ o QA técnico do E2E-3 não passar;
- ❌ o vídeo de 20 min tiver legenda dessincronizada em qualquer ponto (verifique no início, no
  meio e no fim — o erro de offset só aparece longe do começo).

## 8. Relatório final de QA

Ao terminar, escreva `Videos_Longos_Plano/RELATORIO-QA.md` com:

- saída da suíte (antes e depois);
- tabela dos 10 cenários E2E com resultado;
- números medidos (tempo, RAM, disco) por duração;
- lista de warnings que o sistema emitiu e em que situação;
- decisões que você tomou nos pontos ABERTOS do plano `02` §10;
- limitações conhecidas que ficam para uma v2.
