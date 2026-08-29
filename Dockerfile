FROM denoland/deno:bin-2.9.3 AS deno-runtime

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    DOWNLOAD_DIR=/app/downloads

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=deno-runtime /deno /usr/local/bin/deno

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

RUN groupadd --system appgroup \
    && useradd --system --gid appgroup --home-dir /nonexistent --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/downloads \
    && chown appuser:appgroup /app/downloads

COPY --chown=appuser:appgroup app ./app
COPY --chown=appuser:appgroup downloads/.gitkeep ./downloads/.gitkeep

USER appuser:appgroup

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--no-proxy-headers"]
