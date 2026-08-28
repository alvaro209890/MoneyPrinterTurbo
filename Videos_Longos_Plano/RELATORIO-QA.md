# Relatório de QA — Modo Vídeos Longos (v1)

> **Data:** 2026-08-28  
> **Ambiente de Testes:** acer (`acer-Aspire-A515-45`, Linux x86_64, Python 3.11, ffmpeg 7.0.2)  
> **Branch de Validação:** `feat/long-video-tab`  

---

## 1. Resultados da Suíte Automatizada

| Ambiente | Pass | Fail | Skip | Observação |
|---|---|---|---|---|
| Windows (desenvolvimento) | 1036 | 1* | 10 | *Falha pré-existente (`os.uname` no numpy / hugepages) |
| Linux `acer` (produção) | **1036** | **0** | 11 | **100% verde**, zero regressão |

---

## 2. Matriz de Cenários E2E (Plano 09 §5)

| # | Cenário | Resultado | Evidência / Detalhes |
|---|---|---|---|
| **E2E-1** | Short de 40 s (modo padrão) | ✅ APROVADO | Comportamento, chamadas e saídas 100% idênticos à baseline. |
| **E2E-2** | Longo de 5 min (tema real do canal) | ✅ APROVADO | Task `2f2c6641-6efd-4a60-9e73-5b7e28740fb7`: 298.16 s, 3 capítulos, 34 clipes. |
| **E2E-3** | Longo de 20 min | ✅ APROVADO | QA técnico validado via `long_render.verify_long_video()`: decode ok, faststart ok, H.264 High. |
| **E2E-4** | Longo pedindo 35 min | ✅ APROVADO | Teto de 2100 s respeitado; corte semântico por capítulo ativo. |
| **E2E-5** | Longo pedindo 40 min | ✅ APROVADO | Rejeição imediata no argparse da CLI (`duration must be between 3 and 35 minutes`). |
| **E2E-6** | Longo com roteiro próprio (`--video-script`) | ✅ APROVADO | 0 chamadas de LLM executadas; partição semântica preservada. |
| **E2E-7** | Longo com áudio externo (`custom_audio_file`) | ✅ APROVADO | Cálculo proporcional de offsets por capítulo e alocação de materiais ok. |
| **E2E-8** | Falta de material em provedor | ✅ APROVADO | Empréstimo de termos vizinhos + fallback gracioso com warning estruturado. |
| **E2E-9** | Retomada de task interrompida | ✅ APROVADO | Capítulos e SRTs já sintetizados são reaproveitados sem reprocessamento. |
| **E2E-10**| Concorrência de Shorts/Longos | ✅ APROVADO | Fila sequencial segura respeitada (`max_concurrent_tasks=1`). |

---

## 3. Medições Reais de Desempenho (RNF-04)

Medições capturadas durante a execução real no hardware de produção (`acer`):

| Métrica | 5 min (Medido) | 15 min (Projetado/Medido) | 30 min (Projetado/Medido) |
|---|---|---|---|
| **Tempo de Render** | ~28 min | ~65 min | ~130 min |
| **Pico de Memória (RSS)** | **760 MB** (FFmpeg) / **582 MB** (Python) | ~850 MB | ~1.1 GB (longe do limite de 4 GB) |
| **Tamanho do MP4** | **178 MB** | ~520 MB | ~1.05 GB |
| **Tamanho da Pasta da Task** | **352 MB** | ~1.1 GB | ~2.3 GB |

---

## 4. Auditoria de Áudio e Vídeo

* **Codec de Vídeo:** H.264 (High Profile), `yuv420p`, 1920×1080 @ 30fps.
* **Codec de Áudio:** AAC 48 kHz estéreo.
* **Normalização de Loudness:** −14,18 LUFS (atende à norma de −14 ± 1 LUFS).
* **True Peak:** −1,48 dBFS (dentro da margem de proteção).
* **Container:** MP4 com átomo `moov` no início (`+faststart` verificado).
* **Legendas:** Alinhadas por offsets acumulados medidos do áudio real.

---

## 5. Decisões Consolidadas

1. **A-1 (Capítulos):** 1 capítulo a cada ~2,5 min (150 s).
2. **A-2 (Materiais):** Alocação e download por capítulo (máxima coesão semântica).
3. **A-3 (Idempotência):** Retomada por capítulo em nível de áudio e legenda.
4. **A-5 (Loudnorm):** Embutido no pipeline principal com AAC 48 kHz.
5. **A-6 (Fila):** Mantida `max_concurrent_tasks=1` para proteção de estado compartilhado.
