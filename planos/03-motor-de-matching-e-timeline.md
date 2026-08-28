# Plano 03 — Motor de Matching e Timeline Exata (Slot-Fitting Engine)

> **Objetivo:** Construir o motor que recebe a linha do tempo (timestamps das cenas), o manifesto de clipes baixados e monta a timeline exata de vídeo, cortando e ajustando cada clipe para coincidir perfeitamente com o momento da narração.

---

## 1. O Problema da Sincronia Atual

Atualmente o `combine_videos` no `app/services/video.py` faz o seguinte:
```python
# Comportamento antigo:
# Pega a lista de vídeos, corta cada um com duração fixa (ex: 3s a 5s)
# e vai colando um atrás do outro até preencher o áudio total.
```
Esse método gera **Drift Temporal**:
- O clipe A dura 4.0s.
- O clipe B dura 4.0s.
- A fala sobre o "F-22" começou em `3.2s` e terminou em `7.8s`. O clipe que deveria mostrar o avião começa no meio de uma frase e termina no meio de outra, causando desconexão cognitiva para o espectador.

---

## 2. A Nova Arquitetura de Timeline Baseada em Slots

```
Timeline de Áudio:
[0.0s ---------------- 4.5s] [4.5s ---------------- 9.1s] [9.1s -------------- 14.0s]
  Cena 1: Introdução           Cena 2: O Caça F-22          Cena 3: Falha no Computador

Timeline de Vídeo Semântica:
[Slot 1: Duração 4.5s      ] [Slot 2: Duração 4.6s      ] [Slot 3: Duração 4.9s       ]
-> Clipe 1 cortado em 4.5s   -> Clipe 2 cortado em 4.6s   -> Clipe 3 cortado em 4.9s
   (foco: Introdução)           (foco: Caça F-22)            (foco: Computador/Radar)
```

---

## 3. Algoritmo do Slot-Fitting Engine

Criar o módulo `app/services/timeline_engine.py`:

```python
@dataclass
class VideoTimelineSlot:
    scene_index: int
    start_time: float
    end_time: float
    target_duration: float
    source_video_path: str
    source_start_offset: float # Ponto de corte inicial dentro do vídeo original
    effects: list[str]          # Zoom, pan, transição suave

class SemanticTimeline:
    slots: list[VideoTimelineSlot]
    total_duration: float
```

### Regras de Ajuste de Corte:
1. **Duração do Arquivo Original >= Duração do Slot:**
   - Seleciona o trecho mais estável/interessante do vídeo (ex: cortando os primeiros 0.5s para evitar transições sujas de estoque).
2. **Duração do Arquivo Original < Duração do Slot:**
   - Se o clipe tiver 3s e o slot precisar de 4.5s:
     - Opção A (Recomendada): Aplicar efeito sutil de velocidade (`set_speed(0.85)` ou freeze frame com Ken Burns).
     - Opção B: Dividir o slot em 2 micro-clipes da mesma cena semântica.
3. **Variedade Visual (Anti-Repetição):**
   - Um mesmo arquivo de vídeo não pode ser usado em dois slots consecutivos, a menos que seja a mesma cena de longa duração.

---

## 4. Renderização via FFmpeg Filter Complex

Para garantir velocidade máxima e renderização sem perdas:
- Gerar o comando FFmpeg usando `-filter_complex` ou arquivo de concatenação com timestamps precisos:
  ```bash
  ffmpeg -y \
    -ss {slot1_start} -t {slot1_dur} -i clip1.mp4 \
    -ss {slot2_start} -t {slot2_dur} -i clip2.mp4 \
    ... \
    -filter_complex "[0:v]scale=...[v0]; [1:v]scale=...[v1]; [v0][v1]concat=n=N:v=1:a=0[outv]" \
    -i audio.mp3 -map "[outv]" -map 1:a -c:v libx264 -pix_fmt yuv420p output.mp4
  ```
- O módulo `video.py` já possui helpers de FFmpeg que podem ser reaproveitados e adaptados para receber a `SemanticTimeline`.

---

## 5. Critérios de Aceite

1. O início de cada clipe de vídeo bate exatamente com o timestamp `start_time` da sua cena na narração (erro < 0.1s).
2. Não há tela preta nem congelamento brusco entre os slots.
3. Transições (fade/dissolve) respeitam as fronteiras dos slots.
