"""
长视频渲染 / Long-form rendering.

短视频的 ``video.combine_videos`` 把所有片段用 MoviePy 打开、缩放、合成，再
串起来。35 分钟的成片有约 210 个片段，这条路径会同时出问题：内存里同时存在
大量 clip 对象、每个 letterbox 都要构造 CompositeVideoClip、多次重编码累积
画质损失，而且整个过程没有进度上报。

这里改成 **FFmpeg 优先**：每个片段用一条 FFmpeg 命令归一化后写回磁盘，随即
释放；最后用项目里已有的 ``video.concat_video_clips_with_ffmpeg``（concat
解复用器）一次串联。内存占用因此与片段数量无关。

🔴 拼接的前提是所有片段的分辨率、帧率、像素格式、SAR 完全一致，否则 concat
解复用器会失败或产出音画不同步的结果。这是"拼接成功但视频坏掉"的头号原因。

FFmpeg-first rendering: normalise each clip to identical parameters on disk,
then concatenate once. Memory stays flat regardless of clip count.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from loguru import logger

from app.models import const
from app.utils import utils

# 归一化后的统一参数。拼接要求逐项一致，因此集中定义，不允许调用方逐个覆盖。
NORMALIZED_FPS = 30
NORMALIZED_PIX_FMT = "yuv420p"

# 章节之间的转场时长。片段之间不加转场——210 次转场既昂贵又让人疲劳；只在
# 章节边界给一个短交叉淡入。
CHAPTER_TRANSITION_SECONDS = 0.5

# 验收允许的时长偏差。
DURATION_TOLERANCE_SECONDS = 2.0


class LongVideoVerificationError(RuntimeError):
    """成片未通过技术验收。"""


@dataclass
class VerificationReport:
    """成片技术验收结果，写入任务状态供上传流程判断。"""

    duration: float = 0.0
    width: int = 0
    height: int = 0
    video_codec: str = ""
    pix_fmt: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    faststart: bool = False
    decode_ok: bool = False
    within_cap: bool = False
    problems: List[str] = None

    def __post_init__(self):
        if self.problems is None:
            self.problems = []

    @property
    def passed(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {
            "duration_seconds": round(self.duration, 3),
            "resolution": f"{self.width}x{self.height}",
            "video_codec": self.video_codec,
            "pix_fmt": self.pix_fmt,
            "audio_codec": self.audio_codec,
            "sample_rate": self.sample_rate,
            "faststart": self.faststart,
            "decode_ok": self.decode_ok,
            "within_cap": self.within_cap,
            "passed": self.passed,
            "problems": list(self.problems),
        }


def get_ffprobe_binary() -> str:
    """
    解析 ffprobe 路径。

    项目只集中解析了 ffmpeg（utils.get_ffmpeg_binary）。ffprobe 通常与之同目录，
    因此先按同目录推断，再回落到 PATH——Windows 便携包和 Docker 镜像里两者
    总是一起分发。
    """
    ffmpeg_binary = utils.get_ffmpeg_binary()
    directory = os.path.dirname(ffmpeg_binary)
    if directory:
        candidate = os.path.join(
            directory, "ffprobe.exe" if os.name == "nt" else "ffprobe"
        )
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffprobe") or "ffprobe"


def build_scale_pad_filter(width: int, height: int, fps: int = NORMALIZED_FPS) -> str:
    """
    构造缩放 + letterbox + 帧率 + SAR 的滤镜串。

    与现有 MoviePy 路径的视觉结果一致（等比缩放后居中黑边填充），但只用一个
    FFmpeg 进程完成，内存恒定。``setsar=1`` 不能省：SAR 不一致会让 concat
    解复用器产出画面被拉伸的成片。
    """
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},setsar=1"
    )


def build_clip_normalize_command(
    input_file: str,
    output_file: str,
    width: int,
    height: int,
    fps: int = NORMALIZED_FPS,
    clip_speed: float = 1.0,
    start: Optional[float] = None,
    duration: Optional[float] = None,
    ffmpeg_binary: Optional[str] = None,
) -> List[str]:
    """构造单个片段的归一化命令。"""
    video_filter = build_scale_pad_filter(width, height, fps)
    if clip_speed and clip_speed != 1.0:
        # setpts 在缩放前应用：先改变时间轴，再统一帧率，避免变速后帧率漂移。
        video_filter = f"setpts={1.0 / clip_speed:.6f}*PTS," + video_filter

    command = [ffmpeg_binary or utils.get_ffmpeg_binary(), "-y"]
    if start is not None:
        command += ["-ss", f"{start:.3f}"]
    command += ["-i", input_file]
    if duration is not None:
        command += ["-t", f"{duration:.3f}"]
    command += [
        "-an",  # 素材自带音轨一律丢弃：旁白是唯一音源
        "-vf",
        video_filter,
        "-pix_fmt",
        NORMALIZED_PIX_FMT,
        "-r",
        str(fps),
        output_file,
    ]
    return command


def normalize_clip(
    input_file: str,
    output_file: str,
    width: int,
    height: int,
    **kwargs,
) -> str:
    """归一化单个片段；失败时抛出，由调用方决定是否跳过该片段。"""
    command = build_clip_normalize_command(
        input_file, output_file, width, height, **kwargs
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed to normalize clip {input_file}: {message}")
    return output_file


def plan_clip_timeline(
    material_paths: Sequence[str],
    total_seconds: float,
    clip_duration: int,
    max_source_share: float = 0.03,
) -> List[dict]:
    """
    规划时间线：决定每个素材出场的次数与时长。

    ``max_source_share`` 限制单个素材占成片的比例。素材不足时必须复用，但
    没有上限的复用会让观众直接看出循环；超出上限只在别无选择时才发生。
    """
    usable = [path for path in material_paths if path]
    if not usable or total_seconds <= 0 or clip_duration <= 0:
        return []

    needed_slots = max(1, int(total_seconds // clip_duration) + 1)
    max_uses = max(1, int(needed_slots * max_source_share))

    timeline: List[dict] = []
    uses = {path: 0 for path in usable}
    position = 0
    covered = 0.0
    while covered < total_seconds:
        # 优先挑使用次数最少的素材，天然实现轮转，避免同一段连续出现。
        candidates = [p for p in usable if uses[p] < max_uses]
        if not candidates:
            # 所有素材都到达配额：素材确实不够，只能放开限制继续复用。
            candidates = usable
        path = candidates[position % len(candidates)]
        remaining = total_seconds - covered
        take = min(float(clip_duration), remaining)
        timeline.append({"path": path, "duration": take, "index": len(timeline)})
        uses[path] += 1
        covered += take
        position += 1
    return timeline


def _probe(path: str) -> dict:
    """用 ffprobe 读取媒体信息。"""
    command = [
        get_ffprobe_binary(),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LongVideoVerificationError(
            f"ffprobe failed for {path}: {(result.stderr or '').strip()}"
        )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise LongVideoVerificationError(f"ffprobe returned invalid json: {exc}")


def _has_faststart(path: str) -> bool:
    """
    判断 moov 原子是否位于 mdat 之前。

    faststart 不是可选项：频道的发布检查要求它，历史上曾因为缺少它而发布过
    有问题的视频。这里读取文件头部足够的字节判断两个原子的先后顺序。
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(4 * 1024 * 1024)
    except OSError:
        return False
    moov = head.find(b"moov")
    mdat = head.find(b"mdat")
    if moov == -1:
        # 头部没有 moov：要么在文件尾部，要么文件超出读取窗口——两种情况都
        # 不能算通过。
        return False
    if mdat == -1:
        return True
    return moov < mdat


