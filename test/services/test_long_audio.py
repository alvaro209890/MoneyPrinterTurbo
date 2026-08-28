import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models import const
from app.services import long_audio, long_video


def _write(directory, name, body):
    # newline="" desliga a tradução de fim de linha: sem isso, no Windows um
    # "\r\n" do corpo viraria "\r\r\n" em disco e o arquivo deixaria de ser um
    # SRT válido — o teste passaria a medir o próprio helper, não o parser.
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(body)
    return path


class TestSrtTimestamps(unittest.TestCase):
    def test_parse_and_format_round_trip(self):
        for seconds in (0.0, 1.5, 59.999, 600.25, 2099.999, 3723.004):
            with self.subTest(seconds=seconds):
                text = long_audio.format_srt_timestamp(seconds)
                self.assertAlmostEqual(
                    long_audio.parse_srt_timestamp(text), seconds, places=3
                )

    def test_hour_boundary_is_formatted_correctly(self):
        self.assertEqual(long_audio.format_srt_timestamp(3723.004), "01:02:03,004")
        self.assertEqual(long_audio.format_srt_timestamp(34 * 60 + 59.999), "00:34:59,999")

    def test_accepts_dot_as_millisecond_separator(self):
        self.assertAlmostEqual(
            long_audio.parse_srt_timestamp("00:00:01.500"), 1.5, places=3
        )

    def test_short_millisecond_field_is_padded(self):
        # ",5" significa 500 ms, não 5 ms.
        self.assertAlmostEqual(
            long_audio.parse_srt_timestamp("00:00:01,5"), 1.5, places=3
        )

    def test_negative_seconds_clamp_to_zero(self):
        self.assertEqual(long_audio.format_srt_timestamp(-3), "00:00:00,000")

    def test_invalid_timestamp_raises(self):
        with self.assertRaises(ValueError):
            long_audio.parse_srt_timestamp("not a timestamp")


class TestMergeSrtWithOffsets(unittest.TestCase):
    def setUp(self):
        self.tmp = unittest.mock.MagicMock()
        import tempfile

        self.directory = tempfile.mkdtemp()

    def _part(self, name, body):
        return _write(self.directory, name, body)

    def test_offsets_shift_every_cue(self):
        first = self._part(
            "a.srt",
            "1\n00:00:00,000 --> 00:00:02,500\nPrimeiro\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\nSegundo\n",
        )
        second = self._part(
            "b.srt", "1\n00:00:00,000 --> 00:00:03,000\nTerceiro\n"
        )
        output = long_audio.merge_srt_with_offsets(
            [(first, 0.0), (second, 5.0)],
            os.path.join(self.directory, "merged.srt"),
        )
        blocks = long_audio._read_srt_blocks(output)
        self.assertEqual([round(start, 3) for start, _, _ in blocks], [0.0, 2.5, 5.0])
        self.assertEqual([text for _, _, text in blocks], ["Primeiro", "Segundo", "Terceiro"])

    def test_indices_are_renumbered_sequentially(self):
        first = self._part("a.srt", "7\n00:00:00,000 --> 00:00:01,000\nA\n")
        second = self._part("b.srt", "42\n00:00:00,000 --> 00:00:01,000\nB\n")
        output = long_audio.merge_srt_with_offsets(
            [(first, 0.0), (second, 1.0)],
            os.path.join(self.directory, "merged.srt"),
        )
        content = open(output, encoding="utf-8").read()
        self.assertTrue(content.startswith("1\n"))
        self.assertIn("\n2\n", content)
        self.assertNotIn("\n7\n", content)
        self.assertNotIn("\n42\n", content)

    def test_offset_error_would_be_visible_late_not_early(self):
        # Regressão do erro clássico: offset aplicado só ao primeiro bloco.
        # Um capítulo tardio precisa cair exatamente no seu offset.
        parts = []
        for index in range(12):
            parts.append(
                (
                    self._part(
                        f"c{index}.srt",
                        f"1\n00:00:00,000 --> 00:00:02,000\nCap{index}\n",
                    ),
                    index * 100.0,
                )
            )
        output = long_audio.merge_srt_with_offsets(
            parts, os.path.join(self.directory, "merged.srt")
        )
        blocks = long_audio._read_srt_blocks(output)
        self.assertAlmostEqual(blocks[-1][0], 1100.0, places=3)
        self.assertEqual(blocks[-1][2], "Cap11")

    def test_empty_input_returns_empty_string(self):
        self.assertEqual(
            long_audio.merge_srt_with_offsets(
                [], os.path.join(self.directory, "merged.srt")
            ),
            "",
        )

    def test_missing_files_are_skipped(self):
        output = long_audio.merge_srt_with_offsets(
            [(os.path.join(self.directory, "nope.srt"), 0.0)],
            os.path.join(self.directory, "merged.srt"),
        )
        self.assertEqual(output, "")

    def test_malformed_block_is_skipped_not_fatal(self):
        good = self._part("g.srt", "1\n00:00:00,000 --> 00:00:01,000\nOk\n")
        bad = self._part("b.srt", "1\nnao e timestamp\nTexto\n")
        output = long_audio.merge_srt_with_offsets(
            [(good, 0.0), (bad, 1.0)], os.path.join(self.directory, "merged.srt")
        )
        self.assertEqual(len(long_audio._read_srt_blocks(output)), 1)

    def test_crlf_files_are_parsed(self):
        crlf = self._part(
            "crlf.srt", "1\r\n00:00:00,000 --> 00:00:01,000\r\nTexto\r\n"
        )
        output = long_audio.merge_srt_with_offsets(
            [(crlf, 0.0)], os.path.join(self.directory, "merged.srt")
        )
        self.assertEqual(len(long_audio._read_srt_blocks(output)), 1)


