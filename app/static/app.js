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
  download: document.querySelector("#download"),
  reset: document.querySelector("#reset"),
};

let analyzedUrl = "";

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

async function errorFromResponse(response) {
  try {
    const payload = await response.json();
    const message = payload.detail || "La solicitud no se pudo completar.";
    if (!payload.code) return message;
    const suffix = payload.reference
      ? `Código: ${payload.code} · Ref: ${payload.reference}`
      : `Código: ${payload.code}`;
    return `${message} ${suffix}`;
  } catch {
    return `La solicitud no se pudo completar. Código HTTP: ${response.status}`;
  }
}

function requestHeaders() {
  return {
    "Content-Type": "application/json",
  };
}

function updateFormatOptions() {
  const isAudio = elements.outputFormat.value === "mp3";
  elements.videoQualityOption.hidden = isAudio;
  elements.codecOption.hidden = isAudio;
  elements.audioQualityOption.hidden = !isAudio;
  elements.download.textContent = isAudio ? "Descargar MP3" : "Descargar vídeo";
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
  const isAudio = elements.outputFormat.value === "mp3";
  const isConversion = !isAudio && elements.videoCodec.value !== "original";
  showStatus(
    isAudio
      ? "Preparando MP3…"
      : isConversion
        ? `Convirtiendo a ${elements.videoCodec.value.toUpperCase()}…`
        : "Preparando MP4…"
  );
  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: requestHeaders(),
      body: JSON.stringify({
        url: analyzedUrl,
        quality: elements.quality.value,
        output_format: elements.outputFormat.value,
        video_codec: elements.videoCodec.value,
        audio_quality: elements.audioQuality.value,
      }),
    });
    if (!response.ok) throw new Error(await errorFromResponse(response));
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const disposition = response.headers.get("Content-Disposition") || "";
    const encodedName = disposition.match(/filename\*=utf-8''([^;]+)/i);
    const simpleName = disposition.match(/filename="?([^";]+)"?/i);
    const fallbackName = isAudio ? "audio.mp3" : "video.mp4";
    link.download = encodedName ? decodeURIComponent(encodedName[1]) : (simpleName?.[1] || fallbackName);
    link.href = objectUrl;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    showStatus("Descarga iniciada.", "success");
  } catch (error) {
    showStatus(error.message || "No se pudo descargar el vídeo.", "error");
  } finally {
    setBusy(false);
  }
});

elements.reset.addEventListener("click", () => {
  analyzedUrl = "";
  elements.url.value = "";
  elements.result.hidden = true;
  elements.thumbnail.removeAttribute("src");
  clearStatus();
  elements.url.focus();
});
