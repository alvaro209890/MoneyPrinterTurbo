# 06 — Backend: render de vídeo longo (memória, tempo e qualidade)

> **Objetivo:** montar um MP4 de até 35 minutos num notebook, sem OOM, sem degradar qualidade por
> re-encode repetido, e com progresso visível.

---

## 1. Onde o caminho atual quebra

`video.combine_videos()` (`video.py:538`) faz, hoje:

1. abre **cada** material com MoviePy para ler duração e tamanho (`_open_video_clip_quietly`,
   `video.py:423`);
2. cria uma lista de `SubClippedVideoClip` para **todos** os cortes possíveis;
3. reabre cada subclipe, redimensiona, compõe transição, e acumula em `processed_clips`.

Para 35 min:

| Sintoma | Causa |
|---|---|
| Pico de RAM | dezenas de clipes MoviePy vivos ao mesmo tempo em `processed_clips` |
| Lentidão desproporcional | cada `CompositeVideoClip` de letterbox (`video.py:662-664`) é caro |
| Perda de qualidade | múltiplos re-encodes em cadeia |
| Sem feedback | o loop não emite progresso |

> ✅ **A boa notícia:** o projeto já tem o caminho certo pronto —
> `concat_video_clips_with_ffmpeg()` (`video.py:332`), com fallback de codec e suporte a
> `max_duration`. O modo longo deve usá-lo como via principal.

## 2. Estratégia: processar em janelas, concatenar por FFmpeg

```
para cada capítulo i:
   ├─ selecionar clipes do capítulo (plano 05)
   ├─ normalizar cada clipe (escala + letterbox + fps + speed)  →  arquivo .mp4 no disco
   ├─ FECHAR o clipe (liberar memória)  ←── crítico
   └─ concatenar os clipes do capítulo  →  chapter-01-video.mp4
concatenar todos os chapter-XX-video.mp4  →  combined.mp4   (FFmpeg concat, -t cap)
combined.mp4 + audio.mp3 + subtitle.srt   →  final-1.mp4    (generate_video)
```

**Invariante de memória:** em nenhum momento mais que ~um punhado de clipes MoviePy fica aberto.
O `video.close_clip()` (`video.py:453`) já existe — **use religiosamente, em `finally`**.

## 3. Normalização dos clipes: prefira FFmpeg puro

A etapa "redimensionar + letterbox + ajustar fps" hoje passa por `CompositeVideoClip`
(`video.py:662-664`). Para o modo longo, faça com um filtro FFmpeg único por clipe:

```
-vf "scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2:black,fps=30,setsar=1"
```

Vantagens: um processo por clipe, memória constante, muito mais rápido, e o resultado é
**exatamente** o mesmo letterbox que o código atual produz.

> ⚠️ **Todos os clipes precisam sair idênticos** em resolução, fps, `pix_fmt`, SAR e timebase, senão
> o `concat` demuxer falha ou produz saída corrompida. Padronize:
> `1920x1080` (ou o que `VideoAspect.to_resolution()` retornar), `30 fps`, `yuv420p`, `setsar=1`.
> Isto é a causa nº 1 de "o vídeo concatenou mas ficou com áudio/vídeo dessincronizado".

## 4. Transições em 35 minutos

`video.combine_videos` aplica transições via MoviePy (`VideoTransitionMode`, `schema.py:23`).
Com 210 cortes, transição em **todos** eles é caro e visualmente cansativo.

Regra sugerida para o modo longo:
- **sem transição** entre clipes dentro do mesmo capítulo (corte seco, é o padrão de documentário);
- **fade curto (~0,5 s)** apenas na **fronteira entre capítulos**.

Isso reduz o número de transições de 210 para ~13, e melhora o resultado. Implemente o crossfade de
fronteira com `xfade` do FFmpeg (o binário já é verificado para esse filtro no doctor da skill de
vídeo do Hermes).

## 5. Legendas queimadas em vídeo longo

`generate_video()` (`video.py:991`) queima o SRT. Pontos de atenção:

1. **Fonte com cobertura de português** — `video.subtitle_font_supports_text()` (`video.py:977`) já
   valida. Rode a validação sobre o roteiro **inteiro** antes de renderizar, não sobre uma amostra:
   descobrir no minuto 28 que a fonte não tem "ç" custa um render inteiro.
2. **Contraste** — `video.subtitle_colors_are_indistinguishable()` (`video.py:935`) já existe; use.
3. **Quebra de linha** — `video.wrap_text()` (`video.py:765`). Em 16:9 a largura útil é maior que em
   9:16; confira que o `max_width` é calculado a partir da resolução real e não de um valor fixo.
4. **Custo**: queimar legenda em 35 min é um passe de encode completo. Não faça duas vezes.

## 6. Áudio final, loudness e faststart

Ordem correta das operações (fazer fora de ordem custa um re-encode a mais):

```
1. combined.mp4  (só vídeo, sem áudio)
2. + narração + BGM  →  mix
3. + legenda queimada
4. loudnorm  (-14 LUFS / TP -1.5)          ← -c:v copy, só re-encoda áudio
5. -movflags +faststart                     ← pode ser feito junto do passo 4
```

`video.generate_video()` já cobre 2 e 3. Os passos 4 e 5 são a etapa nova (plano `04` §8).

