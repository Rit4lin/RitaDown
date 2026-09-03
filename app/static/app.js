"use strict";

const elements = {
  url: document.querySelector("#url"),
  analyze: document.querySelector("#analyze"),
  status: document.querySelector("#status"),
  result: document.querySelector("#result"),
  thumbnail: document.querySelector("#thumbnail"),
  title: document.querySelector("#video-title"),
  platform: document.querySelector("#platform"),
  duration: document.querySelector("#duration"),
  maxHeight: document.querySelector("#max-height"),
  outputFormat: document.querySelector("#output-format"),
  quality: document.querySelector("#quality"),
  videoQualityOption: document.querySelector("#video-quality-option"),
  videoCodec: document.querySelector("#video-codec"),
  codecOption: document.querySelector("#codec-option"),
  audioQuality: document.querySelector("#audio-quality"),
  audioQualityOption: document.querySelector("#audio-quality-option"),
  subtitleMode: document.querySelector("#subtitle-mode"),
  subtitleOption: document.querySelector("#subtitle-option"),
  download: document.querySelector("#download"),
  reset: document.querySelector("#reset"),
  progressPanel: document.querySelector("#progress-panel"),
  progress: document.querySelector("#progress"),
  progressStage: document.querySelector("#progress-stage"),
  progressPercent: document.querySelector("#progress-percent"),
  progressMeta: document.querySelector("#progress-meta"),
};

let analyzedUrl = "";
let currentJobId = "";

function showStatus(message, kind = "busy") {
  elements.status.textContent = message;
  elements.status.className = `status ${kind}`;
  elements.status.hidden = false;
}

function clearStatus() {
  elements.status.hidden = true;
  elements.status.textContent = "";
}

function setBusy(isBusy) {
  elements.analyze.disabled = isBusy;
  elements.download.disabled = isBusy;
  elements.reset.disabled = isBusy;
}

