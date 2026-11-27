"""WebAuthn / passkeys: configuración del Relying Party y verificación.

Este módulo encapsula todo lo delicado del estándar WebAuthn para que los
endpoints sólo se ocupen del flujo HTTP:

* resuelve ``RP_ID`` (dominio) y ``RP_ORIGIN`` (URL completa) desde la
  configuración del proyecto;
* genera las opciones de **registro** y de **login** con ``py_webauthn``;
* verifica criptográficamente la firma del navegador (clave pública COSE,
  ``clientDataJSON``, ``authenticatorData`` y origen);
* emite y valida el **token de desafío**: un JWT de vida corta que transporta el
  ``challenge`` y el usuario entre las dos peticiones del flujo.

¿Por qué un token de desafío y no una sesión en memoria? El despliegue corre en
Vercel (serverless): la petición de *opciones* y la de *verificación* pueden
caer en instancias distintas, así que un diccionario en memoria fallaría de
forma intermitente. El JWT va firmado con ``SECRET_KEY`` y expira en minutos:
el cliente no puede inventar un desafío, cambiarlo ni reutilizarlo después de
ese plazo. La reutilización inmediata de una firma capturada no aporta nada al
atacante sin la clave privada del dispositivo, y el contador ``sign_count``
corta la clonación cuando el autenticador lo implementa.

La biometría **no reemplaza** la contraseña: es un atajo para el celular. Si el
dispositivo no soporta passkeys, el login tradicional sigue disponible.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import timedelta
from typing import Optional, Sequence, Tuple

import jwt
from fastapi import HTTPException, status
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.core.security import ALGORITMO_JWT

logger = logging.getLogger("app.core.webauthn")

#: Tipo de desafío para dar de alta un dispositivo (usuario ya autenticado).
TIPO_DESAFIO_REGISTRO = "registro"
#: Tipo de desafío para ingresar con huella (endpoint público).
TIPO_DESAFIO_LOGIN = "login"


# --------------------------------------------------------------------------- #
# Base64url (el formato de binarios de WebAuthn)
# --------------------------------------------------------------------------- #
def b64url(datos: bytes) -> str:
    """Codifica bytes a base64url **sin relleno** (formato del estándar)."""
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def bytes_de_b64url(valor: str) -> bytes:
    """Decodifica base64url con o sin relleno.

    Lanza ``ValueError`` (o su subclase ``binascii.Error``) si el texto no es
    base64url válido; los llamadores deciden si eso es un 400 o un 401.
    """
    texto = (valor or "").strip()
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def credential_id_canonico(valor: str) -> Optional[str]:
    """Devuelve el ``credential_id`` en la forma canónica de la base.

    Algunos navegadores envían el identificador con ``=`` de relleno y otros
    sin él; la base guarda siempre base64url sin relleno. ``None`` indica que el
    valor recibido no es base64url válido (se responde como credencial
    desconocida, sin filtrar detalles).
    """
    try:
        return b64url(bytes_de_b64url(valor))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Token de desafío (JWT de vida corta)
# --------------------------------------------------------------------------- #
def crear_token_desafio(desafio: bytes, usuario_id: uuid.UUID, tipo: str) -> str:
    """Firma un JWT con el ``challenge`` y el usuario al que pertenece.

    El token viaja al navegador y vuelve en la verificación. No contiene
    secretos (el desafío es público por definición); la firma impide que el
    cliente lo altere y permite validar la ventana temporal sin estado.
    """
    ahora = utcnow()
    minutos = max(1, int(settings.webauthn_challenge_expire_minutes))
    payload = {
        "sub": str(usuario_id),
        "challenge": b64url(desafio),
        "tipo": tipo,
        "iat": ahora,
        "exp": ahora + timedelta(minutes=minutos),
    }
    return jwt.encode(payload, settings.jwt_secret_efectivo, algorithm=ALGORITMO_JWT)


def leer_token_desafio(token: str, tipo_esperado: str) -> Tuple[uuid.UUID, bytes]:
    """Valida el token de desafío y devuelve ``(usuario_id, challenge)``.

    Se comprueba la firma, la expiración, el tipo (un desafío de registro no
    sirve para login y viceversa) y que el ``sub`` sea un UUID. Cualquier fallo
    es un 400 uniforme: no se distingue token vencido de token adulterado.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_efectivo, algorithms=[ALGORITMO_JWT]
        )
        if payload.get("tipo") != tipo_esperado:
            raise ValueError("tipo de desafío incorrecto")
        usuario_id = uuid.UUID(str(payload["sub"]))
        desafio = bytes_de_b64url(str(payload["challenge"]))
        if not desafio:
            raise ValueError("desafío vacío")
        return usuario_id, desafio
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "Desafío WebAuthn rechazado (%s): %s", tipo_esperado, type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El desafío biométrico expiró o no es válido. "
                "Volvé a intentar el ingreso con huella."
            ),
        ) from exc



