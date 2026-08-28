# Plano 02 — Busca Direcionada e Indexação de Materiais por Cena

> **Objetivo:** Adaptar o download e gerenciamento de vídeos de stock (Pexels, Pixabay, Local) para que os clipes sejam buscados especificamente para atender cada cena da narração, com metadados semânticos anexados a cada arquivo baixado.

---

## 1. O Desafio Atual

No `app/services/material.py`:
- O sistema faz uma busca em lote no início com 5 a 10 termos globais.
- Os vídeos são salvos em `task_dir` como `downloaded-1.mp4`, `downloaded-2.mp4` etc.
- O sistema perde completamente a associação de **qual termo** ou **qual intenção visual** aquele arquivo baixado pretendia ilustrar.

---

## 2. Nova Arquitetura de Download por Cena

```
+-------------------------------------------------------------------+
| VideoSemanticPlan (Cenas 1 a N)                                   |
+---------------------------------+---------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| Scene Material Sourcing (app/services/material.py)                |
| - Para a Cena 01 (ex: "F-22 decolando"):                          |
|   Busca Pexels com termo "f22 raptor taking off"                  |
|   Baixa 1-2 melhores candidatos -> salva com metadados de cena    |
| - Para a Cena 02 (ex: "Tela de radar com erro"):                  |
|   Busca Pexels com termo "radar screen glitch tech"               |
|   Baixa 1-2 melhores candidatos -> salva com metadados de cena    |
+---------------------------------+---------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| Catálogo de Materiais Indexados da Tarefa (materials_manifest)    |
| [                                                                 |
|   {                                                               |
|     "file_path": ".../scene_01_clip_01.mp4",                      |
|     "target_scene_index": 1,                                      |
|     "search_term": "f22 raptor taking off",                       |
|     "duration": 6.2,                                              |
|     "resolution": [1920, 1080],                                   |
|     "aspect": "16:9"                                              |
|   }, ...                                                          |
| ]                                                                 |
+-------------------------------------------------------------------+
```

---

## 3. Regras de Eficiência e Rate Limiting

1. **Evitar Explosão de Requisições de API:**
   - Em vídeos curtos (30-60s), teremos 6 a 12 cenas. Buscar 1 a 2 vídeos por cena é rápido e consome ~15 chamadas de API (muito abaixo do limite de 200 req/hora do Pexels).
   - Em vídeos longos (8 a 20 min), usar a estratégia de deduplicação semântica: agrupar cenas com termos similares ou buscar pacotes de 5 vídeos para 3 cenas adjacentes.
2. **Fallback Gracioso:**
   - Se a busca específica de uma cena não retornar resultados (ex: termo muito específico), fazer fallback automático para o termo geral do assunto do vídeo (`video_subject`), garantindo que o slot nunca fique sem vídeo.
3. **Suporte a Materiais Locais e Categorizados:**
   - Permitir que o usuário forneça uma pasta de materiais locais e a IA indexe os nomes dos arquivos ou use visão leve/metadados para encaixar nos slots adequados.

---

## 4. Especificação de Mudança de Código

### Em `app/services/material.py`:
- Criar a função:
  ```python
  def download_materials_for_scenes(
      task_id: str,
      semantic_plan: VideoSemanticPlan,
      video_source: str,
      video_aspect: VideoAspect,
      max_clips_per_scene: int = 2
  ) -> list[dict]:
      """
      Executa downloads focados por cena e retorna o manifesto
      com mapping de cena -> arquivos disponíveis.
      """
  ```
- Estrutura do arquivo de saída gravado em disco: `task_dir/scene_materials_manifest.json`.

---

## 5. Critérios de Aceite

1. Cada cena do `VideoSemanticPlan` tem pelo menos 1 clipe de vídeo diretamente correspondente ao seu prompt de busca baixado e validado.
2. `scene_materials_manifest.json` é persistido na pasta da tarefa para permitir inspeção, debug e re-renderização sem re-download.
