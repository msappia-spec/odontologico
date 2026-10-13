"""Esquemas Pydantic de ``Turno``."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.datetime_utils import to_naive_utc
from app.models.enums import EstadoTurno, EstadoWhatsApp


class TurnoBase(BaseModel):
    paciente_id: int = Field(..., gt=0)
    doctor_id: int = Field(..., gt=0)
    fecha_hora_inicio: datetime
    fecha_hora_fin: datetime

    @model_validator(mode="after")
    def _normalizar_y_validar_rango(self) -> "TurnoBase":
        """Normaliza las fechas a UTC naive y valida el orden inicio < fin."""
        self.fecha_hora_inicio = to_naive_utc(self.fecha_hora_inicio)
        self.fecha_hora_fin = to_naive_utc(self.fecha_hora_fin)

        if self.fecha_hora_fin <= self.fecha_hora_inicio:
            raise ValueError(
                "fecha_hora_fin debe ser estrictamente posterior a fecha_hora_inicio"
            )
        return self


class TurnoCreate(TurnoBase):
    """Payload de creación de un turno."""

    estado_turno: EstadoTurno = EstadoTurno.PENDIENTE
    estado_whatsapp: EstadoWhatsApp = EstadoWhatsApp.PENDIENTE_ENVIO


class TurnoUpdate(BaseModel):
    """Payload de actualización parcial de un turno (``PATCH``).

    Todos los campos son opcionales: sólo se aplican los enviados. Si llegan las
    dos fechas se valida el orden; el servicio revalida además contra los valores
    vigentes del turno (para el caso en que se envíe una sola de las fechas).
    """

    paciente_id: Optional[int] = Field(None, gt=0)
    doctor_id: Optional[int] = Field(None, gt=0)
    fecha_hora_inicio: Optional[datetime] = None
    fecha_hora_fin: Optional[datetime] = None
    estado_turno: Optional[EstadoTurno] = None
    estado_whatsapp: Optional[EstadoWhatsApp] = None
    whatsapp_mensaje_id: Optional[str] = None

    @model_validator(mode="after")
    def _normalizar_y_validar_rango(self) -> "TurnoUpdate":
        """Normaliza las fechas a UTC naive y valida el orden cuando vienen ambas."""
        if self.fecha_hora_inicio is not None:
            self.fecha_hora_inicio = to_naive_utc(self.fecha_hora_inicio)
        if self.fecha_hora_fin is not None:
            self.fecha_hora_fin = to_naive_utc(self.fecha_hora_fin)

        if (
            self.fecha_hora_inicio is not None
            and self.fecha_hora_fin is not None
            and self.fecha_hora_fin <= self.fecha_hora_inicio
        ):
            raise ValueError(
                "fecha_hora_fin debe ser estrictamente posterior a fecha_hora_inicio"
            )
        return self


class TurnoRead(TurnoBase):
    """Representación de salida de un turno."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    estado_turno: EstadoTurno
    estado_whatsapp: EstadoWhatsApp
    whatsapp_mensaje_id: Optional[str] = None
