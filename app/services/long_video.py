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


# ---------------------------------------------------------------------------
# 提示词 / Prompts
# ---------------------------------------------------------------------------

# 长视频专用的 system prompt。刻意不复用 llm.DEFAULT_SCRIPT_SYSTEM_PROMPT：
# 那一份是为 30 秒短视频调优的，节奏、句长和开场方式都不适合纪录片式长视频。
# 短视频流水线继续使用原来的那份，本文件的改动不会影响它。
LONG_FORM_SYSTEM_PROMPT = """
# Role: Long-form Documentary Narration Writer

## Goals:
Write one chapter of a longer narrated documentary-style video.

## Constrains:
1. the script is to be returned as a string with the specified number of paragraphs.
2. do not under any circumstance reference this prompt in your response.
3. you must not include any type of markdown or formatting in the script, never use a title.
4. only return the raw content of the script.
5. do not include "voiceover", "narrator" or similar indicators at the beginning of any line.
6. never mention chapters, sections, paragraph counts, or the structure of the video itself.
7. respond in the same language as the video subject.
8. write in a measured documentary register: full sentences, concrete detail, no hype,
   no exclamation marks, no rhetorical questions stacked one after another.
9. do not use parentheses or square brackets; they are stripped from the output and
   would take your words with them.
10. do not open with meta-narration such as "in this video" or "today we will look at".
11. this chapter continues a video already in progress unless it is chapter 1:
    never re-introduce the subject, and never summarise what was already said.
12. end the chapter with a sentence that pulls the viewer toward what comes next,
    without announcing it as a preview.
""".strip()

_OUTLINE_PROMPT_TEMPLATE = """
# Role: Long-form Video Outline Planner

## Goals:
Break the subject into {chapter_count} sequential chapters that together form a single
{minutes:.0f}-minute narrated documentary-style video.

## Constrains:
1. return ONLY a json array. no prose, no markdown fence, no trailing commentary.
2. each item must be an object with exactly these keys: "title", "brief", "weight".
3. "title" is a short chapter name. "brief" describes what this chapter must cover,
   in one or two sentences. "weight" is this chapter's share of the total runtime.
4. all weights must be positive numbers that sum to 1.0.
5. chapters must be sequential and non-overlapping: chapter N+1 continues where N stopped.
6. chapter 1 must hook the viewer within its first sentences.
7. the final chapter must deliver a payoff or conclusion, not a recap of the others.
8. write "title" and "brief" in this language: {language}

## Output Example:
[{{"title": "...", "brief": "...", "weight": 0.2}}, {{"title": "...", "brief": "...", "weight": 0.8}}]

## Context:
### Video Subject
{subject}

### Total runtime
{minutes:.0f} minutes
""".strip()


def build_outline_prompt(
    subject: str,
    chapter_count: int,
    target_seconds: float,
    language: str = "",
) -> str:
    """构造大纲提示词。独立成函数，便于测试和 WebUI 预览。"""
    return _OUTLINE_PROMPT_TEMPLATE.format(
        subject=subject,
        chapter_count=chapter_count,
        minutes=target_seconds / 60.0,
        language=language or "the same language as the subject",
    )


def parse_outline_response(response: str) -> List[dict]:
    """
    解析大纲响应，尽最大努力从不规范的输出中恢复。

    模型经常在 JSON 外面套 markdown 代码块，或者在数组前后附带解释文字。
    这里先剥代码块，失败再用正则截取第一个数组——与 llm.generate_terms 的
    恢复策略保持一致，避免两处对同一类问题有不同的容错行为。
    """
    import json

    from app.services.llm import _strip_code_fence

    text = (response or "").strip()
    if not text:
        raise ValueError("outline response is empty")
    if text.startswith("Error: "):
        # _generate_response 用这个前缀表达 Provider 故障。必须当成异常，
        # 否则下游会把错误文案当作合法大纲继续处理。
        raise ValueError(text)

    payload = None
    try:
        payload = json.loads(_strip_code_fence(text))
    except Exception:
        match = re.search(r"\[.*]", text, re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group())
            except Exception as exc:
                raise ValueError(f"outline response is not valid json: {exc}") from exc

    if payload is None:
        raise ValueError("outline response does not contain a json array")
    if not isinstance(payload, list) or not payload:
        raise ValueError("outline response is not a non-empty json array")

    items: List[dict] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            # 没有标题的条目无法在界面上引用，也无法用于章节提示词，直接丢弃。
            continue
        items.append(
            {
                "title": title[:200],
                "brief": str(entry.get("brief") or "").strip()[:1000],
                "weight": entry.get("weight"),
            }
        )

    if not items:
        raise ValueError("outline response has no usable chapters")
    return items


