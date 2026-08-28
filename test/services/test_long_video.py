import sys
import unittest
import unittest.mock
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


class TestOutlineParsing(unittest.TestCase):
    def test_parses_plain_json_array(self):
        items = long_video.parse_outline_response(
            '[{"title": "A", "brief": "b", "weight": 0.5}]'
        )
        self.assertEqual(items[0]["title"], "A")

    def test_parses_json_wrapped_in_code_fence(self):
        # Claude/Gemini frequentemente envolvem o JSON em cerca de markdown.
        raw = '```json\n[{"title": "A", "brief": "b", "weight": 1.0}]\n```'
        self.assertEqual(len(long_video.parse_outline_response(raw)), 1)

    def test_recovers_array_from_surrounding_prose(self):
        raw = 'Claro! Aqui está:\n[{"title": "A", "brief": "b", "weight": 1.0}]\nEspero ajudar.'
        self.assertEqual(len(long_video.parse_outline_response(raw)), 1)

    def test_rejects_provider_error_string(self):
        # _generate_response sinaliza falha com este prefixo; tratar como texto
        # válido faria o pipeline seguir com um "roteiro" que é uma mensagem de erro.
        with self.assertRaises(ValueError):
            long_video.parse_outline_response("Error: quota exceeded")

    def test_rejects_empty_response(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    long_video.parse_outline_response(value)

    def test_rejects_non_array_payload(self):
        with self.assertRaises(ValueError):
            long_video.parse_outline_response('{"title": "A"}')

    def test_drops_entries_without_title(self):
        items = long_video.parse_outline_response(
            '[{"title": "", "brief": "x"}, {"title": "B", "brief": "y"}]'
        )
        self.assertEqual([item["title"] for item in items], ["B"])

    def test_raises_when_no_entry_is_usable(self):
        with self.assertRaises(ValueError):
            long_video.parse_outline_response('[{"brief": "no title"}]')


class TestPlanChapters(unittest.TestCase):
    OUTLINE = (
        '[{"title": "O erro", "brief": "o bug", "weight": 0.3},'
        ' {"title": "A queda", "brief": "consequencia", "weight": 0.4},'
        ' {"title": "A licao", "brief": "payoff", "weight": 0.3}]'
    )

    def test_chapter_seconds_sum_to_target(self):
        with unittest.mock.patch(
            "app.services.llm._generate_response", return_value=self.OUTLINE
        ):
            plan = long_video.plan_chapters("Ariane 5", 600, "pt-BR")
        total = sum(chapter.target_seconds for chapter in plan.chapters)
        self.assertAlmostEqual(total, 600, delta=1)

    def test_chapters_are_indexed_from_one(self):
        with unittest.mock.patch(
            "app.services.llm._generate_response", return_value=self.OUTLINE
        ):
            plan = long_video.plan_chapters("Ariane 5", 600, "pt-BR")
        self.assertEqual([c.index for c in plan.chapters], [1, 2, 3])

    def test_supplied_outline_skips_the_llm_entirely(self):
        outline = [ChapterOutlineItem(title="A", brief="x", weight=0.5)]
        with unittest.mock.patch("app.services.llm._generate_response") as mocked:
            plan = long_video.plan_chapters("Ariane 5", 600, "pt-BR", outline=outline)
        mocked.assert_not_called()
        self.assertEqual(plan.chapter_count, 1)

    def test_retries_then_fails_with_clear_error(self):
        with unittest.mock.patch(
            "app.services.llm._generate_response", return_value="not json"
        ) as mocked:
            with self.assertRaises(ValueError):
                long_video.plan_chapters("Ariane 5", 600, "pt-BR")
        self.assertGreater(mocked.call_count, 1)

    def test_recovers_when_a_later_attempt_succeeds(self):
        responses = ["garbage", self.OUTLINE]
        with unittest.mock.patch(
            "app.services.llm._generate_response", side_effect=responses
        ):
            plan = long_video.plan_chapters("Ariane 5", 600, "pt-BR")
        self.assertEqual(plan.chapter_count, 3)


class TestChapterContinuity(unittest.TestCase):
    def _plan(self):
        return long_video.LongVideoPlan(
            subject="Ariane 5",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(index=1, title="A", brief="ba", target_seconds=200),
                long_video.Chapter(index=2, title="B", brief="bb", target_seconds=200),
                long_video.Chapter(index=3, title="C", brief="bc", target_seconds=200),
            ],
        )

    def test_requirements_include_full_outline(self):
        plan = self._plan()
        text = long_video.build_chapter_requirements(plan, plan.chapters[1])
        for title in ("A", "B", "C"):
            self.assertIn(title, text)

    def test_requirements_stay_within_prompt_limit(self):
        from app.services.llm import MAX_SCRIPT_PROMPT_LENGTH

        plan = long_video.LongVideoPlan(
            subject="x",
            language="pt-BR",
            target_seconds=2100,
            chapters=[
                long_video.Chapter(
                    index=i + 1,
                    title="t" * 200,
                    brief="b" * 1000,
                    target_seconds=150,
                )
                for i in range(14)
            ],
        )
        text = long_video.build_chapter_requirements(
            plan, plan.chapters[7], previous_tail="z" * 600
        )
        self.assertLessEqual(len(text), MAX_SCRIPT_PROMPT_LENGTH)

    def test_first_chapter_is_told_to_hook(self):
        plan = self._plan()
        text = long_video.build_chapter_requirements(plan, plan.chapters[0])
        self.assertIn("hook", text.lower())

    def test_later_chapters_are_told_not_to_reintroduce(self):
        plan = self._plan()
        text = long_video.build_chapter_requirements(plan, plan.chapters[1])
        self.assertIn("re-introduce", text.lower())

    def test_final_chapter_is_told_to_close(self):
        plan = self._plan()
        text = long_video.build_chapter_requirements(plan, plan.chapters[2])
        self.assertIn("final chapter", text.lower())

    def test_previous_tail_is_injected_only_after_chapter_one(self):
        plan = self._plan()
        first = long_video.build_chapter_requirements(
            plan, plan.chapters[0], previous_tail="fim anterior."
        )
        second = long_video.build_chapter_requirements(
            plan, plan.chapters[1], previous_tail="fim anterior."
        )
        self.assertNotIn("fim anterior.", first)
        self.assertIn("fim anterior.", second)


