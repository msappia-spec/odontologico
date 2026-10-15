"""Modelo ORM ``EvolucionMedica``: cada atención de la historia clínica (Módulo 2).

Una evolución es el registro que hace el odontólogo al terminar una atención:
qué motivó la consulta, qué diagnosticó y qué tratamiento efectuó. Es el
documento con valor **legal** de la historia clínica, por eso su regla central
es la **inmutabilidad**:

* La API (``/api/v1``) sólo expone ``POST`` (crear) y ``GET`` (leer): no hay
  ``PUT``, ``PATCH`` ni ``DELETE`` para este recurso.
* El servicio (:mod:`app.services.historia_clinica_service`) sólo ofrece alta y
  consulta; no tiene función de edición ni de baja.
* Acá abajo, los eventos ``before_update`` y ``before_delete`` del ORM levantan
  :class:`~app.core.exceptions.RegistroInmutableError` si alguna otra capa (un
  script, una consola, un test) intenta modificar o borrar una evolución ya
  guardada. Es la última red de seguridad: el registro clínico no se toca ni
  desde el ORM.

Campos:

* ``fecha_atencion``: fecha/hora del acto médico, **UTC naive** (convención del
  proyecto mediante :class:`~app.db.types.UTCDateTime`). Si no se informa se
  completa con el momento del alta (``default=utcnow``) y la base también tiene
  su propio ``server_default`` (``now()``) para las filas insertadas por SQL.
* ``doctor_id``: quién atendió. La clave foránea es ``ON DELETE RESTRICT`` a
  propósito: un profesional no puede desaparecer dejando sus registros clínicos
  sin responsable (la baja de un odontólogo es **lógica**, igual que en el
  Módulo 1).
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Index, Text, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utcnow
from app.core.exceptions import RegistroInmutableError
from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.archivo_adjunto import ArchivoAdjunto
    from app.models.doctor import Doctor
    from app.models.historia_clinica import HistoriaClinica


class EvolucionMedica(Base):
    """Atención registrada en la historia clínica (registro inmutable)."""

    __tablename__ = "evoluciones_medicas"
    __table_args__ = (
        # La línea de tiempo se consulta por historia, de la más nueva a la más
        # vieja: el índice compuesto cubre ese orden sin ordenar en memoria.
        Index("ix_evolucion_historia_fecha", "historia_clinica_id", "fecha_atencion"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    historia_clinica_id: Mapped[int] = mapped_column(
        ForeignKey("historias_clinicas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doctor_id: Mapped[int] = mapped_column(
        # RESTRICT (no CASCADE): una evolución es de un profesional concreto y no
        # se borra en cascada con él; la baja del odontólogo es lógica.
        ForeignKey("doctores.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    fecha_atencion: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        index=True,
        default=utcnow,
        server_default=func.now(),
    )

    motivo_consulta: Mapped[str] = mapped_column(Text, nullable=False)
    diagnostico: Mapped[str] = mapped_column(Text, nullable=False)
    tratamiento_efectuado: Mapped[str] = mapped_column(Text, nullable=False)
    observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relaciones
    # ``lazy="joined"``: el nombre y la especialidad de quien atendió viajan en la
    # misma consulta, de modo que la API puede exponerlos sin depender de un
    # *lazy load* (mismo criterio que ``Paciente.obra_social``).
    doctor: Mapped["Doctor"] = relationship(lazy="joined")
    historia_clinica: Mapped["HistoriaClinica"] = relationship(
        back_populates="evoluciones"
    )
    # ``lazy="selectin"``: los adjuntos (radiografías) de cada evolución de la
    # línea de tiempo se traen en una consulta extra, sin multiplicar filas.
    archivos: Mapped[List["ArchivoAdjunto"]] = relationship(
        back_populates="evolucion",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<EvolucionMedica id={self.id} historia_clinica_id={self.historia_clinica_id} "
            f"doctor_id={self.doctor_id} fecha_atencion={self.fecha_atencion!r}>"
        )


# --- Inmutabilidad del registro clínico --------------------------------------
# El ORM avisa antes de emitir un UPDATE o un DELETE. Acá se corta la operación:
# sólo se admite la inserción (evolución nueva) y la lectura.
def _prohibir_modificacion(mapper, connection, target: EvolucionMedica) -> None:
    """Bloquea cualquier ``UPDATE`` sobre una evolución médica."""
    raise RegistroInmutableError(
        "Una evolución médica no se puede modificar: cargá una evolución nueva "
        "(registro clínico inmutable)."
    )


def _prohibir_borrado(mapper, connection, target: EvolucionMedica) -> None:
    """Bloquea cualquier ``DELETE`` sobre una evolución médica."""
    raise RegistroInmutableError(
        "Una evolución médica no se puede eliminar: el historial clínico es "
        "permanente (registro clínico inmutable)."
    )


event.listen(EvolucionMedica, "before_update", _prohibir_modificacion)
event.listen(EvolucionMedica, "before_delete", _prohibir_borrado)