def plan_chapters(
    subject: str,
    target_seconds: float,
    language: str = "",
    chapter_count: Optional[int] = None,
    outline: Optional[List] = None,
    app_config=None,
) -> LongVideoPlan:
    """
    生成（或采纳）章节大纲，返回只有规划信息、尚未生成文案的计划。

    传入 ``outline`` 时跳过 LLM 调用：WebUI 的"生成结构"预览允许用户编辑
    标题和权重后再渲染，这条路径必须不再产生额外的模型开销。
    """
    from app.services import llm

    if outline:
        raw_items = [
            {
                "title": getattr(item, "title", None) or item.get("title", ""),
                "brief": getattr(item, "brief", None) or item.get("brief", "") or "",
                "weight": getattr(item, "weight", None)
                if not isinstance(item, dict)
                else item.get("weight"),
            }
            for item in outline
        ]
        raw_items = [item for item in raw_items if str(item["title"]).strip()]
        if not raw_items:
            raise ValueError("provided chapter outline has no usable chapters")
    else:
        count = chapter_count or suggest_chapter_count(target_seconds)
        prompt = build_outline_prompt(
            subject=subject,
            chapter_count=count,
            target_seconds=target_seconds,
            language=language,
        )
        raw_items = []
        last_error: Optional[Exception] = None
        for attempt in range(llm._max_retries):
            try:
                if app_config is None:
                    response = llm._generate_response(prompt)
                else:
                    response = llm._generate_response(prompt, app_config=app_config)
                raw_items = parse_outline_response(response)
                break
            except Exception as exc:
                last_error = exc
                logger.warning(
                    f"failed to generate long-video outline "
                    f"(attempt {attempt + 1}/{llm._max_retries}): {exc}"
                )
        if not raw_items:
            raise ValueError(
                f"failed to generate long-video outline: {last_error or 'unknown error'}"
            )

    weights = normalize_chapter_weights([item.get("weight") for item in raw_items])
    chapters = [
        Chapter(
            index=position + 1,
            title=str(item["title"]).strip(),
            brief=str(item.get("brief") or "").strip(),
            target_seconds=weights[position] * target_seconds,
        )
        for position, item in enumerate(raw_items)
    ]

    logger.info(
        f"long-video outline ready: {len(chapters)} chapters, "
        f"target {target_seconds:.0f}s"
    )
    return LongVideoPlan(
        subject=subject,
        language=language,
        target_seconds=target_seconds,
        chapters=chapters,
    )


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def tail_sentences(text: str, count: int = 2) -> str:
    """
    取文本末尾的若干句，用于章节之间的衔接。

    只传"上一章的最后两句"而不是整章内容，是因为 video_script_prompt 有
    2000 字符上限（llm._limit_script_text）。塞入整章会挤掉大纲和衔接指令，
    反而让模型失去结构感。
    """
    content = (text or "").strip()
    if not content:
        return ""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(content) if s.strip()]
    if not sentences:
        return content[-300:]
    return " ".join(sentences[-count:])[-600:]


