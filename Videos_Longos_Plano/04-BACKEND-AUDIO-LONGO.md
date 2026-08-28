# 04 — Backend: narração (TTS) e legendas em escala longa

> **Objetivo:** narrar até 35 minutos sem estourar timeout do provedor, mantendo as legendas
> perfeitamente sincronizadas depois da concatenação.

---

## 1. Por que o caminho atual não serve

Hoje `task.generate_audio()` (`task.py:478`) manda **o roteiro inteiro** para `voice.tts()`
(`voice.py:455`) numa chamada só. Para 35 minutos isso significa ~31.500 caracteres num único
request de streaming.

Problemas concretos:

| Problema | Onde | Consequência |
|---|---|---|
| Timeout do Edge TTS | `voice.get_edge_tts_timeout_seconds()` — `voice.py:700` | falha depois de minutos de espera |
| Limite de caracteres por request | vários provedores (Azure, ElevenLabs, MiniMax) | erro 4xx |
| Falha no fim = perda total | `voice.py:786` streaming | 30 min de TTS jogados fora |
| `sub_maker` gigante em memória | `voice.py:2113` | uso de RAM desnecessário |
| Sem progresso | — | UI muda por dezenas de minutos |

## 2. Arquitetura: um bloco de TTS por capítulo

```
capítulo 1 ──► tts() ──► chapter-01.mp3 + sub_maker_1 ──┐
capítulo 2 ──► tts() ──► chapter-02.mp3 + sub_maker_2 ──┤
    ...                                                  ├──► concat FFmpeg ──► audio.mp3
capítulo N ──► tts() ──► chapter-NN.mp3 + sub_maker_N ──┘         +
                                                          offsets acumulados
                                                                  │
                                                                  ▼
                                                          subtitle.srt (único)
```

**A unidade de bloco é o capítulo do plano `03`**, não um número arbitrário de caracteres. Vantagens:
o bloco já é semanticamente fechado, e o retry por bloco é natural.

> ⚠️ Se um capítulo ainda assim for grande demais para o provedor (raro, mas possível com capítulo
> de 5 min), subdivida por parágrafo (`\n\n`) dentro do capítulo. Nunca corte no meio de uma frase —
> a prosódia do TTS depende da pontuação final.

## 3. Novo módulo/função: `long_audio`

Coloque em `app/services/long_video.py` (mesmo módulo do plano `03`) ou em
`app/services/long_audio.py` se preferir separar. Assinatura sugerida:

```python
def synthesize_long_narration(
    task_id: str,
    params: VideoParams,
    plan: LongVideoPlan,
    progress_cb: Callable[[float], None] | None = None,
) -> tuple[str, float, list[ChapterAudio]]:
    """
    Retorna (audio_file, audio_duration, chapter_audios).
    chapter_audios carrega, por capítulo: caminho, duração e sub_maker/cues.
    """
```

```python
@dataclass(frozen=True)
class ChapterAudio:
    index: int
    audio_file: str
    duration: float
    offset: float          # início deste capítulo dentro do áudio final
    sub_maker: object | None
    script: str
```

## 4. Regras de implementação do TTS por bloco

1. **Reuse `voice.tts()` inteiro.** Ele já faz o dispatch de todos os provedores. Você chama N vezes.
2. **Arquivo por capítulo** em `utils.task_dir(task_id)`, nome estável e ordenável:
   `chapter-001.mp3`, `chapter-002.mp3`, …
3. **Retry por bloco**, isolado: se o capítulo 7 falhar, tente de novo só ele. Depois de esgotar,
   falhe a task com `_mark_task_failed(task_id, "audio", f"chapter {i} narration failed: ...")`.
4. **Idempotência**: se `chapter-007.mp3` já existe e tem duração > 0, reuse. Isso torna a retomada
   de uma task interrompida barata (e viabiliza "regerar só o capítulo X" no futuro).
5. **Progresso**: chame `progress_cb(i / total)` a cada capítulo. O orquestrador mapeia isso para a
   faixa 20% → 35% da barra global.
6. **Duração real**: use `voice.get_audio_duration()` (`voice.py:2171`) sobre o arquivo, **não** a
   estimativa do plano `03`.

## 5. Concatenação e o offset das legendas — a parte que mais quebra

### 5.1 Concatenar o áudio

Use o `concat` demuxer do FFmpeg (mesmo padrão de `video.concat_video_clips_with_ffmpeg`,
`video.py:332`) com **re-encode**, não `-c copy`:

```
ffmpeg -y -f concat -safe 0 -i list.txt -c:a libmp3lame -q:a 2 -ar 44100 audio.mp3
```

> 🔴 **Não use `-c copy`.** Os MP3 vindos do Edge TTS podem ter cabeçalhos/paddings diferentes;
> a cópia de stream gera drift acumulado — e drift de áudio é exatamente o que destrói a
> sincronia de legenda num vídeo de 35 min.

Reuse os helpers já existentes para o caminho e o escaping da lista:
`video._format_ffmpeg_concat_path()` (`video.py:320`) e `_escape_ffmpeg_concat_path()`
(`video.py:315`).

### 5.2 Reconstruir as legendas com offset

Esta é a regra central:

```
tempo_global_do_cue = offset_do_capítulo + tempo_local_do_cue
offset_do_capítulo_i = Σ (duração real dos capítulos 1..i-1)
```

**Use a duração real medida do arquivo concatenado de cada bloco**, nunca a soma das durações
reportadas pelo `sub_maker` — elas divergem por arredondamento.

Duas estratégias, escolha uma:

**(a) SRT por capítulo + merge textual (recomendada).**
Para cada capítulo, gere o SRT local com o `voice.create_subtitle()` já existente
(`voice.py:2113`), depois faça o merge deslocando os timestamps e renumerando os índices.
Vantagem: reusa código testado; o merge é uma função pura e trivial de testar.

