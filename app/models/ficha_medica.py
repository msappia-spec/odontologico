"""Modelos ORM de la ficha médica del paciente (Módulo 4).

La ficha médica digital reproduce la hoja de papel que el consultorio archiva
junto al odontograma (las dos caras de la carpeta del paciente):

* **Cabecera del papel**: vencimiento de la credencial de la obra social,
  grupo familiar y parentesco del titular, y los *antecedentes de enfermedad*
  que anota el profesional.
* **Estado bucal general**: presencia de sarro y enfermedad periodontal (los
  dos casilleros SI/NO del formulario).
* **Trabajo realizado**: el libro de prácticas con fecha, código de
  nomenclador, pieza dentaria y los importes Debe/Haber.

Los datos filiatorios (nombre, obra social, número de afiliado, fecha de
nacimiento, domicilio, teléfono, titular, lugar de trabajo y jerarquía) **no se
duplican acá**: viven en ``pacientes`` y la API los adjunta al leer la ficha.

Decisiones de diseño:

* **Una ficha por historia clínica** (``uq_ficha_medica_historia``): se corrige
  sobre la misma fila y nunca se duplica, como el odontograma.
* Los renglones de «Trabajo realizado» **se reemplazan completos** en cada
  guardado: el libro es una foto del papel, no un registro inmutable como las
  evoluciones. ``orden`` conserva el orden en que se cargaron y el **saldo no se
  persiste**: se calcula sumando Debe y restando Haber en la capa de servicio,
  así nunca queda desactualizado.
* ``ondelete="CASCADE"``: si se elimina la historia clínica, la ficha y sus
  renglones la acompañan (nunca quedan huérfanos).
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.historia_clinica import HistoriaClinica

#: Longitud máxima del detalle de un trabajo realizado.
MAX_TEXTO_TRABAJO = 500

#: Tope de renglones del libro (el papel tiene 16; sobra de largo).
MAX_RENGLONES_TRABAJO = 100


class FichaMedica(Base):
    """Cabecera de la ficha médica de una historia clínica."""

    __tablename__ = "fichas_medicas"
    __table_args__ = (
        Index("uq_ficha_medica_historia", "historia_clinica_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    historia_clinica_id: Mapped[int] = mapped_column(
        ForeignKey("historias_clinicas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Vencimiento de la credencial / autorización («Vto» del papel).
    vencimiento: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: Grupo familiar del titular de la obra social.
    grupo_familiar: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    #: Parentesco del paciente con el titular.
    parentesco: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    #: Antecedentes de enfermedad que anota el profesional en el encabezado.
    antecedentes_enfermedad: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    # Estado bucal general. ``None`` = todavía sin responder (el papel admite
    # dejar los dos casilleros en blanco); ``True`` = SI, ``False`` = NO.
    presencia_sarro: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    enfermedad_periodontal: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

    actualizado_en: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )

    # Relaciones
    trabajos: Mapped[List["FichaMedicaTrabajo"]] = relationship(
        back_populates="ficha_medica",
        cascade="all, delete-orphan",
        order_by="(FichaMedicaTrabajo.orden, FichaMedicaTrabajo.id)",
    )
    historia_clinica: Mapped["HistoriaClinica"] = relationship(
        back_populates="ficha_medica"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<FichaMedica id={self.id} "
            f"historia_clinica_id={self.historia_clinica_id} "
            f"trabajos={len(self.trabajos)}>"
        )


class FichaMedicaTrabajo(Base):
    """Renglón del libro «Trabajo realizado» de la ficha médica."""

    __tablename__ = "ficha_medica_trabajos"
    __table_args__ = (
        Index("ix_ficha_trabajo_ficha", "ficha_medica_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ficha_medica_id: Mapped[int] = mapped_column(
        ForeignKey("fichas_medicas.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Posición dentro del libro (0 = primer renglón del papel).
    orden: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    fecha: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: Código del nomenclador tomado del catálogo de prestaciones (p. ej. ``01.01``).
    #: Se guarda como texto para conservar el histórico aunque la práctica se dé de baja.
    codigo: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    #: Pieza dentaria; admite varias («18, 17») o una referencia libre.
    pieza: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    #: Práctica realizada.
    trabajo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Importes del libro. El saldo se calcula, no se guarda.
    debe: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    haber: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    ficha_medica: Mapped["FichaMedica"] = relationship(back_populates="trabajos")

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<FichaMedicaTrabajo id={self.id} orden={self.orden} "
            f"trabajo={self.trabajo!r}>"
        )
