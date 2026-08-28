"""
Slot-fitting engine: alinha cada cena da narração a um trecho de clipe.

A sincronia vem dos timestamps das cenas, não de cortes de duração fixa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from loguru import logger

from app.models.schema import ScenePrompt, VideoSemanticPlan

MIN_SLOWDOWN = 0.85
HEAD_TRIM_SECONDS = 0.5
GAP_TOLERANCE = 0.05


@dataclass
class VideoTimelineSlot:
    scene_index: int
    start_time: float
    end_time: float
    target_duration: float
    source_video_path: str
    source_start_offset: float = 0.0
    effects: list[str] = field(default_factory=list)
    speed: float = 1.0


@dataclass
class SemanticTimeline:
    slots: list[VideoTimelineSlot] = field(default_factory=list)
    total_duration: float = 0.0

    def has_gaps(self, tolerance: float = GAP_TOLERANCE) -> bool:
        for previous, current in zip(self.slots, self.slots[1:]):
            if abs(current.start_time - previous.end_time) > tolerance:
                return True
        return False


def probe_media_duration(path: str) -> float:
    if not path:
        return 0.0
    try:
        from app.services.long_render import _probe

        info = _probe(path)
        return float((info.get("format") or {}).get("duration") or 0.0)
    except Exception as exc:
        logger.debug(f"ffprobe duration failed for {path}: {exc}")
        return 0.0


def _clip_duration(entry: dict) -> float:
    try:
        duration = float(entry.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        return duration
    return probe_media_duration(str(entry.get("file_path") or ""))


def _clips_for_scene(manifest: Sequence[dict], scene_index: int) -> list[dict]:
    matched = [
        item
        for item in manifest
        if int(item.get("target_scene_index", -1)) == int(scene_index)
        and item.get("file_path")
    ]
    if matched:
        return matched
    return [item for item in manifest if item.get("file_path")]


def _pick_clip(
    candidates: Sequence[dict],
    last_path: str,
    allow_repeat: bool,
) -> Optional[dict]:
    if not candidates:
        return None
    unique = []
    seen = set()
    for item in candidates:
        path = item.get("file_path")
        if not path or path in seen:
            continue
        seen.add(path)
        unique.append(item)
    if not unique:
        return None
    if allow_repeat or not last_path:
        return unique[0]
    for item in unique:
        if item.get("file_path") != last_path:
            return item
    return unique[0]


def _source_offset(clip_duration: float, target_duration: float, speed: float) -> float:
    needed = target_duration * speed
    if clip_duration <= needed:
        return 0.0
    if clip_duration - HEAD_TRIM_SECONDS >= needed:
        return HEAD_TRIM_SECONDS
    return 0.0


def _fit_clip_to_duration(clip_duration: float, target_duration: float) -> float:
    """Retorna a velocidade de reprodução (1.0 ou slowdown >= 0.85)."""
    if clip_duration <= 0 or target_duration <= 0:
        return 1.0
    if clip_duration + 1e-9 >= target_duration:
        return 1.0
    slowed = clip_duration / target_duration
    if slowed >= MIN_SLOWDOWN:
        return slowed
    return MIN_SLOWDOWN


def _make_slot(
    scene: ScenePrompt,
    start: float,
    end: float,
    clip: dict,
    last_path: str,
) -> VideoTimelineSlot:
    target = max(0.05, end - start)
    clip_duration = _clip_duration(clip)
    speed = _fit_clip_to_duration(clip_duration, target)
    path = str(clip.get("file_path") or "")
    effects = []
    if speed < 1.0:
        effects.append("slowdown")
    if last_path == path and scene.scene_index:
        effects.append("same-scene-loop")
    return VideoTimelineSlot(
        scene_index=scene.scene_index,
        start_time=start,
        end_time=end,
        target_duration=target,
        source_video_path=path,
        source_start_offset=_source_offset(clip_duration, target, speed),
        effects=effects,
        speed=speed,
    )


def build_semantic_timeline(
    semantic_plan: VideoSemanticPlan,
    materials_manifest: list[dict] | None,
    video_paths: Sequence[str] | None = None,
) -> SemanticTimeline:
    """
    Constrói slots 1:1 com as cenas, sem buracos na linha do tempo.

    ``video_paths`` é um fallback quando o manifesto não cobre alguma cena.
    """
    manifest = [item for item in (materials_manifest or []) if isinstance(item, dict)]
    fallback_entries = [
        {"file_path": path, "target_scene_index": -1, "duration": 0.0}
        for path in (video_paths or [])
        if path
    ]

    slots: list[VideoTimelineSlot] = []
    last_path = ""
    cursor = 0.0
    scenes = list(semantic_plan.scenes or [])
    if scenes:
        cursor = float(scenes[0].start_time)

    for scene in scenes:
        start = float(scene.start_time)
        end = float(scene.end_time)
        if start < cursor - GAP_TOLERANCE:
            start = cursor
        elif start > cursor + GAP_TOLERANCE:
            start = cursor
        if end <= start:
            end = start + max(0.05, float(scene.duration) or 0.05)

        candidates = _clips_for_scene(manifest, scene.scene_index)
        if not candidates:
            candidates = fallback_entries
        allow_repeat = False
        clip = _pick_clip(candidates, last_path, allow_repeat=allow_repeat)
        if clip is None:
            logger.warning(f"no material for scene {scene.scene_index}")
            cursor = end
            continue

        clip_duration = _clip_duration(clip)
        target = end - start
        playable = clip_duration / MIN_SLOWDOWN if clip_duration > 0 else 0.0

        if clip_duration + 1e-9 >= target or playable + 1e-9 >= target:
            slot = _make_slot(scene, start, end, clip, last_path)
            slots.append(slot)
            last_path = slot.source_video_path
            cursor = end
            continue

        # Clipe curto demais mesmo com slowdown: divide o slot em micro-clipes
        # da mesma cena (repetir o arquivo na mesma cena é permitido).
        remaining_start = start
        remaining = target
        used_here = 0
        pool: list[dict] = list(candidates) or fallback_entries
        while remaining > 0.05 and pool:
            piece_clip = _pick_clip(
                pool,
                last_path,
                allow_repeat=used_here > 0,
            )
            if piece_clip is None:
                break
            piece_duration = _clip_duration(piece_clip)
            take = min(remaining, piece_duration if piece_duration > 0 else remaining)
            if take <= 0.05:
                break
            piece_end = remaining_start + take
            slot = _make_slot(scene, remaining_start, piece_end, piece_clip, last_path)
            slots.append(slot)
            last_path = slot.source_video_path
            remaining_start = piece_end
            remaining = end - remaining_start
            used_here += 1
            if used_here > 8:
                break
        if remaining_start < end - 0.05 and slots:
            # Estica o último micro-slot até o fim da cena (freeze/slow no render).
            slots[-1].end_time = end
            slots[-1].target_duration = slots[-1].end_time - slots[-1].start_time
            slots[-1].speed = _fit_clip_to_duration(
                _clip_duration({"duration": clip_duration, "file_path": slots[-1].source_video_path}),
                slots[-1].target_duration,
            )
        cursor = end

    total = slots[-1].end_time if slots else float(semantic_plan.total_duration or 0.0)
    timeline = SemanticTimeline(slots=slots, total_duration=total)
    if timeline.has_gaps():
        # Fecha gaps residuais empurrando o início do próximo slot.
        for previous, current in zip(timeline.slots, timeline.slots[1:]):
            if abs(current.start_time - previous.end_time) > 0.001:
                current.start_time = previous.end_time
                current.target_duration = current.end_time - current.start_time
    return timeline
