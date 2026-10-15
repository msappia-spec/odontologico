"""Modelo ORM ``HistoriaClinica``: ficha clínica de un paciente (Módulo 2).

Cada paciente tiene **una** historia clínica (relación 1 a 1, garantizada por el
índice único ``uq_historia_paciente``) que actúa de cabecera de su línea de
tiempo: todas sus atenciones se cuelgan de acá como
:class:`~app.models.evolucion_medica.EvolucionMedica`.

El ``numero_ficha`` es el identificador visible en la ficha de papel/carpeta del
consultorio (``HC-000123``). Se deriva del ``id`` del paciente, así que es
**único y estable** sin necesidad de una secuencia propia, y el índice único
``uq_historia_numero_ficha`` lo defiende ante cualquier duplicado.

Notas de diseño:

* Los índices son **nombrados** (mismo criterio que ``uq_paciente_dni``) para
  poder replicarlos sobre bases existentes con ``CREATE UNIQUE INDEX`` sin
  reconstruir la tabla (ver ``scripts/migrar_historias_clinicas.py``).
* ``ondelete="CASCADE"`` en ``paciente_id``: si algún día se borra físicamente un
  paciente, su historia y sus evoluciones lo acompañan (nunca quedan huérfanas).
  En el uso normal el paciente no se borra: se conserva su registro.
"""

from typing import TYPE_CHECKING, List

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.evolucion_medica import EvolucionMedica
    from app.models.paciente import Paciente


class HistoriaClinica(Base):
    """Ficha clínica única de un paciente."""

    __tablename__ = "historias_clinicas"
    __table_args__ = (
        Index("uq_historia_paciente", "paciente_id", unique=True),
        Index("uq_historia_numero_ficha", "numero_ficha", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    paciente_id: Mapped[int] = mapped_column(
        ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    numero_ficha: Mapped[str] = mapped_column(String(30), nullable=False)

    # Relaciones
    paciente: Mapped["Paciente"] = relationship(back_populates="historia_clinica")
    # ``passive_deletes``: el borrado de la historia (que la API no expone) lo
    # resuelve la base con el ``ON DELETE CASCADE``, sin que el ORM cargue y
    # borre una por una las evoluciones.
    evoluciones: Mapped[List["EvolucionMedica"]] = relationship(
        back_populates="historia_clinica",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<HistoriaClinica id={self.id} paciente_id={self.paciente_id} "
            f"numero_ficha={self.numero_ficha!r}>"
        )