# --------------------------------------------------------------------------- #
# Opciones (lo que consume navigator.credentials)
# --------------------------------------------------------------------------- #
def _descriptores(credenciales_ids: Sequence[str]) -> list[PublicKeyCredentialDescriptor]:
    """Convierte IDs canónicos de la base a descriptores WebAuthn."""
    descriptores: list[PublicKeyCredentialDescriptor] = []
    for valor in credenciales_ids:
        try:
            descriptores.append(
                PublicKeyCredentialDescriptor(id=bytes_de_b64url(valor))
            )
        except (ValueError, TypeError):  # pragma: no cover - dato corrupto
            logger.warning("Se omite una credencial con ID inválido en la base.")
    return descriptores


def opciones_de_registro(
    *,
    usuario_id: uuid.UUID,
    email: str,
    nombre_completo: str,
    credenciales_ids: Sequence[str] = (),
) -> Tuple[dict, bytes]:
    """Genera las opciones de alta de un dispositivo y su desafío en bytes.

    ``exclude_credentials`` evita registrar dos veces el mismo celular: el
    navegador avisa con ``InvalidStateError`` en lugar de crear una llave
    duplicada. Se pide un *resident key* preferido (passkey) y verificación de
    usuario preferida; el servidor acepta autenticadores que no la implementen
    (la biometría la exige el propio sistema operativo del celular).
    """
    opciones = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=str(usuario_id).encode("utf-8"),
        user_name=email,
        user_display_name=nombre_completo or email,
        timeout=max(10000, int(settings.webauthn_timeout_ms)),
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=_descriptores(credenciales_ids),
    )
    return json.loads(options_to_json(opciones)), opciones.challenge


def opciones_de_login(*, credenciales_ids: Sequence[str] = ()) -> Tuple[dict, bytes]:
    """Genera el desafío de ingreso y la lista de llaves aceptadas.

    ``allow_credentials`` vacío deja que el navegador ofrezca cualquier passkey
    del dominio; con IDs, sólo las registradas por esa cuenta (mejor UX y menos
    intentos fallidos). El endpoint decide qué lista mandar según el email.
    """
    opciones = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        timeout=max(10000, int(settings.webauthn_timeout_ms)),
        allow_credentials=_descriptores(credenciales_ids),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return json.loads(options_to_json(opciones)), opciones.challenge


# --------------------------------------------------------------------------- #
# Verificación de firmas
# --------------------------------------------------------------------------- #
def verificar_registro(credencial: dict, desafio: bytes):
    """Valida la respuesta de ``navigator.credentials.create()``.

    Devuelve el objeto ``VerifiedRegistration`` de ``py_webauthn`` (incluye
    ``credential_id``, ``credential_public_key`` y ``sign_count``). Se pide
    presencia del usuario y no se exige UV: algunos autenticadores de plataforma
    marcan UV de forma distinta, pero la biometría ya la valida el sistema.
    """
    try:
        return verify_registration_response(
            credential=credencial,
            expected_challenge=desafio,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_rp_origin,
            require_user_presence=True,
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001 - el estándar lanza varios subtipos
        # Sólo se registra la clase del error: el mensaje puede incluir datos
        # del autenticador y no aporta nada al diagnóstico del consultorio.
        logger.warning("Registro WebAuthn inválido: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No se pudo validar la huella de este dispositivo. "
                "Probá de nuevo o seguí usando tu contraseña."
            ),
        ) from exc


def verificar_login(
    credencial: dict,
    desafio: bytes,
    *,
    public_key: bytes,
    sign_count: int,
):
    """Valida la firma de ``navigator.credentials.get()``.

    Comprueba el desafío, el origen, el ``RP_ID`` y la firma contra la clave
    pública guardada. Devuelve ``VerifiedAuthentication``; el contador nuevo se
    persiste en el endpoint (acá no se toca la base).
    """
    try:
        return verify_authentication_response(
            credential=credencial,
            expected_challenge=desafio,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_rp_origin,
            credential_public_key=public_key,
            credential_current_sign_count=int(sign_count or 0),
            require_user_verification=False,
        )
    except Exception as exc:  # noqa: BLE001 - el estándar lanza varios subtipos
        logger.warning("Login WebAuthn inválido: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "No pudimos validar tu huella. Probá de nuevo o ingresá con "
                "tu contraseña."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
