"""Esquemas Pydantic del portal público de turnos (``/reservar``).

El portal no comparte los esquemas internos de paciente y turno a propósito:
así la respuesta pública sólo puede incluir los campos que se declaran acá y
nunca se filtra por accidente un teléfono, un apellido, una obra social de otro
paciente ni el motivo de consulta de un tercero.

Convención de fechas: ``fecha_hora`` llega desde el navegador como hora *de
pared* del consultorio (``2026-11-30T09:00``), sin zona horaria, igual que el
resto del ERP (ver ``app.core.datetime_utils``).
"""

import re
from datetime import date as Date
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.datetime_utils import to_naive_utc

#: Mismo formato de documento que el alta interna de pacientes: 5 a 20
#: caracteres alfanuméricos, sin puntos, guiones ni espacios.
_RE_DOCUMENTO = re.compile(r"^[0-9A-Za-z]{5,20}$")
#: Largo máximo del motivo que escribe el paciente (coincide con ``Turno``).
MAX_MOTIVO_CONSULTA = 500


def normalizar_dni(valor: str) -> str:
    """Limpia el documento y valida su formato (``ValueError`` si no sirve)."""
    texto = re.sub(r"[\s.\-]", "", str(valor or ""))
    if not _RE_DOCUMENTO.match(texto):
        raise ValueError(
            "El DNI debe tener entre 5 y 20 caracteres alfanuméricos "
            "(se ignoran puntos, guiones y espacios)."
        )
    return texto


def _texto_obligatorio(valor: str, campo: str, maximo: int) -> str:
    """Recorta un texto obligatorio y rechaza los valores vacíos."""
    texto = str(valor or "").strip()
    if not texto:
        raise ValueError(f"{campo} no puede estar vacío.")
    if len(texto) > maximo:
        raise ValueError(f"{campo} no puede superar los {maximo} caracteres.")
    return texto


class PortalDoctorRead(BaseModel):
    """Profesional activo que el paciente puede elegir (sin datos internos)."""

    id: int
    nombre: str
    especialidad: str


class PortalObraSocialRead(BaseModel):
    """Cobertura activa que se ofrece en el paso de datos de contacto."""

    id: int
    nombre: str


class PortalInicialRead(BaseModel):
    """Catálogos públicos del portal: profesionales activos y coberturas."""

    doctores: List[PortalDoctorRead]
    obras_sociales: List[PortalObraSocialRead]


class PortalDisponibilidadRead(BaseModel):
    """Horarios libres de un profesional en una fecha.

    Sólo viajan las horas: no se expone el turno que ocupa un hueco ni ningún
    dato del paciente que lo reservó (regla de privacidad del portal).
    """

    doctor_id: int
    fecha: Date
    dia: str
    duracion_minutos: int
    horarios: List[str]


class PortalPacienteRead(BaseModel):
    """Resultado de buscar un DNI en el registro de pacientes.

    Si existe se devuelve **sólo el nombre de pila** y la obra social, con el
    teléfono enmascarado (``***4589``) para que el paciente reconozca su ficha
    sin que la respuesta sirva para cosechar datos de terceros.
    """

    nuevo: bool
    nombre: Optional[str] = None
    obra_social_id: Optional[int] = None
    obra_social: Optional[str] = None
    telefono_enmascarado: Optional[str] = None


class PortalReservaCreate(BaseModel):
    """Datos que envía el formulario público para reservar un turno."""

    dni: str = Field(..., max_length=20)
    nombre: str = Field(..., max_length=120)
    apellido: str = Field(
        "",
        max_length=120,
        description=(
            "Apellido del paciente. Es obligatorio para un DNI nuevo; en una "
            "ficha existente el alta conserva el nombre ya registrado."
        ),
    )
    telefono_whatsapp: str = Field(
        ...,
        min_length=6,
        max_length=30,
        description="Teléfono con o sin código de país; se normaliza para WAHA.",
    )
    obra_social_id: Optional[int] = Field(None, gt=0)
    doctor_id: int = Field(..., gt=0)
    fecha_hora: datetime
    motivo_consulta: Optional[str] = Field(None, max_length=MAX_MOTIVO_CONSULTA)

    @field_validator("dni", mode="before")
    @classmethod
    def _dni_valido(cls, valor):
        return normalizar_dni(valor)

    @field_validator("nombre", mode="before")
    @classmethod
    def _nombre(cls, valor):
        return _texto_obligatorio(valor, "nombre", 120)

    @field_validator("apellido", mode="before")
    @classmethod
    def _apellido(cls, valor):
        """Se recorta siempre; el servicio exige que exista sólo si el DNI es nuevo."""
        return str(valor or "").strip()

    @field_validator("telefono_whatsapp", mode="before")
    @classmethod
    def _telefono(cls, valor):
        texto = str(valor or "").strip()
        digitos = "".join(c for c in texto if c.isdigit())
        if len(digitos) < 6:
            raise ValueError("El teléfono debe tener al menos 6 dígitos.")
        return texto

    @field_validator("obra_social_id", mode="before")
    @classmethod
    def _obra_social_vacia(cls, valor):
        """El ``<select>`` manda ``""`` cuando el paciente no tiene cobertura."""
        if valor is None:
            return None
        if isinstance(valor, str):
            texto = valor.strip()
            return int(texto) if texto else None
        return valor

    @field_validator("motivo_consulta", mode="before")
    @classmethod
    def _motivo(cls, valor):
        texto = str(valor or "").strip()
        return texto or None

    @model_validator(mode="after")
    def _fecha_normalizada(self) -> "PortalReservaCreate":
        self.fecha_hora = to_naive_utc(self.fecha_hora)
        return self


class PortalReservaRead(BaseModel):
    """Confirmación que se muestra al paciente tras reservar."""

    turno_id: int
    fecha_hora: datetime
    fecha: Date
    hora: str
    doctor: str
    especialidad: str
    paciente: str
    obra_social: Optional[str] = None
    motivo_consulta: Optional[str] = None
    codigo_cancelacion: str
    whatsapp_enviado: bool = False


class PortalCancelacionCreate(BaseModel):
    """Cancelación con los dos datos que conoce el paciente: DNI y código."""

    dni: str = Field(..., max_length=20)
    codigo: str = Field(
        ...,
        max_length=20,
        description="Código corto de autogestión o número de turno.",
    )

    @field_validator("dni", mode="before")
    @classmethod
    def _dni_valido(cls, valor):
        return normalizar_dni(valor)

    @field_validator("codigo", mode="before")
    @classmethod
    def _codigo(cls, valor):
        texto = str(valor or "").strip().upper()
        if not texto:
            raise ValueError("Ingresá el código de cancelación del turno.")
        return texto


class PortalCancelacionRead(BaseModel):
    """Resultado de una cancelación pública."""

    turno_id: int
    fecha_hora: datetime
    estado_turno: str
    ya_cancelado: bool
    mensaje: str

