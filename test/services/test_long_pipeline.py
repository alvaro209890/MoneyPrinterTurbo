"""
Integração do modo longo no orquestrador (`task._run_pipeline`).

O teste mais importante deste arquivo é `TestShortModeIsUntouched`: o caminho
dos Shorts roda em produção todo dia, e uma regressão silenciosa nele custa a
publicação diária do canal.
"""

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models import const
from app.models.schema import VideoParams
from app.services import task as tm


class _PipelineHarness:
    """Mocka tudo que o pipeline toca, para exercitar só o roteamento."""

    def __init__(self, **overrides):
        self.patches = []
        self.mocks = {}
        defaults = {
            "app.services.task.sm.state.update_task": unittest.mock.DEFAULT,
            "app.utils.utils.check_ffmpeg_ready": True,
            "app.services.task.save_script_data": unittest.mock.DEFAULT,
            "app.services.task_artifacts.patch_script_data": True,
        }
        defaults.update(overrides)
        self.defaults = defaults

    def __enter__(self):
        for target, value in self.defaults.items():
            if value is unittest.mock.DEFAULT:
                patcher = unittest.mock.patch(target)
            else:
                patcher = unittest.mock.patch(target, return_value=value)
            self.patches.append(patcher)
            self.mocks[target] = patcher.start()
        return self.mocks

    def __exit__(self, *exc):
        for patcher in reversed(self.patches):
            patcher.stop()
        return False


