"""
长视频的配音与字幕 / Long-form narration and subtitles.

短视频把整篇文案一次性交给 TTS。35 分钟的文案（约 3 万字符）这样做会超时、
撞上供应商的单次长度上限，而且一旦在末尾失败，前面几十分钟的合成全部作废。

这里按**章节**切分：每章一次 TTS，各自重试，最后用 FFmpeg 拼接，并把每章的
字幕时间轴按累计偏移平移后合并成一份 SRT。

字幕偏移是整条链路里最容易出错、也最难发现的地方——偏移错误只在视频后半段
显现出来，前 30 秒看起来完全正常。因此偏移必须来自**实际测得的音频时长**，
而不是 sub_maker 报告的时长或估算值。

Chapter-sized TTS blocks, concatenated with FFmpeg, with subtitle timelines
shifted by the accumulated *measured* audio duration of the preceding chapters.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from loguru import logger

from app.models import const
from app.utils import utils

# SRT 时间戳：HH:MM:SS,mmm（逗号，不是点）。与 subtitle.file_to_subtitles
# 使用的识别规则保持一致。
_SRT_TIMESTAMP_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_SRT_ARROW = "-->"

# 单章配音的重试次数。重试的粒度是章节，不是整篇文案——这正是分块合成的
# 主要收益之一。
CHAPTER_TTS_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ChapterAudio:
    """单章配音的产物与它在完整音轨中的位置。"""

    index: int
    audio_file: str
    duration: float
    offset: float  # 该章在最终音轨中的起始秒数
    subtitle_file: str = ""
    script: str = ""


def parse_srt_timestamp(value: str) -> float:
    """把 SRT 时间戳解析成秒。支持超过一小时的时间码。"""
    match = _SRT_TIMESTAMP_RE.search(value or "")
    if not match:
        raise ValueError(f"invalid srt timestamp: {value!r}")
    hours, minutes, seconds, millis = match.groups()
    # 毫秒位数可能少于 3（部分工具写成 ",5"），按右侧补零处理。
    millis = millis.ljust(3, "0")
    return (
        int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0
    )


def format_srt_timestamp(seconds: float) -> str:
    """
    把秒格式化成 SRT 时间戳。

    小时位必须支持进位：35 分钟的视频本身不会跨小时，但格式化函数不应该在
    边界上出错——用毫秒整数运算避免浮点累积误差。
    """
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _read_srt_blocks(subtitle_file: str) -> List[tuple[float, float, str]]:
    """
    读取 SRT，返回 (开始秒, 结束秒, 文本) 列表。

    自行解析而不是复用 subtitle.file_to_subtitles，是因为后者返回的是原始
    时间戳字符串；这里需要数值才能做偏移运算。
    """
    if not subtitle_file or not os.path.isfile(subtitle_file):
        return []

    with open(subtitle_file, "r", encoding="utf-8") as handle:
        content = handle.read()

    blocks: List[tuple[float, float, str]] = []
    # SRT 以空行分块。用 \n\s*\n 兼容 CRLF 和块间多余空白。
    for raw_block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        lines = [line for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        timing_line = None
        timing_position = 0
        for position, line in enumerate(lines):
            if _SRT_ARROW in line:
                timing_line = line
                timing_position = position
                break
        if timing_line is None:
            continue
        try:
            start_text, end_text = timing_line.split(_SRT_ARROW, 1)
            start = parse_srt_timestamp(start_text)
            end = parse_srt_timestamp(end_text)
        except ValueError as exc:
            logger.warning(f"skipping malformed subtitle block: {exc}")
            continue
        text = "\n".join(lines[timing_position + 1 :]).strip()
        if not text:
            continue
        blocks.append((start, end, text))
    return blocks


def merge_srt_with_offsets(
    parts: Sequence[tuple[str, float]],
    output_file: str,
) -> str:
    """
    合并多份 SRT，按各自偏移平移时间轴，并重新连续编号。

    ``parts`` 是 (字幕文件路径, 偏移秒数) 的序列。偏移必须来自实际音频时长，
    否则误差会随章节累积——第 3 章偏差 0.2 秒不易察觉，第 12 章就会明显错位。

    返回写入的文件路径；没有任何有效字幕块时返回空字符串，让调用方按"无字幕"
    处理，而不是产出一个空文件让下游误以为字幕已就绪。
    """
    merged: List[tuple[float, float, str]] = []
    for subtitle_file, offset in parts:
        for start, end, text in _read_srt_blocks(subtitle_file):
            merged.append((start + offset, end + offset, text))

    if not merged:
        logger.warning("no subtitle blocks to merge; skipping subtitle output")
        return ""

    # 各章内部本来就是有序的，但章节之间的偏移可能因为四舍五入产生极小的
    # 交叠。统一排序保证输出严格递增，播放器不会因为乱序丢弃字幕。
    merged.sort(key=lambda item: (item[0], item[1]))

    lines: List[str] = []
    for position, (start, end, text) in enumerate(merged, start=1):
        lines.append(str(position))
        lines.append(
            f"{format_srt_timestamp(start)} {_SRT_ARROW} {format_srt_timestamp(end)}"
        )
        lines.append(text)
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    logger.info(f"merged {len(merged)} subtitle blocks into {output_file}")
    return output_file


def build_audio_concat_command(
    concat_list_file: str,
    output_file: str,
    ffmpeg_binary: Optional[str] = None,
) -> List[str]:
    """
    构造音频拼接命令。

    🔴 **必须重新编码，不能用 `-c copy`。** Edge TTS 返回的 MP3 各段头部和
    padding 不一致，流复制会累积时间漂移——而音频漂移正是 35 分钟视频里字幕
    错位的根源。重编码的开销远小于一条不可用的成片。
    """
    return [
        ffmpeg_binary or utils.get_ffmpeg_binary(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list_file,
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        "-ar",
        "44100",
        output_file,
    ]


def concat_audio_files(
    audio_files: Sequence[str],
    output_file: str,
    ffmpeg_binary: Optional[str] = None,
) -> str:
    """把各章音频拼接成一条音轨。"""
    from app.services.video import _format_ffmpeg_concat_path

    usable = [f for f in audio_files if f and os.path.isfile(f)]
    if not usable:
        raise ValueError("no chapter audio files to concatenate")

    output_dir = os.path.dirname(os.path.abspath(output_file))
    os.makedirs(output_dir, exist_ok=True)
    concat_list_file = os.path.join(output_dir, "ffmpeg-audio-concat-list.txt")
    with open(concat_list_file, "w", encoding="utf-8") as handle:
        for audio_file in usable:
            handle.write(f"file '{_format_ffmpeg_concat_path(audio_file)}'\n")

    try:
        command = build_audio_concat_command(
            concat_list_file, output_file, ffmpeg_binary
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(message or "ffmpeg audio concat failed")
    finally:
        try:
            os.remove(concat_list_file)
        except OSError:
            pass

    return output_file


def build_loudnorm_command(
    input_file: str,
    output_file: str,
    target_lufs: float = -14.0,
    true_peak: float = -1.5,
    loudness_range: float = 11.0,
    ffmpeg_binary: Optional[str] = None,
) -> List[str]:
    """
    构造响度归一化命令。

    视频流直接复制（``-c:v copy``），只重编码音频：35 分钟的成片重新编码视频
    要付出一次完整的编码开销，而响度处理并不需要动画面。``+faststart`` 一并
    应用——频道的发布检查要求它，历史上曾因为缺少它而发布过有问题的视频。
    """
    return [
        ffmpeg_binary or utils.get_ffmpeg_binary(),
        "-y",
        "-i",
        input_file,
        "-af",
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={loudness_range}",
        "-c:v",
        "copy",
        "-movflags",
        "+faststart",
        output_file,
    ]


def normalize_loudness(
    input_file: str,
    output_file: str,
    ffmpeg_binary: Optional[str] = None,
    **kwargs,
) -> str:
    """
    归一化成片响度并写入 faststart。

    失败时返回原文件而不是抛异常：响度不达标的视频仍然可用，为此丢弃一次
    数十分钟的渲染是不划算的。调用方应据此发出警告。
    """
    command = build_loudnorm_command(
        input_file, output_file, ffmpeg_binary=ffmpeg_binary, **kwargs
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        logger.warning(f"loudness normalization failed, keeping original: {message}")
        return input_file
    return output_file


def chapters_within_budget(
    chapter_audios: Sequence[ChapterAudio],
    max_seconds: float = const.LONG_VIDEO_MAX_DURATION_SECONDS,
) -> tuple[List[ChapterAudio], List[ChapterAudio]]:
    """
    按整章切分预算，返回 (保留, 丢弃)。

    绝不在句子中间截断：一个说到一半就停下的结尾比一个更短的视频糟糕得多。
    首章始终保留——即使它单独超预算，也应该由调用方以明确的错误暴露出来。
    """
    kept: List[ChapterAudio] = []
    dropped: List[ChapterAudio] = []
    used = 0.0
    for chapter_audio in chapter_audios:
        if kept and used + chapter_audio.duration > max_seconds:
            dropped.append(chapter_audio)
            continue
        if dropped:
            # 一旦开始丢弃就不再回头收纳后面的短章节：保持叙事连续性比
            # 塞满时长预算更重要。
            dropped.append(chapter_audio)
            continue
        kept.append(chapter_audio)
        used += chapter_audio.duration
    return kept, dropped


def synthesize_long_narration(
    task_id: str,
    params,
    plan,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> tuple[str, float, List[ChapterAudio], List[dict]]:
    """
    按章合成配音，拼接成一条音轨，并生成对齐的字幕。

    返回 ``(audio_file, audio_duration, chapter_audios, warnings)``。

    幂等：已经存在且时长有效的章节音频会被复用，因此中断后重新提交任务不必
    重新合成前面的章节——这也是"只重新生成第 N 章"能力的基础。
    """
    from app.services import voice

    task_dir = utils.task_dir(task_id)
    os.makedirs(task_dir, exist_ok=True)
    warnings: List[dict] = []

    voice_name = voice.parse_voice_name(params.voice_name)
    total = len(plan.chapters)
    chapter_audios: List[ChapterAudio] = []
    offset = 0.0

    for position, chapter in enumerate(plan.chapters):
        audio_file = os.path.join(task_dir, f"chapter-{chapter.index:03d}.mp3")
        duration = 0.0

        if os.path.isfile(audio_file):
            duration = voice.get_audio_duration(audio_file)
            if duration > 0:
                logger.info(
                    f"reusing existing narration for chapter {chapter.index} "
                    f"({duration:.1f}s)"
                )

        sub_maker = None
        if duration <= 0:
            # 逐章重试，而不是让整条流水线重来：一次网络抖动不应该作废前面
            # 几十分钟已经合成好的配音。
            last_error: Optional[Exception] = None
            for attempt in range(CHAPTER_TTS_MAX_ATTEMPTS):
                try:
                    sub_maker = voice.tts(
                        text=chapter.script,
                        voice_name=voice_name,
                        voice_rate=params.voice_rate,
                        voice_file=audio_file,
                    )
                    if sub_maker is None:
                        raise RuntimeError("tts returned no subtitle maker")
                    duration = voice.get_audio_duration(audio_file)
                    if duration <= 0:
                        raise RuntimeError("synthesized audio has zero duration")
                    break
                except Exception as exc:
                    last_error = exc
                    sub_maker = None
                    duration = 0.0
                    logger.warning(
                        f"chapter {chapter.index} narration attempt "
                        f"{attempt + 1}/{CHAPTER_TTS_MAX_ATTEMPTS} failed: {exc}"
                    )
                    # 半成品文件会让下一轮的幂等检查误判为"已存在"，必须清掉。
                    try:
                        if os.path.isfile(audio_file):
                            os.remove(audio_file)
                    except OSError:
                        pass

            if duration <= 0:
                raise RuntimeError(
                    f"failed to synthesize narration for chapter {chapter.index} "
                    f"({chapter.title}): {last_error or 'unknown error'}"
                )

        # 每章单独产出字幕，稍后按偏移合并。逐章生成让 Edge 的时间轴与该章
        # 文案严格对应，避免整篇匹配时的错行。
        subtitle_file = ""
        if params.subtitle_enabled and sub_maker is not None:
            candidate = os.path.join(task_dir, f"chapter-{chapter.index:03d}.srt")
            try:
                voice.create_subtitle(
                    text=chapter.script, sub_maker=sub_maker, subtitle_file=candidate
                )
                if os.path.isfile(candidate):
                    subtitle_file = candidate
            except Exception as exc:
                # 字幕失败不应作废已经合成好的配音。
                logger.warning(
                    f"failed to build subtitle for chapter {chapter.index}: {exc}"
                )

        chapter_audios.append(
            ChapterAudio(
                index=chapter.index,
                audio_file=audio_file,
                duration=duration,
                offset=offset,
                subtitle_file=subtitle_file,
                script=chapter.script,
            )
        )
        offset += duration
        if progress_cb:
            progress_cb((position + 1) / total)

    # 真实时长是最终的裁决依据：估算只用于规划，这里才是上限真正生效的地方。
    kept, dropped = chapters_within_budget(chapter_audios)
    if not kept:
        raise RuntimeError(
            "the first chapter alone exceeds the long-video duration cap"
        )
    if dropped:
        warnings.append(
            {
                "code": const.WARNING_LONG_VIDEO_TRUNCATED,
                "message": (
                    f"video truncated to the "
                    f"{const.LONG_VIDEO_MAX_DURATION_SECONDS // 60}-minute cap; "
                    f"{len(dropped)} chapter(s) dropped"
                ),
            }
        )
        logger.warning(
            f"dropping {len(dropped)} chapter(s) to fit the duration cap"
        )

    audio_file = os.path.join(task_dir, "audio.mp3")
    concat_audio_files([item.audio_file for item in kept], audio_file)
    audio_duration = voice.get_audio_duration(audio_file)
    if audio_duration <= 0:
        raise RuntimeError("concatenated narration has zero duration")

    logger.success(
        f"long narration ready: {len(kept)} chapters, {audio_duration:.1f}s"
    )
    return audio_file, audio_duration, kept, warnings


def build_long_subtitle(
    task_id: str,
    chapter_audios: Sequence[ChapterAudio],
) -> str:
    """把各章字幕按偏移合并成一份 SRT。"""
    parts = [
        (item.subtitle_file, item.offset)
        for item in chapter_audios
        if item.subtitle_file
    ]
    if not parts:
        return ""
    output_file = os.path.join(utils.task_dir(task_id), "subtitle.srt")
    return merge_srt_with_offsets(parts, output_file)
