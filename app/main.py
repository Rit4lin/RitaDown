from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request, status
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
            response = JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "La solicitud es demasiado grande."},
            )
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            return response
        client = request.client.host if request.client else "unknown"
        if not await rate_limiter.allowed(client):
            response = JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Demasiadas solicitudes. Espera unos minutos."},
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
    return JSONResponse(status_code=422, content={"detail": "Solicitud no válida."})


@app.get("/")
async def index(request: Request):  # type: ignore[no-untyped-def]
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze(payload: URLRequest) -> dict[str, object]:
    try:
        validated = await asyncio.to_thread(validate_media_url, payload.url)
        info = await analyze_url(validated.url, timeout_seconds=EXTRACT_TIMEOUT)
        return {"media": info.as_dict()}
    except (URLValidationError, DownloaderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Fallo interno durante el análisis (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="No se pudo analizar el enlace.") from exc


@app.post("/api/download")
async def download(payload: DownloadRequest) -> FileResponse:
    if download_lock.locked():
        raise HTTPException(
            status_code=429,
            detail="Ya hay una descarga en curso. Inténtalo cuando termine.",
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
        except (URLValidationError, DownloaderError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Fallo interno durante la descarga (%s)", type(exc).__name__)
            raise HTTPException(status_code=500, detail="No se pudo preparar el vídeo.") from exc
