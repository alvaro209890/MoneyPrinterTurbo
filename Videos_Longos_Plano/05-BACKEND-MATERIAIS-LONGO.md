# 05 — Backend: materiais visuais em escala longa

> **Objetivo:** cobrir até 35 minutos de narração com imagem que faça sentido, sem esgotar as APIs
> gratuitas e sem que o vídeo vire um loop óbvio dos mesmos 12 clipes.

---

## 1. A aritmética que define este plano

| Cenário | Clipes necessários |
|---|---:|
| 35 min ÷ 5 s (default atual) | **420** |
| 35 min ÷ 10 s (default longo proposto) | **210** |
| 35 min ÷ 15 s | **140** |

E do lado da oferta: uma busca no Pexels devolve tipicamente **10–80** resultados por termo, dos
quais uma parte é descartada pelo filtro de aspecto (`material._filter_materials_by_aspect`,
`material.py:265`) e de resolução (`video.is_material_resolution_acceptable`, `video.py:105`).

Com ~70 termos (plano `03` §8) e 10–30 clipes úteis por termo, a oferta bruta é suficiente — **o
gargalo não é quantidade, é rate limit, download e repetição.**

## 2. O que já existe e você reusa

`app/services/material.py`:

- `download_videos()` — linha 1145 — entrada única, roteia por `source`.
- `_download_videos_by_script_order()` — linha 1475 — **este é o modo que você quer**: baixa
  seguindo a ordem dos termos, que no plano `03` §8 é a ordem narrativa.
- `_search_videos_with_cache()` — linha 1052 — cache de 24 h por (provedor, termo, aspecto).
  **Este cache é seu melhor amigo** em execuções longas e em reprocessamentos.
- `save_video()` — linha 992 — download com retry.
- `_persist_material_sources()` — linha 112 — grava a proveniência (crédito/licença) na task.
- Provedores: Pexels (296), Pixabay (378), Coverr (504), WaveSpeed (699, **pago**),
  Volcengine Seedance (`volcengine_seedance.py`, **pago**).

## 3. Orçamento de requisições e rate limit

> ⚠️ Pexels: o limite público é da ordem de **200 requisições/hora**. Um vídeo longo com 70 termos
> gasta 70 buscas + ~200 downloads. **Confirme o limite vigente na documentação do provedor antes
> de fixar números** — não copie este parágrafo como verdade.

Implemente um **orçamento explícito** no modo longo:

```python
@dataclass(frozen=True)
class MaterialBudget:
    max_search_requests: int      # buscas de catálogo
    max_downloads: int            # arquivos baixados
    max_seconds_per_term: float   # quanto de timeline cada termo pode cobrir
```

Regras:
1. **Pare de buscar assim que a duração coberta ≥ duração do áudio + margem.** O loop atual já faz
   isso no caminho padrão (`material.py` ~1265); garanta que o caminho por ordem de script também
   faça.
2. **Throttle entre requisições** — um `time.sleep` pequeno e configurável entre buscas. Sem isso
   você toma 429 no meio de uma geração de 30 minutos.
3. **Trate 429 como retriável com backoff**, não como falha da task.
4. **Nunca** deixe o modo longo cair silenciosamente em provedor pago (`wavespeed`,
   `volcengine_seedance`). Esses só entram se o usuário escolher explicitamente — eles cobram
   **por clipe gerado**, e 210 clipes gerados por IA seria uma conta absurda.
   > 🔴 Adicione uma trava: no modo longo, se `video_source` for um provedor pago, exija
   > confirmação explícita e mostre a estimativa de clipes na UI antes de começar.

## 4. Distribuição do material ao longo do tempo

O erro clássico: baixar tudo e concatenar aleatoriamente → o clipe do assunto do minuto 30 aparece
no minuto 2.

**Regra: material é alocado por capítulo.**

```
capítulo i  →  termos do capítulo i  →  clipes do capítulo i  →  janela [offset_i, offset_i + dur_i]
```

Os offsets vêm prontos do plano `04` (`ChapterAudio.offset` e `.duration`). Para cada capítulo:

```python
needed_seconds = chapter_audio.duration + margem
clips = download_for_terms(chapter.terms, needed_seconds, budget)
```

Isso resolve de graça o principal problema de coesão e é a tradução direta do
`match_materials_to_script=True` que já existe — só que **por capítulo em vez de global**.

## 5. Anti-repetição

`video._prioritize_unique_source_clips()` (`video.py:116`) já espalha as fontes para não repetir o
mesmo arquivo em sequência. Para o modo longo, reforce:

