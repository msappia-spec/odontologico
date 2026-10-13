"""Modelo ORM ``Doctor``."""

from typing import TYPE_CHECKING, List

from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.disponibilidad import DisponibilidadDoctor
    from app.models.turno import Turno


class Doctor(Base):
    """Profesional odontológico que atiende turnos."""

    __tablename__ = "doctores"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    especialidad: Mapped[str] = mapped_column(String(120), nullable=False)
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    # Relaciones
    turnos: Mapped[List["Turno"]] = relationship(
        back_populates="doctor",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Rangos horarios semanales del profesional (varios por día, si hace falta).
    disponibilidades: Mapped[List["DisponibilidadDoctor"]] = relationship(
        back_populates="doctor",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DisponibilidadDoctor.dia_semana, DisponibilidadDoctor.hora_inicio",
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Doctor id={self.id} nombre={self.nombre!r} "
            f"especialidad={self.especialidad!r} activo={self.activo}>"
        )
