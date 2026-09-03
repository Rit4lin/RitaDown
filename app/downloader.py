from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal


logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024
VideoQuality = Literal["original", "2160", "1440", "1080", "720", "480", "360"]
OutputFormat = Literal["mp4", "mp3", "m4a", "opus", "audio_original", "srt"]
VideoCodec = Literal["original", "h264", "h265"]
AudioQuality = Literal["320", "256", "192", "128"]
SubtitleMode = Literal["none", "es", "original", "auto"]
EncoderBackend = Literal["cpu", "nvenc"]
LineCallback = Callable[[str], None]
ProgressCallback = Callable[["DownloadProgress"], None]


class DownloaderError(RuntimeError):
    """Fallo controlado que no expone detalles internos."""


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    percent: float
    stage: str
    speed: str | None = None
    eta: str | None = None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    title: str
    platform: str
    duration: int | None
    thumbnail: str | None
    max_height: int | None
    available_heights: tuple[int, ...]
    language: str | None
    subtitle_languages: tuple[str, ...]
    automatic_caption_languages: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "platform": self.platform,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "max_height": self.max_height,
            "available_heights": list(self.available_heights),
            "language": self.language,
            "subtitle_languages": list(self.subtitle_languages),
            "automatic_caption_languages": list(self.automatic_caption_languages),
            "has_subtitles": bool(self.subtitle_languages or self.automatic_caption_languages),
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
        "--socket-timeout",
        "15",
        "--retries",
        "2",
        "--fragment-retries",
        "2",
    ]


def _progress_arguments() -> list[str]:
    return [
        "--newline",
        "--progress",
        "--progress-template",
        "download:RDL_PROGRESS:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
    ]


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _read_stream(
    stream: asyncio.StreamReader | None,
    buffer: bytearray,
    line_callback: LineCallback | None,
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        buffer.extend(line)
        if line_callback is not None:
            line_callback(line.decode("utf-8", errors="replace").rstrip())


async def _run_process(
    arguments: list[str],
    *,
    timeout_seconds: int,
    stage: str,
    failure_message: str,
    line_callback: LineCallback | None = None,
) -> bytes:
    child_environment = os.environ.copy()
    child_environment["PYTHONUNBUFFERED"] = "1"
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_environment,
    )
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    stdout_task = asyncio.create_task(
        _read_stream(process.stdout, stdout_buffer, line_callback)
    )
    stderr_task = asyncio.create_task(
        _read_stream(process.stderr, stderr_buffer, line_callback)
    )
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        await asyncio.gather(stdout_task, stderr_task)
    except TimeoutError as exc:
        await _terminate_process(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        logger.warning("Tiempo agotado durante %s", stage)
        raise DownloaderError("La operación tardó demasiado y fue cancelada.") from exc

    if process.returncode != 0:
        stderr_text = stderr_buffer.decode("utf-8", errors="replace").strip()
        if len(stderr_text) > 8000:
            stderr_text = f"{stderr_text[:8000]}\n...[stderr truncado]"
        logger.warning(
            "El proceso falló durante %s (código %s). stderr: %s",
            stage,
            process.returncode,
            stderr_text or "<vacío>",
        )
        raise DownloaderError(failure_message)
    return bytes(stdout_buffer)


def _emit_progress(
    callback: ProgressCallback | None,
    percent: float,
    stage: str,
    *,
    speed: str | None = None,
    eta: str | None = None,
) -> None:
    if callback is None:
        return
    callback(
        DownloadProgress(
            percent=max(0.0, min(100.0, percent)),
            stage=stage,
            speed=speed or None,
            eta=eta or None,
        )
    )


def _parse_percent(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.,]", "", value).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _yt_dlp_progress_parser(
    callback: ProgressCallback | None,
    *,
    start: float,
    end: float,
    stage: str,
) -> LineCallback | None:
    if callback is None:
        return None

    def handle(line: str) -> None:
        marker = "RDL_PROGRESS:"
        if marker not in line:
            return
        payload = line.split(marker, 1)[1]
        parts = payload.split("|", 2)
        raw_percent = _parse_percent(parts[0])
        if raw_percent is None:
            return
        speed = parts[1].strip() if len(parts) > 1 else None
        eta = parts[2].strip() if len(parts) > 2 else None
        mapped = start + ((end - start) * max(0.0, min(100.0, raw_percent)) / 100.0)
        _emit_progress(callback, mapped, stage, speed=speed, eta=eta)

    return handle


