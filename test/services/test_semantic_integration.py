import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import ScenePrompt, VideoAspect, VideoParams, VideoSemanticPlan
from app.services import video as video_service
from app.services.timeline_engine import build_semantic_timeline
from app.utils import utils


def _ffmpeg() -> str:
    return utils.get_ffmpeg_binary()


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "ffmpeg failed")


def _duration(path: str) -> float:
    from app.services.long_render import _probe

    info = _probe(path)
    return float((info.get("format") or {}).get("duration") or 0.0)


@unittest.skipUnless(shutil.which("ffmpeg") or True, "ffmpeg required")
class TestSemanticIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name

    def _make_color_clip(self, path: str, color: str, seconds: float) -> None:
        _run(
            [
                _ffmpeg(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=320x240:d={seconds:.3f}:r=30",
                "-pix_fmt",
                "yuv420p",
                "-an",
                path,
            ]
        )

    def _make_silent_audio(self, path: str, seconds: float) -> None:
        _run(
            [
                _ffmpeg(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=44100:cl=mono",
                "-t",
                f"{seconds:.3f}",
                path,
            ]
        )

    def test_render_semantic_timeline_matches_audio_duration(self):
        red = os.path.join(self.root, "red.mp4")
        blue = os.path.join(self.root, "blue.mp4")
        audio = os.path.join(self.root, "silent.mp3")
        output = os.path.join(self.root, "combined.mp4")
        self._make_color_clip(red, "red", 5.0)
        self._make_color_clip(blue, "blue", 5.0)
        self._make_silent_audio(audio, 10.0)

        plan = VideoSemanticPlan(
            video_subject="colors",
            total_duration=10.0,
            scenes=[
                ScenePrompt(
                    scene_index=1,
                    start_time=0.0,
                    end_time=5.0,
                    narration_text="red",
                    search_terms=["red"],
                ),
                ScenePrompt(
                    scene_index=2,
                    start_time=5.0,
                    end_time=10.0,
                    narration_text="blue",
                    search_terms=["blue"],
                ),
            ],
        )
        manifest = [
            {
                "file_path": red,
                "target_scene_index": 1,
                "search_term": "red",
                "duration": 5.0,
                "resolution": [320, 240],
                "aspect": "9:16",
            },
            {
                "file_path": blue,
                "target_scene_index": 2,
                "search_term": "blue",
                "duration": 5.0,
                "resolution": [320, 240],
                "aspect": "9:16",
            },
        ]
        timeline = build_semantic_timeline(plan, manifest)
        self.assertEqual(len(timeline.slots), 2)
        self.assertFalse(timeline.has_gaps())

        params = VideoParams(
            video_subject="colors",
            video_aspect=VideoAspect.portrait.value,
            enable_semantic_matching=True,
        )
        rendered = video_service.render_semantic_timeline(
            task_id="semantic-it",
            timeline=timeline,
            audio_file=audio,
            params=params,
            output_file=output,
        )
        self.assertTrue(os.path.isfile(rendered))
        duration = _duration(rendered)
        self.assertAlmostEqual(duration, 10.0, delta=0.25)

        info = __import__("app.services.long_render", fromlist=["_probe"])._probe(rendered)
        video_stream = next(
            stream
            for stream in info.get("streams", [])
            if stream.get("codec_type") == "video"
        )
        fps_text = str(video_stream.get("r_frame_rate") or "30/1")
        if "/" in fps_text:
            num, den = fps_text.split("/", 1)
            fps = float(num) / float(den or 1)
        else:
            fps = float(fps_text)
        self.assertAlmostEqual(fps, 30.0, delta=0.1)

    def test_scene_manifest_roundtrip(self):
        path = os.path.join(self.root, "scene_materials_manifest.json")
        payload = [
            {
                "file_path": "/tmp/scene_01_clip_01.mp4",
                "target_scene_index": 1,
                "search_term": "f22 raptor taking off",
                "duration": 6.2,
                "resolution": [1920, 1080],
                "aspect": "16:9",
            }
        ]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        self.assertEqual(loaded[0]["target_scene_index"], 1)
        self.assertEqual(loaded[0]["search_term"], "f22 raptor taking off")


if __name__ == "__main__":
    unittest.main()