def verify_long_video(
    path: str,
    expected_duration: Optional[float] = None,
    check_decode: bool = True,
) -> VerificationReport:
    """
    对成片做技术验收，返回结构化报告。

    这是"上传前该不该发布"的依据。检查项对应频道已有的 QA：完整解码、时长、
    编码格式、faststart。响度由 long_audio.normalize_loudness 负责。
    """
    report = VerificationReport()
    if not path or not os.path.isfile(path):
        report.problems.append("output file does not exist")
        return report

    info = _probe(path)
    fmt = info.get("format", {})
    try:
        report.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        report.duration = 0.0

    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and not report.video_codec:
            report.video_codec = str(stream.get("codec_name") or "")
            report.pix_fmt = str(stream.get("pix_fmt") or "")
            report.width = int(stream.get("width") or 0)
            report.height = int(stream.get("height") or 0)
        elif stream.get("codec_type") == "audio" and not report.audio_codec:
            report.audio_codec = str(stream.get("codec_name") or "")
            try:
                report.sample_rate = int(stream.get("sample_rate") or 0)
            except (TypeError, ValueError):
                report.sample_rate = 0

    report.within_cap = 0 < report.duration <= const.LONG_VIDEO_MAX_DURATION_SECONDS
    if not report.within_cap:
        report.problems.append(
            f"duration {report.duration:.1f}s is outside the allowed range "
            f"(0, {const.LONG_VIDEO_MAX_DURATION_SECONDS}]"
        )

    if expected_duration and report.duration > 0:
        drift = abs(report.duration - expected_duration)
        if drift > DURATION_TOLERANCE_SECONDS:
            report.problems.append(
                f"duration {report.duration:.1f}s differs from the expected "
                f"{expected_duration:.1f}s by {drift:.1f}s"
            )

    if report.video_codec and report.video_codec not in ("h264", "libx264"):
        report.problems.append(f"unexpected video codec: {report.video_codec}")
    if report.pix_fmt and report.pix_fmt != NORMALIZED_PIX_FMT:
        report.problems.append(f"unexpected pixel format: {report.pix_fmt}")
    if not report.audio_codec:
        report.problems.append("output has no audio stream")

    report.faststart = _has_faststart(path)
    if not report.faststart:
        report.problems.append("moov atom is not at the front (missing +faststart)")

    if check_decode:
        report.decode_ok = decode_check(path)
        if not report.decode_ok:
            report.problems.append("full decode reported errors")
    else:
        report.decode_ok = True

    return report


