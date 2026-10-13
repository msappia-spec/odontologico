"""Esquemas Pydantic de ``DisponibilidadDoctor`` y de los horarios libres del día.

Los ``time`` se serializan como ``"HH:MM"`` (no como ISO con segundos) para que
el panel web los reutilice directamente en los ``<input type="time">`` y en los
bloques del calendario. Las ``date`` salen como ISO (``AAAA-MM-DD``) por el mismo
motivo: alimentan los ``<input type="date">`` sin conversiones.

Este módulo cubre las dos capas de la agenda:

* ``Disponibilidad*``: la **semana** habitual del odontólogo
  (``DisponibilidadDoctor``), editable en la pestaña *Disponibilidad*.
* ``*ExcepcionBase`` / ``DisponibilidadEspecifica*``: las **excepciones de un
  día concreto** (``DisponibilidadEspecifica``), que habilitan o bloquean horas
  de una fecha puntual sin tocar la semana.
"""

from datetime import date as Date
from datetime import time as Time
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.models.enums import TipoDisponibilidadEspecifica

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

# De dónde salieron los rangos que el calendario tiene en cuenta para una fecha:
# de la agenda semanal (``SEMANAL``) o de las excepciones de esa fecha
# (``ESPECIFICO``). Cuando hay excepciones, la semana se ignora por completo.
OrigenDisponibilidad = Literal["SEMANAL", "ESPECIFICO"]


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


# --------------------------------------------------------------------------- #
# Excepciones por fecha concreta (# DisponibilidadEspecifica)
# --------------------------------------------------------------------------- #


class ExcepcionFechaBase(BaseModel):
    """Un tramo horario dentro de **una** fecha concreta.

    El ``tipo`` decide el efecto sobre la agenda semanal: ``HABILITADO`` agrega
    atención ese día y ``BLOQUEADO`` la recorta (vacaciones, congreso, feriado,
    un almuerzo más largo). Los tramos de un mismo tipo se unen entre sí, y los
    ``BLOQUEADO`` se restan de los ``HABILITADO``.
    """

    hora_inicio: Time
    hora_fin: Time
    tipo: TipoDisponibilidadEspecifica = Field(
        TipoDisponibilidadEspecifica.HABILITADO,
        description="`HABILITADO` agrega atención y `BLOQUEADO` la recorta.",
    )

    @model_validator(mode="after")
    def _validar_orden(self) -> "ExcepcionFechaBase":
        """``hora_fin`` debe ser estrictamente posterior a ``hora_inicio``."""
        if self.hora_fin <= self.hora_inicio:
            raise ValueError(
                "hora_fin debe ser estrictamente posterior a hora_inicio"
            )
        return self

    @field_serializer("hora_inicio", "hora_fin")
    def _serializar_hora(self, valor: Time) -> str:  # pragma: no cover - trivial
        return valor.strftime("%H:%M")


class DisponibilidadEspecificaCreate(ExcepcionFechaBase):
    """Payload de creación de una excepción de fecha (``POST``)."""

    fecha: Date = Field(
        ...,
        description="Fecha del calendario afectada (``AAAA-MM-DD``).",
    )


class DisponibilidadEspecificaRead(ExcepcionFechaBase):
    """Representación de salida de una excepción de fecha."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    fecha: Date

    @field_serializer("fecha")
    def _serializar_fecha(self, valor: Date) -> str:  # pragma: no cover - trivial
        return valor.isoformat()


class DisponibilidadEspecificaReemplazo(BaseModel):
    """Payload del reemplazo total de **un día** (``PUT``).

    Sustituye todas las excepciones de esa fecha por la lista enviada; una lista
    vacía limpia las excepciones y el día vuelve a regirse por la agenda
    semanal. Se rechazan los ``HABILITADO`` solapados (``422``) porque un mismo
    horario no debería habilitarse dos veces; los ``BLOQUEADO`` pueden solaparse
    entre sí sin problema.
    """

    rangos: List[ExcepcionFechaBase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sin_solapes_habilitados(self) -> "DisponibilidadEspecificaReemplazo":
        habilitados = [
            r for r in self.rangos if r.tipo == TipoDisponibilidadEspecifica.HABILITADO
        ]
        for indice, actual in enumerate(habilitados):
            for otro in habilitados[indice + 1 :]:
                if (
                    actual.hora_inicio < otro.hora_fin
                    and otro.hora_inicio < actual.hora_fin
                ):
                    raise ValueError(
                        "Los rangos habilitados se solapan "
                        f"({actual.hora_inicio:%H:%M}-{actual.hora_fin:%H:%M} y "
                        f"{otro.hora_inicio:%H:%M}-{otro.hora_fin:%H:%M})."
                    )
        return self


class RangoEfectivoRead(BaseModel):
    """Rango horario que el calendario realmente tiene en cuenta para una fecha.

    Puede venir de la agenda **semanal** o de las **excepciones** cargadas para
    esa fecha; ``origen`` lo aclara para que el panel muestre el motivo.
    """

    model_config = ConfigDict(from_attributes=True)

    hora_inicio: Time
    hora_fin: Time
    origen: OrigenDisponibilidad = "SEMANAL"

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
    origen: OrigenDisponibilidad = Field(
        "SEMANAL",
        description=(
            "`SEMANAL` si el día se calculó con la agenda habitual y "
            "`ESPECIFICO` si hay excepciones cargadas para esa fecha."
        ),
    )
    rangos: List[RangoEfectivoRead]
    excepciones: List[DisponibilidadEspecificaRead] = Field(default_factory=list)
    bloques: List[BloqueDisponible]