class TestFfmpegCommands(unittest.TestCase):
    def test_audio_concat_reencodes_and_never_stream_copies(self):
        # -c copy acumula drift entre os MP3 do Edge TTS, e drift de áudio é
        # exatamente o que destrói a sincronia de legenda num vídeo de 35 min.
        command = long_audio.build_audio_concat_command(
            "list.txt", "out.mp3", ffmpeg_binary="ffmpeg"
        )
        joined = " ".join(command)
        self.assertNotIn("-c copy", joined)
        self.assertNotIn("-c:a copy", joined)
        self.assertIn("libmp3lame", command)

    def test_audio_concat_uses_concat_demuxer(self):
        command = long_audio.build_audio_concat_command(
            "list.txt", "out.mp3", ffmpeg_binary="ffmpeg"
        )
        self.assertIn("concat", command)
        self.assertIn("-safe", command)

    def test_loudnorm_targets_channel_spec(self):
        command = long_audio.build_loudnorm_command(
            "in.mp4", "out.mp4", ffmpeg_binary="ffmpeg"
        )
        joined = " ".join(command)
        self.assertIn("loudnorm=I=-14.0:TP=-1.5", joined)

    def test_loudnorm_copies_video_and_sets_faststart(self):
        command = long_audio.build_loudnorm_command(
            "in.mp4", "out.mp4", ffmpeg_binary="ffmpeg"
        )
        self.assertIn("copy", command)
        self.assertIn("+faststart", command)
        self.assertIn("aac", command)
        self.assertIn("48000", command)

    def test_loudnorm_failure_returns_original_file(self):
        with unittest.mock.patch("subprocess.run") as run:
            run.return_value = unittest.mock.Mock(returncode=1, stderr="boom", stdout="")
            result = long_audio.normalize_loudness("in.mp4", "out.mp4")
        # Um vídeo com loudness fora do alvo ainda é publicável; descartar um
        # render de dezenas de minutos por isso não compensa.
        self.assertEqual(result, "in.mp4")


class TestDurationCap(unittest.TestCase):
    def _audio(self, index, duration):
        return long_audio.ChapterAudio(
            index=index, audio_file=f"c{index}.mp3", duration=duration, offset=0.0
        )

    def test_everything_within_budget_is_kept(self):
        audios = [self._audio(i, 100) for i in range(1, 6)]
        kept, dropped = long_audio.chapters_within_budget(audios)
        self.assertEqual(len(kept), 5)
        self.assertEqual(dropped, [])

    def test_whole_chapters_are_dropped_from_the_end(self):
        cap = const.LONG_VIDEO_MAX_DURATION_SECONDS
        audios = [self._audio(i, cap / 3) for i in range(1, 6)]
        kept, dropped = long_audio.chapters_within_budget(audios)
        self.assertEqual(len(kept), 3)
        self.assertEqual([a.index for a in dropped], [4, 5])

    def test_first_chapter_is_always_kept(self):
        audios = [self._audio(1, const.LONG_VIDEO_MAX_DURATION_SECONDS * 2)]
        kept, dropped = long_audio.chapters_within_budget(audios)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_short_trailing_chapter_is_not_smuggled_in(self):
        # Depois de começar a cortar, não se volta atrás para encaixar um
        # capítulo curto: a continuidade narrativa importa mais que preencher
        # o orçamento.
        cap = const.LONG_VIDEO_MAX_DURATION_SECONDS
        audios = [self._audio(1, cap * 0.9), self._audio(2, cap * 0.5), self._audio(3, 1)]
        kept, dropped = long_audio.chapters_within_budget(audios)
        self.assertEqual([a.index for a in kept], [1])
        self.assertEqual([a.index for a in dropped], [2, 3])


