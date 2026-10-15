"""Modelo ORM ``Paciente``."""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.historia_clinica import HistoriaClinica
    from app.models.obra_social import ObraSocial
    from app.models.turno import Turno


class Paciente(Base):
    """Persona que reserva turnos y recibe notificaciones por WhatsApp.

    Datos requeridos: ``nombre`` y ``telefono`` (el teléfono es la vía de aviso
    por WhatsApp). El resto de la información es **opcional**:

    * ``email``: correo de contacto.
    * ``dni``: documento del paciente, **único** mediante el índice
      ``uq_paciente_dni``. Al ser opcional, las filas sin documento quedan en
      ``NULL`` y un índice único admite tantos ``NULL`` como haga falta (ni
      SQLite ni PostgreSQL consideran duplicados dos ``NULL``); sí se rechaza
      repetir un documento informado.
    * ``obra_social_id`` / ``numero_afiliado``: cobertura médica del paciente.
      La obra social es una **clave foránea** al catálogo ``obras_sociales``
      (``ondelete="SET NULL"``: si algún día se borra físicamente una cobertura,
      el paciente queda sin obra social en lugar de perder la fila), mientras
      que el número de afiliado sigue siendo texto libre.
    """

    __tablename__ = "pacientes"
    __table_args__ = (
        # Índice único *nombrado* (en lugar de una UniqueConstraint) para poder
        # replicarlo sobre bases existentes con ``CREATE UNIQUE INDEX`` sin
        # reconstruir la tabla (ver scripts/migrar_pacientes_datos.py).
        Index("uq_paciente_dni", "dni", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    dni: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    telefono: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    obra_social_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("obras_sociales.id", ondelete="SET NULL"), nullable=True, index=True
    )
    numero_afiliado: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # Relaciones
    turnos: Mapped[List["Turno"]] = relationship(
        back_populates="paciente",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # ``lazy="joined"``: el nombre de la obra social viaja en la misma consulta,
    # de modo que la respuesta de la API puede exponerlo sin depender de un
    # *lazy load* posterior al cierre de la sesión.
    obra_social: Mapped[Optional["ObraSocial"]] = relationship(
        back_populates="pacientes",
        lazy="joined",
    )
    # Ficha clínica del paciente (Módulo 2): una sola por paciente. Se crea sola
    # cuando el odontólogo carga la primera evolución (ver
    # app.services.historia_clinica_service) y cuelga de acá su línea de tiempo.
    historia_clinica: Mapped[Optional["HistoriaClinica"]] = relationship(
        back_populates="paciente",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Paciente id={self.id} nombre={self.nombre!r} "
            f"telefono={self.telefono!r} dni={self.dni!r}>"
        )
