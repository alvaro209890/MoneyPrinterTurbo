# Plano 04 — Integração no Pipeline e Compatibilidade Retroativa

> **Objetivo:** Integrar o novo fluxo semântico nos orquestradores de tarefas (`task.py` e `long_video.py`), na API FastAPI e na interface WebUI Streamlit sem quebrar o comportamento legado.

---

## 1. Modificações de Schema e Parâmetros (`app/models/schema.py`)

Adicionar nova flag e configurações no `VideoParams`:

```python
class VideoParams(BaseModel):
    # Campos existentes...
    
    # Novo modo inteligente de correspondência semântica:
    enable_semantic_matching: bool = True  # Ativa o novo pipeline inteligente
    semantic_min_scene_duration: float = 3.5 # Duração mínima por corte
    semantic_max_scene_duration: float = 7.0 # Duração máxima por corte
    semantic_search_provider: str = "pexels" # pexels, pixabay, local
```

---

## 2. Refatoração do Fluxo em `app/services/task.py`

No método `start(task_id, params)`:

```python
# 1. Geração / Processamento do Áudio (TTS + Whisper)
audio_file, audio_duration, sub_maker = voice.tts(...)
subtitles = subtitle.create(sub_maker, ...)

if params.enable_semantic_matching:
    # 2. FLUXO NOVO: Análise Semântica e Segmentação de Cenas
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=30)
    semantic_plan = semantic_analyzer.analyze_narration_scenes(
        task_id=task_id,
        script=video_script,
        subtitles=subtitles,
        subject=params.video_subject
    )
    
    # 3. Busca e Download de Materiais Segmentados por Cena
    sm.state.update_task(task_id, progress=45)
    materials_manifest = material.download_materials_for_scenes(
        task_id=task_id,
        semantic_plan=semantic_plan,
        video_source=params.video_source,
        video_aspect=params.video_aspect
    )
    
    # 4. Construção da Timeline Exata e Renderização
    sm.state.update_task(task_id, progress=65)
    timeline = timeline_engine.build_semantic_timeline(
        semantic_plan=semantic_plan,
        materials_manifest=materials_manifest
    )
    
    combined_video_path = video.render_semantic_timeline(
        task_id=task_id,
        timeline=timeline,
        audio_file=audio_file,
        params=params
    )
else:
    # FLUXO LEGADO: Mantido intacto caso o usuário desative a flag
    ...
```

---

## 3. Adaptação para o Modo de Vídeo Longo (`app/services/long_video.py`)

No pipeline de vídeos longos:
- Cada capítulo já possui seu próprio arquivo de áudio e script parcial.
- O `analyze_narration_scenes` roda **por capítulo**, garantindo que vídeos de 10 a 20 minutos sejam divididos em dezenas de micro-cenas perfeitamente alinhadas, sem estourar o contexto do LLM.

---

## 4. Atualização da Interface WebUI (`webui/Main.py` e `webui/i18n/*.json`)

1. Adicionar toggle no painel de configurações de vídeo:
   - `enable_semantic_matching`: *"Alinhamento Semântico Inteligente de Cenas (IA)"*
   - Tooltip explicativo: *"A IA analisa a fala segundo a segundo e busca clipes que mostram exatamente o que está sendo narrado naquele momento."*
2. Adicionar chaves de tradução no `pt.json`, `en.json`, `zh.json`.
