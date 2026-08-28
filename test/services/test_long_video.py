import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models import const
from app.models.schema import ChapterOutlineItem, VideoParams
from app.services import long_video


class TestShortModeUnchanged(unittest.TestCase):
    """短视频流水线每天都在生产运行，这些断言是它的回归防线。"""

    def test_default_params_stay_in_short_mode(self):
        params = VideoParams(video_subject="Coffee")
        self.assertEqual(params.video_mode, const.VIDEO_MODE_SHORT)

    def test_short_mode_field_defaults_are_untouched(self):
        params = VideoParams(video_subject="Coffee")
        self.assertEqual(params.video_aspect, "9:16")
        self.assertEqual(params.video_clip_duration, 5)
        self.assertEqual(params.video_count, 1)
        self.assertEqual(params.paragraph_number, 1)
        self.assertEqual(params.n_threads, 2)
        self.assertFalse(params.match_materials_to_script)

    def test_long_mode_fields_default_to_inert_values(self):
        params = VideoParams(video_subject="Coffee")
        self.assertIsNone(params.target_duration_minutes)
        self.assertIsNone(params.chapter_count)
        self.assertIsNone(params.chapter_outline)
        self.assertFalse(params.narrate_chapter_titles)

    def test_apply_defaults_returns_short_params_untouched(self):
        params = VideoParams(video_subject="Coffee")
        # 必须返回同一个对象：短视频路径不应该付出任何拷贝开销，也不能被改写。
        self.assertIs(long_video.apply_long_video_defaults(params), params)


class TestLongModeValidation(unittest.TestCase):
    def test_long_mode_requires_duration_or_outline(self):
        with self.assertRaises(ValidationError):
            VideoParams(video_subject="Coffee", video_mode="long")

    def test_long_mode_accepts_duration(self):
        params = VideoParams(
            video_subject="Coffee", video_mode="long", target_duration_minutes=12
        )
        self.assertEqual(params.target_duration_minutes, 12)

    def test_long_mode_accepts_outline_without_duration(self):
        params = VideoParams(
            video_subject="Coffee",
            video_mode="long",
            chapter_outline=[ChapterOutlineItem(title="Abertura", weight=0.5)],
        )
        self.assertEqual(len(params.chapter_outline), 1)

    def test_long_mode_rejects_multiple_output_videos(self):
        with self.assertRaises(ValidationError):
            VideoParams(
                video_subject="Coffee",
                video_mode="long",
                target_duration_minutes=12,
                video_count=2,
            )

    def test_duration_above_cap_is_rejected(self):
        cap_minutes = const.LONG_VIDEO_MAX_DURATION_SECONDS / 60
        with self.assertRaises(ValidationError):
            VideoParams(
                video_subject="Coffee",
                video_mode="long",
                target_duration_minutes=cap_minutes + 1,
            )

    def test_duration_at_cap_is_accepted(self):
        cap_minutes = const.LONG_VIDEO_MAX_DURATION_SECONDS / 60
        params = VideoParams(
            video_subject="Coffee",
            video_mode="long",
            target_duration_minutes=cap_minutes,
        )
        self.assertEqual(params.target_duration_minutes, cap_minutes)

    def test_duration_below_minimum_is_rejected(self):
        with self.assertRaises(ValidationError):
            VideoParams(
                video_subject="Coffee", video_mode="long", target_duration_minutes=1
            )

    def test_chapter_count_bounds(self):
        for value in (const.LONG_VIDEO_MIN_CHAPTERS - 1, const.LONG_VIDEO_MAX_CHAPTERS + 1):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    VideoParams(
                        video_subject="Coffee",
                        video_mode="long",
                        target_duration_minutes=12,
                        chapter_count=value,
                    )