function setProgress(job = null) {
  if (!job) {
    elements.progressPanel.hidden = true;
    elements.progress.value = 0;
    elements.progressStage.textContent = "";
    elements.progressPercent.textContent = "0%";
    elements.progressMeta.textContent = "";
    return;
  }

  const value = Number.isFinite(Number(job.progress)) ? Number(job.progress) : 0;
  elements.progressPanel.hidden = false;
  elements.progress.value = value;
  elements.progressStage.textContent = job.stage || "Preparando";
  elements.progressPercent.textContent = `${Math.round(value)}%`;

  const metadata = [];
  if (job.status === "queued" && job.position) metadata.push(`Posición ${job.position}`);
  if (job.speed && job.speed !== "N/A") metadata.push(job.speed);
  if (job.eta && job.eta !== "N/A") metadata.push(`ETA ${job.eta}`);
  elements.progressMeta.textContent = metadata.join(" · ");
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "No indicada";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainingSeconds = Math.floor(seconds % 60);
  return [hours, minutes, remainingSeconds]
    .filter((_, index) => hours > 0 || index > 0)
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

function errorPayloadMessage(payload, fallback = "La solicitud no se pudo completar.") {
  const message = payload?.detail || fallback;
  if (!payload?.code) return message;
  const suffix = payload.reference
    ? `Código: ${payload.code} · Ref: ${payload.reference}`
    : `Código: ${payload.code}`;
  return `${message} ${suffix}`;
}

async function errorFromResponse(response) {
  try {
    return errorPayloadMessage(await response.json());
  } catch {
    return `La solicitud no se pudo completar. Código HTTP: ${response.status}`;
  }
}

function requestHeaders() {
  return { "Content-Type": "application/json" };
}

function updateFormatOptions() {
  const format = elements.outputFormat.value;
  const isVideo = format === "mp4";
  const isMp3 = format === "mp3";
  const isSubtitle = format === "srt";

  elements.videoQualityOption.hidden = !isVideo;
  elements.codecOption.hidden = !isVideo;
  elements.audioQualityOption.hidden = !isMp3;
  elements.subtitleOption.hidden = !(isVideo || isSubtitle);

  if (isSubtitle && elements.subtitleMode.value === "none") {
    elements.subtitleMode.value = "original";
  }

  const labels = {
    mp4: "Descargar vídeo",
    audio_original: "Descargar audio",
    m4a: "Descargar M4A",
    opus: "Descargar Opus",
    mp3: "Descargar MP3",
    srt: "Descargar SRT",
  };
  elements.download.textContent = labels[format] || "Descargar";
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function fetchJob(jobId) {
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(await errorFromResponse(response));
  return (await response.json()).job;
}

function triggerFileDownload(job) {
  const link = document.createElement("a");
  link.href = job.file_url;
  link.download = job.filename || "";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function waitForJob(jobId) {
  let transientFailures = 0;

  while (currentJobId === jobId) {
    try {
      const job = await fetchJob(jobId);
      transientFailures = 0;
      setProgress(job);

      if (job.status === "queued") {
        showStatus(job.position ? `En cola · posición ${job.position}` : "En cola");
      } else if (job.status === "working") {
        showStatus(job.stage || "Procesando");
      } else if (job.status === "error") {
        throw new Error(errorPayloadMessage(job.error, "No se pudo completar la descarga."));
      } else if (job.status === "ready") {
        triggerFileDownload(job);
        showStatus("Descarga iniciada.", "success");
        currentJobId = "";
        setBusy(false);
        return;
      }
    } catch (error) {
      transientFailures += 1;
      if (transientFailures < 3) {
        await sleep(1000);
        continue;
      }
      currentJobId = "";
      setBusy(false);
      showStatus(error.message || "No se pudo consultar la descarga.", "error");
      return;
    }
    await sleep(800);
  }
}

elements.outputFormat.addEventListener("change", updateFormatOptions);
updateFormatOptions();

elements.analyze.addEventListener("click", async () => {
  const url = elements.url.value.trim();
  if (!url) {
    showStatus("Introduce una URL.", "error");
    return;
  }

  setBusy(true);
  elements.result.hidden = true;
  setProgress(null);
  showStatus("Analizando…");

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: requestHeaders(),
      body: JSON.stringify({ url }),
    });
    if (!response.ok) throw new Error(await errorFromResponse(response));

    const { media } = await response.json();
    analyzedUrl = url;
    elements.title.textContent = media.title;
    elements.platform.textContent = media.platform;
    elements.duration.textContent = formatDuration(media.duration);
    elements.maxHeight.textContent = media.available_heights?.length
      ? media.available_heights.slice(0, 6).map((height) => `${height}p`).join(", ")
      : "No indicada";

    if (media.thumbnail) {
      elements.thumbnail.src = media.thumbnail;
      elements.thumbnail.hidden = false;
    } else {
      elements.thumbnail.removeAttribute("src");
      elements.thumbnail.hidden = true;
    }

    elements.result.hidden = false;
    showStatus("Listo.", "success");
  } catch (error) {
    showStatus(error.message || "No se pudo analizar el enlace.", "error");
  } finally {
    setBusy(false);
  }
});

elements.download.addEventListener("click", async () => {
  if (!analyzedUrl || elements.url.value.trim() !== analyzedUrl) {
    showStatus("El enlace ha cambiado. Analízalo de nuevo.", "error");
    elements.result.hidden = true;
    return;
  }

  setBusy(true);
  setProgress({
    status: "queued",
    progress: 0,
    stage: "En cola",
    position: null,
  });
  showStatus("Añadiendo a la cola…");

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: requestHeaders(),
      body: JSON.stringify({
        url: analyzedUrl,
        quality: elements.quality.value,
        output_format: elements.outputFormat.value,
        video_codec: elements.videoCodec.value,
        audio_quality: elements.audioQuality.value,
        subtitle_mode: elements.subtitleMode.value,
      }),
    });
    if (!response.ok) throw new Error(await errorFromResponse(response));

    const { job } = await response.json();
    currentJobId = job.id;
    setProgress(job);
    await waitForJob(job.id);
  } catch (error) {
    currentJobId = "";
    setBusy(false);
    setProgress(null);
    showStatus(error.message || "No se pudo iniciar la descarga.", "error");
  }
});

elements.reset.addEventListener("click", () => {
  if (currentJobId) return;
  analyzedUrl = "";
  elements.url.value = "";
  elements.result.hidden = true;
  elements.thumbnail.removeAttribute("src");
  setProgress(null);
  clearStatus();
  elements.url.focus();
});
