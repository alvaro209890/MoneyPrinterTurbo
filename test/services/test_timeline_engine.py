import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import ScenePrompt, VideoSemanticPlan
from app.services.timeline_engine import (
    SemanticTimeline,
    build_semantic_timeline,
)


def _scene(index, start, end, text="narration"):
    return ScenePrompt(
        scene_index=index,
        start_time=start,
        end_time=end,
        narration_text=text,
        search_terms=[f"term-{index}"],
    )


def _clip(path, scene_index, duration):
    return {
        "file_path": path,
        "target_scene_index": scene_index,
        "search_term": f"term-{scene_index}",
        "duration": duration,
        "resolution": [1920, 1080],
        "aspect": "16:9",
    }


class TestBuildSemanticTimeline(unittest.TestCase):
    def test_build_semantic_timeline(self):
        scenes = [
            _scene(1, 0.0, 4.0),
            _scene(2, 4.0, 9.0),
            _scene(3, 9.0, 13.0),
            _scene(4, 13.0, 18.0),
            _scene(5, 18.0, 22.0),
        ]
        manifest = []
        for scene in scenes:
            manifest.append(_clip(f"/tmp/scene{scene.scene_index}a.mp4", scene.scene_index, 8.0))
            manifest.append(_clip(f"/tmp/scene{scene.scene_index}b.mp4", scene.scene_index, 8.0))

        plan = VideoSemanticPlan(
            video_subject="transport",
            total_duration=22.0,
            scenes=scenes,
        )
        timeline = build_semantic_timeline(plan, manifest)

        self.assertIsInstance(timeline, SemanticTimeline)
        self.assertEqual(len(timeline.slots), 5)
        for scene, slot in zip(scenes, timeline.slots):
            self.assertAlmostEqual(slot.start_time, scene.start_time, places=3)
            self.assertAlmostEqual(slot.end_time, scene.end_time, places=3)
            self.assertAlmostEqual(
                slot.target_duration, scene.end_time - scene.start_time, places=3
            )
        self.assertFalse(timeline.has_gaps(tolerance=0.05))
        for previous, current in zip(timeline.slots, timeline.slots[1:]):
            self.assertNotEqual(previous.source_video_path, current.source_video_path)
            self.assertAlmostEqual(previous.end_time, current.start_time, places=3)

    def test_short_clip_splits_same_scene_without_stealing_next_scene(self):
        scenes = [_scene(1, 0.0, 4.5), _scene(2, 4.5, 8.0)]
        manifest = [
            _clip("/tmp/tiny-a.mp4", 1, 2.0),
            _clip("/tmp/tiny-b.mp4", 1, 2.0),
            _clip("/tmp/ok.mp4", 2, 8.0),
        ]
        timeline = build_semantic_timeline(
            VideoSemanticPlan(video_subject="x", total_duration=8.0, scenes=scenes),
            manifest,
        )
        scene_one = [slot for slot in timeline.slots if slot.scene_index == 1]
        scene_two = [slot for slot in timeline.slots if slot.scene_index == 2]
        self.assertGreaterEqual(len(scene_one), 2)
        self.assertEqual(len(scene_two), 1)
        self.assertEqual(scene_two[0].source_video_path, "/tmp/ok.mp4")
        self.assertAlmostEqual(timeline.slots[0].start_time, 0.0, places=3)
        self.assertAlmostEqual(timeline.slots[-1].end_time, 8.0, places=3)
        self.assertFalse(timeline.has_gaps())


if __name__ == "__main__":
    unittest.main()
