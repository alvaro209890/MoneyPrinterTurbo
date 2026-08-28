"""
Análise semântica da narração e segmentação em cenas visuais.

Agrupa legendas do Whisper (ou SRT equivalente) em blocos de 3–8s e pede ao
LLM prompts visuais estruturados, com filtros anti-anacronismo e blacklist de
clichês de stock footage.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable, Sequence

from loguru import logger

from app.models.schema import ScenePrompt, VideoSemanticPlan
from app.utils import utils

SENTENCE_END_RE = re.compile(r"[.!?。！？…][\"'”’)]?\s*$")
_WHITESPACE_RE = re.compile(r"\s+")

# Clichês tóxicos de stock (máscara de hacker, lupa no monitor, etc.).
STOCK_SLOP_BLACKLIST = (
    "hacker mask",
    "anonymous mask",
    "guy in hoodie hacking",
    "hooded hacker",
    "hacker with mask",
    "magnifying glass computer",
    "person pointing at laptop",
    "stock businessman laptop",
    "woman with magnifying glass",
    "anonymous guy fawkes",
)

# Termos de época que costumam puxar material anacrônico.
ANACHRONISM_AVOID = {
    "cold war": (
        "musket",
        "napoleonic",
        "18th century",
        "civil war union",
        "french revolution",
        "medieval knight",
    ),
    "modern military": (
        "musket",
        "biplane ww1",
        "spitfire ww2",
        "civil war",
    ),
    "aviation": (
        "hacker mask",
        "anonymous mask",
        "hooded hacker",
    ),
}

_ERA_HINTS = (
    (re.compile(r"\b(198[0-9]|cold war|guerra fria|soviet|petrov)\b", re.I), "1980s Cold War"),
    (re.compile(r"\b(f-?22|200[0-9]|201[0-9]|202[0-9]|stealth fighter)\b", re.I), "Modern Military 2000s"),
    (re.compile(r"\b(ww2|world war ii|194[0-9])\b", re.I), "World War II 1940s"),
    (re.compile(r"\b(victorian|19th century)\b", re.I), "19th century"),
)


def infer_historical_era(subject: str, script: str = "") -> str:
    blob = f"{subject or ''} {script or ''}"
    for pattern, era in _ERA_HINTS:
        if pattern.search(blob):
            return era
    return ""


def _normalize_term(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip().lower())


def filter_blacklisted_terms(
    terms: Iterable[str],
    extra_avoid: Sequence[str] | None = None,
    era: str = "",
) -> list[str]:
    banned = {_normalize_term(item) for item in STOCK_SLOP_BLACKLIST}
    for item in extra_avoid or []:
        token = _normalize_term(item)
        if token:
            banned.add(token)
    era_key = _normalize_term(era)
    for key, avoids in ANACHRONISM_AVOID.items():
        if key in era_key or era_key in key:
            banned.update(_normalize_term(item) for item in avoids)

    cleaned: list[str] = []
    seen: set[str] = set()
    for term in terms or []:
        text = (term or "").strip()
        if not text:
            continue
        lowered = _normalize_term(text)
        if any(bad and bad in lowered for bad in banned):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(text)
    return cleaned


def _as_subtitle(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text = str(
        item.get("text")
        or item.get("msg")
        or item.get("narration_text")
        or ""
    ).strip()
    try:
        start = float(item.get("start_time", item.get("start", 0.0)))
        end = float(item.get("end_time", item.get("end", start)))
    except (TypeError, ValueError):
        return None
    if end < start:
        start, end = end, start
    if not text or end <= start:
        return None
    return {"text": text, "start_time": start, "end_time": end}


def _split_overlong_subtitle(item: dict[str, Any], max_sec: float) -> list[dict[str, Any]]:
    duration = item["end_time"] - item["start_time"]
    if duration <= max_sec + 1e-9:
        return [item]

    chunks = max(2, int(duration // max_sec) + (1 if duration % max_sec > 0.5 else 0))
    # Prefer chunks that stay inside [something, max_sec].
    while chunks > 1 and duration / chunks > max_sec:
        chunks += 1
    piece = duration / chunks
    words = item["text"].split()
    words_per = max(1, len(words) // chunks) if words else 1
    result = []
    for index in range(chunks):
        start = item["start_time"] + piece * index
        end = item["end_time"] if index == chunks - 1 else start + piece
        if words:
            w_start = index * words_per
            w_end = len(words) if index == chunks - 1 else min(len(words), w_start + words_per)
            text = " ".join(words[w_start:w_end]).strip() or item["text"]
        else:
            text = item["text"]
        result.append({"text": text, "start_time": start, "end_time": end})
    return result


def _group_duration(group: Sequence[dict[str, Any]]) -> float:
    return float(group[-1]["end_time"] - group[0]["start_time"])


def _merge_short_groups(
    groups: list[list[dict[str, Any]]],
    min_sec: float,
    max_sec: float,
) -> list[list[dict[str, Any]]]:
    if not groups:
        return groups
    merged: list[list[dict[str, Any]]] = []
    for group in groups:
        if not merged:
            merged.append(group)
            continue
        if _group_duration(group) >= min_sec:
            merged.append(group)
            continue
        combined = merged[-1] + group
        if _group_duration(combined) <= max_sec + 1e-9:
            merged[-1] = combined
        else:
            merged.append(group)

    if len(merged) >= 2 and _group_duration(merged[-1]) < min_sec:
        combined = merged[-2] + merged[-1]
        if _group_duration(combined) <= max_sec + 1e-9:
            merged[-2] = combined
            merged.pop()
    return merged


def _split_scene_window(
    start: float,
    end: float,
    text: str,
    min_sec: float,
    max_sec: float,
) -> list[dict[str, float | str]]:
    duration = end - start
    if duration <= max_sec + 1e-9:
        return [{"start_time": start, "end_time": end, "narration_text": text}]

    scenes = []
    cursor = start
    remaining_text = text
    while end - cursor > max_sec + 1e-9:
        piece_end = cursor + max_sec
        scenes.append(
            {
                "start_time": cursor,
                "end_time": piece_end,
                "narration_text": remaining_text,
            }
        )
        cursor = piece_end
    tail = end - cursor
    if tail < min_sec and scenes:
        # Doa o resto para a cena anterior se ela continuar <= max após o ajuste.
        prev = scenes[-1]
        extra = min_sec - tail
        new_prev_end = prev["end_time"] - extra
        if new_prev_end - prev["start_time"] >= min_sec - 1e-9:
            prev["end_time"] = new_prev_end
            cursor = new_prev_end
            tail = end - cursor
    scenes.append(
        {"start_time": cursor, "end_time": end, "narration_text": remaining_text}
    )
    return scenes


def _enforce_duration_bounds(
    scenes: list[dict[str, Any]],
    min_sec: float,
    max_sec: float,
) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for scene in scenes:
        bounded.extend(
            _split_scene_window(
                float(scene["start_time"]),
                float(scene["end_time"]),
                str(scene.get("narration_text") or ""),
                min_sec,
                max_sec,
            )
        )

    # Re-merge caudas curtas após o split.
    changed = True
    while changed and len(bounded) >= 2:
        changed = False
        for index in range(len(bounded) - 1, -1, -1):
            duration = bounded[index]["end_time"] - bounded[index]["start_time"]
            if duration >= min_sec - 1e-9:
                continue
            neighbor = index - 1 if index > 0 else index + 1
            if neighbor < 0 or neighbor >= len(bounded):
                continue
            left, right = (
                (bounded[neighbor], bounded[index])
                if neighbor < index
                else (bounded[index], bounded[neighbor])
            )
            combined_dur = right["end_time"] - left["start_time"]
            if combined_dur <= max_sec + 1e-9:
                left["end_time"] = right["end_time"]
                left["narration_text"] = (
                    f"{left.get('narration_text', '')} {right.get('narration_text', '')}"
                ).strip()
                bounded.pop(max(index, neighbor))
                changed = True
                break
    return bounded


def group_subtitles_into_scenes(
    subtitles: list[dict] | None,
    min_sec: float = 3.5,
    max_sec: float = 7.0,
    total_duration: float | None = None,
) -> list[dict]:
    """
    Agrupa legendas adjacentes em cenas de ``min_sec`` a ``max_sec``.

    A cobertura temporal é contínua: a soma das durações coincide com
    ``total_duration`` (ou com o fim da última legenda, se a duração total
    não for informada).
    """
    min_sec = float(min_sec)
    max_sec = float(max_sec)
    if max_sec < min_sec:
        max_sec = min_sec

    cleaned: list[dict[str, Any]] = []
    for raw in subtitles or []:
        item = _as_subtitle(raw)
        if item:
            cleaned.append(item)
    cleaned.sort(key=lambda item: (item["start_time"], item["end_time"]))

    for index in range(1, len(cleaned)):
        if cleaned[index]["start_time"] < cleaned[index - 1]["end_time"]:
            cleaned[index]["start_time"] = cleaned[index - 1]["end_time"]
        if cleaned[index]["end_time"] <= cleaned[index]["start_time"]:
            cleaned[index]["end_time"] = cleaned[index]["start_time"] + 0.05

    expanded: list[dict[str, Any]] = []
    for item in cleaned:
        expanded.extend(_split_overlong_subtitle(item, max_sec))
    if not expanded:
        return []

    groups: list[list[dict[str, Any]]] = [[expanded[0]]]
    for item in expanded[1:]:
        current = groups[-1]
        start = current[0]["start_time"]
        proposed = item["end_time"] - start
        current_dur = current[-1]["end_time"] - start
        if proposed > max_sec + 1e-9:
            groups.append([item])
            continue
        if current_dur >= min_sec and SENTENCE_END_RE.search(current[-1]["text"]):
            gap = item["start_time"] - current[-1]["end_time"]
            if gap >= 0.25 or proposed >= min_sec:
                groups.append([item])
                continue
        current.append(item)

    groups = _merge_short_groups(groups, min_sec, max_sec)

    scenes = []
    for group in groups:
        start = group[0]["start_time"]
        end = group[-1]["end_time"]
        text = " ".join(item["text"] for item in group).strip()
        scenes.append(
            {
                "start_time": start,
                "end_time": end,
                "duration": end - start,
                "narration_text": text,
            }
        )

    audio_end = (
        float(total_duration)
        if total_duration and float(total_duration) > 0
        else float(scenes[-1]["end_time"])
    )
    scenes[0]["start_time"] = 0.0
    for index in range(len(scenes) - 1):
        if scenes[index + 1]["start_time"] > scenes[index]["end_time"]:
            scenes[index]["end_time"] = scenes[index + 1]["start_time"]
        elif scenes[index + 1]["start_time"] < scenes[index]["end_time"]:
            scenes[index + 1]["start_time"] = scenes[index]["end_time"]
    scenes[-1]["end_time"] = max(scenes[-1]["end_time"], audio_end)

    scenes = _enforce_duration_bounds(scenes, min_sec, max_sec)
    # Garante cobertura contínua após splits/merges.
    if scenes:
        scenes[0]["start_time"] = 0.0
        for index in range(len(scenes) - 1):
            scenes[index]["end_time"] = scenes[index + 1]["start_time"]
        scenes[-1]["end_time"] = audio_end

    result = []
    for index, scene in enumerate(scenes, start=1):
        duration = float(scene["end_time"]) - float(scene["start_time"])
        if duration <= 0:
            continue
        result.append(
            {
                "scene_index": index,
                "start_time": float(scene["start_time"]),
                "end_time": float(scene["end_time"]),
                "duration": duration,
                "narration_text": str(scene.get("narration_text") or ""),
            }
        )
    return result


def load_subtitles_from_srt(subtitle_path: str) -> list[dict]:
    if not subtitle_path or not os.path.isfile(subtitle_path):
        return []
    from app.services.long_audio import _read_srt_blocks

    return [
        {"text": text, "start_time": start, "end_time": end}
        for start, end, text in _read_srt_blocks(subtitle_path)
    ]


def _fallback_visual_prompt(scene: dict, subject: str, era: str) -> dict[str, Any]:
    narration = (scene.get("narration_text") or "").strip()
    terms = []
    if subject:
        terms.append(subject)
        terms.append(f"{subject} cinematic footage")
    if era:
        terms.append(era)
    snippet = " ".join(narration.split()[:8])
    if snippet:
        terms.append(snippet)
    return {
        "visual_description": narration or subject or "cinematic b-roll",
        "search_terms": filter_blacklisted_terms(terms, era=era) or [subject or "cinematic b-roll"],
        "visual_keywords": filter_blacklisted_terms(terms[:3], era=era),
        "mood": "neutral",
        "must_avoid_terms": list(STOCK_SLOP_BLACKLIST[:6]),
        "historical_era": era or None,
    }


def _apply_llm_prompts(
    scenes: list[dict],
    prompts: list[dict],
    subject: str,
    era: str,
) -> list[ScenePrompt]:
    by_index = {}
    for item in prompts or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("scene_index"))
        except (TypeError, ValueError):
            continue
        by_index[index] = item

    result: list[ScenePrompt] = []
    for scene in scenes:
        index = int(scene["scene_index"])
        payload = by_index.get(index) or {}
        fallback = _fallback_visual_prompt(scene, subject, era)
        avoid = list(payload.get("must_avoid_terms") or fallback["must_avoid_terms"])
        scene_era = str(payload.get("historical_era") or era or "")
        search_terms = filter_blacklisted_terms(
            payload.get("search_terms") or fallback["search_terms"],
            extra_avoid=avoid,
            era=scene_era,
        )
        visual_keywords = filter_blacklisted_terms(
            payload.get("visual_keywords") or fallback["visual_keywords"],
            extra_avoid=avoid,
            era=scene_era,
        )
        if not search_terms:
            search_terms = fallback["search_terms"]
        result.append(
            ScenePrompt(
                scene_index=index,
                start_time=float(scene["start_time"]),
                end_time=float(scene["end_time"]),
                duration=float(scene["duration"]),
                narration_text=str(scene.get("narration_text") or ""),
                visual_description=str(
                    payload.get("visual_description") or fallback["visual_description"]
                ),
                search_terms=search_terms,
                visual_keywords=visual_keywords,
                mood=str(payload.get("mood") or fallback["mood"] or "neutral"),
                must_avoid_terms=avoid,
                historical_era=scene_era or None,
            )
        )
    return result


def analyze_narration_scenes(
    task_id: str,
    script: str,
    subtitles: list[dict] | None,
    subject: str,
    min_sec: float = 3.5,
    max_sec: float = 7.0,
    total_duration: float | None = None,
    historical_era: str = "",
    app_config=None,
) -> VideoSemanticPlan:
    """Decompõe a narração em cenas e anexa prompts visuais."""
    from app.services import llm

    era = historical_era or infer_historical_era(subject, script)
    scenes = group_subtitles_into_scenes(
        subtitles,
        min_sec=min_sec,
        max_sec=max_sec,
        total_duration=total_duration,
    )
    if not scenes:
        return VideoSemanticPlan(
            video_subject=subject or "",
            historical_era=era,
            total_duration=float(total_duration or 0.0),
            scenes=[],
        )

    prompts: list[dict] = []
    try:
        prompts = llm.generate_scene_prompts(
            scenes_data=scenes,
            subject=subject or "",
            historical_era=era,
            app_config=app_config,
        )
    except Exception as exc:
        logger.warning(f"scene prompt LLM failed, using heuristic fallback: {exc}")

    plan = VideoSemanticPlan(
        video_subject=subject or "",
        historical_era=era,
        total_duration=float(
            total_duration if total_duration else scenes[-1]["end_time"]
        ),
        scenes=_apply_llm_prompts(scenes, prompts, subject or "", era),
    )
    _persist_semantic_plan(task_id, plan)
    return plan


def analyze_long_narration_scenes(
    task_id: str,
    plan,
    chapter_audios: Sequence,
    subject: str,
    min_sec: float = 3.5,
    max_sec: float = 7.0,
    historical_era: str = "",
    app_config=None,
) -> VideoSemanticPlan:
    """Roda a decomposição por capítulo e unifica os timestamps globais."""
    era = historical_era or infer_historical_era(subject, getattr(plan, "full_script", ""))
    audio_by_index = {item.index: item for item in chapter_audios or []}
    merged_scenes: list[ScenePrompt] = []
    global_end = 0.0

    chapters = list(getattr(plan, "chapters", []) or [])
    for chapter in chapters:
        audio = audio_by_index.get(chapter.index)
        if audio is None:
            continue
        offset = float(getattr(audio, "offset", 0.0) or 0.0)
        duration = float(getattr(audio, "duration", 0.0) or 0.0)
        subtitle_file = str(getattr(audio, "subtitle_file", "") or "")
        subs = load_subtitles_from_srt(subtitle_file)
        if not subs:
            continue
        chapter_subject = f"{subject} — {chapter.title}" if chapter.title else subject
        chapter_plan = analyze_narration_scenes(
            task_id=task_id,
            script=chapter.script,
            subtitles=subs,
            subject=chapter_subject,
            min_sec=min_sec,
            max_sec=max_sec,
            total_duration=duration,
            historical_era=era,
            app_config=app_config,
        )
        for scene in chapter_plan.scenes:
            merged_scenes.append(
                scene.model_copy(
                    update={
                        "start_time": scene.start_time + offset,
                        "end_time": scene.end_time + offset,
                    }
                )
            )
            global_end = max(global_end, scene.end_time + offset)

    for index, scene in enumerate(merged_scenes, start=1):
        merged_scenes[index - 1] = scene.model_copy(update={"scene_index": index})

    unified = VideoSemanticPlan(
        video_subject=subject or "",
        historical_era=era,
        total_duration=global_end,
        scenes=merged_scenes,
    )
    _persist_semantic_plan(task_id, unified)
    return unified


def _persist_semantic_plan(task_id: str, plan: VideoSemanticPlan) -> None:
    if not task_id:
        return
    path = os.path.join(utils.task_dir(task_id), "semantic_plan.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(plan.model_dump(), handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning(f"failed to persist semantic plan: {exc}")