> ⚠️ **`+faststart` não é opcional.** Sem ele o YouTube aceita, mas o preview e o upload ficam
> lentos, e o QA do canal exige. O gotcha nº 6 da ficha do projeto registra que já houve um vídeo
> publicado errado por causa disso.

## 7. Progresso, timeout e a fila de uma task por vez

`webui_task.py:19-22` fixa `max_concurrent_tasks=1`, e `_run_generation` segura
`config.runtime_config_lock()` (`webui_task.py:81`) durante toda a geração. Um render de 35 min
**bloqueia qualquer outra geração** — inclusive os 4 Shorts do dia.

Três coisas a fazer (item A-6 do plano `02`):

1. **Progresso granular** — o modo longo deve atualizar `progress` em pelo menos 15 pontos
   distintos. Use `sm.state.update_task(task_id, progress=N)` como o pipeline já faz. Mapa sugerido:

   | Faixa | Etapa |
   |---|---|
   | 5–8 | pré-voo e outline |
   | 8–18 | roteiro dos capítulos |
   | 18–20 | palavras-chave |
   | 20–35 | TTS por capítulo |
   | 35–38 | legendas |
   | 38–55 | materiais |
   | 55–85 | normalização + concatenação dos clipes |
   | 85–95 | mix, legenda queimada |
   | 95–100 | loudnorm + faststart + verificação |

2. **Aviso explícito na UI** de que a geração longa ocupa a fila (plano `07`).
3. **Não aumente `max_concurrent_tasks`** sem entender o `runtime_config_lock`. Duas gerações
   simultâneas compartilhariam configuração de provedor em processo — foi exatamente por isso que o
   upstream fixou em 1. Se quiser paralelismo, a saída correta é **fila com prioridade** (Shorts
   antes de longo), não concorrência real.

## 8. Verificação automática do arquivo final (QA técnico)

Antes de marcar a task como completa, verifique — e **falhe** se não bater:

```python
def verify_long_video(path: str, expected_duration: float) -> dict:
    """
    Retorna métricas e levanta erro se o arquivo não for publicável.
    """
```

Checagens:

| Checagem | Como | Critério |
|---|---|---|
| Decode integral | `ffmpeg -v error -i X -f null -` | stderr vazio |
| Duração | `ffprobe` | dentro de ±2 s do esperado **e** ≤ 2100 s |
| Faststart | `ffprobe` / posição do átomo `moov` | `moov` antes do `mdat` |
| Codec/pix_fmt | `ffprobe` | H.264 High, `yuv420p` |
| Áudio | `ffprobe` | AAC, 48 kHz, 2 canais |
| Loudness | `ffmpeg -af loudnorm=print_format=json -f null -` | I ≈ −14 ± 1 LUFS, TP ≤ −1,5 dBTP |
| Vídeo não-preto | amostragem de frames | nenhum trecho longo totalmente preto |

Grave o resultado no `script.json`/estado da task. O Hermes vai ler isso para decidir se publica
(plano `10`).

## 9. Estimativa de tempo (meça, não chute)

RNF-04 está **ABERTO** de propósito. Sua obrigação é medir e registrar em
`12-CHECKLIST-EXECUCAO.md`:

- tempo de render de um vídeo de 5, 15 e 30 min na máquina alvo;
- pico de RSS em cada um;
- espaço em disco consumido pela pasta da task.

Se o render de 30 min passar de ~2 h no `acer`, isso é um problema real de operação (o cron roda
08:30 e precisa entregar 5 vídeos). Nesse caso, avalie:
- reduzir a resolução de trabalho para 1280×720 no modo longo (o canal já publicou longo em 720p);
- usar encoder por GPU — o doctor da skill de vídeo do Hermes registra **VAAPI h264 disponível** no
  acer (GPU AMD). `video._get_effective_video_codec()` (`video.py:222`) já tem a infraestrutura de
  detecção e fallback de codec para plugar isso.

## 10. Testes obrigatórios deste plano

Em `test/services/test_long_render.py`:

- `test_long_mode_uses_ffmpeg_concat_path` — o modo longo chama
  `concat_video_clips_with_ffmpeg`, não o merge MoviePy.
- `test_concat_receives_duration_cap` — `max_duration` ≤ 2100 é passado ao FFmpeg.
- `test_normalization_filter_is_deterministic` — o `-vf` gerado é idêntico para o mesmo input
  (teste de string).
- `test_all_normalized_clips_share_output_params` — resolução/fps/pix_fmt uniformes.
- `test_clips_are_closed_after_processing` — `close_clip` chamado para cada clipe aberto
  (spy/mock contando chamadas) — **este teste protege contra o vazamento que causa OOM**.
- `test_transitions_only_at_chapter_boundaries` — nº de transições == nº de capítulos − 1.
- `test_verify_long_video_rejects_missing_faststart`.
- `test_verify_long_video_rejects_over_cap_duration`.
- `test_progress_is_monotonic_and_reaches_100` — progresso nunca retrocede.
- `test_subtitle_font_validated_against_full_script` — validação sobre o roteiro completo.

FFmpeg **mockado** em todos (verifique o comando montado, não execute). Se quiser um teste de
integração real, marque com `@pytest.mark.slow` e deixe fora do CI padrão.
