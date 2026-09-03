# Changelog

Todos los cambios relevantes de RitaDown se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa [versionado semántico](https://semver.org/lang/es/).

## [1.1.2] - 2026-09-03

### Corregido

- Fijado `curl-cffi` en `0.16.0` como solución temporal al bloqueo actual de TikTok provocado por objetivos de impersonación más recientes.
- Los errores de la API muestran códigos públicos estables (`RDL-xxxx`) sin exponer `stderr`, rutas internas ni detalles sensibles.
- Los fallos internos incluyen una referencia corta que también queda registrada en los logs del contenedor.

### Cambiado

- Simplificada la interfaz eliminando avisos, notas y textos redundantes.
- Reducidos los mensajes de estado a indicaciones breves durante análisis, conversión y descarga.

## [1.1.1] - 2026-09-03

### Corregido

- Añadido soporte de impersonación de navegador de `yt-dlp` mediante `curl_cffi` para mejorar la compatibilidad con plataformas que aplican fingerprinting TLS y medidas anti-bot.
- Los errores técnicos de `yt-dlp` y FFmpeg quedan registrados en los logs del contenedor sin exponerlos en la interfaz web.

### Mantenimiento

- `yt-dlp` queda fijado a una versión concreta para que las actualizaciones sean reproducibles y puedan gestionarse mediante Dependabot.
- Añadido Dependabot para comprobar diariamente nuevas versiones de `yt-dlp`.
- CI valida también que la imagen Docker se pueda construir correctamente.

## [1.1.0] - 2026-08-29

### Añadido

- Compatibilidad con enlaces públicos de TikTok, incluidos `vm.tiktok.com` y `vt.tiktok.com`.

## [1.0.0] - 2026-08-29

### Añadido

- Descarga de publicaciones públicas de Instagram, YouTube, Facebook y X/Twitter.
- Salida en MP4 o MP3, con selección de calidad y códec H.264/H.265.
- Conversión opcional acelerada por NVIDIA NVENC con respaldo automático por CPU.
- Contenedor Docker sin usuario root y con límites de recursos.
- Compose de despliegue genérico y anulación opcional para GPU NVIDIA.
