"""
长视频的素材获取 / Long-form material sourcing.

35 分钟 ÷ 每段 10 秒 = 210 个片段。短视频那套"为整篇文案取一组关键词、一次性
下载"的做法在这个量级上会同时踩三个坑：库存 API 限流、素材重复到肉眼可见，
以及画面与叙述脱节——第 28 分钟的主题用第 2 分钟的关键词去搜。

这里的做法是**按章节分配**：每一章用自己的关键词，只下载覆盖该章配音时长所
需的素材。章节的时间区间来自 long_audio.ChapterAudio，因此画面天然跟着叙述走。

Materials are allocated per chapter, so the clip illustrating minute 28 comes
from the terms of minute 28.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from loguru import logger

from app.models import const
from app.models.schema import VideoAspect, VideoConcatMode
from app.utils import utils

# 按条计费的素材来源。长视频需要上百个片段，误用这些来源会产生离谱的账单，
# 因此必须由调用方显式选择，绝不能作为兜底。
PAID_VIDEO_SOURCES = frozenset({"wavespeed", "volcengine_seedance", "loomloom"})

# 章节之间的请求间隔。没有这个间隔，一次 35 分钟的生成会在中途撞上库存 API
# 的限流，而那时前面的 LLM 和 TTS 配额已经花掉了。
DEFAULT_THROTTLE_SECONDS = 0.5

# 单个素材文件在成片中允许占据的最大比例。超过这个比例，观众就会明显感觉到
# "同一段画面又出现了"。
DEFAULT_MAX_SOURCE_SHARE = 0.03

# 素材时长相对配音时长的冗余系数。拼接时需要一些余量来吸收片段长度的取整。
MATERIAL_DURATION_MARGIN = 1.15


@dataclass
class MaterialBudget:
    """一次长视频任务的素材预算。"""

    max_downloads: int = 400
    max_seconds_per_chapter: float = 0.0  # 0 表示按章节实际时长计算
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS
    max_source_share: float = DEFAULT_MAX_SOURCE_SHARE
    downloads_used: int = field(default=0, init=False)

    @property
    def remaining_downloads(self) -> int:
        return max(0, self.max_downloads - self.downloads_used)

    def consume(self, count: int) -> None:
        self.downloads_used += max(0, count)


class PaidSourceNotConfirmedError(RuntimeError):
    """长视频模式下未经确认地选择了按条计费的素材来源。"""


def ensure_source_is_allowed(video_source: str, confirmed: bool = False) -> None:
    """
    拦截未经确认的付费素材来源。

    长视频需要上百个片段。把 wavespeed / seedance 当作默认来源会在用户毫无
    察觉的情况下产生数百次计费生成，因此这里要求显式确认。
    """
    source = (video_source or "").strip().lower()
    if source in PAID_VIDEO_SOURCES and not confirmed:
        raise PaidSourceNotConfirmedError(
            f"'{source}' bills per generated clip and a long video needs hundreds "
            "of them; pass an explicit confirmation to use it in long mode"
        )


def estimate_clip_count(duration_seconds: float, clip_duration: int) -> int:
    """估算成片需要的片段数量，用于界面提示和预算校验。"""
    if clip_duration <= 0:
        return 0
    return max(1, int(round(duration_seconds / clip_duration)))


def _read_script_data(task_id: str) -> Dict[str, Any]:
    """读取任务清单；不存在或损坏时返回空字典。"""
    script_file = os.path.join(utils.task_dir(task_id), "script.json")
    try:
        with open(script_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning(f"failed to read task script data: {exc}")
        return {}


def _read_material_sources(task_id: str) -> List[dict]:
    sources = _read_script_data(task_id).get("material_sources")
    return list(sources) if isinstance(sources, list) else []


def _dedupe_material_sources(sources: Sequence[dict]) -> List[dict]:
    """按 (provider, asset_id) 或本地文件名去重，保留首次出现的顺序。"""
    seen: set = set()
    unique: List[dict] = []
    for record in sources:
        if not isinstance(record, dict):
            continue
        asset_id = record.get("asset_id")
        provider = record.get("provider", "")
        key = (
            (str(provider), str(asset_id))
            if asset_id not in (None, "")
            else ("file", str(record.get("local_file", "")))
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def collect_materials(
    task_id: str,
    params,
    plan,
    chapter_audios: Sequence,
    budget: Optional[MaterialBudget] = None,
    progress_cb: Optional[Callable[[float], None]] = None,
    paid_source_confirmed: bool = False,
) -> tuple[List[str], List[dict]]:
    """
    按章节获取素材，返回 ``(素材路径列表, 警告列表)``。

    每一章调用一次未经修改的 ``material.download_videos``，传入该章自己的
    关键词和实际配音时长。素材路径按章节顺序返回，与 ``sequential`` 拼接模式
    配合后，画面顺序即叙述顺序。
    """
    from app.services import material

    ensure_source_is_allowed(params.video_source, confirmed=paid_source_confirmed)

    budget = budget or MaterialBudget()
    warnings: List[dict] = []
    audio_by_index = {item.index: item for item in chapter_audios}

    # download_videos 内部会用 patch_script_data 覆盖 material_sources 这个键。
    # 逐章调用时，后一章会把前一章的来源记录冲掉，最终只剩最后一章的素材出处
    # ——那样 YouTube 描述里的署名就会漏掉绝大部分素材。这里在每次调用后立刻
    # 取回本次写入的记录，自己累积，最后一次性回写完整列表。
    accumulated_sources: List[dict] = []

    ordered_paths: List[str] = []
    seen_paths: set = set()
    total_chapters = len(plan.chapters)
    chapters_without_material = 0

    for position, chapter in enumerate(plan.chapters):
        chapter_audio = audio_by_index.get(chapter.index)
        if chapter_audio is None:
            # 该章在时长上限裁剪时被丢弃，不需要素材。
            continue

        terms = list(chapter.terms)
        if not terms:
            # 该章没有关键词时借用相邻章节的，画面稍逊但不至于开天窗。
            terms = _borrow_terms(plan, chapter.index)
            if terms:
                logger.info(
                    f"chapter {chapter.index} has no terms; borrowing from neighbours"
                )

        if not terms:
            chapters_without_material += 1
            logger.warning(
                f"chapter {chapter.index} has no usable search terms; skipping"
            )
            continue

        needed_seconds = (
            budget.max_seconds_per_chapter
            or chapter_audio.duration * MATERIAL_DURATION_MARGIN
        )

        if budget.remaining_downloads <= 0:
            warnings.append(
                {
                    "code": const.WARNING_LONG_VIDEO_MATERIAL_REPEATED,
                    "message": (
                        "material download budget exhausted; later chapters reuse "
                        "earlier footage"
                    ),
                }
            )
            logger.warning("material download budget exhausted")
            break

        logger.info(
            f"collecting material for chapter {chapter.index}/{total_chapters} "
            f"'{chapter.title}': {needed_seconds:.0f}s from {len(terms)} terms"
        )

        try:
            paths = material.download_videos(
                task_id=task_id,
                search_terms=terms,
                source=params.video_source,
                video_aspect=VideoAspect(params.video_aspect),
                video_concat_mode=VideoConcatMode.sequential,
                audio_duration=needed_seconds,
                max_clip_duration=params.video_clip_duration,
                match_script_order=True,
            )
        except Exception as exc:
            # 单章素材失败不应作废整条流水线：后续拼接会用其它章节的素材补足，
            # 画面变差但视频仍然成立。
            logger.warning(
                f"failed to collect material for chapter {chapter.index}: {exc}"
            )
            paths = []

        accumulated_sources.extend(_read_material_sources(task_id))

        new_paths = 0
        for path in paths or []:
            if path in seen_paths:
                # 同一个素材被不同章节搜到属于正常现象，但重复放进时间线会
                # 让观众直接看出循环。
                continue
            seen_paths.add(path)
            ordered_paths.append(path)
            new_paths += 1
        budget.consume(new_paths)

        if not paths:
            chapters_without_material += 1

        if progress_cb:
            progress_cb((position + 1) / max(1, total_chapters))

        if budget.throttle_seconds > 0 and position < total_chapters - 1:
            time.sleep(budget.throttle_seconds)

    if chapters_without_material:
        warnings.append(
            {
                "code": const.WARNING_LONG_VIDEO_MATERIAL_REPEATED,
                "message": (
                    f"{chapters_without_material} chapter(s) had no dedicated "
                    "material; existing footage is reused for them"
                ),
            }
        )

    _persist_accumulated_sources(task_id, accumulated_sources)
    logger.success(
        f"long-video materials ready: {len(ordered_paths)} clips "
        f"across {total_chapters} chapters"
    )
    return ordered_paths, warnings


def _borrow_terms(plan, chapter_index: int) -> List[str]:
    """从相邻章节借用关键词，优先前一章。"""
    by_index = {chapter.index: chapter for chapter in plan.chapters}
    for candidate in (chapter_index - 1, chapter_index + 1):
        chapter = by_index.get(candidate)
        if chapter and chapter.terms:
            return list(chapter.terms)
    for chapter in plan.chapters:
        if chapter.terms:
            return list(chapter.terms)
    return []


def _persist_accumulated_sources(task_id: str, sources: Sequence[dict]) -> None:
    """把逐章累积的素材来源一次性写回任务清单。"""
    if not sources:
        return
    from app.services import task_artifacts

    unique = _dedupe_material_sources(sources)
    try:
        task_artifacts.patch_script_data(task_id, material_sources=unique)
        logger.info(f"saved {len(unique)} material source records for {task_id}")
    except Exception as exc:
        # 来源记录是辅助信息，写盘失败不能影响成片。
        logger.warning(f"failed to persist accumulated material sources: {exc}")


def build_credits_block(task_id: str) -> str:
    """
    生成可直接粘贴到视频描述里的素材署名文本。

    素材来源的许可通常要求署名。长视频里这一点更重要：一条视频可能用到上百个
    片段，事后逐个回溯是不现实的，必须在生成时就整理好。
    """
    sources = _dedupe_material_sources(_read_material_sources(task_id))
    if not sources:
        return ""

    by_provider: Dict[str, List[dict]] = {}
    for record in sources:
        provider = str(record.get("provider") or "unknown").strip() or "unknown"
        by_provider.setdefault(provider, []).append(record)

    lines: List[str] = ["Materiais de vídeo:"]
    for provider in sorted(by_provider):
        records = by_provider[provider]
        lines.append("")
        lines.append(f"{provider.capitalize()} ({len(records)} clipes):")
        credited = 0
        for record in records:
            creator = record.get("creator")
            name = ""
            if isinstance(creator, dict):
                name = str(creator.get("name") or "").strip()
            page = str(record.get("source_page") or "").strip()
            if not name and not page:
                continue
            entry = f"  - {name}" if name else "  -"
            if page:
                entry = f"{entry} — {page}" if name else f"  - {page}"
            lines.append(entry)
            credited += 1
        if not credited:
            lines.append("  - (sem dados de autoria no registro da task)")

    return "\n".join(lines).strip()


def write_credits_file(task_id: str) -> str:
    """把署名文本写入任务目录，供上传流程直接读取。"""
    block = build_credits_block(task_id)
    if not block:
        return ""
    target = os.path.join(utils.task_dir(task_id), "credits.txt")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(block + "\n")
    return target