class TestLongVideoDefaults(unittest.TestCase):
    def _long_params(self, **overrides):
        base = {
            "video_subject": "Coffee",
            "video_mode": "long",
            "target_duration_minutes": 12,
        }
        base.update(overrides)
        return VideoParams(**base)

    def test_defaults_applied_when_caller_is_silent(self):
        params = long_video.apply_long_video_defaults(self._long_params())
        self.assertEqual(params.video_aspect, "16:9")
        self.assertEqual(params.video_concat_mode, "sequential")
        self.assertEqual(params.video_transition_mode, "FadeIn")
        self.assertEqual(params.video_clip_duration, 10)
        self.assertTrue(params.match_materials_to_script)
        self.assertEqual(params.video_count, 1)
        self.assertAlmostEqual(params.bgm_volume, 0.15)

    def test_explicit_values_are_never_overwritten(self):
        params = long_video.apply_long_video_defaults(
            self._long_params(
                video_aspect="9:16", video_clip_duration=4, bgm_volume=0.5
            )
        )
        self.assertEqual(params.video_aspect, "9:16")
        self.assertEqual(params.video_clip_duration, 4)
        self.assertAlmostEqual(params.bgm_volume, 0.5)

    def test_thread_count_is_raised_for_long_renders(self):
        params = long_video.apply_long_video_defaults(self._long_params())
        self.assertGreaterEqual(params.n_threads, 4)

    def test_explicit_thread_count_is_kept(self):
        params = long_video.apply_long_video_defaults(self._long_params(n_threads=2))
        self.assertEqual(params.n_threads, 2)

    def test_missing_duration_gets_default_when_no_outline(self):
        params = VideoParams(
            video_subject="Coffee",
            video_mode="long",
            chapter_outline=[ChapterOutlineItem(title="Abertura", weight=1.0)],
        )
        # 大纲已固定时长来源，不应再塞入默认目标时长。
        applied = long_video.apply_long_video_defaults(params)
        self.assertIsNone(applied.target_duration_minutes)


class TestDurationResolution(unittest.TestCase):
    def test_resolve_uses_explicit_duration(self):
        params = VideoParams(
            video_subject="Coffee", video_mode="long", target_duration_minutes=20
        )
        self.assertEqual(long_video.resolve_target_seconds(params), 1200)

    def test_resolve_falls_back_to_default_duration(self):
        params = VideoParams(
            video_subject="Coffee",
            video_mode="long",
            chapter_outline=[ChapterOutlineItem(title="Abertura", weight=1.0)],
        )
        expected = long_video.DEFAULT_TARGET_DURATION_MINUTES * 60
        self.assertEqual(long_video.resolve_target_seconds(params), expected)

    def test_resolve_rejects_duration_above_cap(self):
        # 绕过 Pydantic 校验，模拟历史任务记录或内部调用传入的超限值。
        params = VideoParams(
            video_subject="Coffee", video_mode="long", target_duration_minutes=20
        )
        object.__setattr__(params, "target_duration_minutes", 40)
        with self.assertRaises(ValueError):
            long_video.resolve_target_seconds(params)


class TestNarrationEstimate(unittest.TestCase):
    def test_empty_text_is_zero(self):
        self.assertEqual(long_video.estimate_narration_seconds(""), 0.0)
        self.assertEqual(long_video.estimate_narration_seconds("   "), 0.0)

    def test_estimate_is_monotonic(self):
        short = "uma frase curta de teste"
        longer = short * 12
        self.assertGreater(
            long_video.estimate_narration_seconds(longer),
            long_video.estimate_narration_seconds(short),
        )

    def test_latin_text_uses_word_rate(self):
        text = " ".join(["palavra"] * 150)
        # 150 palavras a 150 wpm ~= 60 s
        self.assertAlmostEqual(
            long_video.estimate_narration_seconds(text), 60.0, delta=1.0
        )

    def test_cjk_text_is_not_underestimated(self):
        # 中文整段没有空格，按词数会被算成 1 个词；必须走字符估算。
        text = "这是一段没有空格的中文文案" * 20
        self.assertGreater(long_video.estimate_narration_seconds(text), 30.0)


class TestParagraphNumberDerivation(unittest.TestCase):
    def test_never_exceeds_generate_script_limit(self):
        from app.services.llm import (
            MAX_SCRIPT_PARAGRAPH_NUMBER,
            MIN_SCRIPT_PARAGRAPH_NUMBER,
        )

        # 覆盖整个长视频时长区间，含远超单章合理长度的极端值。
        for seconds in (0, 1, 45, 150, 600, 2100, 10_000):
            with self.subTest(seconds=seconds):
                value = long_video.paragraph_number_for_seconds(seconds)
                self.assertGreaterEqual(value, MIN_SCRIPT_PARAGRAPH_NUMBER)
                self.assertLessEqual(value, MAX_SCRIPT_PARAGRAPH_NUMBER)

    def test_longer_chapters_ask_for_more_paragraphs(self):
        self.assertLess(
            long_video.paragraph_number_for_seconds(45),
            long_video.paragraph_number_for_seconds(300),
        )


