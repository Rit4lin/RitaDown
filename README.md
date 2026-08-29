# RitaDown

Aplicación web privada, sencilla y adaptada a móviles para analizar publicaciones públicas y descargar un único vídeo en MP4 o extraer su audio en MP3. Se distribuye como imagen Docker y está preparada para ejecutarse en tu propio servidor.

> Usa RitaDown únicamente con contenido propio o cuando dispongas de autorización para descargarlo. Respeta los derechos de autor, los términos de cada plataforma y la legislación aplicable.

## Plataformas previstas

- Instagram (`instagram.com`).
- TikTok (`tiktok.com`, incluidos sus enlaces cortos `vm.tiktok.com` y `vt.tiktok.com`).
- YouTube (`youtube.com`, `youtu.be` y `youtube-nocookie.com`).
- Facebook (`facebook.com` y `fb.watch`).
- X/Twitter (`x.com` y `twitter.com`).

El soporte real depende de los cambios de cada plataforma y de la versión de `yt-dlp`. Solo se admiten publicaciones públicas: no se usan cookies, cuentas, credenciales, evasión de DRM ni técnicas para saltar restricciones.

Para TikTok, RitaDown descarga el flujo público original que `yt-dlp` pueda obtener. Normalmente es el flujo sin marca de agua que la plataforma expone para esa publicación; RitaDown no elimina ni altera marcas de agua mediante procesamiento de vídeo. Si TikTok no ofrece ese flujo o limita el acceso, la descarga puede fallar.

La imagen incluye Deno y `yt-dlp-ejs` exclusivamente como runtime interno para el soporte moderno de YouTube. El frontend continúa siendo HTML, CSS y JavaScript directo, sin Node.js ni proceso de compilación.

## Límites y seguridad

- Solo se aceptan HTTP/HTTPS, dominios permitidos y destinos DNS con direcciones IP públicas.
- Se rechazan credenciales en la URL, puertos no estándar, listas, carruseles y descargas múltiples.
- Hay un límite de 500 MB, un único procesamiento simultáneo, timeouts y limitación básica por IP.
- Cada operación vuelve a validar el enlace. La validación DNS reduce el riesgo SSRF, aunque `yt-dlp` necesita seguir redirecciones y acceder a CDN de las plataformas; por ello no debe exponerse directamente a Internet.
- El formato MP4 permite conservar el códec original o convertir expresamente a H.264 o H.265/HEVC. La opción original es la más rápida. H.264 ofrece mayor compatibilidad; H.265 suele ocupar menos, pero tarda más y no funciona en todos los dispositivos.
- Las conversiones H.264/H.265 usan NVIDIA NVENC cuando `VIDEO_ENCODER_BACKEND` lo solicita o cuando el modo `auto` detecta una GPU NVIDIA expuesta por Docker. Si NVENC falla, la aplicación reintenta automáticamente mediante CPU.
- Las calidades de vídeo son original, 2160p, 1440p, 1080p, 720p, 480p y 360p. Cada valor es un máximo: nunca se amplía artificialmente una fuente de menor resolución.
- El formato MP3 permite elegir 320, 256, 192 o 128 kbps. Convertir a un bitrate alto no recupera calidad que la fuente no tenga.
- Los archivos se guardan en directorios UUID y se borran al terminar la respuesta. Una tarea periódica elimina restos antiguos tras fallos o interrupciones.

## Instalación

La imagen publicada se encuentra en GitHub Container Registry. Crea un archivo `compose.yaml` con este contenido:

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

Arranca el servicio:

```bash
docker compose up -d
```

Abre <http://127.0.0.1:8787>. Para actualizar a la última versión publicada:

```bash
docker compose pull
docker compose up -d
```

El puerto se enlaza de forma deliberada solo a `127.0.0.1`. Para usar RitaDown desde otro equipo, publícalo detrás de una VPN privada o de un proxy inverso con HTTPS y autenticación. No lo expongas directamente a Internet.

## NVIDIA / NVENC opcional

Si tu servidor tiene una GPU NVIDIA configurada para Docker, añade un archivo `compose.nvidia.yaml`:

```yaml
services:
  ritadown:
    runtime: nvidia
    environment:
      VIDEO_ENCODER_BACKEND: nvenc
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,video,utility
```

Y arranca con:

```bash
docker compose -f compose.yaml -f compose.nvidia.yaml up -d
```

En Unraid necesitas primero el plugin **Nvidia-Driver**. La aceleración se aplica únicamente al convertir a H.264 o H.265; no acelera MP3 ni el modo de códec original.

## Desarrollo local

No se necesita configuración para arrancar. Opcionalmente, puedes crear `.env` para cambiar los tiempos máximos de análisis y descarga:

```powershell
Copy-Item .env.example .env
```

`VIDEO_ENCODER_BACKEND` admite `auto` (valor predeterminado), `nvenc` o `cpu`.

## Construcción y arranque

```powershell
docker compose -f docker-compose.yml build
docker compose -f docker-compose.yml up -d
```

Abre <http://127.0.0.1:8787>, pega un enlace público compatible y pulsa **Analizar**. Después elige formato, calidad y, para MP4, el códec.

Ver registros:

```powershell
docker compose logs -f --tail=100
```

Detener la aplicación sin borrar el volumen temporal:

```powershell
docker compose down
```

Comprobar el healthcheck:

```powershell
docker compose ps
docker inspect --format='{{json .State.Health}}' video-downloader-video-downloader-1
```

El nombre real del contenedor aparece en `docker compose ps`. También puedes verificar el endpoint local:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/health
```

## Versiones y Releases

RitaDown usa [versionado semántico](https://semver.org/lang/es/):

- `v1.0.0`: primera versión estable.
- `v1.0.1`: corrección compatible.
- `v1.1.0`: funcionalidad nueva compatible.
- `v2.0.0`: cambio incompatible.

Cada etiqueta `vX.Y.Z` crea una Release de GitHub y publica las imágenes `ghcr.io/rit4lin/ritadown:X.Y.Z`, `X.Y`, `X` y `latest`. Para una instalación estable puedes fijar una versión concreta, por ejemplo `ghcr.io/rit4lin/ritadown:1.0.0`.

## Actualizar yt-dlp durante el desarrollo

La dependencia usa un mínimo de versión sin fijar el parche. Fuerza una reconstrucción sin caché para obtener una versión reciente compatible:

```powershell
docker compose -f docker-compose.yml build --pull --no-cache
docker compose -f docker-compose.yml up -d
```

Revisa los cambios de `yt-dlp` antes de actualizar en una instalación estable.

## Pruebas

Las pruebas unitarias no realizan conexiones reales: simulan las respuestas DNS relevantes y usan únicamente la biblioteca estándar.

Con Python 3.12 y un entorno virtual local:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

## Seguridad

RitaDown no incluye autenticación integrada. No montes el socket de Docker, no uses modo privilegiado ni `network_mode: host`, y no lo publiques directamente en Internet. Consulta [SECURITY.md](SECURITY.md) para informar de vulnerabilidades.
