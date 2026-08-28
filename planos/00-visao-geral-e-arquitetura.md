# Visão Geral da Arquitetura e Roadmap — Semantic Video Matching

> **Objetivo Principal:** Fazer com que a IA analise a narração (roteiro/áudio/transcrição com timestamps) de forma granular e selecione/posicione clipes de vídeo que sejam visualmente e contextualmente condizentes com cada momento específico do vídeo.

---

## 1. Problema Atual

Atualmente no MoneyPrinterTurbo:
1. **Vídeo Curto:** O LLM gera de 5 a 8 termos globais (`generate_terms`) e busca dezenas de vídeos no Pexels/Pixabay. O `combine_videos` apenas corta e concatena os vídeos em ordem sequencial ou aleatória, **sem saber o que cada trecho da narração está dizendo no segundo X**.
2. **Vídeo Longo:** Divide por capítulos e gera termos por capítulo (`long_materials.py`), o que melhora, mas ainda distribui clipes cegamente dentro da duração do capítulo.
3. **Resultado:** Se a narração diz *"O caça F-22 decolou em direção ao Pacífico"*, o clipe exibido pode ser uma pessoa digitando num laptop ou uma floresta qualquer que veio da busca geral.

---

## 2. A Solução: Pipeline de Alinhamento Semântico de Clipes (Semantic Timeline)

```
+-----------------------------------------------------------------------------------+
| 1. Roteiro / Narração (Áudio gerado)                                               |
|    - Transcrição precisa com timestamps via Whisper (Word/Segment-level)           |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 2. Análise Semântica de Cenas (LLM Scene Decomposition)                           |
|    - LLM analisa as frases e divide o vídeo em "Cenas Visuais" (ex: a cada 3-7s) |
|    - Para cada cena: gera Prompt de Busca / Descrição Visual / Palavras-chave     |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 3. Busca Direcionada e Pool de Materiais                                          |
|    - Busca segmentada no Pexels/Pixabay ou IA generativa por cena                 |
|    - Tagging e metadados dos vídeos baixados (tags de busca + metadados de API)    |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 4. Matching & Scoring Semântico (Slot-Fitting Engine)                             |
|    - Mapeia cada segmento de tempo [start, end] para o melhor clipe                |
|    - Validação de densidade semântica e variedade (evita repetição estúpida)      |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| 5. Renderização Baseada em Timeline Precisa                                       |
|    - Concatenação/Corte exato do clipe para casar com o início/fim da cena         |
+-----------------------------------------------------------------------------------+
```

---

## 3. Estrutura dos Documentos de Plano

Para guiar o desenvolvimento pelo próximo agente, este diretório está dividido em 5 planos modulares e progressivos:

| Arquivo | Título / Foco |
|---|---|
| `01-analise-da-narracao-e-segmentacao.md` | Como quebrar a narração em slots de tempo e prompts visuais via LLM + Whisper. |
| `02-busca-e-indexacao-de-materiais.md` | Estratégia de busca direcionada por cena (Pexels, Pixabay, Local) e tags. |
| `03-motor-de-matching-e-timeline.md` | Algoritmo de slot-fitting e alinhamento do clipe ao timestamp exato da fala. |
| `04-integracao-pipeline-e-compatibilidade.md` | Integração no `app/services/task.py`, `long_video.py` e schemas sem quebrar o legado. |
| `05-guias-de-testes-e-qa.md` | Casos de teste automatizados, métricas de precisão visual e plano de validação. |
