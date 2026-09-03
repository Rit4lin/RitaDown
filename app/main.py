from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.cleanup import periodic_cleanup, remove_job_directory
from app.downloader import (
    DownloadedFile,
    DownloaderError,
    DownloadProgress,
    analyze_url,
    download_url,
)
from app.validation import URLValidationError, validate_media_url


BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR.parent / "downloads")).resolve()
EXTRACT_TIMEOUT = int(os.getenv("EXTRACT_TIMEOUT_SECONDS", "60"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "1800"))
MAX_QUEUE_SIZE = max(1, min(32, int(os.getenv("MAX_QUEUE_SIZE", "8"))))
JOB_MAX_AGE_SECONDS = max(300, int(os.getenv("JOB_MAX_AGE_SECONDS", "3600")))
logger = logging.getLogger(__name__)


class URLRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class DownloadRequest(URLRequest):
    quality: Literal["original", "2160", "1440", "1080", "720", "480", "360"] = "original"
    output_format: Literal["mp4", "mp3", "m4a", "opus", "audio_original", "srt"] = "mp4"
    video_codec: Literal["original", "h264", "h265"] = "original"
    audio_quality: Literal["320", "256", "192", "128"] = "192"
    subtitle_mode: Literal["none", "es", "original", "auto"] = "none"


class RateLimiter:
    def __init__(self, limit: int = 30, window_seconds: int = 300) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()

    async def allowed(self, client: str) -> bool:
        now = time.monotonic()
        async with self.lock:
            history = self.requests[client]
            while history and history[0] <= now - self.window_seconds:
                history.popleft()
            if len(history) >= self.limit:
                return False
            history.append(now)
            return True


@dataclass(slots=True)
class DownloadJob:
    job_id: str
    request: DownloadRequest
    created_at: float
    status: Literal["queued", "working", "ready", "error"] = "queued"
    progress: float = 0.0
    stage: str = "En cola"
    speed: str | None = None
    eta: str | None = None
    downloaded: DownloadedFile | None = None
    error_detail: str | None = None
    error_code: str | None = None
    error_reference: str | None = None


rate_limiter = RateLimiter()
job_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
jobs: dict[str, DownloadJob] = {}


def _error_reference() -> str:
    return uuid.uuid4().hex[:8].upper()


def _error_response(
    status_code: int,
    detail: str,
    code: str,
    *,
    reference: str | None = None,
) -> JSONResponse:
    content: dict[str, str] = {"detail": detail, "code": code}
    if reference:
        content["reference"] = reference
    return JSONResponse(status_code=status_code, content=content)


def _downloader_error_code(message: str) -> str:
    normalized = message.lower()
    if "tardó demasiado" in normalized:
        return "RDL-2101"
    if "plataforma no pudo procesar" in normalized:
        return "RDL-2100"
    if "listas" in normalized or "carruseles" in normalized:
        return "RDL-2102"
    if "metadatos" in normalized or "interpretar la información" in normalized:
        return "RDL-2103"
    if "subtítulos" in normalized:
        return "RDL-2200"
    if "500 mb" in normalized:
        return "RDL-3201"
    if "ffmpeg" in normalized:
        return "RDL-3100"
    if "generar correctamente" in normalized or "no se encontró" in normalized:
        return "RDL-3200"
    return "RDL-2000"


def _queue_position(job_id: str) -> int | None:
    queued = sorted(
        (job for job in jobs.values() if job.status == "queued"),
        key=lambda job: job.created_at,
    )
    for index, job in enumerate(queued, start=1):
        if job.job_id == job_id:
            return index
    return None


def _job_payload(job: DownloadJob) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": job.job_id,
        "status": job.status,
        "progress": round(job.progress, 1),
        "stage": job.stage,
        "speed": job.speed,
        "eta": job.eta,
        "position": _queue_position(job.job_id),
    }
    if job.status == "ready" and job.downloaded is not None:
        payload["file_url"] = f"/api/jobs/{job.job_id}/file"
        payload["filename"] = job.downloaded.download_name
    if job.status == "error":
        error: dict[str, str] = {
            "detail": job.error_detail or "No se pudo completar la descarga.",
            "code": job.error_code or "RDL-2000",
        }
        if job.error_reference:
            error["reference"] = job.error_reference
        payload["error"] = error
    return payload


def _apply_progress(job: DownloadJob, progress: DownloadProgress) -> None:
    if job.status != "working":
        return
    job.progress = max(job.progress, progress.percent)
    job.stage = progress.stage
    job.speed = progress.speed
    job.eta = progress.eta


async def _download_worker() -> None:
    while True:
        job_id = await job_queue.get()
        job = jobs.get(job_id)
        if job is None:
            job_queue.task_done()
            continue

        job.status = "working"
        job.stage = "Preparando"
        job.progress = max(job.progress, 1.0)
        try:
            payload = job.request
            downloaded = await download_url(
                payload.url,
                payload.quality,
                payload.output_format,
                payload.video_codec,
                payload.audio_quality,
                payload.subtitle_mode,
                DOWNLOAD_ROOT,
                extract_timeout_seconds=EXTRACT_TIMEOUT,
                download_timeout_seconds=DOWNLOAD_TIMEOUT,
                progress_callback=lambda progress: _apply_progress(job, progress),
            )
            job.downloaded = downloaded
            job.status = "ready"
            job.progress = 100.0
            job.stage = "Listo"
            job.speed = None
            job.eta = None
        except DownloaderError as exc:
            job.status = "error"
            job.stage = "Error"
            job.error_detail = str(exc)
            job.error_code = _downloader_error_code(str(exc))
            job.speed = None
            job.eta = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reference = _error_reference()
            logger.exception(
                "[RDL-5000/%s] Fallo interno durante trabajo de descarga (%s)",
                reference,
                type(exc).__name__,
            )
            job.status = "error"
            job.stage = "Error"
            job.error_detail = "No se pudo preparar el archivo."
            job.error_code = "RDL-5000"
            job.error_reference = reference
            job.speed = None
            job.eta = None
        finally:
            job_queue.task_done()


