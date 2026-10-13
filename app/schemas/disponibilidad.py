"""Esquemas Pydantic de ``DisponibilidadDoctor`` y de los horarios libres del día.

Los ``time`` se serializan como ``"HH:MM"`` (no como ISO con segundos) para que
el panel web los reutilice directamente en los ``<input type="time">`` y en los
bloques del calendario.
"""

from datetime import date as Date
from datetime import time as Time
from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

# Nombres de los días en el mismo orden que ``dia_semana`` (0 = domingo).
NOMBRES_DIA = (
    "domingo",
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
)


class DisponibilidadBase(BaseModel):
    """Un tramo de atención semanal de un odontólogo."""

    dia_semana: int = Field(
        ...,
        ge=0,
        le=6,
        description="Día de la semana: 0 = domingo … 6 = sábado.",
    )
    hora_inicio: Time
    hora_fin: Time

    @model_validator(mode="after")
    def _validar_orden(self) -> "DisponibilidadBase":
        """``hora_fin`` debe ser estrictamente posterior a ``hora_inicio``."""
        if self.hora_fin <= self.hora_inicio:
            raise ValueError(
                "hora_fin debe ser estrictamente posterior a hora_inicio"
            )
        return self

    @field_serializer("hora_inicio", "hora_fin")
    def _serializar_hora(self, valor: Time) -> str:  # pragma: no cover - trivial
        return valor.strftime("%H:%M")


class DisponibilidadCreate(DisponibilidadBase):
    """Payload de creación de un rango horario (``POST``)."""


class DisponibilidadRead(DisponibilidadBase):
    """Representación de salida de un rango horario."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int


class DisponibilidadReemplazo(BaseModel):
    """Payload del reemplazo total de la semana de un odontólogo (``PUT``).

    Reemplaza *todos* los rangos del profesional por la lista enviada (una lista
    vacía deja al odontólogo sin atención configurada). Se rechazan los rangos
    solapados dentro del mismo día (``422``) porque un mismo horario no debería
    estar habilitado dos veces.
    """

    rangos: List[DisponibilidadBase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sin_solapes(self) -> "DisponibilidadReemplazo":
        for indice, actual in enumerate(self.rangos):
            for otro in self.rangos[indice + 1 :]:
                if actual.dia_semana != otro.dia_semana:
                    continue
                if (
                    actual.hora_inicio < otro.hora_fin
                    and otro.hora_inicio < actual.hora_fin
                ):
                    dia = NOMBRES_DIA[actual.dia_semana]
                    raise ValueError(
                        f"Los rangos del {dia} se solapan "
                        f"({actual.hora_inicio:%H:%M}-{actual.hora_fin:%H:%M} y "
                        f"{otro.hora_inicio:%H:%M}-{otro.hora_fin:%H:%M})."
                    )
        return self


class BloqueDisponible(BaseModel):
    """Bloque horario concreto que el consultorio puede ofrecer."""

    hora_inicio: Time
    hora_fin: Time

    @field_serializer("hora_inicio", "hora_fin")
    def _serializar_hora(self, valor: Time) -> str:  # pragma: no cover - trivial
        return valor.strftime("%H:%M")


class HorariosDisponiblesRead(BaseModel):
    """Bloques libres de un odontólogo para una fecha concreta."""

    doctor_id: int
    fecha: Date
    dia_semana: int
    dia: str
    duracion_minutos: int
    rangos: List[DisponibilidadRead]
    bloques: List[BloqueDisponible]
