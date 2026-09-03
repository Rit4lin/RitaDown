import unittest
from pathlib import Path
from unittest.mock import patch

from app.downloader import (
    MediaInfo,
    _configured_encoder_backend,
    _ffmpeg_transcode_arguments,
    _preferred_subtitle_language,
    _subtitle_arguments,
    _video_format_selector,
    _yt_dlp_progress_parser,
    safe_download_name,
)


class DownloaderTests(unittest.TestCase):
    def test_removes_dangerous_characters(self) -> None:
        self.assertEqual(
            safe_download_name('../../Vídeo\r\n "especial"'),
            "Video especial.mp4",
        )

    def test_has_fallback(self) -> None:
        self.assertEqual(safe_download_name("🔥🔥"), "video.mp4")

    def test_generates_audio_and_subtitle_names(self) -> None:
        self.assertEqual(safe_download_name("Mi canción", "mp3"), "Mi cancion.mp3")
        self.assertEqual(safe_download_name("Mi canción", "m4a"), "Mi cancion.m4a")
        self.assertEqual(safe_download_name("Mi canción", "opus"), "Mi cancion.opus")
        self.assertEqual(safe_download_name("Texto", "srt"), "Texto.srt")

    def test_blocks_unknown_extension(self) -> None:
        self.assertEqual(safe_download_name("archivo", "exe"), "archivo.mp4")

    def test_video_quality_selector(self) -> None:
        self.assertEqual(_video_format_selector("original"), "bv*+ba/b")
        self.assertEqual(_video_format_selector("720"), "bv*[height<=720]+ba/b[height<=720]")

    @patch.dict(
        "os.environ",
        {"VIDEO_ENCODER_BACKEND": "auto", "NVIDIA_VISIBLE_DEVICES": "all"},
        clear=True,
    )
    def test_auto_selects_nvenc_when_nvidia_is_visible(self) -> None:
        self.assertEqual(_configured_encoder_backend(), "nvenc")

    @patch.dict("os.environ", {"VIDEO_ENCODER_BACKEND": "auto"}, clear=True)
    def test_auto_selects_cpu_without_nvidia(self) -> None:
        self.assertEqual(_configured_encoder_backend(), "cpu")

    def test_builds_h264_nvenc_arguments(self) -> None:
        arguments = _ffmpeg_transcode_arguments(
            Path("source.mkv"), Path("result.mp4"), "h264", "nvenc"
        )
        self.assertIn("h264_nvenc", arguments)
        self.assertIn("-cq", arguments)
        self.assertNotIn("-crf", arguments)
        self.assertIn("mov_text", arguments)

    def test_builds_h265_cpu_arguments_with_hvc1_tag(self) -> None:
        arguments = _ffmpeg_transcode_arguments(
            Path("source.mkv"), Path("result.mp4"), "h265", "cpu"
        )
        self.assertIn("libx265", arguments)
        self.assertIn("-crf", arguments)
        self.assertIn("hvc1", arguments)

    def test_parses_live_ytdlp_progress(self) -> None:
        updates = []
        parser = _yt_dlp_progress_parser(
            updates.append,
            start=10.0,
            end=90.0,
            stage="Descargando vídeo",
        )
        self.assertIsNotNone(parser)
        parser("RDL_PROGRESS: 50.0%|2.5MiB/s|00:10")
        self.assertEqual(len(updates), 1)
        self.assertAlmostEqual(updates[0].percent, 50.0)
        self.assertEqual(updates[0].speed, "2.5MiB/s")
        self.assertEqual(updates[0].eta, "00:10")

    def test_subtitle_selection(self) -> None:
        media = MediaInfo(
            title="x",
            platform="YouTube",
            duration=10,
            thumbnail=None,
            max_height=1080,
            available_heights=(1080,),
            language="en",
            subtitle_languages=("en", "es"),
            automatic_caption_languages=("en", "es"),
        )
        self.assertEqual(_preferred_subtitle_language(media, "original"), "en")
        self.assertEqual(_preferred_subtitle_language(media, "auto"), "en")
        self.assertEqual(_preferred_subtitle_language(media, "es"), "es.*,es")
        self.assertIn("--embed-subs", _subtitle_arguments(media, "es", embed=True))
        self.assertNotIn("--embed-subs", _subtitle_arguments(media, "es", embed=False))


if __name__ == "__main__":
    unittest.main()