def build_decode_check_command(
    path: str, ffmpeg_binary: Optional[str] = None
) -> List[str]:
    """构造完整解码检查命令：只报错误，不产出文件。"""
    return [
        ffmpeg_binary or utils.get_ffmpeg_binary(),
        "-v",
        "error",
        "-i",
        path,
        "-f",
        "null",
        "-",
    ]


def decode_check(path: str) -> bool:
    """完整解码一遍成片，确认没有损坏的帧。"""
    result = subprocess.run(
        build_decode_check_command(path),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(f"decode check failed for {path}: {(result.stderr or '').strip()}")
        return False
    stderr = (result.stderr or "").strip()
    if stderr:
        logger.warning(f"decode check reported issues: {stderr[:500]}")
        return False
    return True


def render_long_video(
    task_id: str,
    params,
    material_paths: Sequence[str],
    audio_duration: float,
    output_file: str,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> str:
    """
    把素材归一化并串联成与旁白等长的无声视频轨。

    内存恒定的关键在于：每个片段独立处理、独立落盘、立即释放，任何时刻都不会
    有大量解码器实例同时存在。
    """
    from app.models.schema import VideoAspect
    from app.services import video as video_service

    aspect = VideoAspect(params.video_aspect)
    width, height = aspect.to_resolution()
    task_dir = utils.task_dir(task_id)
    work_dir = os.path.join(task_dir, "normalized")
    os.makedirs(work_dir, exist_ok=True)

    required_seconds = video_service._get_required_video_duration(audio_duration)
    timeline = plan_clip_timeline(
        material_paths,
        required_seconds,
        params.video_clip_duration,
    )
    if not timeline:
        raise RuntimeError("no material available to build the long video timeline")

    logger.info(
        f"long render: {len(timeline)} slots covering {required_seconds:.0f}s "
        f"at {width}x{height}"
    )

    normalized_files: List[str] = []
    total = len(timeline)
    for position, slot in enumerate(timeline):
        target = os.path.join(work_dir, f"clip-{slot['index']:05d}.mp4")
        try:
            normalize_clip(
                slot["path"],
                target,
                width,
                height,
                duration=slot["duration"],
                clip_speed=getattr(params, "video_clip_speed", 1.0) or 1.0,
            )
            normalized_files.append(target)
        except Exception as exc:
            # 单个素材损坏不应作废整条渲染：跳过它，时间线会略短，拼接时的
            # max_duration 与旁白仍然对齐。
            logger.warning(f"skipping unusable clip {slot['path']}: {exc}")
        if progress_cb:
            progress_cb((position + 1) / total)

    if not normalized_files:
        raise RuntimeError("every material clip failed to normalize")

    # 上限在这里作为最后一道保险再次生效：即使上游计算出错，成片也不会超过
    # 35 分钟。
    max_duration = min(required_seconds, const.LONG_VIDEO_MAX_DURATION_SECONDS)
    video_service.concat_video_clips_with_ffmpeg(
        clip_files=normalized_files,
        output_file=output_file,
        threads=params.n_threads or 2,
        output_dir=work_dir,
        max_duration=max_duration,
    )
    return output_file


def cleanup_render_workdir(task_id: str) -> None:
    """
    删除归一化中间文件。

    35 分钟的成片会产生几百个中间片段，占用可观的磁盘空间；每天一条长视频的
    节奏下，不清理会很快把磁盘吃满。
    """
    work_dir = os.path.join(utils.task_dir(task_id), "normalized")
    if os.path.isdir(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)
