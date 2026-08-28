"""
长视频模式的编排逻辑 / Long-form video orchestration.

该模块把"最长 35 分钟的长视频"拆成短视频流水线能够消化的单元：先规划章节
大纲，再逐章生成文案、关键词、配音和素材，最后交回给既有的合成流程。

设计约束（见 Videos_Longos_Plano/）：

1. **不修改 llm.generate_script**。短视频流水线每天都在生产运行，任何共享
   代码的改动都必须保持向后兼容。长视频通过"多次调用 + 每次都在 10 段以内"
   来绕开 MAX_SCRIPT_PARAGRAPH_NUMBER，而不是抬高这个上限。
2. **时长上限是硬约束**，在预检、配音后和渲染三处分别校验。
3. 章节是重试、缓存和进度上报的基本单位。

This module turns "a video of up to 35 minutes" into units the existing short-form
pipeline can already handle: plan a chapter outline, then generate script, terms,
narration and materials chapter by chapter, and hand the result back to the
unchanged rendering stage.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from app.models import const
from app.models.schema import (
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)

# ---------------------------------------------------------------------------
# 叙述时长估算 / Narration duration estimation
# ---------------------------------------------------------------------------
# 这些是"点火"用的经验值，用于在真正合成配音之前规划章节预算。真实时长永远
# 以 voice.get_audio_duration 读到的音频为准（见 04 号计划）。
#
# 需要按频道实际使用的语音重新标定：pt-BR-ThalitaMultilingualNeural 在
# rate=1.0 下的语速与这里的默认值可能不同。标定方法见本文件末尾的说明。
#
# Seed values used only to budget chapters before any TTS runs. The real duration
# always comes from the rendered audio.
DEFAULT_WORDS_PER_MINUTE = 150.0

# 中日韩文本没有空格分词，用字符数估算比词数更可靠。
# CJK text is not space-delimited, so characters estimate better than words.
DEFAULT_CJK_CHARS_PER_MINUTE = 240.0

_CJK_PATTERN = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]"
)

# 一个章节大致对应的目标时长，用来在用户没有指定章节数时推算数量。
# Target seconds per chapter, used when the caller does not pin chapter_count.
DEFAULT_SECONDS_PER_CHAPTER = 150.0

# generate_script 的段落上限是 10；按这个秒数换算段落数可以保证永远不越界。
# Seconds per script paragraph, chosen so the derived count stays within the
# existing 1..10 clamp of llm.generate_script.
SECONDS_PER_SCRIPT_PARAGRAPH = 45.0

# 长视频模式下未显式指定时的目标时长。
DEFAULT_TARGET_DURATION_MINUTES = 10.0


def is_long_mode(params: VideoParams) -> bool:
    """判断当前任务是否运行在长视频模式。"""
    return getattr(params, "video_mode", const.VIDEO_MODE_SHORT) == (
        const.VIDEO_MODE_LONG
    )


def estimate_narration_seconds(text: str, language: str = "") -> float:
    """
    估算一段文案朗读所需的秒数。

    仅用于在生成配音之前做预算规划，不能替代真实音频时长。对中日韩文本按
    字符计，对其它语言按空白分词计；混合文本取两者中较大的估算值，避免把
    含大量中文的葡语文案严重低估。
    """
    content = (text or "").strip()
    if not content:
        return 0.0

    cjk_chars = len(_CJK_PATTERN.findall(content))
    word_count = len([token for token in content.split() if token])

    seconds_from_words = (word_count / DEFAULT_WORDS_PER_MINUTE) * 60.0
    seconds_from_cjk = (cjk_chars / DEFAULT_CJK_CHARS_PER_MINUTE) * 60.0

    # 纯拉丁文本的 cjk_chars 为 0，纯中文文本的 word_count 往往为 1（整段没有
    # 空格）。取最大值让两种情况都落在合理区间，混合文本则不会被低估。
    return max(seconds_from_words, seconds_from_cjk)


def paragraph_number_for_seconds(target_seconds: float) -> int:
    """
    把章节目标时长换算成 llm.generate_script 可接受的段落数。

    结果始终落在 [MIN_SCRIPT_PARAGRAPH_NUMBER, MAX_SCRIPT_PARAGRAPH_NUMBER]
    区间内——这正是长视频绕开段落上限的方式：不抬高上限，而是多次调用。
    """
    # 延迟导入：llm 模块会加载各家 SDK，模型定义阶段不需要付出这个开销。
    from app.services.llm import (
        MAX_SCRIPT_PARAGRAPH_NUMBER,
        MIN_SCRIPT_PARAGRAPH_NUMBER,
    )

    if target_seconds <= 0:
        return MIN_SCRIPT_PARAGRAPH_NUMBER

    raw = round(target_seconds / SECONDS_PER_SCRIPT_PARAGRAPH)
    return max(MIN_SCRIPT_PARAGRAPH_NUMBER, min(int(raw), MAX_SCRIPT_PARAGRAPH_NUMBER))


def suggest_chapter_count(target_seconds: float) -> int:
    """在用户未指定时推算章节数量，并夹在安全区间内。"""
    if target_seconds <= 0:
        return const.LONG_VIDEO_MIN_CHAPTERS

    raw = round(target_seconds / DEFAULT_SECONDS_PER_CHAPTER)
    return max(
        const.LONG_VIDEO_MIN_CHAPTERS,
        min(int(raw), const.LONG_VIDEO_MAX_CHAPTERS),
    )


def resolve_target_seconds(params: VideoParams) -> float:
    """
    读取长视频的目标时长（秒），并强制执行上下限。

    上限在这里就要生效：预检阶段拦截可以避免先消耗 LLM、TTS 和素材配额，
    最后才在渲染阶段失败。
    """
    minutes = params.target_duration_minutes
    if minutes is None:
        minutes = DEFAULT_TARGET_DURATION_MINUTES

    seconds = float(minutes) * 60.0
    if seconds > const.LONG_VIDEO_MAX_DURATION_SECONDS:
        raise ValueError(
            f"target duration {seconds:.0f}s exceeds the long-video cap of "
            f"{const.LONG_VIDEO_MAX_DURATION_SECONDS}s "
            f"({const.LONG_VIDEO_MAX_DURATION_SECONDS // 60} minutes)"
        )
    if seconds < const.LONG_VIDEO_MIN_DURATION_SECONDS:
        raise ValueError(
            f"target duration {seconds:.0f}s is below the long-video minimum of "
            f"{const.LONG_VIDEO_MIN_DURATION_SECONDS}s; use the regular pipeline"
        )
    return seconds


def _default_thread_count() -> int:
    """长视频渲染是 CPU 密集型任务，默认线程数比短视频更激进。"""
    cpu_count = os.cpu_count() or 2
    return max(4, cpu_count // 2)


# 长视频模式下的差异化默认值。键是字段名，值是"调用方没有显式指定时"要写入
# 的值。这里刻意不改动 VideoParams 的字段默认值——短视频流水线必须保持不变。
_LONG_MODE_DEFAULTS = {
    "video_aspect": VideoAspect.landscape.value,
    "video_concat_mode": VideoConcatMode.sequential.value,
    "video_transition_mode": VideoTransitionMode.fade_in.value,
    "match_materials_to_script": True,
    "video_clip_duration": 10,
    "video_count": 1,
    "subtitle_enabled": True,
    "bgm_volume": 0.15,
    "normalize_loudness": True,
}


def apply_long_video_defaults(params: VideoParams) -> VideoParams:
    """
    为长视频任务补齐差异化默认值，返回新的 VideoParams。

    只填充调用方**没有显式设置**的字段：Pydantic v2 的 ``model_fields_set``
    精确记录了哪些字段来自输入。因此 CLI 只传 ``--long --duration 12`` 时会
    得到 16:9、顺序拼接等长视频默认值，而显式传入 ``--aspect 9:16`` 的调用
    方不会被覆盖。

    非长视频任务原样返回，不做任何改动。

    Only fills fields the caller did not set explicitly, using Pydantic v2's
    ``model_fields_set``. Non-long tasks are returned untouched.
    """
    if not is_long_mode(params):
        return params

    explicit = set(params.model_fields_set)
    updates = {
        field_name: value
        for field_name, value in _LONG_MODE_DEFAULTS.items()
        if field_name not in explicit
    }

    if "n_threads" not in explicit:
        updates["n_threads"] = _default_thread_count()

    if "target_duration_minutes" not in explicit or (
        params.target_duration_minutes is None
    ):
        # 大纲已经固定时，时长由大纲决定，不需要再补默认目标时长。
        if not params.chapter_outline:
            updates["target_duration_minutes"] = DEFAULT_TARGET_DURATION_MINUTES

    if not updates:
        return params

    logger.debug(f"applying long-video defaults: {sorted(updates)}")
    # model_copy(update=...) 不会重跑校验器；长视频的组合约束已经在构造
    # VideoParams 时校验过，这里只是补默认值，不引入新的非法组合。
    return params.model_copy(update=updates)


# ---------------------------------------------------------------------------
# 章节数据结构 / Chapter data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chapter:
    """长视频大纲中的一章，含规划期与生成期的全部信息。"""

    index: int  # 从 1 开始，与用户界面显示一致
    title: str
    brief: str
    target_seconds: float
    script: str = ""
    terms: tuple[str, ...] = ()
    # 该章文案在完整文案中的字符区间，供字幕逐章校正和界面定位使用。
    char_start: int = 0
    char_end: int = 0

    @property
    def estimated_seconds(self) -> float:
        return estimate_narration_seconds(self.script)


@dataclass
class LongVideoPlan:
    """一次长视频任务的完整规划。"""

    subject: str
    language: str
    target_seconds: float
    chapters: List[Chapter] = field(default_factory=list)

    @property
    def full_script(self) -> str:
        """按章节顺序拼接的完整文案。"""
        parts = [chapter.script.strip() for chapter in self.chapters]
        return "\n\n".join(part for part in parts if part)

    @property
    def estimated_seconds(self) -> float:
        """基于已生成文案的估算总时长；未生成时回落到目标时长之和。"""
        if any(chapter.script.strip() for chapter in self.chapters):
            return estimate_narration_seconds(self.full_script)
        return sum(chapter.target_seconds for chapter in self.chapters)

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    def collect_terms(self) -> List[str]:
        """
        按叙事顺序汇总各章关键词并去重。

        顺序必须保留：长视频的素材是按章节时间线分配的，打乱顺序会让第 28
        分钟的画面出现在第 2 分钟。这也是长视频模式不使用 TwelveLabs 语义
        重排的原因——重排会破坏时间顺序。
        """
        seen: set[str] = set()
        ordered: List[str] = []
        for chapter in self.chapters:
            for term in chapter.terms:
                normalized = (term or "").strip()
                if not normalized:
                    continue
                key = normalized.casefold()
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(normalized)
        return ordered


def normalize_chapter_weights(
    weights: List[Optional[float]],
) -> List[float]:
    """
    把大纲返回的权重归一化成和为 1 的比例。

    模型经常给出不合法的权重：缺字段、给 0、给负数，或者一组加起来不等于 1
    的数字。这些都不应该让整个任务失败——权重只是时长分配的建议，退化成均分
    仍然能产出可用的视频。
    """
    count = len(weights)
    if count == 0:
        return []

    usable = [
        float(weight)
        if isinstance(weight, (int, float)) and weight is not None and weight > 0
        else 0.0
        for weight in weights
    ]
    total = sum(usable)
    if total <= 0:
        logger.warning("outline returned no usable chapter weights; using even split")
        return [1.0 / count] * count

    normalized = [weight / total for weight in usable]

    # 个别章节权重为 0 时，它会分不到任何时长。给它一个最小份额，再重新归一化，
    # 避免大纲里出现"存在但时长为零"的空章节。
    minimum_share = 0.25 / count
    if any(weight < minimum_share for weight in normalized):
        normalized = [max(weight, minimum_share) for weight in normalized]
        total = sum(normalized)
        normalized = [weight / total for weight in normalized]

    return normalized