def build_chapter_requirements(
    plan: LongVideoPlan,
    chapter: Chapter,
    previous_tail: str = "",
) -> str:
    """
    构造单章的 additional requirements 文本。

    连贯性靠三件事保证：完整大纲（让模型知道自己在整体中的位置）、当前章节
    的职责，以及上一章的结尾（让衔接自然）。三者都必须压缩到 2000 字符内。
    """
    outline_lines = "\n".join(
        f"{item.index}. {item.title}" for item in plan.chapters
    )
    parts = [
        f"This is chapter {chapter.index} of {plan.chapter_count}.",
        f"Full outline:\n{outline_lines}",
        f"This chapter must cover: {chapter.brief}" if chapter.brief else "",
        f"Approximate spoken length: {chapter.target_seconds:.0f} seconds.",
    ]
    if chapter.index > 1 and previous_tail:
        parts.append(
            "The previous chapter ended with these words, continue naturally from "
            f"them without repeating them: {previous_tail}"
        )
    if chapter.index == 1:
        parts.append("Open with a concrete hook. Do not introduce yourself.")
    else:
        parts.append(
            "The viewer has already watched the previous chapters. Continue directly; "
            "do not re-introduce the subject and do not summarise what came before."
        )
    if chapter.index == plan.chapter_count:
        parts.append("This is the final chapter: deliver the payoff and close.")

    requirements = "\n\n".join(part for part in parts if part)
    # llm._limit_script_text 会截断超长内容并打日志。这里先行截断，避免每章
    # 都产生一条噪声警告，同时保证被裁掉的一定是末尾的衔接提示而不是大纲。
    from app.services.llm import MAX_SCRIPT_PROMPT_LENGTH

    if len(requirements) > MAX_SCRIPT_PROMPT_LENGTH:
        requirements = requirements[:MAX_SCRIPT_PROMPT_LENGTH]
    return requirements


def generate_chapter_script(
    plan: LongVideoPlan,
    chapter: Chapter,
    previous_tail: str = "",
    app_config=None,
) -> str:
    """
    生成单章文案，复用未经修改的 llm.generate_script。

    段落数由 paragraph_number_for_seconds 换算，始终落在 1..10 内——长视频
    正是通过"多次调用"而不是"抬高上限"来突破单次生成的长度限制。
    """
    from app.services import llm

    paragraph_number = paragraph_number_for_seconds(chapter.target_seconds)
    subject = f"{plan.subject} — {chapter.title}" if chapter.title else plan.subject

    script = llm.generate_script(
        video_subject=subject,
        language=plan.language,
        paragraph_number=paragraph_number,
        video_script_prompt=build_chapter_requirements(plan, chapter, previous_tail),
        custom_system_prompt=LONG_FORM_SYSTEM_PROMPT,
        app_config=app_config,
    )
    script = (script or "").strip()
    if not script or script.startswith("Error: "):
        raise ValueError(
            f"failed to generate script for chapter {chapter.index} "
            f"({chapter.title}): {script or 'empty response'}"
        )
    return script


def truncate_plan_to_budget(
    plan: LongVideoPlan,
    max_seconds: float = const.LONG_VIDEO_MAX_DURATION_SECONDS,
) -> tuple[LongVideoPlan, int]:
    """
    丢弃超出时长预算的末尾章节，返回 (新计划, 被丢弃的章节数)。

    只按整章丢弃：在句子中间截断会留下一个话说到一半的结尾，比视频短一些
    糟糕得多。首章始终保留——即便它单独就超预算，也应该在别处以明确的错误
    暴露出来，而不是在这里产出一个空计划。
    """
    kept: List[Chapter] = []
    used = 0.0
    for chapter in plan.chapters:
        chapter_seconds = (
            chapter.estimated_seconds
            if chapter.script.strip()
            else chapter.target_seconds
        )
        if kept and used + chapter_seconds > max_seconds:
            break
        kept.append(chapter)
        used += chapter_seconds

    dropped = len(plan.chapters) - len(kept)
    if dropped:
        logger.warning(
            f"long-video plan exceeds the {max_seconds:.0f}s cap; "
            f"dropping the last {dropped} chapter(s)"
        )
        plan = LongVideoPlan(
            subject=plan.subject,
            language=plan.language,
            target_seconds=plan.target_seconds,
            chapters=kept,
        )
    return plan, dropped


