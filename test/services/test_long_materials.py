import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models import const
from app.services import long_materials, long_video


def _chapter(index, title="C", terms=("term",), seconds=120.0):
    return long_video.Chapter(
        index=index,
        title=title,
        brief="",
        target_seconds=seconds,
        script=f"texto {index}",
        terms=tuple(terms),
    )


def _audio(index, duration=120.0, offset=0.0):
    from app.services.long_audio import ChapterAudio

    return ChapterAudio(
        index=index, audio_file=f"c{index}.mp3", duration=duration, offset=offset
    )


class TestPaidSourceGuard(unittest.TestCase):
    def test_paid_sources_require_confirmation(self):
        for source in ("wavespeed", "volcengine_seedance", "loomloom"):
            with self.subTest(source=source):
                with self.assertRaises(long_materials.PaidSourceNotConfirmedError):
                    long_materials.ensure_source_is_allowed(source)

    def test_paid_sources_pass_when_confirmed(self):
        long_materials.ensure_source_is_allowed("wavespeed", confirmed=True)

    def test_free_sources_never_need_confirmation(self):
        for source in ("pexels", "pixabay", "coverr", "local"):
            with self.subTest(source=source):
                long_materials.ensure_source_is_allowed(source)

    def test_guard_is_case_insensitive(self):
        with self.assertRaises(long_materials.PaidSourceNotConfirmedError):
            long_materials.ensure_source_is_allowed("  WaveSpeed  ")


class TestClipEstimate(unittest.TestCase):
    def test_clip_count_scales_with_duration(self):
        self.assertEqual(long_materials.estimate_clip_count(2100, 10), 210)
        self.assertEqual(long_materials.estimate_clip_count(2100, 5), 420)

    def test_zero_clip_duration_is_not_a_division_error(self):
        self.assertEqual(long_materials.estimate_clip_count(600, 0), 0)


class TestMaterialBudget(unittest.TestCase):
    def test_consume_reduces_remaining(self):
        budget = long_materials.MaterialBudget(max_downloads=10)
        budget.consume(4)
        self.assertEqual(budget.remaining_downloads, 6)

    def test_remaining_never_goes_negative(self):
        budget = long_materials.MaterialBudget(max_downloads=3)
        budget.consume(99)
        self.assertEqual(budget.remaining_downloads, 0)


class TestCollectMaterials(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.plan = long_video.LongVideoPlan(
            subject="Ariane 5",
            language="pt-BR",
            target_seconds=360,
            chapters=[
                _chapter(1, "A", ("rocket launch",)),
                _chapter(2, "B", ("control room",)),
                _chapter(3, "C", ("engineer desk",)),
            ],
        )
        self.audios = [_audio(1), _audio(2, offset=120), _audio(3, offset=240)]
        self.params = unittest.mock.Mock(
            video_source="pexels",
            video_aspect="16:9",
            video_clip_duration=10,
        )
        self.budget = long_materials.MaterialBudget(throttle_seconds=0)

    def _collect(self, download_side_effect):
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.material.download_videos", side_effect=download_side_effect
        ) as download, unittest.mock.patch(
            "app.services.task_artifacts.patch_script_data", return_value=True
        ):
            paths, warnings = long_materials.collect_materials(
                "t", self.params, self.plan, self.audios, budget=self.budget
            )
        return paths, warnings, download

    def test_each_chapter_uses_its_own_terms(self):
        _, _, download = self._collect(lambda **k: [f"{k['search_terms'][0]}.mp4"])
        used_terms = [call.kwargs["search_terms"] for call in download.call_args_list]
        self.assertEqual(
            used_terms,
            [["rocket launch"], ["control room"], ["engineer desk"]],
        )

    def test_download_is_called_once_per_chapter(self):
        _, _, download = self._collect(lambda **k: ["a.mp4"])
        self.assertEqual(download.call_count, 3)

    def test_requested_duration_follows_measured_chapter_audio(self):
        _, _, download = self._collect(lambda **k: ["a.mp4"])
        for call in download.call_args_list:
            # 120 s de áudio + margem de 15 %
            self.assertAlmostEqual(call.kwargs["audio_duration"], 138.0, delta=0.1)

    def test_material_order_follows_chapter_order(self):
        paths, _, _ = self._collect(lambda **k: [f"{k['search_terms'][0]}.mp4"])
        self.assertEqual(
            paths, ["rocket launch.mp4", "control room.mp4", "engineer desk.mp4"]
        )

    def test_duplicate_paths_are_dropped_across_chapters(self):
        paths, _, _ = self._collect(lambda **k: ["same.mp4"])
        self.assertEqual(paths, ["same.mp4"])

    def test_always_requests_script_order_matching(self):
        _, _, download = self._collect(lambda **k: ["a.mp4"])
        for call in download.call_args_list:
            self.assertTrue(call.kwargs["match_script_order"])

    def test_chapter_failure_does_not_abort_the_run(self):
        def flaky(**kwargs):
            if kwargs["search_terms"] == ["control room"]:
                raise RuntimeError("429 rate limited")
            return [f"{kwargs['search_terms'][0]}.mp4"]

        paths, warnings, download = self._collect(flaky)
        self.assertEqual(download.call_count, 3)
        self.assertIn("rocket launch.mp4", paths)
        self.assertIn("engineer desk.mp4", paths)
        self.assertTrue(warnings)

    def test_missing_material_warns_instead_of_failing(self):
        paths, warnings, _ = self._collect(lambda **k: [])
        self.assertEqual(paths, [])
        self.assertEqual(
            warnings[0]["code"], const.WARNING_LONG_VIDEO_MATERIAL_REPEATED
        )

    def test_budget_exhaustion_stops_downloading(self):
        self.budget = long_materials.MaterialBudget(
            max_downloads=1, throttle_seconds=0
        )
        _, warnings, download = self._collect(
            lambda **k: [f"{k['search_terms'][0]}.mp4"]
        )
        self.assertLess(download.call_count, 3)
        self.assertTrue(
            any(
                w["code"] == const.WARNING_LONG_VIDEO_MATERIAL_REPEATED
                for w in warnings
            )
        )

    def test_chapter_without_terms_borrows_from_neighbour(self):
        self.plan.chapters[1] = _chapter(2, "B", ())
        _, _, download = self._collect(lambda **k: ["a.mp4"])
        second_call_terms = download.call_args_list[1].kwargs["search_terms"]
        self.assertEqual(second_call_terms, ["rocket launch"])

    def test_dropped_chapters_get_no_material(self):
        # O capítulo 3 foi cortado pelo teto de duração: não há áudio para ele.
        self.audios = [_audio(1), _audio(2, offset=120)]
        _, _, download = self._collect(lambda **k: ["a.mp4"])
        self.assertEqual(download.call_count, 2)

    def test_paid_source_is_refused_without_confirmation(self):
        self.params.video_source = "wavespeed"
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ):
            with self.assertRaises(long_materials.PaidSourceNotConfirmedError):
                long_materials.collect_materials(
                    "t", self.params, self.plan, self.audios, budget=self.budget
                )


