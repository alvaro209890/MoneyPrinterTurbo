import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models import const
from app.services import long_render


class TestNormalizeFilter(unittest.TestCase):
    def test_filter_is_deterministic(self):
        first = long_render.build_scale_pad_filter(1920, 1080)
        second = long_render.build_scale_pad_filter(1920, 1080)
        self.assertEqual(first, second)

    def test_filter_preserves_aspect_and_pads(self):
        value = long_render.build_scale_pad_filter(1920, 1080)
        self.assertIn("force_original_aspect_ratio=decrease", value)
        self.assertIn("pad=1920:1080", value)

    def test_filter_pins_sar(self):
        # SAR inconsistente faz o concat demuxer produzir vídeo esticado.
        self.assertIn("setsar=1", long_render.build_scale_pad_filter(1920, 1080))

    def test_filter_pins_fps(self):
        self.assertIn("fps=30", long_render.build_scale_pad_filter(1920, 1080))


class TestClipNormalizeCommand(unittest.TestCase):
    def _command(self, **kwargs):
        return long_render.build_clip_normalize_command(
            "in.mp4", "out.mp4", 1920, 1080, ffmpeg_binary="ffmpeg", **kwargs
        )

    def test_all_clips_share_identical_output_parameters(self):
        # O concat demuxer exige parâmetros idênticos entre todos os arquivos.
        first = long_render.build_clip_normalize_command(
            "a.mp4", "o1.mp4", 1920, 1080, ffmpeg_binary="ffmpeg", duration=3
        )
        second = long_render.build_clip_normalize_command(
            "b.mov", "o2.mp4", 1920, 1080, ffmpeg_binary="ffmpeg", duration=7
        )
        self.assertEqual(first[first.index("-vf") + 1], second[second.index("-vf") + 1])
        self.assertEqual(
            first[first.index("-pix_fmt") + 1], second[second.index("-pix_fmt") + 1]
        )
        self.assertEqual(first[first.index("-r") + 1], second[second.index("-r") + 1])

    def test_source_audio_is_dropped(self):
        # A narração é a única fonte de áudio; trilha do estoque entraria como ruído.
        self.assertIn("-an", self._command())

    def test_pixel_format_is_pinned(self):
        command = self._command()
        self.assertEqual(
            command[command.index("-pix_fmt") + 1], long_render.NORMALIZED_PIX_FMT
        )

    def test_seek_and_duration_are_applied(self):
        command = self._command(start=2.5, duration=4.0)
        self.assertEqual(command[command.index("-ss") + 1], "2.500")
        self.assertEqual(command[command.index("-t") + 1], "4.000")

    def test_seek_precedes_input_for_fast_seeking(self):
        command = self._command(start=2.5)
        self.assertLess(command.index("-ss"), command.index("-i"))

    def test_clip_speed_is_applied_before_scaling(self):
        command = self._command(clip_speed=2.0)
        video_filter = command[command.index("-vf") + 1]
        self.assertTrue(video_filter.startswith("setpts="))
        self.assertLess(video_filter.index("setpts"), video_filter.index("scale"))

    def test_speed_one_adds_no_setpts(self):
        self.assertNotIn("setpts", self._command(clip_speed=1.0)[
            self._command(clip_speed=1.0).index("-vf") + 1
        ])


