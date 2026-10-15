"""Modelo ORM ``Prestacion``: catálogo de prácticas facturables (Módulo 3).

Una prestación (o *práctica*) es lo que el consultorio puede cobrar: una
obturación, una limpieza, una endodoncia… Se identifica por su
``codigo_nomenclador`` (el código con el que la obra social la reconoce, p. ej.
``01.01`` del Nomenclador Nacional de Prestaciones Odontológicas) y lleva el
``valor_base``: el precio de referencia cuando el paciente no tiene cobertura o
cuando la cobertura no tiene arancel cargado para esa práctica.

Notas de diseño:

* ``codigo_nomenclador`` es **único** mediante el índice nombrado
  ``uq_prestacion_codigo`` (y no una ``UniqueConstraint``) para poder replicarlo
  en bases existentes con ``CREATE UNIQUE INDEX`` sin reconstruir la tabla, igual
  que ``uq_paciente_dni`` y ``uq_obra_social_nombre``.
* ``activa`` implementa el **borrado lógico** del catálogo: una práctica dada de
  baja deja de ofrecerse al facturar, pero se conserva porque los aranceles y
  las facturas ya emitidas (``detalles_movimiento_caja``) la referencian.
* ``valor_base`` es ``Float`` (decisión del Módulo 3): los importes se redondean
  a dos decimales en la capa de servicio al facturar.
"""

from typing import TYPE_CHECKING, List

from sqlalchemy import Boolean, Float, Index, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.arancel_obra_social import ArancelObraSocial
    from app.models.movimiento_caja import DetalleMovimientoCaja


class Prestacion(Base):
    """Práctica facturable del consultorio (con su código de nomenclador)."""

    __tablename__ = "prestaciones"
    __table_args__ = (
        Index("uq_prestacion_codigo", "codigo_nomenclador", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    codigo_nomenclador: Mapped[str] = mapped_column(String(30), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False)
    valor_base: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    activa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    # Relaciones
    # ``ArancelObraSocial`` es la configuración de esta práctica por cobertura
    # (una fila por obra social). Al borrar la prestación sus aranceles la
    # acompañan, porque sin práctica no significan nada.
    aranceles: Mapped[List["ArancelObraSocial"]] = relationship(
        back_populates="prestacion",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # Detalles ya facturados: se conservan (la factura es un registro contable).
    detalles: Mapped[List["DetalleMovimientoCaja"]] = relationship(
        back_populates="prestacion",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Prestacion id={self.id} codigo={self.codigo_nomenclador!r} "
            f"descripcion={self.descripcion!r} valor_base={self.valor_base} "
            f"activa={self.activa}>"
        )