class TestMaterialSourceAccumulation(unittest.TestCase):
    """
    download_videos sobrescreve material_sources no script.json a cada chamada.
    Sem acumulação, só os créditos do último capítulo sobreviveriam.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.script_file = os.path.join(self.directory, "script.json")

    def _write_script(self, sources):
        with open(self.script_file, "w", encoding="utf-8") as handle:
            json.dump({"script": "x", "material_sources": sources}, handle)

    def test_sources_from_every_chapter_survive(self):
        plan = long_video.LongVideoPlan(
            subject="x",
            language="pt-BR",
            target_seconds=240,
            chapters=[_chapter(1, "A", ("a",)), _chapter(2, "B", ("b",))],
        )
        audios = [_audio(1), _audio(2, offset=120)]
        params = unittest.mock.Mock(
            video_source="pexels", video_aspect="16:9", video_clip_duration=10
        )

        per_chapter = {
            "a": [{"provider": "pexels", "asset_id": "1", "local_file": "a.mp4"}],
            "b": [{"provider": "pexels", "asset_id": "2", "local_file": "b.mp4"}],
        }

        def download(**kwargs):
            # Reproduz o comportamento real: sobrescreve a chave inteira.
            self._write_script(per_chapter[kwargs["search_terms"][0]])
            return [f"{kwargs['search_terms'][0]}.mp4"]

        captured = {}

        def patch_script_data(task_id, **updates):
            captured.update(updates)
            return True

        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.material.download_videos", side_effect=download
        ), unittest.mock.patch(
            "app.services.task_artifacts.patch_script_data",
            side_effect=patch_script_data,
        ):
            long_materials.collect_materials(
                "t",
                params,
                plan,
                audios,
                budget=long_materials.MaterialBudget(throttle_seconds=0),
            )

        asset_ids = sorted(r["asset_id"] for r in captured["material_sources"])
        self.assertEqual(asset_ids, ["1", "2"])

    def test_duplicate_assets_are_deduplicated(self):
        records = [
            {"provider": "pexels", "asset_id": "1", "local_file": "a.mp4"},
            {"provider": "pexels", "asset_id": "1", "local_file": "a.mp4"},
            {"provider": "pixabay", "asset_id": "1", "local_file": "b.mp4"},
        ]
        unique = long_materials._dedupe_material_sources(records)
        self.assertEqual(len(unique), 2)

    def test_records_without_asset_id_dedupe_by_filename(self):
        records = [
            {"provider": "pexels", "local_file": "a.mp4"},
            {"provider": "pexels", "local_file": "a.mp4"},
        ]
        self.assertEqual(len(long_materials._dedupe_material_sources(records)), 1)


class TestCredits(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def _write_sources(self, sources):
        with open(
            os.path.join(self.directory, "script.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump({"material_sources": sources}, handle)

    def test_credits_group_by_provider(self):
        self._write_sources(
            [
                {
                    "provider": "pexels",
                    "asset_id": "1",
                    "creator": {"name": "Ana"},
                    "source_page": "https://pexels.com/a",
                },
                {
                    "provider": "pixabay",
                    "asset_id": "2",
                    "creator": {"name": "Bruno"},
                    "source_page": "https://pixabay.com/b",
                },
            ]
        )
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ):
            block = long_materials.build_credits_block("t")
        self.assertIn("Pexels", block)
        self.assertIn("Pixabay", block)
        self.assertIn("Ana", block)
        self.assertIn("https://pixabay.com/b", block)

    def test_empty_sources_produce_empty_block(self):
        self._write_sources([])
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ):
            self.assertEqual(long_materials.build_credits_block("t"), "")

    def test_missing_script_file_is_not_fatal(self):
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ):
            self.assertEqual(long_materials.build_credits_block("t"), "")

    def test_credits_file_is_written(self):
        self._write_sources(
            [{"provider": "pexels", "asset_id": "1", "creator": {"name": "Ana"}}]
        )
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ):
            target = long_materials.write_credits_file("t")
        self.assertTrue(os.path.isfile(target))


if __name__ == "__main__":
    unittest.main()
