"""Tokens firmados para la ficha pública de salud (anamnesis).

El paciente recibe por WhatsApp un enlace ``/anamnesis/<token>`` y **no** inicia
sesión. Para que el enlace sea seguro y no permita cambiar el turno ni el
paciente de la URL, el token es un valor **HMAC-SHA256** con esta forma::

    base64url(payload_json) "." base64url(firma)

El payload sólo contiene ``turno_id``, ``paciente_id`` y la versión del formato.
El servidor verifica la firma con :attr:`Settings.anamnesis_secreto_efectivo`
antes de leer cualquier dato; si alguien cambia un id, la firma deja de coincidir
y se responde un error de dominio (HTTP 400).

El token es determinístico por turno/paciente (no se guarda una columna extra ni
se necesita una tabla de enlaces): mientras el turno exista, el enlace sigue
siendo válido aunque se reenvíe el recordatorio.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings
from app.core.exceptions import AppError

#: Versión del payload, para poder evolucionar el formato sin ambigüedad.
VERSION_TOKEN_ANAMNESIS = 1
#: Largo máximo aceptado (un token real ronda los 100 caracteres).
_MAX_TOKEN = 512


class TokenAnamnesisInvalidoError(AppError):
    """El token no tiene la firma esperada o su formato está corrupto."""

    status_code = 400
    default_detail = (
        "Este enlace de la ficha médica no es válido. Pedí uno nuevo en el "
        "consultorio."
    )


@dataclass(frozen=True)
class DatosTokenAnamnesis:
    """Identificadores confiables extraídos de un token ya verificado."""

    turno_id: int
    paciente_id: int


def _b64url_encode(valor: bytes) -> str:
    """Codifica bytes en base64 URL-safe sin relleno ``=``."""
    return base64.urlsafe_b64encode(valor).decode("ascii").rstrip("=")


def _b64url_decode(texto: str) -> bytes:
    """Decodifica base64 URL-safe agregando el relleno que se quitó."""
    relleno = "=" * (-len(texto) % 4)
    return base64.urlsafe_b64decode(texto + relleno)


def firmar_token_anamnesis(turno_id: int, paciente_id: int) -> str:
    """Firma el par ``(turno_id, paciente_id)`` para enviarlo en un enlace."""
    payload: dict[str, Any] = {
        "v": VERSION_TOKEN_ANAMNESIS,
        "turno_id": int(turno_id),
        "paciente_id": int(paciente_id),
    }
    crudo = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    firma = hmac.new(
        settings.anamnesis_secreto_efectivo.encode("utf-8"),
        crudo,
        hashlib.sha256,
    ).digest()
    return f"{_b64url_encode(crudo)}.{_b64url_encode(firma)}"


def validar_token_anamnesis(token: Optional[str]) -> DatosTokenAnamnesis:
    """Valida la firma y devuelve los ids; lanza 400 si no es confiable.

    Se usa ``hmac.compare_digest`` para comparar en tiempo constante. Cualquier
    error de formato se traduce al mismo mensaje: no conviene revelar si el
    token «existió» o cuál de las dos partes falló.
    """
    valor = (token or "").strip()
    if not valor or len(valor) > _MAX_TOKEN or valor.count(".") != 1:
        raise TokenAnamnesisInvalidoError()

    parte_payload, parte_firma = valor.split(".", 1)
    try:
        crudo = _b64url_decode(parte_payload)
        firma = _b64url_decode(parte_firma)
    except (ValueError, TypeError) as exc:
        raise TokenAnamnesisInvalidoError() from exc

    esperada = hmac.new(
        settings.anamnesis_secreto_efectivo.encode("utf-8"),
        crudo,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(firma, esperada):
        raise TokenAnamnesisInvalidoError()

    try:
        payload = json.loads(crudo.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("El payload del token no es un objeto JSON.")
        if payload.get("v") != VERSION_TOKEN_ANAMNESIS:
            raise ValueError("Versión de token no soportada.")
        turno_id = int(payload["turno_id"])
        paciente_id = int(payload["paciente_id"])
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise TokenAnamnesisInvalidoError() from exc

    if turno_id <= 0 or paciente_id <= 0:
        raise TokenAnamnesisInvalidoError()
    return DatosTokenAnamnesis(turno_id=turno_id, paciente_id=paciente_id)


def construir_enlace_anamnesis(turno_id: int, paciente_id: int) -> str:
    """URL pública absoluta que recibe el paciente por WhatsApp."""
    token = firmar_token_anamnesis(turno_id, paciente_id)
    return f"{settings.url_publica}/anamnesis/{token}"
