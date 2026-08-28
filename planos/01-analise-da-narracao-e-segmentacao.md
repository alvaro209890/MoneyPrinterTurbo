# Plano 01 — Análise da Narração e Segmentação em Cenas

> **Objetivo:** Quebrar a narração completa do vídeo em unidades semânticas ("Cenas" ou "Beats Visuais") associadas a timestamps precisos gerados pelo Whisper.

---

## 1. Contexto Atual vs. O Que Mudar

- **Atual:** O `voice.py` / Whisper gera um arquivo `.srt` e uma lista de legendas com `start` e `end` para cada frase curta/linha. O `llm.py` recebe o texto corrido e gera termos soltos.
- **Novo:** Precisamos de uma etapa de **Decomposição Semântica de Cenas** que:
  1. Agrupe ou alinhe as legendas geradas pelo Whisper em blocos de cena (duração típica recomendada: entre 3s e 8s).
  2. Submeta o texto de cada cena para o LLM gerar:
     - `visual_description`: O que visualmente deve estar acontecendo na tela naquele momento.
     - `search_terms`: Termos de busca em inglês específicos para bancos de stock footage (ex: `["f22 raptor flying", "military jet cockpit", "fighter jet supersonic"]`).
     - `mood_or_tone`: Tom do momento (ex: `action`, `dramatic`, `technological`, `historical`).
     - `visual_keywords`: Palavras-chave para scoring.

---

## 2. Estrutura de Dados Proposta

Definir em `app/models/schema.py` ou módulo novo `app/models/semantic_scene.py`:

```python
from pydantic import BaseModel
from typing import List, Optional

class ScenePrompt(BaseModel):
    scene_index: int
    start_time: float      # Segundo de início exato (vindo do Whisper)
    end_time: float        # Segundo de fim exato
    duration: float        # end_time - start_time
    narration_text: str    # Texto falado neste trecho
    visual_description: str # Descrição visual conceitual
    search_terms: List[str] # Termos em inglês otimizados para busca de vídeo stock (ex: Pexels)
    mood: Optional[str] = "neutral"
    must_avoid_terms: Optional[List[str]] = [] # O que não pode aparecer (ex: cartoons se for sério)

class VideoSemanticPlan(BaseModel):
    video_subject: str
    total_duration: float
    scenes: List[ScenePrompt]
```

---

## 3. Algoritmo de Segmentação

### Passo 1: Alinhamento de Subtítulos em Cenas (Grouping)
No `app/services/subtitle.py` ou novo serviço `app/services/semantic_analyzer.py`:
1. Recebe a lista de legendas brutas do Whisper (cada item tem `start_time`, `end_time`, `text`).
2. Agrupa sentenças adjacentes respeitando:
   - Duração mínima por cena: `min_scene_duration = 3.0` segundos.
   - Duração máxima por cena: `max_scene_duration = 7.0` a `8.0` segundos.
   - Pausas naturais / pontuação (pontos finais, exclamações, quebras de parágrafo).

### Passo 2: Extração de Metadados Semânticos via LLM
Criar função no `app/services/llm.py`:
`generate_scene_visual_prompts(scenes_text_with_timestamps: list, subject: str) -> list[ScenePrompt]`

Prompt estruturado para o LLM (com structured output JSON):
- **Entrada:** Lista de cenas numeradas com seu texto falado.
- **Regras no Prompt:**
  1. Para cada cena, identifique o foco visual real do que está sendo dito.
  2. NUNCA gere termos literais vazios como "history" ou "thinking" se a fala fala de "O avião quase colidiu contra a montanha". Gere termos concretos: "airplane flying dangerously close to mountain peak", "cockpit warning alarm".
  3. Termos sempre em inglês (Pexels/Pixabay funcionam melhor em inglês).
  4. Retornar um array JSON estrito mapeando cada `scene_index`.

---

## 4. Arquivos a Criar / Modificar

1. **`app/services/semantic_analyzer.py`** (Novo):
   - Função `group_subtitles_into_scenes(subtitles: list[dict], min_sec=3.5, max_sec=7.0) -> list[dict]`
   - Função `analyze_narration_scenes(task_id: str, script: str, subtitles: list[dict], subject: str) -> VideoSemanticPlan`
2. **`app/services/llm.py`**:
   - Adicionar método `generate_scene_prompts(scenes_data: list, subject: str)` com tratamento de JSON resiliente.
3. **`test/services/test_semantic_analyzer.py`** (Novo):
   - Teste unitário de agrupamento de timestamps e validação do schema JSON retornado pelo LLM.