class TestShortModeIsUntouched(unittest.TestCase):
    """Rede de segurança contra regressão no pipeline diário de Shorts."""

    def _short_params(self):
        return VideoParams(video_subject="Coffee")

    def test_short_mode_calls_the_original_script_path(self):
        with _PipelineHarness(
            **{
                "app.services.task.generate_script": "roteiro curto",
                "app.services.long_video.build_long_script": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", self._short_params(), stop_at="script")

        mocks["app.services.task.generate_script"].assert_called_once()
        mocks["app.services.long_video.build_long_script"].assert_not_called()

    def test_short_mode_calls_the_original_terms_path(self):
        with _PipelineHarness(
            **{
                "app.services.task.generate_script": "roteiro curto",
                "app.services.task.generate_terms": ["a", "b"],
                "app.services.long_video.generate_chapter_terms": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", self._short_params(), stop_at="terms")

        mocks["app.services.task.generate_terms"].assert_called_once()
        mocks["app.services.long_video.generate_chapter_terms"].assert_not_called()

    def test_short_mode_never_touches_long_audio(self):
        with _PipelineHarness(
            **{
                "app.services.task.generate_script": "roteiro",
                "app.services.task.generate_terms": ["a"],
                "app.services.task.generate_audio": ("a.mp3", 30, unittest.mock.Mock()),
                "app.services.long_audio.synthesize_long_narration": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", self._short_params(), stop_at="audio")

        mocks["app.services.task.generate_audio"].assert_called_once()
        mocks[
            "app.services.long_audio.synthesize_long_narration"
        ].assert_not_called()

    def test_short_mode_never_touches_long_materials(self):
        with _PipelineHarness(
            **{
                "app.services.task.generate_script": "roteiro",
                "app.services.task.generate_terms": ["a"],
                "app.services.task.generate_audio": ("a.mp3", 30, unittest.mock.Mock()),
                "app.services.task.generate_subtitle": "s.srt",
                "app.services.task.get_video_materials": ["m.mp4"],
                "app.services.long_materials.collect_materials": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", self._short_params(), stop_at="materials")

        mocks["app.services.task.get_video_materials"].assert_called_once()
        mocks["app.services.long_materials.collect_materials"].assert_not_called()

    def test_short_mode_result_has_no_long_video_key(self):
        with _PipelineHarness(
            **{"app.services.task.generate_script": "roteiro curto"}
        ):
            result = tm._run_pipeline("t", self._short_params(), stop_at="script")
        self.assertNotIn("long_video", result)

    def test_short_mode_script_error_still_fails_the_same_way(self):
        with _PipelineHarness(
            **{
                "app.services.task.generate_script": "Error: quota",
                "app.services.task._mark_task_failed": {"state": -1},
            }
        ) as mocks:
            tm._run_pipeline("t", self._short_params(), stop_at="script")
        args = mocks["app.services.task._mark_task_failed"].call_args.args
        self.assertEqual(args[1], "script")


class TestLongModeRouting(unittest.TestCase):
    def _long_params(self, **overrides):
        base = {
            "video_subject": "Ariane 5",
            "video_mode": "long",
            "target_duration_minutes": 10,
        }
        base.update(overrides)
        return VideoParams(**base)

    def _plan(self):
        from app.services import long_video

        return long_video.LongVideoPlan(
            subject="Ariane 5",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(
                    index=1,
                    title="A",
                    brief="",
                    target_seconds=600,
                    script="texto longo",
                    terms=("rocket",),
                )
            ],
        )

    def test_long_mode_uses_the_chaptered_script_path(self):
        with _PipelineHarness(
            **{
                "app.services.long_video.build_long_script": self._plan(),
                "app.services.task.generate_script": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", self._long_params(), stop_at="script")

        mocks["app.services.long_video.build_long_script"].assert_called_once()
        mocks["app.services.task.generate_script"].assert_not_called()

    def test_long_mode_script_result_carries_the_chapter_structure(self):
        with _PipelineHarness(
            **{"app.services.long_video.build_long_script": self._plan()}
        ):
            result = tm._run_pipeline("t", self._long_params(), stop_at="script")
        self.assertIn("long_video", result)
        self.assertEqual(len(result["long_video"]["chapters"]), 1)

    def test_long_mode_uses_per_chapter_terms(self):
        plan = self._plan()
        with _PipelineHarness(
            **{
                "app.services.long_video.build_long_script": plan,
                "app.services.long_video.generate_chapter_terms": plan,
                "app.services.task.generate_terms": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", self._long_params(), stop_at="terms")

        mocks["app.services.long_video.generate_chapter_terms"].assert_called_once()
        mocks["app.services.task.generate_terms"].assert_not_called()

    def test_long_mode_uses_chapter_narration(self):
        plan = self._plan()
        with _PipelineHarness(
            **{
                "app.services.long_video.build_long_script": plan,
                "app.services.long_video.generate_chapter_terms": plan,
                "app.services.long_audio.synthesize_long_narration": (
                    "audio.mp3",
                    600.0,
                    [],
                    [],
                ),
                "app.services.task.generate_audio": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", self._long_params(), stop_at="audio")

        mocks[
            "app.services.long_audio.synthesize_long_narration"
        ].assert_called_once()
        mocks["app.services.task.generate_audio"].assert_not_called()

    def test_long_mode_defaults_are_applied_before_generation(self):
        plan = self._plan()
        captured = {}

        def capture(params, **kwargs):
            captured["aspect"] = params.video_aspect
            captured["clip"] = params.video_clip_duration
            return plan

        with _PipelineHarness(
            **{"app.services.long_video.build_long_script": unittest.mock.DEFAULT}
        ) as mocks:
            mocks["app.services.long_video.build_long_script"].side_effect = capture
            tm._run_pipeline("t", self._long_params(), stop_at="script")

        self.assertEqual(captured["aspect"], "16:9")
        self.assertEqual(captured["clip"], 10)


class TestLongModePreflight(unittest.TestCase):
    def test_duration_above_cap_fails_before_spending_quota(self):
        params = VideoParams(
            video_subject="x", video_mode="long", target_duration_minutes=30
        )
        # Simula uma task antiga ou chamada interna que escapou do Pydantic.
        object.__setattr__(params, "target_duration_minutes", 60)

        with _PipelineHarness(
            **{
                "app.services.task._mark_task_failed": {"state": -1},
                "app.services.long_video.build_long_script": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", params, stop_at="script")

        # O ponto do preflight: falhar ANTES de gastar LLM/TTS/materiais.
        mocks["app.services.long_video.build_long_script"].assert_not_called()
        self.assertEqual(
            mocks["app.services.task._mark_task_failed"].call_args.args[1], "preflight"
        )

    def test_paid_source_is_refused_in_preflight(self):
        params = VideoParams(
            video_subject="x",
            video_mode="long",
            target_duration_minutes=10,
            video_source="wavespeed",
        )
        with _PipelineHarness(
            **{
                "app.services.task._mark_task_failed": {"state": -1},
                "app.services.long_video.build_long_script": unittest.mock.DEFAULT,
            }
        ) as mocks:
            tm._run_pipeline("t", params, stop_at="script")

        mocks["app.services.long_video.build_long_script"].assert_not_called()
        self.assertEqual(
            mocks["app.services.task._mark_task_failed"].call_args.args[1], "preflight"
        )

    def test_custom_audio_above_cap_is_rejected(self):
        from app.services import long_video

        plan = long_video.LongVideoPlan(
            subject="x",
            language="pt-BR",
            target_seconds=600,
            chapters=[
                long_video.Chapter(
                    index=1, title="A", brief="", target_seconds=600, script="t",
                    terms=("a",),
                )
            ],
        )
        params = VideoParams(
            video_subject="x",
            video_mode="long",
            target_duration_minutes=10,
            custom_audio_file="narracao.mp3",
        )
        over_cap = const.LONG_VIDEO_MAX_DURATION_SECONDS + 60

        with _PipelineHarness(
            **{
                "app.services.long_video.build_long_script": plan,
                "app.services.long_video.generate_chapter_terms": plan,
                "app.services.task.generate_audio": ("n.mp3", over_cap, None),
                "app.services.task._mark_task_failed": {"state": -1},
            }
        ) as mocks:
            tm._run_pipeline("t", params, stop_at="audio")

        self.assertEqual(
            mocks["app.services.task._mark_task_failed"].call_args.args[1], "audio"
        )


if __name__ == "__main__":
    unittest.main()
