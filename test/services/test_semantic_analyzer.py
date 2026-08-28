import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import ScenePrompt, VideoParams, VideoSemanticPlan
from app.services import llm
from app.services import semantic_analyzer as analyzer


def _subtitle(start, end, text):
    return {"msg": text, "start_time": start, "end_time": end}


class TestGroupSubtitlesIntoScenes(unittest.TestCase):
    def _fifteen_cues(self):
        cues = []
        phrases = [
            "Horses and carriages ruled the dirt roads.",
            "Steam trains cut across continents.",
            "Rockets now aim at Mars.",
            "The F-22 fighters took off from Hawaii.",
            "Each jet cost more than 150 million dollars.",
            "They crossed the international date line.",
            "Every computer froze at once.",
            "There was no GPS and no screens.",
            "A date-change bug caused the failure.",
            "Tankers had to guide them home.",
            "Stanislav Petrov watched the radar.",
            "Protocol said he must report a strike.",
            "Satellites claimed missiles were flying.",
            "He judged it was a false alarm.",
            "That pause may have saved the world.",
        ]
        start = 0.0
        for text in phrases:
            cues.append(_subtitle(start, start + 3.0, text))
            start += 3.0
        return cues, 45.0

    def test_group_subtitles_into_scenes(self):
        cues, total = self._fifteen_cues()
        scenes = analyzer.group_subtitles_into_scenes(
            cues, min_sec=3.0, max_sec=8.0, total_duration=total
        )

        self.assertGreaterEqual(len(scenes), 6)
        durations = [scene["duration"] for scene in scenes]
        for duration in durations:
            self.assertGreaterEqual(duration, 3.0 - 1e-6)
            self.assertLessEqual(duration, 8.0 + 1e-6)
        self.assertAlmostEqual(sum(durations), total, places=3)
        self.assertAlmostEqual(scenes[0]["start_time"], 0.0, places=3)
        self.assertAlmostEqual(scenes[-1]["end_time"], total, places=3)
        for previous, current in zip(scenes, scenes[1:]):
            self.assertAlmostEqual(previous["end_time"], current["start_time"], places=3)

    def test_splits_a_single_overlong_cue(self):
        scenes = analyzer.group_subtitles_into_scenes(
            [_subtitle(0.0, 45.0, "A very long uninterrupted narration.")],
            min_sec=3.0,
            max_sec=8.0,
            total_duration=45.0,
        )
        self.assertGreater(len(scenes), 1)
        for scene in scenes:
            self.assertLessEqual(scene["duration"], 8.0 + 1e-6)
            self.assertGreaterEqual(scene["duration"], 3.0 - 1e-6)

    def test_skips_empty_text_and_sorts_timestamps(self):
        scenes = analyzer.group_subtitles_into_scenes(
            [
                _subtitle(6.0, 9.0, "Third."),
                {"msg": "   ", "start_time": 3.0, "end_time": 6.0},
                _subtitle(0.0, 3.0, "First."),
                _subtitle(3.0, 6.0, "Second."),
            ],
            min_sec=3.0,
            max_sec=8.0,
            total_duration=9.0,
        )
        texts = [scene["narration_text"] for scene in scenes]
        joined = " ".join(texts)
        self.assertIn("First", joined)
        self.assertIn("Second", joined)
        self.assertIn("Third", joined)
        self.assertNotIn("   ", joined)


class TestLlmScenePromptsParsing(unittest.TestCase):
    def test_llm_scene_prompts_parsing(self):
        payload = [
            {
                "scene_index": 1,
                "visual_description": "horses on a dirt road",
                "search_terms": ["horses dirt road", "horse carriage"],
                "mood": "historical",
            }
        ]
        raw = json.dumps(payload)
        fenced = "```json\n" + raw + "\n```"
        malformed = raw.replace("}]", ",}]")

        for blob in (raw, fenced, malformed):
            parsed = llm.parse_scene_prompts_response(blob)
            self.assertEqual(parsed[0]["scene_index"], 1)
            self.assertIn("horses dirt road", parsed[0]["search_terms"])

    def test_blacklists_hacker_mask_cliches(self):
        cleaned = analyzer.filter_blacklisted_terms(
            ["f22 raptor flying", "hacker mask laptop", "anonymous mask"],
            era="Modern Military 2000s",
        )
        self.assertEqual(cleaned, ["f22 raptor flying"])

    def test_infers_cold_war_era(self):
        era = analyzer.infer_historical_era(
            "Stanislav Petrov 1983",
            "Pelo protocolo militar da Guerra Fria...",
        )
        self.assertIn("Cold War", era)


class TestAnalyzeNarrationScenes(unittest.TestCase):
    def test_builds_plan_with_heuristic_when_llm_fails(self):
        cues = [
            _subtitle(0.0, 4.0, "Horses and carriages ruled the roads."),
            _subtitle(4.0, 8.0, "Steam trains cut continents."),
        ]
        with patch(
            "app.services.llm.generate_scene_prompts",
            return_value=[],
        ):
            plan = analyzer.analyze_narration_scenes(
                task_id="",
                script="transport history",
                subtitles=cues,
                subject="Evolution of transport",
                min_sec=3.0,
                max_sec=8.0,
                total_duration=8.0,
            )
        self.assertIsInstance(plan, VideoSemanticPlan)
        self.assertEqual(len(plan.scenes), 2)
        self.assertTrue(all(scene.search_terms for scene in plan.scenes))
        self.assertTrue(all(isinstance(scene, ScenePrompt) for scene in plan.scenes))


if __name__ == "__main__":
    unittest.main()
