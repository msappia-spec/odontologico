"""Modelos ORM de caja y facturación: ``MovimientoCaja`` y su detalle.

Al cerrar la visita de un paciente la secretaría elige las prácticas realizadas
y el sistema guarda **un movimiento de caja por turno**
(``uq_movimiento_caja_turno``), que resume cómo se pagó la atención:

* ``monto_efectivo``: lo que se cobra (o se cobró) en el mostrador. Es la suma
  de los coseguros y de las prácticas sin cobertura.
* ``monto_pendiente_os``: lo que queda a cobrarle a la obra social del paciente.
* ``estado_pago``: dónde está ese importe en el circuito de la obra social
  (``PENDIENTE`` → ``EN_PROCESO_LIQUIDACION`` → ``COBRADO`` / ``DEBITADO_OS``).
* ``monto_cobrado_os``: cuánto depositó realmente la cobertura al cerrarse la
  presentación (permite registrar un pago parcial o un débito).

El **detalle** (``DetalleMovimientoCaja``) guarda cada práctica con los importes
**congelados** al momento de facturar: si mañana se actualiza el arancel, la
factura ya emitida y las presentaciones a la obra social siguen mostrando el
importe con el que se atendió al paciente (trazabilidad contable).

Notas de diseño:

* ``ondelete="RESTRICT"`` en ``turno_id`` y ``doctor_id``: un movimiento es un
  registro contable con valor de comprobante; no debe desaparecer en cascada
  (igual criterio que ``EvolucionMedica.doctor_id``).
* ``fecha`` es ``UTCDateTime`` (UTC naive, convención del proyecto) y está
  indexada porque la caja diaria y las liquidaciones filtran por rango.
* Los importes son ``Float`` y se redondean a dos decimales en el servicio.
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime
from app.models.enums import EstadoPagoMovimiento

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.doctor import Doctor
    from app.models.prestacion import Prestacion
    from app.models.turno import Turno


class MovimientoCaja(Base):
    """Cierre económico de un turno: efectivo del paciente + saldo de la OS."""

    __tablename__ = "movimientos_caja"
    __table_args__ = (
        # Un único cierre por visita: evita facturar dos veces el mismo turno.
        Index("uq_movimiento_caja_turno", "turno_id", unique=True),
        # La caja diaria y las liquidaciones consultan por fecha (rango de mes).
        Index("ix_movimiento_caja_fecha", "fecha"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    turno_id: Mapped[int] = mapped_column(
        ForeignKey("turnos.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctores.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    fecha: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        index=True,
        default=utcnow,
        server_default=func.now(),
    )

    monto_efectivo: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    monto_pendiente_os: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    monto_cobrado_os: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    estado_pago: Mapped[EstadoPagoMovimiento] = mapped_column(
        SAEnum(
            EstadoPagoMovimiento,
            name="estado_pago_movimiento",
            native_enum=False,
            validate_strings=True,
            length=25,
        ),
        nullable=False,
        default=EstadoPagoMovimiento.PENDIENTE,
        server_default=EstadoPagoMovimiento.PENDIENTE.value,
    )

    # Motivo del cierre: obligatorio en los débitos (por qué la OS no pagó) y
    # libre en los cobros (nº de depósito, diferencia de importe, etc.).
    observaciones: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relaciones
    # ``lazy="joined"``: el turno (con su paciente y su obra social) viaja en la
    # misma consulta para que la API pueda exponer los datos del paciente sin un
    # *lazy load* posterior al cierre de la sesión.
    turno: Mapped["Turno"] = relationship(lazy="joined")
    doctor: Mapped["Doctor"] = relationship(lazy="joined")
    # ``lazy="selectin"``: el detalle de prácticas se trae en una consulta extra
    # (sin multiplicar filas de movimientos), como los adjuntos de una evolución.
    detalles: Mapped[List["DetalleMovimientoCaja"]] = relationship(
        back_populates="movimiento",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<MovimientoCaja id={self.id} turno_id={self.turno_id} "
            f"doctor_id={self.doctor_id} efectivo={self.monto_efectivo} "
            f"pendiente_os={self.monto_pendiente_os} estado={self.estado_pago}>"
        )



class DetalleMovimientoCaja(Base):
    """Práctica facturada dentro de un movimiento, con sus importes congelados."""

    __tablename__ = "detalles_movimiento_caja"
    __table_args__ = (
        # El detalle se lee siempre por movimiento (factura / presentación).
        Index("ix_detalle_movimiento", "movimiento_caja_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    movimiento_caja_id: Mapped[int] = mapped_column(
        ForeignKey("movimientos_caja.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prestacion_id: Mapped[int] = mapped_column(
        # La práctica no se borra (baja lógica), pero si alguien la borrara
        # físicamente la factura conserva sus datos congelados y la referencia.
        ForeignKey("prestaciones.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Copia de la práctica al momento de facturar: la factura no cambia si luego
    # se corrige el catálogo (código de nomenclador y descripción del día).
    codigo_nomenclador: Mapped[str] = mapped_column(String(30), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False)

    cantidad: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    monto_cubre_os: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    monto_coseguro_paciente: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )

    # Relaciones
    movimiento: Mapped["MovimientoCaja"] = relationship(back_populates="detalles")
    prestacion: Mapped["Prestacion"] = relationship(
        back_populates="detalles", lazy="joined"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<DetalleMovimientoCaja id={self.id} "
            f"movimiento_caja_id={self.movimiento_caja_id} "
            f"codigo={self.codigo_nomenclador!r} cantidad={self.cantidad}>"
        )
