"""Esquemas Pydantic de WebAuthn (huella digital / Face ID).

El objeto ``publicKey`` que consume ``navigator.credentials`` es JSON con
nombres en *camelCase* y binarios en base64url; por eso se declara como ``dict``
y se documenta en la descripción, en lugar de duplicar la estructura del
estándar (que ya genera y valida ``py_webauthn``).

La parte que sí se valida acá es la que cruza el límite de confianza: el
``challenge_token`` firmado por el servidor y el nombre visible del dispositivo.
"""

from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class OpcionesWebAuthnRead(BaseModel):
    """Desafío + opciones que el navegador pasa a ``navigator.credentials``.

    ``challenge_token`` es un JWT de vida corta (2 minutos por defecto) que
    contiene el desafío y el usuario. El navegador lo devuelve tal cual en el
    paso de verificación: así el servidor no necesita guardar estado entre las
    dos peticiones (funciona igual en Vercel, donde cada invocación puede caer
    en una instancia distinta).
    """

    challenge_token: str = Field(
        description="JWT con el desafío y el usuario; se devuelve en la verificación."
    )
    publicKey: Dict[str, Any] = Field(
        description=(
            "Opciones WebAuthn en JSON (challenge y credenciales en base64url) "
            "para navigator.credentials.create()/get()."
        )
    )


class WebAuthnOpcionesLoginRequest(BaseModel):
    """Email del profesional que quiere entrar con huella."""

    email: EmailStr = Field(examples=["doctora@consultorio.com"])

    @field_validator("email")
    @classmethod
    def _email_normalizado(cls, valor: str) -> str:
        return valor.strip().lower()


class RegistroBiometricoRequest(BaseModel):
    """Credencial recién creada por ``navigator.credentials.create()``."""

    challenge_token: str = Field(min_length=10)
    credential: Dict[str, Any] = Field(
        description="PublicKeyCredential serializado (id, rawId, response)."
    )
    nombre_dispositivo: str = Field(
        default="Dispositivo",
        max_length=100,
        description='Nombre para reconocerlo, por ejemplo "Celular del doctor".',
        examples=["Celular del doctor"],
    )

    @field_validator("nombre_dispositivo")
    @classmethod
    def _nombre_limpio(cls, valor: str) -> str:
        limpio = " ".join((valor or "").split())
        return limpio[:100] or "Dispositivo"


class LoginBiometricoRequest(BaseModel):
    """Firma de la huella devuelta por ``navigator.credentials.get()``."""

    challenge_token: str = Field(min_length=10)
    credential: Dict[str, Any] = Field(
        description="PublicKeyCredential serializado (id, rawId, response)."
    )


class CredencialBiometricaRead(BaseModel):
    """Dispositivo registrado tal como se lista en el perfil del usuario."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nombre_dispositivo: str
    fecha_registro: datetime


class CredencialRegistradaRead(BaseModel):
    """Respuesta del alta: el mensaje y la credencial recién guardada."""

    detail: str
    credencial: CredencialBiometricaRead