async def _prune_job_records() -> None:
    while True:
        await asyncio.sleep(300)
        cutoff = time.time() - JOB_MAX_AGE_SECONDS
        stale = [
            job_id
            for job_id, job in jobs.items()
            if job.created_at < cutoff and job.status in {"ready", "error"}
        ]
        for job_id in stale:
            job = jobs.pop(job_id, None)
            if job and job.downloaded:
                await asyncio.to_thread(remove_job_directory, job.downloaded.job_directory)


async def _finish_file_delivery(job_id: str, job_directory: Path) -> None:
    await asyncio.to_thread(remove_job_directory, job_directory)
    jobs.pop(job_id, None)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_task = asyncio.create_task(periodic_cleanup(DOWNLOAD_ROOT))
    worker_task = asyncio.create_task(_download_worker())
    prune_task = asyncio.create_task(_prune_job_records())
    yield
    for task in (worker_task, prune_task, cleanup_task):
        task.cancel()
    for task in (worker_task, prune_task, cleanup_task):
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="RitaDown",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def security_and_rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path.startswith("/api/"):
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > 16_384:
                response = _error_response(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "La solicitud es demasiado grande.",
                    "RDL-1002",
                )
                response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["Cache-Control"] = "no-store"
                return response
            client = request.client.host if request.client else "unknown"
            if not await rate_limiter.allowed(client):
                response = _error_response(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Demasiadas solicitudes. Espera unos minutos.",
                    "RDL-1003",
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' https: data:; connect-src 'self'; "
        "script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return _error_response(422, "Solicitud no válida.", "RDL-1001")


@app.get("/")
async def index(request: Request):  # type: ignore[no-untyped-def]
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze(payload: URLRequest):  # type: ignore[no-untyped-def]
    try:
        validated = await asyncio.to_thread(validate_media_url, payload.url)
        info = await analyze_url(validated.url, timeout_seconds=EXTRACT_TIMEOUT)
        return {"media": info.as_dict()}
    except URLValidationError as exc:
        return _error_response(400, str(exc), "RDL-1000")
    except DownloaderError as exc:
        return _error_response(400, str(exc), _downloader_error_code(str(exc)))
    except Exception as exc:
        reference = _error_reference()
        logger.exception(
            "[RDL-5000/%s] Fallo interno durante el análisis (%s)",
            reference,
            type(exc).__name__,
        )
        return _error_response(
            500,
            "No se pudo analizar el enlace.",
            "RDL-5000",
            reference=reference,
        )


@app.post("/api/jobs", status_code=202)
async def create_job(payload: DownloadRequest):  # type: ignore[no-untyped-def]
    try:
        validated = await asyncio.to_thread(validate_media_url, payload.url)
    except URLValidationError as exc:
        return _error_response(400, str(exc), "RDL-1000")

    if job_queue.full():
        return _error_response(
            429,
            "La cola de descargas está llena. Inténtalo cuando termine alguna.",
            "RDL-1005",
        )

    job_id = uuid.uuid4().hex
    normalized_payload = payload.model_copy(update={"url": validated.url})
    job = DownloadJob(
        job_id=job_id,
        request=normalized_payload,
        created_at=time.time(),
    )
    jobs[job_id] = job
    try:
        job_queue.put_nowait(job_id)
    except asyncio.QueueFull:
        jobs.pop(job_id, None)
        return _error_response(
            429,
            "La cola de descargas está llena. Inténtalo cuando termine alguna.",
            "RDL-1005",
        )
    return {"job": _job_payload(job)}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):  # type: ignore[no-untyped-def]
    job = jobs.get(job_id)
    if job is None:
        return _error_response(404, "La descarga no existe o ha caducado.", "RDL-1006")
    return {"job": _job_payload(job)}


@app.get("/api/jobs/{job_id}/file")
async def get_job_file(job_id: str):  # type: ignore[no-untyped-def]
    job = jobs.get(job_id)
    if job is None:
        return _error_response(404, "La descarga no existe o ha caducado.", "RDL-1006")
    if job.status == "error":
        return _error_response(
            400,
            job.error_detail or "No se pudo completar la descarga.",
            job.error_code or "RDL-2000",
            reference=job.error_reference,
        )
    if job.status != "ready" or job.downloaded is None:
        return _error_response(409, "La descarga todavía no está lista.", "RDL-1007")
    if not job.downloaded.path.is_file():
        jobs.pop(job_id, None)
        return _error_response(410, "El archivo de descarga ha caducado.", "RDL-3202")

    downloaded = job.downloaded
    return FileResponse(
        path=downloaded.path,
        media_type=downloaded.media_type,
        filename=downloaded.download_name,
        background=BackgroundTask(
            _finish_file_delivery,
            job_id,
            downloaded.job_directory,
        ),
    )
