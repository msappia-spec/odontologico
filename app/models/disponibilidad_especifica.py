"""Modelo ORM ``DisponibilidadEspecifica``: excepciones de agenda por fecha.

La agenda *semanal* (``DisponibilidadDoctor``) describe la rutina del
odontólogo; esta tabla permite **romperla para un día puntual** sin tocar la
semana:

* ``HABILITADO`` agrega atención en una fecha que la rutina no cubre (p. ej. un
  sábado de guardia o un domingo excepcional).
* ``BLOQUEADO`` recorta atención en una fecha que la rutina sí cubre
  (vacaciones, congreso, feriado, un almuerzo más largo).

La precedencia la resuelve el servicio de disponibilidad: si un odontólogo
tiene **cualquier** excepción cargada para una fecha, ese día se calcula *sólo*
con las excepciones (los ``HABILITADO`` menos los ``BLOQUEADO``) y la agenda
semanal queda ignorada; si no hay excepciones, se usa la semana como siempre.
"""

from datetime import date as Date
from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date as SADate, Enum as SAEnum, ForeignKey
from sqlalchemy import Index, Time, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import TipoDisponibilidadEspecifica

if TYPE_CHECKING:  # pragma: no cover - sólo para los type checkers
    from app.models.doctor import Doctor


class DisponibilidadEspecifica(Base):
    """Tramo horario ``HABILITADO`` o ``BLOQUEADO`` de una fecha concreta."""

    __tablename__ = "disponibilidad_especifica"
    __table_args__ = (
        # Mismo criterio que la agenda semanal: el rango debe tener duración.
        CheckConstraint(
            "hora_fin > hora_inicio", name="ck_especifica_fin_posterior_inicio"
        ),
        CheckConstraint(
            "tipo IN ('HABILITADO', 'BLOQUEADO')", name="ck_especifica_tipo_valido"
        ),
        # Todas las consultas del día filtran por doctor + fecha.
        Index("ix_especifica_doctor_fecha", "doctor_id", "fecha"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fecha: Mapped[Date] = mapped_column(SADate, nullable=False, index=True)
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False)
    tipo: Mapped[TipoDisponibilidadEspecifica] = mapped_column(
        # native_enum=False guarda el valor como VARCHAR: portable entre SQLite
        # y PostgreSQL y, además, legible al inspeccionar la base a mano.
        SAEnum(
            TipoDisponibilidadEspecifica,
            name="tipo_disponibilidad_especifica",
            native_enum=False,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=TipoDisponibilidadEspecifica.HABILITADO,
        server_default=text("'HABILITADO'"),
    )

    doctor: Mapped["Doctor"] = relationship(back_populates="excepciones")

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return (
            f"<DisponibilidadEspecifica id={self.id} doctor_id={self.doctor_id} "
            f"fecha={self.fecha} {self.hora_inicio}-{self.hora_fin} "
            f"tipo={self.tipo}>"
        )