def build_long_script(
    params: VideoParams,
    progress_cb=None,
    app_config=None,
) -> LongVideoPlan:
    """
    完整的长视频文案生成流程：大纲 → 逐章文案 → 缝合 → 预算裁剪。

    ``progress_cb`` 接收 0..1 的完成度，供任务编排层映射到全局进度条。长视频
    的文案生成可能持续数分钟，没有进度上报的话界面会长时间静默。
    """
    target_seconds = resolve_target_seconds(params)
    plan = plan_chapters(
        subject=params.video_subject,
        target_seconds=target_seconds,
        language=params.video_language or "",
        chapter_count=params.chapter_count,
        outline=params.chapter_outline,
        app_config=app_config,
    )

    if progress_cb:
        progress_cb(0.0)

    written: List[Chapter] = []
    previous_tail = ""
    cursor = 0
    total = len(plan.chapters)
    for position, chapter in enumerate(plan.chapters):
        script = generate_chapter_script(
            plan, chapter, previous_tail=previous_tail, app_config=app_config
        )
        estimated = estimate_narration_seconds(script)
        drift = estimated - chapter.target_seconds
        logger.info(
            f"chapter {chapter.index}/{total} '{chapter.title}': "
            f"{estimated:.0f}s vs {chapter.target_seconds:.0f}s target "
            f"({drift:+.0f}s)"
        )

        # 记录每章在完整文案中的字符区间。字幕逐章校正和界面定位都依赖它，
        # 缝合规则（\n\n 分隔）必须与 full_script 保持一致。
        char_start = cursor
        char_end = char_start + len(script)
        cursor = char_end + 2  # 与 "\n\n" 分隔符对齐

        written.append(
            Chapter(
                index=chapter.index,
                title=chapter.title,
                brief=chapter.brief,
                target_seconds=chapter.target_seconds,
                script=script,
                terms=chapter.terms,
                char_start=char_start,
                char_end=char_end,
            )
        )
        previous_tail = tail_sentences(script)
        if progress_cb:
            progress_cb((position + 1) / total)

    plan.chapters = written
    plan, _dropped = truncate_plan_to_budget(plan)
    return plan


def generate_chapter_terms(
    plan: LongVideoPlan,
    app_config=None,
) -> LongVideoPlan:
    """
    为每一章生成按叙事顺序排列的素材关键词。

    与短视频"整篇一组关键词"的做法不同：35 分钟的视频里，第 2 分钟的关键词
    无法描述第 28 分钟的画面。关键词按章生成、按章使用，素材才能跟着叙述走。

    这里同样不调用 twelvelabs 的语义重排——它按主题相关度重排，会破坏时间
    顺序，正如 task.py 在 match_materials_to_script 打开时也会跳过它。
    """
    from app.services import llm

    updated: List[Chapter] = []
    for chapter in plan.chapters:
        amount = max(3, round(chapter.target_seconds / 30))
        try:
            terms = llm.generate_terms(
                video_subject=f"{plan.subject} — {chapter.title}",
                video_script=chapter.script,
                amount=amount,
                match_script_order=True,
                app_config=app_config,
            )
        except Exception as exc:
            logger.warning(
                f"failed to generate terms for chapter {chapter.index}: {exc}"
            )
            terms = []

        if not terms:
            # 单章没有关键词不应该让整个任务失败：素材分配阶段会用相邻章节的
            # 关键词兜底，画面稍差但视频仍然成立。
            logger.warning(
                f"chapter {chapter.index} '{chapter.title}' produced no search terms"
            )

        updated.append(
            Chapter(
                index=chapter.index,
                title=chapter.title,
                brief=chapter.brief,
                target_seconds=chapter.target_seconds,
                script=chapter.script,
                terms=tuple(terms or ()),
                char_start=chapter.char_start,
                char_end=chapter.char_end,
            )
        )

    plan.chapters = updated
    return plan


def plan_to_dict(plan: LongVideoPlan, estimated_seconds: Optional[float] = None) -> dict:
    """
    序列化计划，写入任务的 script.json。

    这是"重开界面、重新生成某一章、在变更日志里描述视频结构"的数据基础。
    """
    return {
        "subject": plan.subject,
        "language": plan.language,
        "target_seconds": plan.target_seconds,
        "estimated_seconds": (
            estimated_seconds if estimated_seconds is not None else plan.estimated_seconds
        ),
        "chapters": [
            {
                "index": chapter.index,
                "title": chapter.title,
                "brief": chapter.brief,
                "target_seconds": chapter.target_seconds,
                "char_start": chapter.char_start,
                "char_end": chapter.char_end,
                "terms": list(chapter.terms),
            }
            for chapter in plan.chapters
        ],
    }
