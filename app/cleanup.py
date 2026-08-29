from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path


logger = logging.getLogger(__name__)


def remove_job_directory(job_directory: Path) -> None:
    try:
        shutil.rmtree(job_directory, ignore_errors=False)
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("No se pudo limpiar un directorio temporal")


def remove_stale_directories(download_root: Path, max_age_seconds: int) -> None:
    cutoff = time.time() - max_age_seconds
    try:
        candidates = tuple(download_root.iterdir())
    except FileNotFoundError:
        return

    for candidate in candidates:
        try:
            if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
                remove_job_directory(candidate)
        except OSError:
            logger.warning("No se pudo inspeccionar un directorio temporal")


async def periodic_cleanup(
    download_root: Path,
    *,
    max_age_seconds: int = 3600,
    interval_seconds: int = 300,
) -> None:
    while True:
        await asyncio.to_thread(remove_stale_directories, download_root, max_age_seconds)
        await asyncio.sleep(interval_seconds)
