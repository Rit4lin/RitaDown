from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024
VideoQuality = Literal["original", "2160", "1440", "1080", "720", "480", "360"]
OutputFormat = Literal["mp4", "mp3"]
VideoCodec = Literal["original", "h264", "h265"]
AudioQuality = Literal["320", "256", "192", "128"]
EncoderBackend = Literal["cpu", "nvenc"]


class DownloaderError(RuntimeError):
    """Fallo controlado que no expone detalles internos."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    title: str
    platform: str
    duration: int | None
    thumbnail: str | None
    max_height: int | None
    available_heights: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "platform": self.platform,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "max_height": self.max_height,
            "available_heights": list(self.available_heights),
        }


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    path: Path
    download_name: str
    media_type: str
    job_directory: Path


def _base_arguments() -> list[str]:
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-cache-dir",
        "--no-warnings",
        "--no-progress",
        "--socket-timeout",
        "15",
        "--retries",
        "2",
        "--fragment-retries",
        "2",
    ]


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _run_process(
    arguments: list[str],
    *,
    timeout_seconds: int,
    stage: str,
    failure_message: str,
) -> bytes:
    child_environment = os.environ.copy()
    child_environment["PYTHONUNBUFFERED"] = "1"
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_environment,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        await _terminate_process(process)
        logger.warning("Tiempo agotado durante %s", stage)
        raise DownloaderError("La operación tardó demasiado y fue cancelada.") from exc

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if len(stderr_text) > 8000:
            stderr_text = f"{stderr_text[:8000]}\n...[stderr truncado]"
        logger.warning(
            "El proceso falló durante %s (código %s). stderr: %s",
            stage,
            process.returncode,
            stderr_text or "<vacío>",
        )
        raise DownloaderError(failure_message)
    return stdout


async def _run_yt_dlp(arguments: list[str], *, timeout_seconds: int, stage: str) -> bytes:
    return await _run_process(
        arguments,
        timeout_seconds=timeout_seconds,
        stage=stage,
        failure_message=(
            "La plataforma no pudo procesar ese enlace público. "
            "Comprueba el enlace o inténtalo más tarde."
        ),
    )


def _metadata_to_media_info(metadata: dict[str, Any]) -> MediaInfo:
    if metadata.get("_type") in {"playlist", "multi_video"} or metadata.get("entries"):
        raise DownloaderError("No se admiten listas, carruseles ni publicaciones con varios vídeos.")

    formats = metadata.get("formats") or []
    heights = {
        int(value)
        for item in formats
        if isinstance(item, dict)
        and item.get("vcodec") != "none"
        and isinstance((value := item.get("height")), (int, float))
        and value > 0
    }
    ordered_heights = tuple(sorted(heights, reverse=True))
    duration_value = metadata.get("duration")
    duration = int(duration_value) if isinstance(duration_value, (int, float)) else None
    thumbnail = metadata.get("thumbnail")
    if not isinstance(thumbnail, str) or not thumbnail.startswith("https://"):
        thumbnail = None
    title = metadata.get("title")
    platform = metadata.get("extractor_key") or metadata.get("extractor")
    return MediaInfo(
        title=str(title)[:200] if title else "Vídeo sin título",
        platform=str(platform)[:80] if platform else "Plataforma compatible",
        duration=duration,
        thumbnail=thumbnail,
        max_height=ordered_heights[0] if ordered_heights else None,
        available_heights=ordered_heights,
    )


async def analyze_url(url: str, *, timeout_seconds: int = 60) -> MediaInfo:
    arguments = _base_arguments() + [
        "--dump-single-json",
        "--skip-download",
        "--yes-playlist",
        "--playlist-end",
        "2",
        url,
    ]
    stdout = await _run_yt_dlp(arguments, timeout_seconds=timeout_seconds, stage="análisis")
    try:
        metadata = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DownloaderError("La plataforma devolvió metadatos no válidos.") from exc
    if not isinstance(metadata, dict):
        raise DownloaderError("No se pudo interpretar la información del vídeo.")
    return _metadata_to_media_info(metadata)


def _video_format_selector(quality: VideoQuality) -> str:
    height_filter = "" if quality == "original" else f"[height<={quality}]"
    return f"bv*{height_filter}+ba/b{height_filter}"


def _configured_encoder_backend() -> EncoderBackend:
    configured = os.getenv("VIDEO_ENCODER_BACKEND", "auto").strip().lower()
    if configured == "cpu":
        return "cpu"
    if configured == "nvenc":
        return "nvenc"
    if configured != "auto":
        logger.warning(
            "VIDEO_ENCODER_BACKEND=%r no es válido; se usará detección automática.",
            configured,
        )

    visible_devices = os.getenv("NVIDIA_VISIBLE_DEVICES", "").strip().lower()
    if visible_devices and visible_devices not in {"none", "void"}:
        return "nvenc"
    return "cpu"


def _video_encoder_arguments(
    codec: VideoCodec,
    backend: EncoderBackend,
) -> list[str]:
    if backend == "nvenc":
        encoder = "h264_nvenc" if codec == "h264" else "hevc_nvenc"
        constant_quality = "23" if codec == "h264" else "28"
        arguments = [
            "-c:v",
            encoder,
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            constant_quality,
            "-b:v",
            "0",
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        encoder = "libx264" if codec == "h264" else "libx265"
        crf = "23" if codec == "h264" else "28"
        preset = "veryfast" if codec == "h264" else "fast"
        arguments = [
            "-c:v",
            encoder,
            "-preset",
            preset,
            "-crf",
            crf,
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
        ]

    if codec == "h265":
        arguments += ["-tag:v", "hvc1"]
    return arguments


def _ffmpeg_transcode_arguments(
    source: Path,
    result: Path,
    codec: VideoCodec,
    backend: EncoderBackend,
) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map_metadata",
        "-1",
        *_video_encoder_arguments(codec, backend),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(result),
    ]


def safe_download_name(title: str, extension: str = "mp4") -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", normalized)
    cleaned = re.sub(r"[\s._-]+", " ", cleaned).strip(" .")[:80]
    safe_extension = "mp3" if extension == "mp3" else "mp4"
    fallback = "audio" if safe_extension == "mp3" else "video"
    return f"{cleaned or fallback}.{safe_extension}"


def _ensure_size_limit(path: Path) -> None:
    if path.stat().st_size > MAX_FILE_SIZE_BYTES:
        raise DownloaderError("El archivo supera el límite de 500 MB.")


def _single_file_with_extension(job_directory: Path, extension: str) -> Path:
    matches = [
        path
        for path in job_directory.iterdir()
        if path.is_file() and path.suffix.lower() == f".{extension}"
    ]
    if len(matches) != 1:
        raise DownloaderError(f"No se pudo generar correctamente el archivo {extension.upper()}.")
    return matches[0]


def _largest_media_file(job_directory: Path) -> Path:
    candidates = [
        path
        for path in job_directory.iterdir()
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".json"}
    ]
    if not candidates:
        raise DownloaderError("No se encontró el vídeo descargado para convertirlo.")
    return max(candidates, key=lambda path: path.stat().st_size)


async def _download_mp3(
    url: str,
    audio_quality: AudioQuality,
    output_template: str,
    job_directory: Path,
    timeout_seconds: int,
) -> Path:
    arguments = _base_arguments() + [
        "--no-playlist",
        "--max-filesize",
        "500M",
        "--format",
        "ba/b",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        f"{audio_quality}K",
        "--output",
        output_template,
        url,
    ]
    await _run_yt_dlp(arguments, timeout_seconds=timeout_seconds, stage="descarga MP3")
    result = _single_file_with_extension(job_directory, "mp3")
    _ensure_size_limit(result)
    return result


async def _download_original_video(
    url: str,
    quality: VideoQuality,
    output_template: str,
    job_directory: Path,
    timeout_seconds: int,
) -> Path:
    arguments = _base_arguments() + [
        "--no-playlist",
        "--max-filesize",
        "500M",
        "--format",
        _video_format_selector(quality),
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        "--output",
        output_template,
        url,
    ]
    await _run_yt_dlp(arguments, timeout_seconds=timeout_seconds, stage="descarga MP4")
    result = _single_file_with_extension(job_directory, "mp4")
    _ensure_size_limit(result)
    return result


async def _download_and_transcode_video(
    url: str,
    quality: VideoQuality,
    codec: VideoCodec,
    output_template: str,
    job_directory: Path,
    job_id: str,
    timeout_seconds: int,
) -> Path:
    arguments = _base_arguments() + [
        "--no-playlist",
        "--max-filesize",
        "500M",
        "--format",
        _video_format_selector(quality),
        "--merge-output-format",
        "mkv",
        "--output",
        output_template,
        url,
    ]
    await _run_yt_dlp(arguments, timeout_seconds=timeout_seconds, stage="descarga para conversión")
    source = _largest_media_file(job_directory)
    _ensure_size_limit(source)
    result = job_directory / f"{job_id}_convertido.mp4"
    preferred_backend = _configured_encoder_backend()
    backends: tuple[EncoderBackend, ...] = (
        ("nvenc", "cpu") if preferred_backend == "nvenc" else ("cpu",)
    )

    for attempt, backend in enumerate(backends):
        logger.info(
            "Iniciando conversión %s mediante %s.",
            codec.upper(),
            backend.upper(),
        )
        try:
            await _run_process(
                _ffmpeg_transcode_arguments(source, result, codec, backend),
                timeout_seconds=timeout_seconds,
                stage=f"conversión {codec.upper()} ({backend})",
                failure_message=(
                    f"FFmpeg no pudo convertir el vídeo a {codec.upper()} "
                    f"mediante {backend.upper()}."
                ),
            )
            break
        except DownloaderError:
            result.unlink(missing_ok=True)
            if attempt + 1 >= len(backends):
                raise DownloaderError(
                    f"FFmpeg no pudo convertir el vídeo a {codec.upper()}. "
                    "Prueba con el códec original o una calidad inferior."
                ) from None
            logger.warning(
                "NVENC falló durante la conversión %s; se reintentará mediante CPU.",
                codec.upper(),
            )

    _ensure_size_limit(result)
    return result


async def download_url(
    url: str,
    quality: VideoQuality,
    output_format: OutputFormat,
    video_codec: VideoCodec,
    audio_quality: AudioQuality,
    download_root: Path,
    *,
    extract_timeout_seconds: int = 60,
    download_timeout_seconds: int = 1800,
) -> DownloadedFile:
    media_info = await analyze_url(url, timeout_seconds=extract_timeout_seconds)
    job_id = uuid.uuid4().hex
    job_directory = download_root / job_id
    job_directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    output_template = str(job_directory / f"{job_id}.%(ext)s")

    try:
        if output_format == "mp3":
            result = await _download_mp3(
                url,
                audio_quality,
                output_template,
                job_directory,
                download_timeout_seconds,
            )
            extension = "mp3"
            media_type = "audio/mpeg"
        elif video_codec == "original":
            result = await _download_original_video(
                url,
                quality,
                output_template,
                job_directory,
                download_timeout_seconds,
            )
            extension = "mp4"
            media_type = "video/mp4"
        else:
            result = await _download_and_transcode_video(
                url,
                quality,
                video_codec,
                output_template,
                job_directory,
                job_id,
                download_timeout_seconds,
            )
            extension = "mp4"
            media_type = "video/mp4"

        return DownloadedFile(
            path=result,
            download_name=safe_download_name(media_info.title, extension),
            media_type=media_type,
            job_directory=job_directory,
        )
    except BaseException:
        from app.cleanup import remove_job_directory

        remove_job_directory(job_directory)
        raise
