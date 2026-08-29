import unittest
from pathlib import Path
from unittest.mock import patch

from app.downloader import (
    _configured_encoder_backend,
    _ffmpeg_transcode_arguments,
    _video_format_selector,
    safe_download_name,
)


class SafeDownloadNameTests(unittest.TestCase):
    def test_removes_dangerous_characters(self) -> None:
        self.assertEqual(
            safe_download_name('../../Vídeo\r\n "especial"'),
            "Video especial.mp4",
        )

    def test_has_fallback(self) -> None:
        self.assertEqual(safe_download_name("🔥🔥"), "video.mp4")

    def test_generates_mp3_name(self) -> None:
        self.assertEqual(safe_download_name("Mi canción", "mp3"), "Mi cancion.mp3")

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

    @patch.dict(
        "os.environ",
        {"VIDEO_ENCODER_BACKEND": "auto"},
        clear=True,
    )
    def test_auto_selects_cpu_without_nvidia(self) -> None:
        self.assertEqual(_configured_encoder_backend(), "cpu")

    def test_builds_h264_nvenc_arguments(self) -> None:
        arguments = _ffmpeg_transcode_arguments(
            Path("source.mkv"),
            Path("result.mp4"),
            "h264",
            "nvenc",
        )
        self.assertIn("h264_nvenc", arguments)
        self.assertIn("-cq", arguments)
        self.assertNotIn("-crf", arguments)

    def test_builds_h265_cpu_arguments_with_hvc1_tag(self) -> None:
        arguments = _ffmpeg_transcode_arguments(
            Path("source.mkv"),
            Path("result.mp4"),
            "h265",
            "cpu",
        )
        self.assertIn("libx265", arguments)
        self.assertIn("-crf", arguments)
        self.assertIn("hvc1", arguments)


if __name__ == "__main__":
    unittest.main()
