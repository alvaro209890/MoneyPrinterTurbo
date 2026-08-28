# Planos de Evolução — MoneyPrinterTurbo

Diretório com a especificação técnica e arquitetura para evolução do **MoneyPrinterTurbo**, com o objetivo de implementar **Análise Semântica de Narração e Alinhamento Inteligente de Clipes por IA (Semantic Timeline Matching)**.

---

## 📑 Índice dos Planos

1. **[`00-visao-geral-e-arquitetura.md`](./00-visao-geral-e-arquitetura.md)**
   - Diagnóstico do problema atual (vídeos desconexos da fala).
   - Diagrama do pipeline de alinhamento semântico.
   - Visão geral da arquitetura de cenas e slots.

2. **[`01-analise-da-narracao-e-segmentacao.md`](./01-analise-da-narracao-e-segmentacao.md)**
   - Integração com Whisper para timestamps precisos.
   - Agrupamento inteligente de legendas em cenas (3s a 8s).
   - Extração de prompts visuais estruturados e termos em inglês via LLM.

3. **[`02-busca-e-indexacao-de-materiais.md`](./02-busca-e-indexacao-de-materiais.md)**
   - Estratégia de download direcionado por cena (Pexels / Pixabay / Local).
   - Manifesto de materiais indexados por cena (`scene_materials_manifest.json`).
   - Rate limiting, deduplicação e fallbacks de busca.

4. **[`03-motor-de-matching-e-timeline.md`](./03-motor-de-matching-e-timeline.md)**
   - Slot-Fitting Engine (`app/services/timeline_engine.py`).
   - Sincronização milimétrica do clipe ao timestamp da narração.
   - Eliminação de drift temporal e concatenação de alta performance via FFmpeg.

5. **[`04-integracao-pipeline-e-compatibilidade.md`](./04-integracao-pipeline-e-compatibilidade.md)**
   - Modificações em `app/services/task.py` e `long_video.py`.
   - Compatibilidade retroativa total (flag `enable_semantic_matching`).
   - Integração com FastAPI e controles na WebUI Streamlit.

6. **[`05-guias-de-testes-e-qa.md`](./05-guias-de-testes-e-qa.md)**
   - Bateria de testes unitários e de integração (`pytest`).
   - Roteiro de validação visual e critérios de aceitação de sincronia.
   - Checklist prático para o próximo agente desenvolvedor.

7. **[`06-diagnostico-frame-a-frame-e-melhorias.md`](./06-diagnostico-frame-a-frame-e-melhorias.md)**
   - Diagnóstico real de vídeos gerados (Shorts F-22 e Vídeo Longo Petrov 1983).
   - Identificação de falhas graves (anacronismo visual, clichês de máscara de hacker, cortes fora do tempo).
   - Regras anti-anacronismo, blacklist semântica de clichês e hierarquia de fallback em 3 níveis.