class TestTimelinePlanning(unittest.TestCase):
    def test_timeline_covers_requested_duration(self):
        timeline = long_render.plan_clip_timeline(["a.mp4", "b.mp4"], 100.0, 10)
        self.assertAlmostEqual(sum(slot["duration"] for slot in timeline), 100.0, places=3)

    def test_last_slot_is_trimmed_not_overshooting(self):
        timeline = long_render.plan_clip_timeline(["a.mp4"], 25.0, 10)
        self.assertAlmostEqual(timeline[-1]["duration"], 5.0, places=3)

    def test_sources_rotate_instead_of_repeating_consecutively(self):
        timeline = long_render.plan_clip_timeline(["a.mp4", "b.mp4", "c.mp4"], 60.0, 10)
        paths = [slot["path"] for slot in timeline]
        consecutive = [
            index for index in range(1, len(paths)) if paths[index] == paths[index - 1]
        ]
        self.assertEqual(consecutive, [])

    def test_share_cap_limits_reuse_when_material_allows(self):
        many = [f"m{index}.mp4" for index in range(60)]
        timeline = long_render.plan_clip_timeline(many, 600.0, 10, max_source_share=0.05)
        counts = {}
        for slot in timeline:
            counts[slot["path"]] = counts.get(slot["path"], 0) + 1
        self.assertLessEqual(max(counts.values()), 4)

    def test_scarce_material_is_reused_rather_than_failing(self):
        # Melhor repetir com aviso do que falhar um render de 30 minutos.
        timeline = long_render.plan_clip_timeline(["only.mp4"], 300.0, 10)
        self.assertAlmostEqual(sum(s["duration"] for s in timeline), 300.0, places=3)

    def test_empty_material_returns_empty_timeline(self):
        self.assertEqual(long_render.plan_clip_timeline([], 100.0, 10), [])

    def test_zero_clip_duration_is_not_a_division_error(self):
        self.assertEqual(long_render.plan_clip_timeline(["a.mp4"], 100.0, 0), [])


class TestVerificationCommands(unittest.TestCase):
    def test_decode_check_reports_errors_only(self):
        command = long_render.build_decode_check_command("v.mp4", ffmpeg_binary="ffmpeg")
        self.assertIn("-v", command)
        self.assertIn("error", command)
        self.assertIn("null", command)

    def test_decode_check_fails_on_stderr_output(self):
        with unittest.mock.patch("subprocess.run") as run:
            run.return_value = unittest.mock.Mock(
                returncode=0, stderr="corrupt frame", stdout=""
            )
            self.assertFalse(long_render.decode_check("v.mp4"))

    def test_decode_check_passes_on_clean_run(self):
        with unittest.mock.patch("subprocess.run") as run:
            run.return_value = unittest.mock.Mock(returncode=0, stderr="", stdout="")
            self.assertTrue(long_render.decode_check("v.mp4"))


class TestVerifyLongVideo(unittest.TestCase):
    GOOD_PROBE = {
        "format": {"duration": "600.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1920,
                "height": 1080,
            },
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
        ],
    }

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "final.mp4")
        with open(self.path, "wb") as handle:
            handle.write(b"\x00" * 64 + b"moov" + b"\x00" * 64 + b"mdat")

    def _verify(self, probe=None, **kwargs):
        with unittest.mock.patch(
            "app.services.long_render._probe", return_value=probe or self.GOOD_PROBE
        ), unittest.mock.patch(
            "app.services.long_render.decode_check", return_value=True
        ):
            return long_render.verify_long_video(self.path, **kwargs)

    def test_good_file_passes(self):
        report = self._verify()
        self.assertTrue(report.passed, report.problems)
        self.assertTrue(report.faststart)
        self.assertTrue(report.within_cap)

    def test_missing_file_is_reported(self):
        report = long_render.verify_long_video(
            os.path.join(self.directory, "nope.mp4")
        )
        self.assertFalse(report.passed)

    def test_duration_above_cap_fails(self):
        probe = dict(self.GOOD_PROBE)
        probe["format"] = {"duration": str(const.LONG_VIDEO_MAX_DURATION_SECONDS + 5)}
        report = self._verify(probe=probe)
        self.assertFalse(report.passed)
        self.assertFalse(report.within_cap)

    def test_duration_at_cap_passes(self):
        probe = dict(self.GOOD_PROBE)
        probe["format"] = {"duration": str(const.LONG_VIDEO_MAX_DURATION_SECONDS)}
        report = self._verify(probe=probe)
        self.assertTrue(report.within_cap)

    def test_duration_drift_from_expected_is_reported(self):
        report = self._verify(expected_duration=500.0)
        self.assertFalse(report.passed)
        self.assertTrue(any("differs" in p for p in report.problems))

    def test_missing_faststart_fails(self):
        with open(self.path, "wb") as handle:
            handle.write(b"\x00" * 64 + b"mdat" + b"\x00" * 64 + b"moov")
        report = self._verify()
        self.assertFalse(report.passed)
        self.assertFalse(report.faststart)

    def test_missing_audio_stream_fails(self):
        probe = {
            "format": {"duration": "600.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv420p",
                    "width": 1920,
                    "height": 1080,
                }
            ],
        }
        report = self._verify(probe=probe)
        self.assertFalse(report.passed)

    def test_wrong_pixel_format_fails(self):
        probe = {
            "format": {"duration": "600.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv444p",
                    "width": 1920,
                    "height": 1080,
                },
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"},
            ],
        }
        report = self._verify(probe=probe)
        self.assertFalse(report.passed)

    def test_report_serializes_for_task_state(self):
        payload = self._verify().to_dict()
        for key in ("duration_seconds", "faststart", "passed", "problems"):
            self.assertIn(key, payload)


