"""Modelo ORM ``ArchivoAdjunto``: radiografías y estudios de una evolución.

Cada fila cuelga de **una** evolución médica (:class:`EvolucionMedica`) y
describe un archivo externo ya subido (radiografía, foto clínica, estudio de
imágenes): dónde está (``url_archivo``) y de qué tipo es (``tipo_archivo``).

Decisiones de diseño:

* Se guarda la **URL** y no el binario: el almacenamiento de archivos es
  responsabilidad del servicio donde se suban (disco del consultorio, nube) y la
  historia clínica sólo referencia el documento. Así la fila es liviana y la
  línea de tiempo puede enlazarlo con un toque.
* ``tipo_archivo`` es texto libre normalizado a mayúsculas
  (``RADIOGRAFIA`` por defecto, también ``FOTO``, ``ESTUDIO``, ``OTRO``…), de modo
  que el consultorio pueda sumar tipos nuevos sin migrar el esquema.
* ``ondelete="CASCADE"`` en ``evolucion_id``: los adjuntos pertenecen a la
  evolución y no tienen vida propia (el ORM además usa ``delete-orphan``).
* ``fecha_subida`` se completa sola al dar de alta el adjunto (``utcnow``), en
  **UTC naive** como el resto del proyecto.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.evolucion_medica import EvolucionMedica

# Tipo que se asume cuando el cliente no informa ninguno.
TIPO_ARCHIVO_POR_DEFECTO = "RADIOGRAFIA"


class ArchivoAdjunto(Base):
    """Radiografía, foto o estudio asociado a una evolución médica."""

    __tablename__ = "archivos_adjuntos"
    __table_args__ = (
        # La línea de tiempo pide los adjuntos de una evolución concreta.
        Index("ix_adjunto_evolucion", "evolucion_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    evolucion_id: Mapped[int] = mapped_column(
        ForeignKey("evoluciones_medicas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url_archivo: Mapped[str] = mapped_column(String(500), nullable=False)
    tipo_archivo: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=TIPO_ARCHIVO_POR_DEFECTO,
        server_default=text(f"'{TIPO_ARCHIVO_POR_DEFECTO}'"),
    )
    fecha_subida: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )

    # Relaciones
    evolucion: Mapped["EvolucionMedica"] = relationship(back_populates="archivos")

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<ArchivoAdjunto id={self.id} evolucion_id={self.evolucion_id} "
            f"tipo_archivo={self.tipo_archivo!r} url_archivo={self.url_archivo!r}>"
        )