class TestChapterCountSuggestion(unittest.TestCase):
    def test_stays_within_bounds(self):
        for seconds in (0, 180, 600, 2100, 99_999):
            with self.subTest(seconds=seconds):
                value = long_video.suggest_chapter_count(seconds)
                self.assertGreaterEqual(value, const.LONG_VIDEO_MIN_CHAPTERS)
                self.assertLessEqual(value, const.LONG_VIDEO_MAX_CHAPTERS)

    def test_longer_videos_get_more_chapters(self):
        self.assertLess(
            long_video.suggest_chapter_count(300),
            long_video.suggest_chapter_count(1800),
        )


class TestWeightNormalization(unittest.TestCase):
    def test_weights_are_normalized_to_one(self):
        result = long_video.normalize_chapter_weights([2.0, 3.0, 5.0])
        self.assertAlmostEqual(sum(result), 1.0)
        self.assertAlmostEqual(result[2], 0.5)

    def test_missing_weights_fall_back_to_even_split(self):
        result = long_video.normalize_chapter_weights([None, None, None])
        self.assertAlmostEqual(sum(result), 1.0)
        for value in result:
            self.assertAlmostEqual(value, 1 / 3)

    def test_invalid_weights_fall_back_to_even_split(self):
        result = long_video.normalize_chapter_weights([0.0, -1.0, "x"])
        self.assertAlmostEqual(sum(result), 1.0)

    def test_empty_input_returns_empty(self):
        self.assertEqual(long_video.normalize_chapter_weights([]), [])

    def test_zero_weight_chapter_still_gets_a_share(self):
        # 章节存在却分不到时长是无意义的；必须给出最小份额。
        result = long_video.normalize_chapter_weights([0.0, 1.0, 1.0])
        self.assertAlmostEqual(sum(result), 1.0)
        self.assertGreater(result[0], 0.0)


class TestLongVideoPlan(unittest.TestCase):
    def _plan(self):
        return long_video.LongVideoPlan(
            subject="Bugs históricos",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(
                    index=1,
                    title="Abertura",
                    brief="gancho",
                    target_seconds=200,
                    script="Primeiro capítulo.",
                    terms=("apollo computer", "nasa control room"),
                ),
                long_video.Chapter(
                    index=2,
                    title="Desenvolvimento",
                    brief="meio",
                    target_seconds=400,
                    script="Segundo capítulo.",
                    terms=("nasa control room", "rocket launch"),
                ),
            ],
        )

    def test_full_script_joins_chapters_with_blank_line(self):
        self.assertEqual(
            self._plan().full_script, "Primeiro capítulo.\n\nSegundo capítulo."
        )

    def test_full_script_skips_empty_chapters(self):
        plan = self._plan()
        plan.chapters.append(
            long_video.Chapter(index=3, title="Vazio", brief="", target_seconds=0)
        )
        self.assertEqual(
            plan.full_script, "Primeiro capítulo.\n\nSegundo capítulo."
        )

    def test_collect_terms_preserves_narrative_order(self):
        self.assertEqual(
            self._plan().collect_terms(),
            ["apollo computer", "nasa control room", "rocket launch"],
        )

    def test_collect_terms_deduplicates_case_insensitively(self):
        plan = self._plan()
        plan.chapters[1] = long_video.Chapter(
            index=2,
            title="Desenvolvimento",
            brief="meio",
            target_seconds=400,
            script="Segundo capítulo.",
            terms=("NASA Control Room", "rocket launch"),
        )
        self.assertEqual(len(plan.collect_terms()), 3)

    def test_chapter_count(self):
        self.assertEqual(self._plan().chapter_count, 2)

    def test_estimated_seconds_uses_target_when_no_script(self):
        plan = long_video.LongVideoPlan(
            subject="x",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(index=1, title="a", brief="", target_seconds=250),
                long_video.Chapter(index=2, title="b", brief="", target_seconds=350),
            ],
        )
        self.assertEqual(plan.estimated_seconds, 600)


if __name__ == "__main__":
    unittest.main()
