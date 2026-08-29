from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


ALLOWED_DOMAIN_SUFFIXES = (
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "x.com",
    "twitter.com",
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
)
ALLOWED_PORTS = {80, 443}


class URLValidationError(ValueError):
    """Error de URL apto para mostrarse al usuario."""


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    url: str
    hostname: str


def _is_allowed_hostname(hostname: str) -> bool:
    return any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in ALLOWED_DOMAIN_SUFFIXES
    )


def _is_public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def resolve_public_addresses(hostname: str) -> tuple[str, ...]:
    """Resuelve todas las direcciones y rechaza cualquier destino no público."""
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLValidationError("No se pudo resolver el dominio indicado.") from exc

    addresses = tuple(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise URLValidationError("El dominio no tiene una dirección válida.")
    if any(not _is_public_address(address) for address in addresses):
        raise URLValidationError("La URL apunta a una red privada o no permitida.")
    return addresses


def validate_media_url(raw_url: str, *, resolve_dns: bool = True) -> ValidatedURL:
    candidate = raw_url.strip()
    if not candidate or len(candidate) > 2048:
        raise URLValidationError("Introduce una URL válida.")
    if any(ord(character) < 32 for character in candidate):
        raise URLValidationError("La URL contiene caracteres no permitidos.")

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise URLValidationError("La URL no tiene un formato válido.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise URLValidationError("Solo se permiten enlaces HTTP o HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise URLValidationError("La URL no puede incluir usuario ni contraseña.")
    if not parsed.hostname:
        raise URLValidationError("La URL debe incluir un dominio.")
    if port is not None and port not in ALLOWED_PORTS:
        raise URLValidationError("La URL utiliza un puerto no permitido.")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise URLValidationError("El dominio no es válido.") from exc

    if not _is_allowed_hostname(hostname):
        raise URLValidationError(
            "Solo se admiten enlaces públicos de Instagram, YouTube, Facebook y X/Twitter."
        )
    if resolve_dns:
        resolve_public_addresses(hostname)

    host_for_url = hostname
    if port is not None:
        host_for_url = f"{hostname}:{port}"
    normalized = urlunsplit(
        (parsed.scheme.lower(), host_for_url, parsed.path or "/", parsed.query, parsed.fragment)
    )
    return ValidatedURL(url=normalized, hostname=hostname)