class TestTailSentences(unittest.TestCase):
    def test_returns_last_two_sentences(self):
        text = "Uma. Duas. Tres. Quatro."
        self.assertEqual(long_video.tail_sentences(text), "Tres. Quatro.")

    def test_handles_text_without_terminators(self):
        self.assertTrue(long_video.tail_sentences("sem pontuacao alguma"))

    def test_empty_text_returns_empty(self):
        self.assertEqual(long_video.tail_sentences(""), "")

    def test_output_is_bounded(self):
        self.assertLessEqual(len(long_video.tail_sentences("a. " * 5000)), 600)


class TestGenerateChapterScript(unittest.TestCase):
    def _plan(self):
        return long_video.LongVideoPlan(
            subject="Ariane 5",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(index=1, title="A", brief="b", target_seconds=600)
            ],
        )

    def test_never_requests_more_paragraphs_than_allowed(self):
        from app.services.llm import MAX_SCRIPT_PARAGRAPH_NUMBER

        plan = self._plan()
        with unittest.mock.patch(
            "app.services.llm.generate_script", return_value="texto"
        ) as mocked:
            long_video.generate_chapter_script(plan, plan.chapters[0])
        self.assertLessEqual(
            mocked.call_args.kwargs["paragraph_number"], MAX_SCRIPT_PARAGRAPH_NUMBER
        )

    def test_uses_the_long_form_system_prompt(self):
        plan = self._plan()
        with unittest.mock.patch(
            "app.services.llm.generate_script", return_value="texto"
        ) as mocked:
            long_video.generate_chapter_script(plan, plan.chapters[0])
        self.assertEqual(
            mocked.call_args.kwargs["custom_system_prompt"],
            long_video.LONG_FORM_SYSTEM_PROMPT,
        )

    def test_raises_on_empty_script(self):
        plan = self._plan()
        with unittest.mock.patch("app.services.llm.generate_script", return_value=""):
            with self.assertRaises(ValueError):
                long_video.generate_chapter_script(plan, plan.chapters[0])

    def test_raises_on_provider_error_string(self):
        plan = self._plan()
        with unittest.mock.patch(
            "app.services.llm.generate_script", return_value="Error: quota"
        ):
            with self.assertRaises(ValueError):
                long_video.generate_chapter_script(plan, plan.chapters[0])


