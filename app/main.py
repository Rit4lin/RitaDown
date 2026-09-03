from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
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
from app.downloader import DownloaderError, analyze_url, download_url
from app.validation import URLValidationError, validate_media_url


BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR.parent / "downloads")).resolve()
EXTRACT_TIMEOUT = int(os.getenv("EXTRACT_TIMEOUT_SECONDS", "60"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "1800"))
logger = logging.getLogger(__name__)


class URLRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class DownloadRequest(URLRequest):
    quality: Literal["original", "2160", "1440", "1080", "720", "480", "360"] = "original"
    output_format: Literal["mp4", "mp3"] = "mp4"
    video_codec: Literal["original", "h264", "h265"] = "original"
    audio_quality: Literal["320", "256", "192", "128"] = "192"


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


rate_limiter = RateLimiter()
download_lock = asyncio.Lock()


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
    if "500 mb" in normalized:
        return "RDL-3201"
    if "ffmpeg" in normalized:
        return "RDL-3100"
    if "generar correctamente" in normalized or "no se encontró" in normalized:
        return "RDL-3200"
    return "RDL-2000"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_task = asyncio.create_task(periodic_cleanup(DOWNLOAD_ROOT))
    yield
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task


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
        logger.exception("[RDL-5000/%s] Fallo interno durante el análisis (%s)", reference, type(exc).__name__)
        return _error_response(
            500,
            "No se pudo analizar el enlace.",
            "RDL-5000",
            reference=reference,
        )


@app.post("/api/download")
async def download(payload: DownloadRequest):  # type: ignore[no-untyped-def]
    if download_lock.locked():
        return _error_response(
            429,
            "Ya hay una descarga en curso. Inténtalo cuando termine.",
            "RDL-1004",
        )
    async with download_lock:
        try:
            validated = await asyncio.to_thread(validate_media_url, payload.url)
            downloaded = await download_url(
                validated.url,
                payload.quality,
                payload.output_format,
                payload.video_codec,
                payload.audio_quality,
                DOWNLOAD_ROOT,
                extract_timeout_seconds=EXTRACT_TIMEOUT,
                download_timeout_seconds=DOWNLOAD_TIMEOUT,
            )
            return FileResponse(
                path=downloaded.path,
                media_type=downloaded.media_type,
                filename=downloaded.download_name,
                background=BackgroundTask(
                    remove_job_directory, downloaded.job_directory
                ),
            )
        except URLValidationError as exc:
            return _error_response(400, str(exc), "RDL-1000")
        except DownloaderError as exc:
            return _error_response(400, str(exc), _downloader_error_code(str(exc)))
        except Exception as exc:
            reference = _error_reference()
            logger.exception("[RDL-5000/%s] Fallo interno durante la descarga (%s)", reference, type(exc).__name__)
            return _error_response(
                500,
                "No se pudo preparar el vídeo.",
                "RDL-5000",
                reference=reference,
            )
