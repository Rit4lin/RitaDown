import unittest
from pathlib import Path
from unittest.mock import patch

from app.downloader import (
    DownloaderError,
    MediaInfo,
    _configured_encoder_backend,
    _ffmpeg_transcode_arguments,
    _metadata_to_media_info,
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

    def test_normal_video_metadata_is_unchanged(self) -> None:
        media = _metadata_to_media_info(
            {
                "title": "Vídeo directo",
                "extractor_key": "Twitter",
                "duration": 12,
                "formats": [{"vcodec": "avc1", "height": 720}],
            }
        )
        self.assertEqual(media.title, "Vídeo directo")
        self.assertEqual(media.platform, "Twitter")
        self.assertEqual(media.duration, 12)
        self.assertEqual(media.available_heights, (720,))

    def test_unwraps_single_entry_playlist_and_keeps_container_fallbacks(self) -> None:
        media = _metadata_to_media_info(
            {
                "_type": "playlist",
                "title": "Publicación en X",
                "extractor_key": "Twitter",
                "thumbnail": "https://example.com/post.jpg",
                "entries": [
                    {
                        "duration": 9,
                        "formats": [
                            {"vcodec": "avc1", "height": 1080},
                            {"vcodec": "avc1", "height": 720},
                        ],
                    }
                ],
            }
        )
        self.assertEqual(media.title, "Publicación en X")
        self.assertEqual(media.platform, "Twitter")
        self.assertEqual(media.thumbnail, "https://example.com/post.jpg")
        self.assertEqual(media.duration, 9)
        self.assertEqual(media.available_heights, (1080, 720))

    def test_unwraps_single_entry_even_without_playlist_type(self) -> None:
        media = _metadata_to_media_info(
            {
                "title": "Contenedor",
                "extractor_key": "Twitter",
                "entries": [
                    None,
                    {
                        "title": "Vídeo único",
                        "duration": 7,
                        "formats": [{"vcodec": "avc1", "height": 480}],
                    },
                ],
            }
        )
        self.assertEqual(media.title, "Vídeo único")
        self.assertEqual(media.platform, "Twitter")
        self.assertEqual(media.duration, 7)
        self.assertEqual(media.available_heights, (480,))

    def test_rejects_two_real_entries(self) -> None:
        with self.assertRaisesRegex(DownloaderError, "listas, carruseles"):
            _metadata_to_media_info(
                {
                    "_type": "playlist",
                    "entries": [
                        {"id": "one", "formats": [{"vcodec": "avc1", "height": 720}]},
                        {"id": "two", "formats": [{"vcodec": "avc1", "height": 720}]},
                    ],
                }
            )

    def test_invalid_playlist_entries_fail_safely(self) -> None:
        with self.assertRaises(DownloaderError):
            _metadata_to_media_info(
                {
                    "_type": "playlist",
                    "title": "Sin vídeo interpretable",
                    "entries": [None, {}, "invalid"],
                }
            )

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