**(b) Deslocar os cues antes de gerar o SRT.**
Mais eficiente, mas mexe em estrutura interna do `SubMaker` — que já tem tratamento de
compatibilidade legado (`voice.ensure_legacy_submaker_fields`, `voice.py:589`). Mais frágil.

Função a criar e testar isoladamente:

```python
def merge_srt_with_offsets(parts: list[tuple[str, float]], output_file: str) -> str:
    """
    parts: [(caminho_do_srt_parcial, offset_em_segundos), ...]
    Renumera os índices sequencialmente e soma o offset em todos os timestamps.
    """
```

> ⚠️ Cuidados no merge de SRT: índice começa em 1 e é contínuo; timestamp no formato
> `HH:MM:SS,mmm` (vírgula, não ponto); **com 35 min você passa de `00:59:59`?** Não — mas o
> formatador precisa suportar horas mesmo assim. Reuse `voice.mktimestamp()` (`voice.py:88`) se ele
> já fizer isso; se não, escreva o seu e teste com 34:59,999.

### 5.3 Caminho Whisper

Se o usuário escolher `subtitle_provider = "whisper"`, o caminho é diferente: `subtitle.create()`
(`subtitle.py:22`) transcreve o **áudio final concatenado** de uma vez, então não há offset a
aplicar. Mas atenção:

- Whisper em 35 min é lento. Avise na UI e emita progresso.
- `subtitle.correct()` (`subtitle.py:202`) usa Levenshtein (`subtitle.py:176`) — custo O(n·m).
  Com um roteiro de 31 mil caracteres isso pode ficar proibitivo.
  **Mitigação:** no modo longo, rode `correct()` **por capítulo**, usando o mapa
  `char_start`/`char_end` que o plano `03` §10 gravou no `script.json`.

## 6. O teto de 35 minutos, aplicado no áudio (a verdade final)

Depois da concatenação, meça a duração real. Se passou de `LONG_VIDEO_MAX_DURATION_SECONDS`:

```python
if audio_duration > const.LONG_VIDEO_MAX_DURATION_SECONDS:
    # descarte capítulos inteiros do fim, refaça a concatenação, e AVISE
    kept = _chapters_within_budget(chapter_audios, const.LONG_VIDEO_MAX_DURATION_SECONDS)
    ...
    warnings.append({
        "code": "long_video_truncated",
        "message": f"video truncated to the {MAX} s cap; {dropped} chapter(s) dropped",
    })
```

Regras:
- **Corte por capítulo inteiro**, nunca no meio — cortar no meio de uma frase é pior que um vídeo
  mais curto.
- O aviso precisa chegar até a task (`warnings` já é um campo suportado — ver `task.py:1479`) e
  aparecer na UI.
- Se **nem o primeiro capítulo** couber (impossível com os limites do plano `03`, mas seja
  defensivo): falhe com mensagem clara em vez de gerar lixo.

## 7. Custom audio (narração externa)

`params.custom_audio_file` (`schema.py:97`) já permite pular o TTS —
`task.resolve_custom_audio_file()` (`task.py:360`) resolve o caminho com trava de segurança
(`file_security.resolve_path_within_directory`).

No modo longo isso **continua valendo e é importante**: permite ao Hermes narrar com outra voz e só
usar o MoneyPrinterTurbo para montar a imagem. Garanta que:

- o teto de 35 min também é verificado para áudio externo;
- com áudio externo + `subtitle_provider="whisper"`, o fluxo funciona (é o único jeito de ter
  legenda nesse caso — não existe `sub_maker`).

## 8. Normalização de loudness (−14 LUFS)

O canal exige −14 LUFS-I e true peak ≤ −1,5 dBTP. Hoje isso é feito **fora** do MoneyPrinterTurbo,
por script do Hermes. **Decisão sugerida (item A-5 do plano `02`): trazer para dentro**, como etapa
final opcional:

```
ffmpeg -i final.mp4 -af loudnorm=I=-14:TP=-1.5:LRA=11 -c:v copy -movflags +faststart final-normalized.mp4
```

Adicione um parâmetro `normalize_loudness: bool = True` no modo longo. Vantagem: o Hermes deixa de
precisar de pós-processo, e o QA do canal passa direto.

> ⚠️ `loudnorm` de passe único é aproximado. Se quiser precisão, use o modo de dois passes
> (primeiro passe com `print_format=json`, segundo com os valores medidos). Para 35 min o custo do
> primeiro passe é baixo porque não há re-encode de vídeo (`-c:v copy`).

## 9. Testes obrigatórios deste plano

Em `test/services/test_long_audio.py`:

- `test_merge_srt_with_offsets_shifts_timestamps` — offset aplicado corretamente em todos os cues.
- `test_merge_srt_renumbers_indices_sequentially` — índices 1..N contínuos após o merge.
- `test_merge_srt_handles_hour_boundary` — timestamp acima de 00:59:59 formata certo.
- `test_synthesize_long_narration_reuses_existing_chapter_files` — idempotência.
- `test_synthesize_long_narration_retries_only_failed_chapter` — retry isolado (mock em `voice.tts`).
- `test_audio_over_cap_drops_whole_chapters` — corte por capítulo, com `warning` emitido.
- `test_audio_concat_uses_reencode_not_stream_copy` — o comando FFmpeg montado **não** contém
  `-c copy` (teste de string sobre o comando, sem executar FFmpeg).
- `test_custom_audio_still_respects_cap` — áudio externo longo demais é rejeitado.

Todos com `voice.tts` e o subprocess do FFmpeg **mockados**.
