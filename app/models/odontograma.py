"""Modelos ORM del odontograma (Módulo 4: ficha odontológica gráfica).

El odontograma es la parte de la **Historia Clínica General** que el profesional
completa **después de revisar al paciente**. En la ficha de papel se dibuja sobre
un cuadro de dientes con tres referencias de color:

* **rojo**: prestaciones existentes (lo que el paciente ya tiene hecho);
* **azul**: prestaciones requeridas (lo que hay que hacer);
* **X**: diente ausente o a extraer.

Para que la pantalla pueda pintar ese cuadro, cada diente guardado tiene un
``estado`` (:data:`ESTADOS_DIENTE`) y la cabecera del odontograma conserva los
datos de prótesis, coronas y cantidad de dientes existentes.

Decisiones de diseño:

* **Un odontograma por historia clínica** (``uq_odontograma_historia``): igual
  que la ficha de salud, se corrige sobre la misma fila y nunca se duplica.
* Los dientes se guardan **sólo cuando tienen un estado**: un diente sano no
  ocupa una fila. Al guardar se reemplaza el conjunto completo, así el gráfico
  que ve el odontólogo es exactamente el que queda en la base.
* ``ondelete="CASCADE"`` en la historia: si se elimina la ficha del paciente, el
  odontograma la acompaña. ``doctor_id`` es la referencia del profesional que lo
  cargó (``SET NULL`` si ese profesional se elimina físicamente).
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.doctor import Doctor
    from app.models.historia_clinica import HistoriaClinica

#: Dientes permanentes en notación FDI (11-18, 21-28, 31-38 y 41-48).
DIENTES_PERMANENTES: tuple[int, ...] = tuple(
    cuadrante * 10 + diente
    for cuadrante in (1, 2, 3, 4)
    for diente in range(1, 9)
)

#: Estados posibles de un diente (los colores del PDF).
ESTADOS_DIENTE: tuple[str, ...] = ("EXISTENTE", "REQUERIDO", "AUSENTE")

#: Longitud máxima de los textos del odontograma.
MAX_TEXTO_ODONTOGRAMA = 1000


class Odontograma(Base):
    """Cabecera del odontograma de una historia clínica."""

    __tablename__ = "odontogramas"
    __table_args__ = (
        Index("uq_odontograma_historia", "historia_clinica_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    historia_clinica_id: Mapped[int] = mapped_column(
        ForeignKey("historias_clinicas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doctor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("doctores.id", ondelete="SET NULL"), nullable=True
    )

    protesis_fija: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    protesis_removible: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    coronas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dientes_existentes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    actualizado_en: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

    # Relaciones
    dientes: Mapped[List["OdontogramaDiente"]] = relationship(
        back_populates="odontograma",
        cascade="all, delete-orphan",
        order_by="OdontogramaDiente.diente",
    )
    doctor: Mapped[Optional["Doctor"]] = relationship(lazy="joined")
    historia_clinica: Mapped["HistoriaClinica"] = relationship(
        back_populates="odontograma"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Odontograma id={self.id} "
            f"historia_clinica_id={self.historia_clinica_id} "
            f"dientes={len(self.dientes)}>"
        )


class OdontogramaDiente(Base):
    """Estado pintado de un diente dentro de un odontograma."""

    __tablename__ = "odontograma_dientes"
    __table_args__ = (
        UniqueConstraint(
            "odontograma_id", "diente", name="uq_odontograma_diente"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    odontograma_id: Mapped[int] = mapped_column(
        ForeignKey("odontogramas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Número FDI del diente (11 a 48).
    diente: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``EXISTENTE`` (rojo), ``REQUERIDO`` (azul) o ``AUSENTE`` (X).
    estado: Mapped[str] = mapped_column(String(12), nullable=False)
    notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    odontograma: Mapped["Odontograma"] = relationship(back_populates="dientes")

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<OdontogramaDiente diente={self.diente} estado={self.estado!r}>"
        )