class TestSynthesizeLongNarration(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.directory = tempfile.mkdtemp()
        self.plan = long_video.LongVideoPlan(
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
        self.params = unittest.mock.Mock(
            voice_name="pt-BR-ThalitaMultilingualNeural-Female",
            voice_rate=1.0,
            voice_volume=0.8,
            subtitle_enabled=False,
        )

    def _patches(self, **overrides):
        defaults = {
            "tts": unittest.mock.DEFAULT,
            "duration": 100.0,
        }
        defaults.update(overrides)
        return defaults

    def test_reuses_existing_chapter_audio(self):
        # Idempotência: retomar uma task interrompida não deve refazer TTS.
        existing = os.path.join(self.directory, "chapter-001.mp3")
        _write(self.directory, "chapter-001.mp3", "x")

        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.voice.parse_voice_name", side_effect=lambda v: v
        ), unittest.mock.patch(
            "app.services.voice.get_audio_duration", return_value=100.0
        ), unittest.mock.patch(
            "app.services.voice.tts", return_value=unittest.mock.Mock()
        ) as tts, unittest.mock.patch(
            "app.services.long_audio.concat_audio_files", return_value="audio.mp3"
        ):
            long_audio.synthesize_long_narration("t", self.params, self.plan)

        called_files = [call.kwargs["voice_file"] for call in tts.call_args_list]
        self.assertNotIn(existing, called_files)
        self.assertEqual(tts.call_count, 1)

    def test_retries_only_the_failing_chapter(self):
        attempts = {"count": 0}

        def flaky(**kwargs):
            if "chapter-001" in kwargs["voice_file"]:
                attempts["count"] += 1
                if attempts["count"] < 2:
                    raise RuntimeError("transient")
            return unittest.mock.Mock()

        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.voice.parse_voice_name", side_effect=lambda v: v
        ), unittest.mock.patch(
            "app.services.voice.get_audio_duration", return_value=100.0
        ), unittest.mock.patch(
            "app.services.voice.tts", side_effect=flaky
        ) as tts, unittest.mock.patch(
            "app.services.long_audio.concat_audio_files", return_value="audio.mp3"
        ):
            long_audio.synthesize_long_narration("t", self.params, self.plan)

        # Capítulo 1 tentado duas vezes, capítulo 2 apenas uma.
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(tts.call_count, 3)

    def test_offsets_accumulate_measured_durations(self):
        durations = {"chapter-001.mp3": 120.0, "chapter-002.mp3": 80.0}

        def measured(target):
            name = os.path.basename(str(target))
            return durations.get(name, 200.0)

        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.voice.parse_voice_name", side_effect=lambda v: v
        ), unittest.mock.patch(
            "app.services.voice.get_audio_duration", side_effect=measured
        ), unittest.mock.patch(
            "app.services.voice.tts", return_value=unittest.mock.Mock()
        ), unittest.mock.patch(
            "app.services.long_audio.concat_audio_files", return_value="audio.mp3"
        ):
            _, _, chapters, _ = long_audio.synthesize_long_narration(
                "t", self.params, self.plan
            )

        self.assertEqual(chapters[0].offset, 0.0)
        self.assertEqual(chapters[1].offset, 120.0)

    def test_persistent_failure_raises_with_chapter_context(self):
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.voice.parse_voice_name", side_effect=lambda v: v
        ), unittest.mock.patch(
            "app.services.voice.get_audio_duration", return_value=0.0
        ), unittest.mock.patch(
            "app.services.voice.tts", return_value=None
        ):
            with self.assertRaises(RuntimeError) as ctx:
                long_audio.synthesize_long_narration("t", self.params, self.plan)
        self.assertIn("chapter 1", str(ctx.exception))

    def test_progress_reaches_one(self):
        seen = []
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.voice.parse_voice_name", side_effect=lambda v: v
        ), unittest.mock.patch(
            "app.services.voice.get_audio_duration", return_value=100.0
        ), unittest.mock.patch(
            "app.services.voice.tts", return_value=unittest.mock.Mock()
        ), unittest.mock.patch(
            "app.services.long_audio.concat_audio_files", return_value="audio.mp3"
        ):
            long_audio.synthesize_long_narration(
                "t", self.params, self.plan, progress_cb=seen.append
            )
        self.assertEqual(seen[-1], 1.0)

    def test_over_cap_emits_truncation_warning(self):
        cap = const.LONG_VIDEO_MAX_DURATION_SECONDS
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.voice.parse_voice_name", side_effect=lambda v: v
        ), unittest.mock.patch(
            "app.services.voice.get_audio_duration", return_value=cap * 0.8
        ), unittest.mock.patch(
            "app.services.voice.tts", return_value=unittest.mock.Mock()
        ), unittest.mock.patch(
            "app.services.long_audio.concat_audio_files", return_value="audio.mp3"
        ):
            _, _, kept, warnings = long_audio.synthesize_long_narration(
                "t", self.params, self.plan
            )
        self.assertEqual(len(kept), 1)
        self.assertEqual(warnings[0]["code"], const.WARNING_LONG_VIDEO_TRUNCATED)

    def test_voice_volume_is_forwarded_to_each_chapter(self):
        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=self.directory
        ), unittest.mock.patch(
            "app.services.voice.parse_voice_name", side_effect=lambda value: value
        ), unittest.mock.patch(
            "app.services.voice.get_audio_duration", return_value=100.0
        ), unittest.mock.patch(
            "app.services.voice.tts", return_value=unittest.mock.Mock()
        ) as tts, unittest.mock.patch(
            "app.services.long_audio.concat_audio_files", return_value="audio.mp3"
        ):
            long_audio.synthesize_long_narration("t", self.params, self.plan)

        for call in tts.call_args_list:
            self.assertEqual(call.kwargs["voice_volume"], 0.8)


