"""Modelo ORM ``DisponibilidadDoctor``: rangos horarios semanales del profesional.

Cada fila es un tramo de atención de **un** odontólogo para **un** día de la
semana. Como no hay unicidad por día, un mismo profesional puede tener
múltiples rangos el mismo día (p. ej. lunes 08:00–12:00 y lunes 16:00–20:00);
el calendario los une para armar los bloques de turnos disponibles.

Convención de ``dia_semana``: **0 = domingo … 6 = sábado**, idéntica a
``Date.getDay()`` de JavaScript (que usa el panel web) y a la antigua agenda
semanal fija del frontend. Equivale a ``(date.weekday() + 1) % 7`` en Python.
"""

from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.doctor import Doctor


class DisponibilidadDoctor(Base):
    """Tramo de atención semanal configurable por odontólogo.

    Restricciones a nivel de base de datos:

    * ``ck_disponibilidad_dia_semana_valido``: ``dia_semana`` entre 0 (domingo)
      y 6 (sábado).
    * ``ck_disponibilidad_fin_posterior_inicio``: ``hora_fin > hora_inicio``
      (no se admiten rangos vacíos ni invertidos).
    * ``ondelete="CASCADE"`` en ``doctor_id``: al borrar un profesional se
      eliminan sus rangos (el ORM además usa ``delete-orphan``).

    Nota: el solapamiento *entre* rangos del mismo día se valida en la capa de
    servicio (:mod:`app.services.disponibilidad_service`) porque depende de la
    fila existente y no es expresable con una restricción portátil.
    """

    __tablename__ = "disponibilidad_doctores"
    __table_args__ = (
        CheckConstraint(
            "dia_semana >= 0 AND dia_semana <= 6",
            name="ck_disponibilidad_dia_semana_valido",
        ),
        CheckConstraint(
            "hora_fin > hora_inicio",
            name="ck_disponibilidad_fin_posterior_inicio",
        ),
        # Índice compuesto: las consultas del calendario buscan por doctor y día.
        Index("ix_disponibilidad_doctor_dia", "doctor_id", "dia_semana"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctores.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 0 = domingo … 6 = sábado
    dia_semana: Mapped[int] = mapped_column(Integer, nullable=False)

    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False)

    # Relaciones
    doctor: Mapped["Doctor"] = relationship(back_populates="disponibilidades")

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<DisponibilidadDoctor id={self.id} doctor_id={self.doctor_id} "
            f"dia_semana={self.dia_semana} "
            f"{self.hora_inicio:%H:%M}-{self.hora_fin:%H:%M}>"
        )