class TestRenderLongVideo(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.params = unittest.mock.Mock(
            video_aspect="16:9",
            video_clip_duration=10,
            video_clip_speed=1.0,
            n_threads=4,
        )

    def _render(self, materials=("a.mp4", "b.mp4"), audio_duration=100.0):
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.long_render.normalize_clip",
            side_effect=lambda src, dst, *a, **k: dst,
        ) as normalize, unittest.mock.patch(
            "app.services.video.concat_video_clips_with_ffmpeg", return_value="h264"
        ) as concat:
            output = long_render.render_long_video(
                "t",
                self.params,
                list(materials),
                audio_duration,
                os.path.join(self.directory, "combined.mp4"),
            )
        return output, normalize, concat

    def test_uses_the_ffmpeg_concat_path(self):
        _, _, concat = self._render()
        concat.assert_called_once()

    def test_duration_cap_is_passed_to_concat(self):
        _, _, concat = self._render(audio_duration=100.0)
        self.assertLessEqual(
            concat.call_args.kwargs["max_duration"],
            const.LONG_VIDEO_MAX_DURATION_SECONDS,
        )

    def test_cap_clamps_even_when_audio_is_longer(self):
        _, _, concat = self._render(audio_duration=99_999.0)
        self.assertEqual(
            concat.call_args.kwargs["max_duration"],
            const.LONG_VIDEO_MAX_DURATION_SECONDS,
        )

    def test_progress_reaches_one(self):
        seen = []
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.long_render.normalize_clip",
            side_effect=lambda src, dst, *a, **k: dst,
        ), unittest.mock.patch(
            "app.services.video.concat_video_clips_with_ffmpeg", return_value="h264"
        ):
            long_render.render_long_video(
                "t",
                self.params,
                ["a.mp4"],
                100.0,
                os.path.join(self.directory, "combined.mp4"),
                progress_cb=seen.append,
            )
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen))

    def test_broken_clip_is_skipped_not_fatal(self):
        calls = {"count": 0}

        def flaky(src, dst, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("corrupt")
            return dst

        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.long_render.normalize_clip", side_effect=flaky
        ), unittest.mock.patch(
            "app.services.video.concat_video_clips_with_ffmpeg", return_value="h264"
        ) as concat:
            long_render.render_long_video(
                "t",
                self.params,
                ["a.mp4", "b.mp4"],
                100.0,
                os.path.join(self.directory, "combined.mp4"),
            )
        self.assertTrue(concat.called)

    def test_no_material_raises(self):
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ):
            with self.assertRaises(RuntimeError):
                long_render.render_long_video(
                    "t", self.params, [], 100.0, "out.mp4"
                )

    def test_all_clips_failing_raises(self):
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.long_render.normalize_clip",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                long_render.render_long_video(
                    "t", self.params, ["a.mp4"], 100.0, "out.mp4"
                )


class TestWorkdirCleanup(unittest.TestCase):
    def test_cleanup_removes_intermediate_clips(self):
        directory = tempfile.mkdtemp()
        work = os.path.join(directory, "normalized")
        os.makedirs(work)
        with open(os.path.join(work, "clip-00000.mp4"), "w") as handle:
            handle.write("x")
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=directory
        ):
            long_render.cleanup_render_workdir("t")
        self.assertFalse(os.path.isdir(work))

    def test_cleanup_is_safe_when_absent(self):
        directory = tempfile.mkdtemp()
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=directory
        ):
            long_render.cleanup_render_workdir("t")


if __name__ == "__main__":
    unittest.main()