async def _run_yt_dlp(
    arguments: list[str],
    *,
    timeout_seconds: int,
    stage: str,
    progress_callback: ProgressCallback | None = None,
    progress_range: tuple[float, float] = (10.0, 85.0),
) -> bytes:
    return await _run_process(
        arguments,
        timeout_seconds=timeout_seconds,
        stage=stage,
        failure_message=(
            "La plataforma no pudo procesar ese enlace público. "
            "Comprueba el enlace o inténtalo más tarde."
        ),
        line_callback=_yt_dlp_progress_parser(
            progress_callback,
            start=progress_range[0],
            end=progress_range[1],
            stage=stage,
        ),
    )


def _metadata_languages(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(
        str(key)[:32]
        for key in value
        if isinstance(key, str) and key and key != "live_chat"
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
    language = metadata.get("language")
    if not isinstance(language, str) or not language.strip():
        language = None
    return MediaInfo(
        title=str(title)[:200] if title else "Vídeo sin título",
        platform=str(platform)[:80] if platform else "Plataforma compatible",
        duration=duration,
        thumbnail=thumbnail,
        max_height=ordered_heights[0] if ordered_heights else None,
        available_heights=ordered_heights,
        language=language[:32] if language else None,
        subtitle_languages=_metadata_languages(metadata.get("subtitles")),
        automatic_caption_languages=_metadata_languages(metadata.get("automatic_captions")),
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
        "-map",
        "0:s?",
        "-map_metadata",
        "-1",
        *_video_encoder_arguments(codec, backend),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-c:s",
        "mov_text",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(result),
    ]


_ALLOWED_EXTENSIONS = {"mp4", "mp3", "m4a", "opus", "webm", "ogg", "aac", "srt"}


def safe_download_name(title: str, extension: str = "mp4") -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", normalized)
    cleaned = re.sub(r"[\s._-]+", " ", cleaned).strip(" .")[:80]
    safe_extension = extension.lower().lstrip(".")
    if safe_extension not in _ALLOWED_EXTENSIONS:
        safe_extension = "mp4"
    fallback = "subtitulos" if safe_extension == "srt" else (
        "audio" if safe_extension in {"mp3", "m4a", "opus", "webm", "ogg", "aac"} else "video"
    )
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
        if path.is_file()
        and path.suffix.lower() not in {".part", ".ytdl", ".json", ".srt", ".vtt", ".ass", ".lrc"}
    ]
    if not candidates:
        raise DownloaderError("No se encontró el archivo descargado.")
    return max(candidates, key=lambda path: path.stat().st_size)


def _subtitle_file(job_directory: Path) -> Path:
    candidates = [
        path
        for path in job_directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".srt"
    ]
    if not candidates:
        raise DownloaderError("No se encontraron subtítulos para ese vídeo.")
    return max(candidates, key=lambda path: path.stat().st_size)


def _preferred_subtitle_language(media_info: MediaInfo, mode: SubtitleMode) -> str:
    if mode == "es":
        return "es.*,es"
    manual = media_info.subtitle_languages
    automatic = media_info.automatic_caption_languages
    if mode == "auto":
        if media_info.language and media_info.language in automatic:
            return media_info.language
        return automatic[0] if automatic else (media_info.language or "es")
    if media_info.language and media_info.language in manual:
        return media_info.language
    if manual:
        return manual[0]
    if media_info.language and media_info.language in automatic:
        return media_info.language
    if automatic:
        return automatic[0]
    return media_info.language or "es"


def _subtitle_arguments(
    media_info: MediaInfo,
    mode: SubtitleMode,
    *,
    embed: bool,
) -> list[str]:
    if mode == "none":
        return []
    arguments: list[str] = []
    if mode == "auto":
        arguments.append("--write-auto-subs")
    else:
        arguments += ["--write-subs", "--write-auto-subs"]
    arguments += [
        "--sub-langs",
        _preferred_subtitle_language(media_info, mode),
        "--convert-subs",
        "srt",
    ]
    if embed:
        arguments.append("--embed-subs")
    return arguments


async def _download_audio(
    url: str,
    output_format: OutputFormat,
    audio_quality: AudioQuality,
    output_template: str,
    job_directory: Path,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
) -> Path:
    arguments = _base_arguments() + _progress_arguments() + [
        "--no-playlist",
        "--max-filesize",
        "500M",
        "--format",
        "ba/b",
    ]
    if output_format != "audio_original":
        arguments += ["--extract-audio", "--audio-format", output_format]
        if output_format == "mp3":
            arguments += ["--audio-quality", f"{audio_quality}K"]
    arguments += ["--output", output_template, url]
    label = "audio original" if output_format == "audio_original" else output_format.upper()
    await _run_yt_dlp(
        arguments,
        timeout_seconds=timeout_seconds,
        stage=f"Descargando {label}",
        progress_callback=progress_callback,
        progress_range=(10.0, 88.0),
    )
    _emit_progress(progress_callback, 96.0, "Finalizando audio")
    if output_format == "audio_original":
        result = _largest_media_file(job_directory)
    else:
        result = _single_file_with_extension(job_directory, output_format)
    _ensure_size_limit(result)
    return result


async def _download_srt(
    url: str,
    media_info: MediaInfo,
    subtitle_mode: SubtitleMode,
    output_template: str,
    job_directory: Path,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
) -> Path:
    mode: SubtitleMode = "original" if subtitle_mode == "none" else subtitle_mode
    _emit_progress(progress_callback, 15.0, "Buscando subtítulos")
    arguments = _base_arguments() + [
        "--no-playlist",
        "--skip-download",
        *_subtitle_arguments(media_info, mode, embed=False),
        "--output",
        output_template,
        url,
    ]
    await _run_yt_dlp(
        arguments,
        timeout_seconds=timeout_seconds,
        stage="Descargando subtítulos",
        progress_callback=progress_callback,
        progress_range=(20.0, 90.0),
    )
    result = _subtitle_file(job_directory)
    _ensure_size_limit(result)
    _emit_progress(progress_callback, 98.0, "Subtítulos listos")
    return result


async def _download_original_video(
    url: str,
    quality: VideoQuality,
    media_info: MediaInfo,
    subtitle_mode: SubtitleMode,
    output_template: str,
    job_directory: Path,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
) -> Path:
    arguments = _base_arguments() + _progress_arguments() + [
        "--no-playlist",
        "--max-filesize",
        "500M",
        "--format",
        _video_format_selector(quality),
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        *_subtitle_arguments(media_info, subtitle_mode, embed=True),
        "--output",
        output_template,
        url,
    ]
    await _run_yt_dlp(
        arguments,
        timeout_seconds=timeout_seconds,
        stage="Descargando vídeo",
        progress_callback=progress_callback,
        progress_range=(10.0, 90.0),
    )
    _emit_progress(progress_callback, 97.0, "Finalizando MP4")
    result = _single_file_with_extension(job_directory, "mp4")
    _ensure_size_limit(result)
    return result


def _ffmpeg_progress_parser(
    callback: ProgressCallback | None,
    duration_seconds: int | None,
) -> LineCallback | None:
    if callback is None or not duration_seconds or duration_seconds <= 0:
        return None

    def handle(line: str) -> None:
        if not line.startswith("out_time_"):
            return
        _, _, value = line.partition("=")
        try:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                seconds = int(value) / 1_000_000
            elif line.startswith("out_time="):
                hours, minutes, seconds_value = value.split(":")
                seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds_value)
            else:
                return
        except (ValueError, TypeError):
            return
        raw = max(0.0, min(100.0, seconds / duration_seconds * 100.0))
        mapped = 82.0 + (raw * 0.16)
        _emit_progress(callback, mapped, "Convirtiendo vídeo")

    return handle


