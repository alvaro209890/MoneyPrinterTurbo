# Plano 05 — Guia de Testes, Validação e Garantia de Qualidade (QA)

> **Objetivo:** Estabelecer uma bateria completa de testes unitários, testes de integração e critérios de QA para garantir que o pipeline de alinhamento semântico funcione de forma estável, sem regressões e com sincronia visual perfeita.

---

## 1. Estratégia de Testes Automatizados (Pytest)

Criar os seguintes arquivos sob o diretório `test/services/`:

### 1.1 `test/services/test_semantic_analyzer.py`
- **Teste de Agrupamento de Cenas (`test_group_subtitles_into_scenes`):**
  - Fornecer lista mock de subtítulos do Whisper com 15 linhas (total ~45s).
  - Verificar se as cenas agrupadas respeitam o piso mínimo (`min_sec >= 3.0s`) e teto máximo (`max_sec <= 8.0s`).
  - Verificar se a soma das durações das cenas é idêntica à duração total do áudio original.
- **Teste de Parsing de Prompts Visuais do LLM (`test_llm_scene_prompts_parsing`):**
  - Testar respostas do LLM com JSON puro, JSON cercado de markdown fences (\`\`\`json) e JSONs levemente malformados (resiliência).

### 1.2 `test/services/test_timeline_engine.py`
- **Teste de Construção de Slots (`test_build_semantic_timeline`):**
  - Fornecer 5 cenas e 10 vídeos baixados.
  - Verificar se cada slot tem exatamente o `start_time` e `end_time` da cena correspondente.
  - Verificar se não há gaps (buracos de tempo) entre um slot e outro.
  - Testar o comportamento anti-repetição (dois slots consecutivos não usam o mesmo vídeo de 2s).

### 1.3 `test/services/test_semantic_integration.py`
- **Teste de Renderização Sintética:**
  - Gerar um áudio mudo de teste de 10s via FFmpeg.
  - Criar 2 clipes de teste coloridos sintéticos (ex: 5s vermelho, 5s azul) usando FFmpeg `testsrc`.
  - Rodar o pipeline `render_semantic_timeline` e verificar se o arquivo final tem exatos 10s, taxa de quadros estável e áudio sincronizado.

---

## 2. Roteiro de Teste Manual / QA de Vídeo Real

Para validar a qualidade da geração com IA:

1. **Roteiro de Teste com Mudanças Bruscas de Tema (Exemplo de Validação):**
   ```text
   Assunto: "A Evolução dos Transportes"
   Trecho 1 (0s-5s): "Nos tempos antigos, cavalos e carruagens dominavam as estradas de terra."
   Trecho 2 (5s-10s): "Com a revolução industrial, os trens a vapor cortaram continentes inteiros."
   Trecho 3 (10s-15s): "Hoje, foguetes espaciais levam a humanidade em direção a Marte."
   ```

2. **Critérios de Aprovação no QA Visual:**
   - [ ] No trecho 1 (0s-5s), o vídeo mostra cavalos, carruagens ou estradas antigas (NÃO trens nem foguetes).
   - [ ] No trecho 2 (5s-10s), o corte ocorre exatamente na virada da frase e mostra locomotivas/ferrovias.
   - [ ] No trecho 3 (10s-15s), o corte ocorre na virada e mostra foguetes/espaço.
   - [ ] Nenhum clipe sofre estiramento de proporção (aspect ratio correto 9:16 ou 16:9).
   - [ ] Transições suaves sem flash preto.

---

## 3. Checklist de Implementação para o Próximo Agente

- [ ] `app/models/schema.py`: Adicionar campos de configuração semântica em `VideoParams`.
- [ ] `app/services/semantic_analyzer.py`: Criar agrupador de legendas e chamadas de prompt estruturado.
- [ ] `app/services/llm.py`: Adicionar helper de geração de prompts de cena.
- [ ] `app/services/material.py`: Implementar busca segmentada por cena.
- [ ] `app/services/timeline_engine.py`: Implementar construtor de timeline e slot-fitting.
- [ ] `app/services/video.py`: Implementar renderização baseada em timeline exata.
- [ ] `app/services/task.py` e `long_video.py`: Conectar o novo fluxo condicionado à flag.
- [ ] `test/`: Adicionar e rodar todos os testes automatizados com sucesso (`pytest`).
- [ ] `webui/`: Adicionar chave de tradução e toggle no painel web.
