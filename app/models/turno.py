"""Modelo ORM ``Turno`` y sus restricciones de integridad."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.db.types import UTCDateTime
from app.models.enums import EstadoTurno, EstadoWhatsApp

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.doctor import Doctor
    from app.models.paciente import Paciente


class Turno(Base):
    """Turno asignado a un paciente con un doctor en un rango horario.

    Restricciones a nivel de base de datos:

    * ``uq_turno_doctor_fecha_inicio``: **índice único parcial** que impide que un
      mismo doctor tenga dos turnos *vigentes* con idéntica ``fecha_hora_inicio``
      (bloquea duplicados exactos). Como excluye los turnos ``CANCELADO``,
      cancelar un turno libera su horario exacto para volver a agendarlo.
    * ``ck_turno_fin_posterior_inicio``: garantiza que ``fecha_hora_fin`` sea
      posterior a ``fecha_hora_inicio``.

    Nota: el solapamiento de rangos *parciales* (p. ej. turnos que se cruzan
    sin iniciar a la misma hora) no puede expresarse con una restricción única
    portátil; se valida adicionalmente en la capa de servicio
    (``app.services.turno_service``).
    """

    __tablename__ = "turnos"
    __table_args__ = (
        # Índice único PARCIAL (SQLite/PostgreSQL): los turnos CANCELADO quedan
        # fuera del índice, de modo que un turno cancelado libera su inicio
        # exacto. En dialectos sin índices parciales (p. ej. MySQL) SQLAlchemy
        # genera un índice único global, equivalente al comportamiento previo.
        Index(
            "uq_turno_doctor_fecha_inicio",
            "doctor_id",
            "fecha_hora_inicio",
            unique=True,
            sqlite_where=text("estado_turno != 'CANCELADO'"),
            postgresql_where=text("estado_turno != 'CANCELADO'"),
        ),
        CheckConstraint(
            "fecha_hora_fin > fecha_hora_inicio",
            name="ck_turno_fin_posterior_inicio",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    paciente_id: Mapped[int] = mapped_column(
        ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctores.id", ondelete="CASCADE"), nullable=False, index=True
    )

    fecha_hora_inicio: Mapped[datetime] = mapped_column(
        # ``UTCDateTime``: en PostgreSQL la columna es ``TIMESTAMP WITH TIME
        # ZONE``, pero el ORM entrega y recibe **UTC naive** (convención del
        # proyecto), para que las comparaciones en Python no mezclen zonas.
        UTCDateTime, nullable=False, index=True
    )
    fecha_hora_fin: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    estado_turno: Mapped[EstadoTurno] = mapped_column(
        SAEnum(
            EstadoTurno,
            name="estado_turno",
            native_enum=False,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=EstadoTurno.PENDIENTE,
        server_default=EstadoTurno.PENDIENTE.value,
    )
    estado_whatsapp: Mapped[EstadoWhatsApp] = mapped_column(
        SAEnum(
            EstadoWhatsApp,
            name="estado_whatsapp",
            native_enum=False,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=EstadoWhatsApp.PENDIENTE_ENVIO,
        server_default=EstadoWhatsApp.PENDIENTE_ENVIO.value,
    )

    # Identificador del mensaje saliente de WhatsApp (wamid) para correlacionar
    # la respuesta del paciente (webhook) con este turno.
    whatsapp_mensaje_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )

    # Relaciones
    paciente: Mapped["Paciente"] = relationship(back_populates="turnos")
    doctor: Mapped["Doctor"] = relationship(back_populates="turnos")

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Turno id={self.id} doctor_id={self.doctor_id} "
            f"paciente_id={self.paciente_id} inicio={self.fecha_hora_inicio!r} "
            f"estado_turno={self.estado_turno} estado_whatsapp={self.estado_whatsapp}>"
        )
