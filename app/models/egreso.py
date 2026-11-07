"""Modelos ORM del Módulo 6: proveedores, gastos y cuentas por pagar.

Contexto delimitado
-------------------

Este paquete implementa el **egreso** del consultorio: la plata que sale para
pagar laboratorios (mecánicos dentales), insumos, impuestos y servicios. Es el
espejo del Módulo 5 (cuentas por **cobrar**) y, igual que aquel, **no toca** la
historia clínica: el único vínculo con el dominio clínico es la clave foránea
opcional ``GastoFactura.paciente_id``, que sirve para saber a qué paciente
corresponde la pieza que facturó el mecánico dental.

Las tres tablas
---------------

* ``Proveedor``: el acreedor del consultorio y su ``saldo_pendiente`` (cuánto se
  le debe **hoy**, sumando todas sus facturas abiertas).
* ``GastoFactura``: la factura recibida (concepto, importes, emisión,
  vencimiento y estado ``PENDIENTE`` / ``PARCIAL`` / ``PAGADO``).
* ``PagoEgreso``: cada pago aplicado a una factura, con su método
  (``EFECTIVO`` / ``TRANSFERENCIA`` / ``TARJETA``). Es *append-only*: un pago
  no se edita ni se borra.

Notas de diseño
---------------

* **Identificadores ``UUID``**: las tres tablas usan ``sqlalchemy.Uuid``
  (``UUID`` nativo en PostgreSQL, ``CHAR(32)`` en SQLite). ``paciente_id`` es
  ``Integer`` porque ``pacientes.id`` es entero en este sistema: cambiarlo
  rompería turnos, historias clínicas y facturación.
* **Importes ``Numeric(10, 2)``**: el módulo no admite error de redondeo. No se
  usan ``Float`` (como en el Módulo 3) sino ``Decimal`` en Python y ``NUMERIC``
  en la base, con la misma convención que el Módulo 5.
* **``monto_pagado`` en la factura**: es el acumulado de sus pagos. Se guarda
  para que el estado y el saldo se puedan leer sin sumar la tabla de pagos en
  cada consulta; el servicio lo actualiza **dentro de la misma transacción** que
  inserta el ``PagoEgreso``, de modo que nunca queda desfasado.
* **``saldo_pendiente`` del proveedor**: se incrementa al registrar una factura
  y se reduce al pagarla, siempre con el proveedor bloqueado (``SELECT … FOR
  UPDATE`` en PostgreSQL) y en la misma unidad de trabajo. Es un saldo
  *derivado*: :func:`app.services.egreso_service.recalcular_saldo_proveedor` lo
  puede reconstruir desde las facturas.
* **``ondelete``**: ``RESTRICT`` de la factura al proveedor (una factura con
  valor de comprobante no se borra en cascada), ``SET NULL`` del paciente
  (borrar un paciente no debe borrar la deuda con el laboratorio) y ``CASCADE``
  de los pagos a su factura (sin factura, un pago no significa nada).
* Las fechas son ``UTCDateTime`` (UTC naive, convención del proyecto) y están
  indexadas porque la cartera de cuentas por pagar filtra por vencimiento.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime
from app.models.enums import EstadoPagoFactura, MetodoPagoEgreso, TipoProveedor

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.paciente import Paciente

#: Saldo de un proveedor sin deuda, y base de todos los cálculos del módulo.
SALDO_CERO = Decimal("0.00")


class Proveedor(Base):
    """Acreedor del consultorio: mecánico dental, insumos o servicios/impuestos."""

    __tablename__ = "proveedores"
    __table_args__ = (
        # Se busca por nombre al cargar una factura (orden alfabético en el panel).
        Index("ix_proveedor_nombre", "nombre"),
        # El saldo es una deuda: nunca puede quedar negativo. Es la última
        # defensa ante un pago aplicado dos veces por fuera del servicio.
        CheckConstraint(
            "saldo_pendiente >= 0", name="ck_proveedor_saldo_no_negativo"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    telefono_contacto: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    tipo_proveedor: Mapped[TipoProveedor] = mapped_column(
        SAEnum(
            TipoProveedor,
            name="tipo_proveedor",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        index=True,
    )

    # Cuánto le debe el consultorio a este proveedor (suma de saldos de facturas).
    saldo_pendiente: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=SALDO_CERO, server_default="0"
    )

    # Relaciones
    # ``lazy="selectin"``: las facturas de un proveedor se traen en una consulta
    # extra (sin multiplicar filas), que es como las usa el detalle del panel.
    facturas: Mapped[List["GastoFactura"]] = relationship(
        back_populates="proveedor",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="GastoFactura.fecha_emision.desc()",
    )

    @property
    def facturas_abiertas(self) -> int:
        """Facturas con saldo (``PENDIENTE`` o ``PARCIAL``)."""
        return len(
            [
                factura
                for factura in self.facturas
                if factura.estado_pago is not EstadoPagoFactura.PAGADO
            ]
        )

    @property
    def deuda_vencida(self) -> Decimal:
        """Parte del saldo cuyas facturas ya pasaron el vencimiento."""
        total = sum(
            (Decimal(str(f.saldo_pendiente)) for f in self.facturas if f.vencida),
            SALDO_CERO,
        )
        return total.quantize(Decimal("0.01"))

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Proveedor id={self.id} nombre={self.nombre!r} "
            f"tipo={self.tipo_proveedor} saldo={self.saldo_pendiente}>"
        )


class GastoFactura(Base):
    """Factura recibida: un gasto concreto que el consultorio debe pagar."""

    __tablename__ = "gastos_facturas"
    __table_args__ = (
        Index("ix_gasto_factura_proveedor", "proveedor_id"),
        # La cartera de cuentas por pagar ordena y filtra por vencimiento.
        Index("ix_gasto_factura_vencimiento", "fecha_vencimiento"),
        CheckConstraint("monto_total > 0", name="ck_gasto_factura_monto_total"),
        CheckConstraint("monto_pagado >= 0", name="ck_gasto_factura_monto_pagado"),
        # Invariante contable: nunca se paga más que el total de la factura.
        CheckConstraint(
            "monto_pagado <= monto_total",
            name="ck_gasto_factura_pago_no_excede",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    proveedor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proveedores.id", ondelete="RESTRICT"), nullable=False
    )
    # Sólo para MECANICO_DENTAL: la pieza que se mandó a hacer para un paciente.
    # ``SET NULL``: borrar un paciente no debe borrar la deuda con el laboratorio.
    paciente_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pacientes.id", ondelete="SET NULL"), nullable=True, index=True
    )

    concepto: Mapped[str] = mapped_column(String(255), nullable=False)
    monto_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    # Acumulado de los ``PagoEgreso`` de esta factura (se actualiza con ellos).
    monto_pagado: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=SALDO_CERO, server_default="0"
    )

    fecha_emision: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    fecha_vencimiento: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False
    )

    estado_pago: Mapped[EstadoPagoFactura] = mapped_column(
        SAEnum(
            EstadoPagoFactura,
            name="estado_pago_factura",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=10,
        ),
        nullable=False,
        default=EstadoPagoFactura.PENDIENTE,
        server_default=EstadoPagoFactura.PENDIENTE.value,
    )

    # Relaciones
    proveedor: Mapped["Proveedor"] = relationship(
        back_populates="facturas", lazy="joined"
    )
    # ``lazy="joined"``: el nombre del paciente viaja en la misma consulta.
    paciente: Mapped[Optional["Paciente"]] = relationship(lazy="joined")
    pagos: Mapped[List["PagoEgreso"]] = relationship(
        back_populates="factura",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="PagoEgreso.fecha_pago",
    )

    @property
    def saldo_pendiente(self) -> Decimal:
        """Lo que falta pagar de esta factura (``monto_total - monto_pagado``)."""
        return Decimal(str(self.monto_total)) - Decimal(str(self.monto_pagado))

    @property
    def vencida(self) -> bool:
        """``True`` si la factura pasó su vencimiento y todavía tiene saldo."""
        if self.estado_pago is EstadoPagoFactura.PAGADO:
            return False
        return self.fecha_vencimiento < utcnow()

    # Nombre del proveedor y del paciente expuestos para los esquemas de salida
    # (``from_attributes``): viajan en la misma consulta por los ``joined``.
    @property
    def proveedor_nombre(self) -> str:
        return self.proveedor.nombre

    @property
    def proveedor_tipo(self) -> TipoProveedor:
        return self.proveedor.tipo_proveedor

    @property
    def paciente_nombre(self) -> Optional[str]:
        return self.paciente.nombre if self.paciente is not None else None

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<GastoFactura id={self.id} proveedor_id={self.proveedor_id} "
            f"total={self.monto_total} pagado={self.monto_pagado} "
            f"estado={self.estado_pago}>"
        )


class PagoEgreso(Base):
    """Pago aplicado a una factura recibida (registro *append-only*)."""

    __tablename__ = "pagos_egresos"
    __table_args__ = (
        Index("ix_pago_egreso_factura", "gasto_factura_id"),
        # El flujo de caja de egresos se consulta por rango de días.
        Index("ix_pago_egreso_fecha", "fecha_pago"),
        CheckConstraint("monto_pagado > 0", name="ck_pago_egreso_monto_positivo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    gasto_factura_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gastos_facturas.id", ondelete="CASCADE"),
        nullable=False,
    )

    monto_pagado: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    metodo_pago: Mapped[MetodoPagoEgreso] = mapped_column(
        SAEnum(
            MetodoPagoEgreso,
            name="metodo_pago_egreso",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=13,
        ),
        nullable=False,
        default=MetodoPagoEgreso.EFECTIVO,
        server_default=MetodoPagoEgreso.EFECTIVO.value,
    )

    fecha_pago: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    factura: Mapped["GastoFactura"] = relationship(back_populates="pagos")

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<PagoEgreso id={self.id} factura_id={self.gasto_factura_id} "
            f"monto={self.monto_pagado} metodo={self.metodo_pago}>"
        )

