PUNCTUATIONS = [
    "?",
    ",",
    ".",
    "、",
    ";",
    ":",
    "!",
    "…",
    "？",
    "，",
    "。",
    "、",
    "；",
    "：",
    "！",
    "...",
    # 阿拉伯语常用标点也应作为自然断句点，避免脚本文本和 edge-tts
    # 返回的字幕停顿边界不一致，导致后续逐行匹配失败。
    "،",
    "؛",
    "؟",
]

TASK_STATE_FAILED = -1
TASK_STATE_COMPLETE = 1
TASK_STATE_PROCESSING = 4

CROSS_POST_STATE_PENDING = "pending"
CROSS_POST_STATE_PROCESSING = "processing"
CROSS_POST_STATE_COMPLETE = "complete"
CROSS_POST_STATE_FAILED = "failed"

FILE_TYPE_VIDEOS = ["mp4", "mov", "mkv", "webm"]
FILE_TYPE_IMAGES = ["jpg", "jpeg", "png", "bmp"]

# 长视频模式 / Long-form video mode.
# 时长上限是硬性技术约束，不只是界面提示：WebUI、CLI、API 和内部调用都必须遵守。
# The duration cap is a hard technical constraint, not just a UI hint: the WebUI,
# CLI, API, and internal callers all enforce it.
VIDEO_MODE_SHORT = "short"
VIDEO_MODE_LONG = "long"
VIDEO_MODES = (VIDEO_MODE_SHORT, VIDEO_MODE_LONG)

LONG_VIDEO_MAX_DURATION_SECONDS = 35 * 60  # 2100
# 低于该时长应使用常规流程；长视频模式的分章开销在很短的视频上得不偿失。
# Below this, use the regular pipeline: chapter planning does not pay off.
LONG_VIDEO_MIN_DURATION_SECONDS = 3 * 60  # 180

# 章节数量的安全范围。超出范围会让大纲要么过于笼统，要么产生大量零碎章节。
# Sane chapter bounds: outside them the outline is either too coarse or too fragmented.
LONG_VIDEO_MIN_CHAPTERS = 3
LONG_VIDEO_MAX_CHAPTERS = 14

# 任务警告码，供 WebUI 与调用方按稳定标识渲染提示。
# Stable warning codes so the WebUI and API clients can render hints reliably.
WARNING_LONG_VIDEO_TRUNCATED = "long_video_truncated"
WARNING_LONG_VIDEO_MATERIAL_REPEATED = "long_video_material_repeated"
