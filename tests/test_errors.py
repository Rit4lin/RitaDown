import unittest

from app.main import _downloader_error_code


class ErrorCodeTests(unittest.TestCase):
    def test_platform_failure_code(self) -> None:
        self.assertEqual(
            _downloader_error_code("La plataforma no pudo procesar ese enlace público."),
            "RDL-2100",
        )

    def test_timeout_code(self) -> None:
        self.assertEqual(
            _downloader_error_code("La operación tardó demasiado y fue cancelada."),
            "RDL-2101",
        )

    def test_ffmpeg_code(self) -> None:
        self.assertEqual(
            _downloader_error_code("FFmpeg no pudo convertir el vídeo a H264."),
            "RDL-3100",
        )

    def test_file_size_code(self) -> None:
        self.assertEqual(
            _downloader_error_code("El archivo supera el límite de 500 MB."),
            "RDL-3201",
        )

    def test_subtitle_code(self) -> None:
        self.assertEqual(
            _downloader_error_code("No se encontraron subtítulos para ese vídeo."),
            "RDL-2200",
        )

    def test_generic_code(self) -> None:
        self.assertEqual(_downloader_error_code("Fallo desconocido"), "RDL-2000")


if __name__ == "__main__":
    unittest.main()
