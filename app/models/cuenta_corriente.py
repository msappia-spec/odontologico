"""Modelos ORM del módulo financiero: cuenta corriente, mayor y planes de cuotas.

Contexto delimitado
-------------------

Este paquete implementa la **extensión financiera** del sistema (Módulo 5). Se
apoya en ``Paciente`` pero **no** toca la historia clínica: la clínica es
inmutable (una evolución médica no se edita ni se borra) y la contabilidad es
transaccional (se corrige con un movimiento nuevo, nunca reescribiendo el
anterior). El único vínculo con el resto del dominio es la clave foránea
``paciente_id``.

Las tres tablas
---------------

* ``CuentaCorriente``: una por paciente (índice único
  ``uq_cuenta_corriente_paciente``). Guarda el ``saldo_actual`` —la foto del
  momento— y ``ultima_actualizacion``; el detalle vive en el mayor.
* ``TransaccionCC``: el **libro mayor**. Es *append-only*: sólo se insertan
  movimientos (``DEBITO`` = deuda, ``CREDITO`` = pago). El saldo siempre se
  puede reconstruir sumando esta tabla, de ahí que el servicio lo recalcule en
  lugar de incrementarlo a ciegas.
* ``PlanCuotas``: la apertura de un tratamiento financiado (entrega inicial +
  cuotas mensuales) con su estado ``ACTIVO`` / ``FINALIZADO`` / ``RECOBRABLE``.

Notas de diseño
---------------

* **Identificadores ``UUID``**: los tres modelos usan ``sqlalchemy.Uuid``
  (``UUID`` nativo en PostgreSQL, ``CHAR(32)`` en SQLite). ``paciente_id``, en
  cambio, es ``Integer`` porque ``pacientes.id`` es entero en este sistema:
  cambiar la PK del paciente rompería turnos, historias clínicas y facturación.
* **Importes ``Numeric(10, 2)``**: a diferencia del Módulo 3 (que usa
  ``Float``), acá no se admite error de redondeo. SQLAlchemy entrega y acepta
  ``Decimal`` de Python en los dos motores soportados.
* ``ondelete="RESTRICT"`` en ``paciente_id``: un paciente con cuenta corriente
  no se puede borrar (el registro contable tiene valor de comprobante). En
  cambio, los movimientos y los planes **sí** acompañan a su cuenta
  (``CASCADE``): sin cuenta no significan nada.
* ``fecha`` es ``UTCDateTime`` (UTC naive, convención del proyecto) e indexada
  junto con la cuenta, porque el historial se lee siempre por cuenta y en
  orden cronológico descendente.
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
from app.models.enums import EstadoPlanCuotas, TipoMovimientoCC

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.paciente import Paciente

#: Saldo de una cuenta recién abierta (``Decimal``, nunca ``float``).
SALDO_CERO = Decimal("0.00")


class CuentaCorriente(Base):
    """Cuenta corriente de un paciente: su saldo y su libro de movimientos."""

    __tablename__ = "cuentas_corrientes"
    __table_args__ = (
        # Una única cuenta por paciente. Se usa un índice único *nombrado* (y no
        # una ``UniqueConstraint``) con el mismo criterio que ``uq_paciente_dni``:
        # se puede replicar en bases existentes con ``CREATE UNIQUE INDEX``.
        Index("uq_cuenta_corriente_paciente", "paciente_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    paciente_id: Mapped[int] = mapped_column(
        # ``RESTRICT``: la cuenta es un registro contable y no se borra en
        # cascada con el paciente.
        ForeignKey("pacientes.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ``saldo_actual`` = suma de débitos - suma de créditos. Lo recalcula
    # ``app.services.finanzas_service`` en cada movimiento y **dentro de la misma
    # transacción**. Positivo = deuda del paciente; negativo = saldo a favor.
    saldo_actual: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=SALDO_CERO, server_default="0"
    )
    ultima_actualizacion: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utcnow,
        server_default=func.now(),
    )

    # Relaciones
    paciente: Mapped["Paciente"] = relationship(back_populates="cuenta_corriente")
    # ``lazy="selectin"``: el historial viaja en una consulta extra, sin
    # multiplicar las filas de la cuenta. Se ordena de lo más nuevo a lo más
    # viejo, que es como se muestra en la ficha del paciente.
    transacciones: Mapped[List["TransaccionCC"]] = relationship(
        back_populates="cuenta_corriente",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="TransaccionCC.fecha.desc()",
    )
    planes: Mapped[List["PlanCuotas"]] = relationship(
        back_populates="cuenta_corriente",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="PlanCuotas.id",
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<CuentaCorriente id={self.id} paciente_id={self.paciente_id} "
            f"saldo_actual={self.saldo_actual}>"
        )



class TransaccionCC(Base):
    """Movimiento del libro mayor (``DEBITO`` = deuda, ``CREDITO`` = pago)."""

    __tablename__ = "transacciones_cc"
    __table_args__ = (
        # El historial se lee siempre por cuenta y ordenado por fecha.
        Index("ix_transaccion_cc_cuenta_fecha", "cuenta_corriente_id", "fecha"),
        # Un movimiento de importe cero no existe: no mueve el saldo y sólo
        # ensucia el historial.
        CheckConstraint("monto > 0", name="ck_transaccion_cc_monto_positivo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cuenta_corriente_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cuentas_corrientes.id", ondelete="CASCADE"), nullable=False
    )
    tipo_movimiento: Mapped[TipoMovimientoCC] = mapped_column(
        SAEnum(
            TipoMovimientoCC,
            name="tipo_movimiento_cc",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=10,
        ),
        nullable=False,
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False)
    fecha: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    cuenta_corriente: Mapped["CuentaCorriente"] = relationship(
        back_populates="transacciones"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<TransaccionCC id={self.id} "
            f"cuenta_corriente_id={self.cuenta_corriente_id} "
            f"tipo={self.tipo_movimiento} monto={self.monto}>"
        )


class PlanCuotas(Base):
    """Tratamiento financiado: entrega inicial + cuotas mensuales."""

    __tablename__ = "planes_cuotas"
    __table_args__ = (
        Index("ix_plan_cuotas_cuenta", "cuenta_corriente_id"),
        CheckConstraint(
            "monto_total_tratamiento > 0", name="ck_plan_cuotas_monto_total"
        ),
        CheckConstraint(
            "monto_entrega_inicial >= 0", name="ck_plan_cuotas_entrega_inicial"
        ),
        CheckConstraint("cantidad_cuotas >= 1", name="ck_plan_cuotas_cantidad"),
        CheckConstraint("monto_por_cuota >= 0", name="ck_plan_cuotas_monto_cuota"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cuenta_corriente_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cuentas_corrientes.id", ondelete="CASCADE"), nullable=False
    )

    # Importe total del tratamiento: se debita completo en la cuenta.
    monto_total_tratamiento: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    # Entrega inicial (por defecto el 50 %), esté pagada o no.
    monto_entrega_inicial: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=SALDO_CERO, server_default="0"
    )
    cantidad_cuotas: Mapped[int] = mapped_column(Integer, nullable=False)
    # Valor de cada cuota mensual, redondeado a dos decimales (medio hacia
    # arriba). La diferencia de centavos la absorbe la última cuota y el servicio
    # la expone como ``ajuste_ultima_cuota`` en la respuesta del alta.
    monto_por_cuota: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=SALDO_CERO, server_default="0"
    )

    estado: Mapped[EstadoPlanCuotas] = mapped_column(
        SAEnum(
            EstadoPlanCuotas,
            name="estado_plan_cuotas",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=12,
        ),
        nullable=False,
        default=EstadoPlanCuotas.ACTIVO,
        server_default=EstadoPlanCuotas.ACTIVO.value,
    )

    cuenta_corriente: Mapped["CuentaCorriente"] = relationship(back_populates="planes")

    @property
    def monto_financiado(self) -> Decimal:
        """Importe que se reparte en cuotas (total menos entrega inicial)."""
        return self.monto_total_tratamiento - self.monto_entrega_inicial

    @property
    def ajuste_ultima_cuota(self) -> Decimal:
        """Diferencia de centavos que absorbe la última cuota.

        ``monto_por_cuota * cantidad_cuotas`` puede no coincidir exactamente con
        el monto financiado (``500 / 3 = 166.67`` → ``500.01``). Este ajuste
        (positivo o negativo) cierra el plan al centavo.
        """
        return self.monto_financiado - self.monto_por_cuota * self.cantidad_cuotas

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<PlanCuotas id={self.id} "
            f"cuenta_corriente_id={self.cuenta_corriente_id} "
            f"total={self.monto_total_tratamiento} "
            f"entrega={self.monto_entrega_inicial} "
            f"cuotas={self.cantidad_cuotas}x{self.monto_por_cuota} "
            f"estado={self.estado}>"
        )