1. **Deduplicação global por URL** — já existe no loop padrão (`valid_video_urls`), mantenha.
2. **Deduplicação por asset_id** entre capítulos — o mesmo vídeo do Pexels pode aparecer em buscas
   de termos diferentes. Use `source_info["asset_id"]` (`material._material_source_record`,
   `material.py:71`).
3. **Cota por fonte**: nenhum arquivo único deve ocupar mais que ~3% da timeline final. Em 35 min
   isso é ~63 s por arquivo. Implemente como filtro na montagem (plano `06`).
4. Se, mesmo assim, faltar material: **é melhor repetir com transição do que falhar**. Emita
   `warning` `long_video_material_repeated` e siga.

## 6. Fallback: imagens + Ken Burns (item A-4 do plano `02`)

Quando um termo não devolve vídeo suficiente, a alternativa barata é **imagem estática com
zoom/pan lento** (efeito Ken Burns) — é exatamente o que a skill `video-criacao` do Hermes já usa
(`image_gen` + `zoompan`) e o que o NotebookLM faz por baixo.

O projeto já sabe abrir imagem como clipe: `video._open_image_clip_with_fallback()` (`video.py:411`)
e `video.preprocess_video()` (`video.py:1300`) aceitam `FILE_TYPE_IMAGES` (`const.py`).

Implementação sugerida (v2 se faltar tempo, mas planeje o gancho agora):
- buscar imagem no mesmo provedor (Pexels/Pixabay têm API de fotos);
- gerar clipe de 8–12 s com `zoompan` do FFmpeg (mais barato que MoviePy);
- marcar na proveniência que aquele trecho é imagem, para o QA visual saber.

> 💡 Isso também é o caminho para pautas sem cobertura de estoque (o gotcha nº 1 da skill
> `moneyprinterturbo`: anime, personagens fictícios, temas abstratos).

## 7. Cache e reaproveitamento entre execuções

O canal publica **1 vídeo longo por dia**, muitas vezes sobre temas próximos (bugs históricos,
falhas de software). O cache de busca de 24 h (`material.py:1052`) ajuda pouco entre dias.

**Proposta:** um diretório de materiais persistente por projeto —
`config.app["material_directory"]` já existe e aceita o valor especial `"task"`
(`material.py:1177-1181`). Se apontado para uma pasta fixa, os downloads se acumulam e são reusados.

Riscos a tratar se você fizer isso:
- crescimento sem limite do disco → precisa de política de limpeza
  (`app/services/cache_manager.py` já existe e a UI tem painel de cache em `Main.py:2149`);
- aumento da repetição visual entre vídeos → equilibre com a cota do §5.3.

## 8. Proveniência e licença (não é opcional)

`_persist_material_sources()` (`material.py:112`) grava provedor, `asset_id`, URL pública e autor.
Em vídeo longo isso importa mais, porque a descrição do YouTube precisa creditar as fontes.

**Entregue uma função que gere o bloco de créditos pronto** para colar na descrição:

```python
def build_credits_block(task_id: str) -> str:
    """Texto de créditos agrupado por provedor, pronto para a descrição do YouTube."""
```

Isso é usado direto pelo Hermes no upload (plano `10`).

## 9. Integração no orquestrador

Em `task.get_video_materials()` (`task.py:625`), o modo longo entra como um ramo:

```python
if params.video_mode == "long":
    return long_video.collect_materials(
        task_id=task_id,
        params=params,
        plan=long_plan,
        chapter_audios=chapter_audios,
        progress_cb=_make_progress_cb(task_id, 40, 55),
    )
# caminho atual, intocado
```

`collect_materials` internamente chama `material.download_videos()` **uma vez por capítulo**, com os
termos daquele capítulo e a duração daquele capítulo. Ou seja: você não reescreve download nenhum —
você orquestra o que já existe.

## 10. Testes obrigatórios deste plano

Em `test/services/test_long_materials.py`:

- `test_materials_are_allocated_per_chapter` — cada capítulo recebe clipes dos seus próprios termos.
- `test_material_budget_stops_searching_when_covered` — para de buscar ao cobrir a duração.
- `test_material_budget_respects_max_search_requests` — não estoura o orçamento.
- `test_rate_limit_response_is_retried_with_backoff` — 429 vira retry, não falha.
- `test_duplicate_asset_ids_are_dropped_across_chapters` — dedupe global.
- `test_no_single_source_exceeds_share_cap` — cota de 3% por arquivo.
- `test_long_mode_refuses_paid_provider_without_explicit_optin` — trava do §3.4.
- `test_insufficient_material_emits_warning_not_failure` — repetição avisada, task segue.
- `test_build_credits_block_groups_by_provider` — créditos formatados.

Rede **sempre** mockada (`requests` monkeypatched, como os testes existentes em
`test_material.py` já fazem).
