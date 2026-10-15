"""Modelo ORM ``Doctor``."""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Enum as SAEnum, Float, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import TipoPagoDoctor

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.disponibilidad import DisponibilidadDoctor
    from app.models.disponibilidad_especifica import DisponibilidadEspecifica
    from app.models.evolucion_medica import EvolucionMedica
    from app.models.movimiento_caja import MovimientoCaja
    from app.models.turno import Turno


class Doctor(Base):
    """Profesional odontológico que atiende turnos.

    Además de sus datos de agenda, guarda **cómo se le paga** (Módulo 3):

    * ``tipo_pago``: ``DUENO`` (se queda con su producción; por defecto),
      ``COMISION`` (un porcentaje) o ``SUELDO`` (un fijo mensual).
    * ``valor_pago``: el porcentaje (0-100) para ``DUENO``/``COMISION`` o el
      monto mensual para ``SUELDO``. Es ``NULL`` mientras no se configure, y
      ``DUENO`` sin valor se interpreta como el 100 % de su producción.
    """

    __tablename__ = "doctores"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    especialidad: Mapped[str] = mapped_column(String(120), nullable=False)
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    # --- Modalidad de pago (Módulo 3: liquidación de profesionales) ---
    tipo_pago: Mapped[TipoPagoDoctor] = mapped_column(
        SAEnum(
            TipoPagoDoctor,
            name="tipo_pago_doctor",
            native_enum=False,
            validate_strings=True,
            length=15,
        ),
        nullable=False,
        default=TipoPagoDoctor.DUENO,
        server_default=TipoPagoDoctor.DUENO.value,
    )
    valor_pago: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

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
    # Excepciones de agenda por fecha: habilitan o bloquean horas de un día
    # puntual sin tocar la agenda semanal (ver DisponibilidadEspecifica).
    excepciones: Mapped[List["DisponibilidadEspecifica"]] = relationship(
        back_populates="doctor",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DisponibilidadEspecifica.fecha, DisponibilidadEspecifica.hora_inicio",
    )
    # Evoluciones médicas que firmó el profesional (Módulo 2). Sin ``cascade``:
    # una evolución es un registro clínico inmutable y no se borra en cascada con
    # el odontólogo (la baja del profesional es lógica, ``activo = false``).
    evoluciones: Mapped[List["EvolucionMedica"]] = relationship(
        back_populates="doctor",
        passive_deletes=True,
    )
    # Movimientos de caja que facturó el profesional (Módulo 3). Sin ``cascade``:
    # un movimiento es un comprobante contable y no se borra con el odontólogo
    # (la baja del profesional es lógica, ``activo = false``).
    movimientos_caja: Mapped[List["MovimientoCaja"]] = relationship(
        back_populates="doctor",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Doctor id={self.id} nombre={self.nombre!r} "
            f"especialidad={self.especialidad!r} activo={self.activo}>"
        )