class TestBuildLongScript(unittest.TestCase):
    OUTLINE = (
        '[{"title": "A", "brief": "a", "weight": 0.34},'
        ' {"title": "B", "brief": "b", "weight": 0.33},'
        ' {"title": "C", "brief": "c", "weight": 0.33}]'
    )

    def _build(self, script="Frase um. Frase dois.", minutes=10):
        params = VideoParams(
            video_subject="Ariane 5",
            video_mode="long",
            target_duration_minutes=minutes,
        )
        with unittest.mock.patch(
            "app.services.llm._generate_response", return_value=self.OUTLINE
        ), unittest.mock.patch(
            "app.services.llm.generate_script", side_effect=lambda **k: script
        ):
            return long_video.build_long_script(params)

    def test_char_offsets_match_the_joined_script(self):
        # Este é o invariante de que a legenda por capítulo depende (plano 04 §5.2).
        plan = self._build()
        full = plan.full_script
        for chapter in plan.chapters:
            with self.subTest(chapter=chapter.index):
                self.assertEqual(
                    full[chapter.char_start : chapter.char_end], chapter.script
                )

    def test_progress_is_reported_and_reaches_one(self):
        seen = []
        params = VideoParams(
            video_subject="Ariane 5", video_mode="long", target_duration_minutes=10
        )
        with unittest.mock.patch(
            "app.services.llm._generate_response", return_value=self.OUTLINE
        ), unittest.mock.patch(
            "app.services.llm.generate_script", side_effect=lambda **k: "Texto."
        ):
            long_video.build_long_script(params, progress_cb=seen.append)
        self.assertEqual(seen[0], 0.0)
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen))

    def test_over_budget_plan_drops_whole_chapters(self):
        # Cada capítulo "narra" muito mais que o alvo, estourando o teto de 35 min.
        huge = " ".join(["palavra"] * 4000)
        plan = self._build(script=huge, minutes=34)
        self.assertLess(plan.chapter_count, 3)
        self.assertGreaterEqual(plan.chapter_count, 1)

    def test_truncation_never_empties_the_plan(self):
        huge = " ".join(["palavra"] * 100_000)
        plan = self._build(script=huge, minutes=34)
        self.assertGreaterEqual(plan.chapter_count, 1)


class TestChapterTerms(unittest.TestCase):
    def _plan(self):
        return long_video.LongVideoPlan(
            subject="Ariane 5",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(
                    index=1, title="A", brief="", target_seconds=300, script="texto a"
                ),
                long_video.Chapter(
                    index=2, title="B", brief="", target_seconds=300, script="texto b"
                ),
            ],
        )

    def test_terms_are_requested_in_script_order_mode(self):
        with unittest.mock.patch(
            "app.services.llm.generate_terms", return_value=["x"]
        ) as mocked:
            long_video.generate_chapter_terms(self._plan())
        for call in mocked.call_args_list:
            self.assertTrue(call.kwargs["match_script_order"])

    def test_terms_are_generated_per_chapter(self):
        with unittest.mock.patch(
            "app.services.llm.generate_terms", return_value=["x"]
        ) as mocked:
            long_video.generate_chapter_terms(self._plan())
        self.assertEqual(mocked.call_count, 2)

    def test_twelvelabs_rerank_is_never_called(self):
        # O rerank ordena por relevância temática e destruiria a ordem cronológica
        # de que a alocação de materiais por capítulo depende.
        with unittest.mock.patch(
            "app.services.llm.generate_terms", return_value=["x"]
        ), unittest.mock.patch(
            "app.services.twelvelabs.rerank_terms_by_subject"
        ) as rerank:
            long_video.generate_chapter_terms(self._plan())
        rerank.assert_not_called()

    def test_chapter_failure_does_not_abort_the_run(self):
        with unittest.mock.patch(
            "app.services.llm.generate_terms",
            side_effect=[RuntimeError("boom"), ["ok"]],
        ):
            plan = long_video.generate_chapter_terms(self._plan())
        self.assertEqual(plan.chapters[0].terms, ())
        self.assertEqual(plan.chapters[1].terms, ("ok",))


class TestPlanSerialization(unittest.TestCase):
    def test_plan_to_dict_round_trips_chapter_data(self):
        plan = long_video.LongVideoPlan(
            subject="Ariane 5",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(
                    index=1,
                    title="A",
                    brief="b",
                    target_seconds=300,
                    script="texto",
                    terms=("t1", "t2"),
                    char_start=0,
                    char_end=5,
                )
            ],
        )
        payload = long_video.plan_to_dict(plan)
        self.assertEqual(payload["subject"], "Ariane 5")
        self.assertEqual(payload["chapters"][0]["terms"], ["t1", "t2"])
        self.assertEqual(payload["chapters"][0]["char_end"], 5)
        # O texto do roteiro não é duplicado aqui: ele já vive em script.json.
        self.assertNotIn("script", payload["chapters"][0])


if __name__ == "__main__":
    unittest.main()
