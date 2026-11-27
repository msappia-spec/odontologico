"""Esquemas Pydantic del módulo de usuarios, autenticación y permisos.

Reglas que se validan acá (y no en los endpoints) para que cualquier cliente
—panel, scripts, consola— reciba el mismo contrato:

* la contraseña es **fuerte**: mínimo 8 caracteres, al menos una mayúscula, una
  minúscula y un número (y como máximo 72 bytes por la limitación de bcrypt);
* los módulos son códigos del catálogo; uno inventado devuelve HTTP 422;
* el email se normaliza a minúsculas para que el login no distinga mayúsculas.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import normalizar_modulos

#: Descripción de la regla de contraseña (se repite en la UI y en OpenAPI).
REGLA_PASSWORD = (
    "Mínimo 8 caracteres, con al menos una mayúscula, una minúscula y un número."
)


class PasswordFuerte(BaseModel):
    """Contraseña en claro que sólo viaja en el cuerpo de la solicitud.

    Nunca se serializa de vuelta: los esquemas de lectura sólo exponen
    ``hashed_password`` en la base y jamás el valor original.
    """

    password: str = Field(
        min_length=8,
        max_length=64,
        description=REGLA_PASSWORD,
        examples=["ClaveSegura2026"],
    )

    @field_validator("password")
    @classmethod
    def _validar_fuerza(cls, valor: str) -> str:
        faltantes = []
        if not any(caracter.isupper() for caracter in valor):
            faltantes.append("una mayúscula")
        if not any(caracter.islower() for caracter in valor):
            faltantes.append("una minúscula")
        if not any(caracter.isdigit() for caracter in valor):
            faltantes.append("un número")
        if faltantes:
            raise ValueError(
                "La contraseña debe incluir al menos " + ", ".join(faltantes) + "."
            )
        return valor


class LoginRequest(BaseModel):
    """Credenciales del login.

    No se aplica la regla de contraseña fuerte al login: una cuenta creada con
    una clave anterior debe poder entrar (y no hay que dar pistas sobre el
    formato esperado a quien prueba contraseñas).
    """

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ModuloRead(BaseModel):
    """Módulo del catálogo de permisos (para dibujar los checkboxes)."""

    codigo: str
    nombre: str
    descripcion: str


class UsuarioCreate(PasswordFuerte):
    """Alta de un usuario desde «Usuarios y permisos»."""

    email: EmailStr
    nombre_completo: str = Field(min_length=2, max_length=150)
    es_superadmin: bool = Field(
        default=False,
        description="Si es True accede a todo, sin importar los módulos marcados.",
    )
    activo: bool = True
    doctor_id: Optional[int] = Field(
        default=None,
        description="Odontólogo vinculado (opcional). Debe existir en doctores.",
    )
    modulos_permitidos: List[str] = Field(
        default_factory=list,
        description="Códigos del catálogo de módulos habilitados.",
    )

    @field_validator("email")
    @classmethod
    def _email_normalizado(cls, valor: str) -> str:
        return valor.strip().lower()

    @field_validator("nombre_completo")
    @classmethod
    def _nombre_limpio(cls, valor: str) -> str:
        return " ".join(valor.split())

    @field_validator("modulos_permitidos")
    @classmethod
    def _modulos_validos(cls, valor: List[str]) -> List[str]:
        return normalizar_modulos(valor)


class UsuarioPermisosUpdate(BaseModel):
    """Cambios de permisos y/o estado de la cuenta.

    Todos los campos son opcionales: se envía sólo lo que cambia. Es el mismo
    endpoint para marcar checkboxes y para activar/desactivar un empleado.
    """

    modulos_permitidos: Optional[List[str]] = Field(
        default=None,
        description="Reemplaza la lista completa de módulos habilitados.",
    )
    activo: Optional[bool] = Field(
        default=None, description="False bloquea el acceso de inmediato."
    )
    es_superadmin: Optional[bool] = Field(
        default=None,
        description="Sólo un superadmin puede cambiar esta marca.",
    )
    doctor_id: Optional[int] = Field(
        default=None,
        description=(
            "Odontólogo al que se vincula la cuenta. Omitirlo deja el vínculo "
            "como está (no se puede desvincular desde el panel)."
        ),
    )

    @field_validator("modulos_permitidos")
    @classmethod
    def _modulos_validos(cls, valor: Optional[List[str]]) -> Optional[List[str]]:
        return None if valor is None else normalizar_modulos(valor)


class ResetPasswordRequest(PasswordFuerte):
    """Blanqueo de contraseña por parte de un administrador."""


class UsuarioRead(BaseModel):
    """Usuario tal como se muestra en el panel (sin el hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    nombre_completo: str
    es_superadmin: bool
    activo: bool
    doctor_id: Optional[int] = None
    doctor_nombre: Optional[str] = None
    modulos_permitidos: List[str] = Field(default_factory=list)
    ultimo_acceso: Optional[datetime] = None


class SesionRead(BaseModel):
    """Respuesta del login: el token y el usuario con sus permisos."""

    access_token: str
    token_type: str = "bearer"
    expira_en_minutos: int
    usuario: UsuarioRead


class MensajeRead(BaseModel):
    """Respuesta simple para operaciones sin cuerpo (logout, reset)."""

    detail: str