async def _download_and_transcode_video(
    url: str,
    quality: VideoQuality,
    codec: VideoCodec,
    media_info: MediaInfo,
    subtitle_mode: SubtitleMode,
    output_template: str,
    job_directory: Path,
    job_id: str,
    timeout_seconds: int,
    progress_callback: ProgressCallback | None,
) -> Path:
    arguments = _base_arguments() + _progress_arguments() + [
        "--no-playlist",
        "--max-filesize",
        "500M",
        "--format",
        _video_format_selector(quality),
        "--merge-output-format",
        "mkv",
        *_subtitle_arguments(media_info, subtitle_mode, embed=True),
        "--output",
        output_template,
        url,
    ]
    await _run_yt_dlp(
        arguments,
        timeout_seconds=timeout_seconds,
        stage="Descargando vídeo",
        progress_callback=progress_callback,
        progress_range=(10.0, 80.0),
    )
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
        _emit_progress(progress_callback, 82.0, f"Convirtiendo a {codec.upper()}")
        try:
            await _run_process(
                _ffmpeg_transcode_arguments(source, result, codec, backend),
                timeout_seconds=timeout_seconds,
                stage=f"conversión {codec.upper()} ({backend})",
                failure_message=(
                    f"FFmpeg no pudo convertir el vídeo a {codec.upper()} "
                    f"mediante {backend.upper()}."
                ),
                line_callback=_ffmpeg_progress_parser(progress_callback, media_info.duration),
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
    _emit_progress(progress_callback, 99.0, "Finalizando MP4")
    return result


def _media_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    explicit = {
        ".mp4": "video/mp4",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".opus": "audio/ogg",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".aac": "audio/aac",
        ".srt": "application/x-subrip",
    }
    return explicit.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


async def download_url(
    url: str,
    quality: VideoQuality,
    output_format: OutputFormat,
    video_codec: VideoCodec,
    audio_quality: AudioQuality,
    subtitle_mode: SubtitleMode,
    download_root: Path,
    *,
    extract_timeout_seconds: int = 60,
    download_timeout_seconds: int = 1800,
    progress_callback: ProgressCallback | None = None,
) -> DownloadedFile:
    _emit_progress(progress_callback, 2.0, "Analizando")
    media_info = await analyze_url(url, timeout_seconds=extract_timeout_seconds)
    _emit_progress(progress_callback, 7.0, "Preparando")
    job_id = uuid.uuid4().hex
    job_directory = download_root / job_id
    job_directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    output_template = str(job_directory / f"{job_id}.%(ext)s")

    try:
        if output_format in {"mp3", "m4a", "opus", "audio_original"}:
            result = await _download_audio(
                url,
                output_format,
                audio_quality,
                output_template,
                job_directory,
                download_timeout_seconds,
                progress_callback,
            )
        elif output_format == "srt":
            result = await _download_srt(
                url,
                media_info,
                subtitle_mode,
                output_template,
                job_directory,
                download_timeout_seconds,
                progress_callback,
            )
        elif video_codec == "original":
            result = await _download_original_video(
                url,
                quality,
                media_info,
                subtitle_mode,
                output_template,
                job_directory,
                download_timeout_seconds,
                progress_callback,
            )
        else:
            result = await _download_and_transcode_video(
                url,
                quality,
                video_codec,
                media_info,
                subtitle_mode,
                output_template,
                job_directory,
                job_id,
                download_timeout_seconds,
                progress_callback,
            )

        extension = result.suffix.lower().lstrip(".")
        _emit_progress(progress_callback, 100.0, "Listo")
        return DownloadedFile(
            path=result,
            download_name=safe_download_name(media_info.title, extension),
            media_type=_media_type_for_path(result),
            job_directory=job_directory,
        )
    except BaseException:
        from app.cleanup import remove_job_directory

        remove_job_directory(job_directory)
        raise