class TestExternalAudioChapterTimings(unittest.TestCase):
    def test_distributes_external_audio_and_preserves_total_duration(self):
        plan = long_video.LongVideoPlan(
            subject="x",
            language="pt-BR",
            target_seconds=100,
            chapters=[
                long_video.Chapter(1, "A", "", 25, script="a"),
                long_video.Chapter(2, "B", "", 75, script="b"),
            ],
        )
        timings = long_audio.chapter_timings_for_audio(plan, "voice.wav", 200.0)
        self.assertEqual([item.duration for item in timings], [50.0, 150.0])
        self.assertEqual([item.offset for item in timings], [0.0, 50.0])
        self.assertAlmostEqual(sum(item.duration for item in timings), 200.0)


class TestLongWhisperSubtitle(unittest.TestCase):
    def test_transcribes_once_and_corrects_each_chapter_slice(self):
        import tempfile

        directory = tempfile.mkdtemp()
        chapters = [
            long_audio.ChapterAudio(1, "audio.mp3", 10.0, 0.0, script="first"),
            long_audio.ChapterAudio(2, "audio.mp3", 10.0, 10.0, script="second"),
        ]

        def create_once(audio_file, subtitle_file):
            _write(
                directory,
                os.path.basename(subtitle_file),
                "1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n"
                "2\n00:00:11,000 --> 00:00:12,000\nsecond\n\n",
            )

        with unittest.mock.patch(
            "app.utils.utils.task_dir", return_value=directory
        ), unittest.mock.patch(
            "app.services.subtitle.create", side_effect=create_once
        ) as create, unittest.mock.patch(
            "app.services.subtitle.correct"
        ) as correct:
            output = long_audio.build_long_whisper_subtitle(
                "task", "audio.mp3", chapters
            )

        create.assert_called_once()
        self.assertEqual(correct.call_count, 2)
        with open(output, "r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("00:00:01,000 --> 00:00:02,000", content)
        self.assertIn("00:00:11,000 --> 00:00:12,000", content)


if __name__ == "__main__":
    unittest.main()
