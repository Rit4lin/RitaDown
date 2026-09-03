# RitaDown

Aplicación web privada y adaptada a móviles para analizar y descargar contenido multimedia público mediante `yt-dlp`. Se distribuye como imagen Docker y está pensada para ejecutarse en un servidor propio.

> Usa RitaDown únicamente con contenido propio o cuando dispongas de autorización para descargarlo. Respeta los derechos de autor, los términos de cada plataforma y la legislación aplicable.

## Plataformas

RitaDown admite explícitamente estas plataformas y mantiene una allowlist de dominios:

- Instagram.
- TikTok, incluidos `vm.tiktok.com` y `vt.tiktok.com`.
- YouTube.
- Facebook.
- X/Twitter.
- Reddit.
- Vimeo.
- Dailymotion.
- Pinterest.
- Bluesky.
- Twitch VOD y clips.

El soporte real depende de los cambios de cada plataforma y de `yt-dlp`. Solo se procesan enlaces públicos: RitaDown no usa cuentas, credenciales ni mecanismos para eludir DRM.

## Formatos

### Vídeo

- MP4 en calidad original o con límite de 2160p, 1440p, 1080p, 720p, 480p o 360p.
- Códec original, H.264 o H.265/HEVC.
- Conversión H.264/H.265 mediante NVIDIA NVENC cuando está disponible, con respaldo automático por CPU.

### Audio

- Audio original, sin recodificar cuando es posible.
- M4A.
- Opus.
- MP3 a 320, 256, 192 o 128 kbps.

### Subtítulos

- Sin subtítulos.
- Español.
- Idioma original.
- Subtítulos automáticos.
- Los subtítulos pueden incrustarse en el MP4.
- También pueden descargarse como archivo SRT independiente.

## Cola y progreso

Las descargas se procesan mediante una cola interna para que varios usuarios puedan enviar trabajos sin recibir el antiguo error de «ya hay una descarga en curso».

La interfaz muestra:

- posición en cola;
- fase actual;
- porcentaje real comunicado por `yt-dlp`;
- velocidad y ETA cuando están disponibles;
- progreso de la conversión FFmpeg cuando se conoce la duración.

La cola admite 8 trabajos pendientes de forma predeterminada y puede ajustarse con `MAX_QUEUE_SIZE`.

## Monitor automático de plataformas

El workflow `Platform health` se ejecuta diariamente y comprueba las plataformas admitidas contra enlaces públicos de referencia usando `yt-dlp --skip-download`.

Si alguna plataforma deja de responder:

1. el workflow queda marcado como fallido;
2. se abre o actualiza una única incidencia de GitHub con el estado de todas las plataformas;
3. cuando todas vuelven a funcionar, la incidencia se cierra automáticamente.

Esto ayuda a detectar cambios de TikTok, Instagram, X y otras plataformas antes de encontrarlos manualmente.

## Seguridad

- Solo se aceptan HTTP/HTTPS.
- Los dominios están en una allowlist explícita.
- Se rechazan credenciales en URL y puertos no estándar.
- La resolución DNS rechaza direcciones privadas, loopback, link-local y otros destinos no públicos para reducir el riesgo SSRF.
- Se rechazan listas, carruseles y publicaciones con varios vídeos.
- Límite máximo de archivo: 500 MB.
- Los detalles internos de `yt-dlp` y FFmpeg se guardan en logs, no se muestran en el navegador.
- Los errores públicos usan códigos `RDL-xxxx`; los fallos internos incorporan además una referencia corta para localizar el evento en los logs.
- Los trabajos usan identificadores UUID y los archivos temporales se eliminan tras la descarga o por limpieza periódica.
- El contenedor se ejecuta sin root, con capacidades eliminadas y filesystem de solo lectura en los Compose incluidos.

RitaDown no incluye autenticación. Si se accede desde Internet debe situarse detrás de un proxy inverso con HTTPS y autenticación o de una solución equivalente.

## Instalación

```yaml
services:
  ritadown:
    image: ghcr.io/rit4lin/ritadown:latest
    ports:
      - "127.0.0.1:8787:8787"
    volumes:
      - ritadown-data:/app/downloads
    restart: unless-stopped

volumes:
  ritadown-data:
```

Arranque:

```bash
docker compose up -d
```

Actualización:

```bash
docker compose pull
docker compose up -d
```

Interfaz local: `http://127.0.0.1:8787`.

## Variables opcionales

```env
EXTRACT_TIMEOUT_SECONDS=60
DOWNLOAD_TIMEOUT_SECONDS=1800
MAX_QUEUE_SIZE=8
JOB_MAX_AGE_SECONDS=3600
VIDEO_ENCODER_BACKEND=auto
```

`VIDEO_ENCODER_BACKEND` admite `auto`, `nvenc` o `cpu`.

## NVIDIA / NVENC

En Unraid instala primero **Nvidia-Driver** y expón la GPU al contenedor. El archivo `compose.nvidia.yaml` incluido activa NVENC:

```bash
docker compose -f compose.yaml -f compose.nvidia.yaml up -d
```

NVENC solo afecta a las conversiones de vídeo H.264/H.265.

## Desarrollo

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Para levantar el contenedor local:

```powershell
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml up -d
```

Ver logs:

```powershell
docker compose logs -f --tail=100
```

## Versiones y releases

RitaDown usa versionado semántico. Al cambiar `VERSION` en `main`, GitHub Actions publica automáticamente:

- `ghcr.io/rit4lin/ritadown:X.Y.Z`
- `ghcr.io/rit4lin/ritadown:X.Y`
- `ghcr.io/rit4lin/ritadown:X`
- `ghcr.io/rit4lin/ritadown:latest`

También crea la Release `vX.Y.Z`.

La versión de `yt-dlp` está fijada en `requirements.txt` para que las imágenes sean reproducibles. Dependabot comprueba actualizaciones periódicamente.
