"""Modelo ORM ``AntecedentesSalud``: declaración de salud previa del paciente.

El paciente completa esta ficha desde un enlace público (``/anamnesis/{token}``)
**antes** de la consulta. Los datos cuelgan de su historia clínica, de modo que
el odontólogo los ve en la misma ficha que las evoluciones (Panel Principal y
vista móvil).

Decisiones de diseño:

* **Una ficha por historia clínica** (``uq_antecedentes_historia_clinica``): el
  paciente puede corregir su declaración con el mismo enlace, pero nunca se
  duplica el cuestionario.
* Los textos son libres y opcionales. Los booleanos permiten que el paciente
  marque los antecedentes con un toque desde el celular; los valores clínicos
  «Sin alergias» / «Ninguna» se representan con texto vacío (``NULL``).
* ``completado`` distingue una ficha creada por una declaración enviada de una
  fila todavía en borrador. ``fecha_completado`` queda en **UTC naive**, igual
  que el resto de las fechas del proyecto.
* ``ondelete=\"CASCADE\"``: si se elimina la historia clínica, la declaración la
  acompaña (no queda información clínica huérfana).
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.historia_clinica import HistoriaClinica

#: Longitud máxima de los textos libres de la declaración.
MAX_TEXTO_ANTECEDENTES = 1000


class AntecedentesSalud(Base):
    """Cuestionario de antecedentes de salud declarado por el paciente."""

    __tablename__ = "antecedentes_salud"
    __table_args__ = (
        Index(
            "uq_antecedentes_historia_clinica",
            "historia_clinica_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    historia_clinica_id: Mapped[int] = mapped_column(
        ForeignKey("historias_clinicas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    alergias_medicamentos: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    presion_alta: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    problemas_cardiacos: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    medicacion_actual: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embarazo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    completado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    fecha_completado: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )

    # Relaciones
    historia_clinica: Mapped["HistoriaClinica"] = relationship(
        back_populates="antecedentes_salud"
    )

    @property
    def motivos_alerta(self) -> list[str]:
        """Antecedentes que el odontólogo debe ver antes de atender.

        Se consideran de riesgo tanto los booleanos marcados como los textos
        informados (alergias y medicación). El orden es estable para que la
        alerta se lea siempre igual.
        """
        motivos: list[str] = []
        if (self.alergias_medicamentos or "").strip():
            motivos.append("Alergias a medicamentos")
        if self.presion_alta:
            motivos.append("Presión arterial alta")
        if self.problemas_cardiacos:
            motivos.append("Problemas cardíacos")
        if (self.medicacion_actual or "").strip():
            motivos.append("Medicación actual")
        if self.embarazo:
            motivos.append("Embarazo")
        return motivos

    @property
    def alerta_medica(self) -> bool:
        """``True`` cuando la declaración tiene algún antecedente de riesgo."""
        return bool(self.motivos_alerta)

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<AntecedentesSalud id={self.id} "
            f"historia_clinica_id={self.historia_clinica_id} "
            f"completado={self.completado}>"
        )
