"""Esquemas Pydantic del Módulo 5: cuentas corrientes y financiación.

Contratos de la extensión financiera:

* **Consulta** (``CuentaCorrienteRead``): el saldo del paciente y su historial de
  débitos/créditos, del movimiento más reciente al más antiguo.
* **Apertura de un plan** (``AbrirPlanCuotasRequest`` /
  ``PlanCuotasAbiertoRead``): total del tratamiento, entrega inicial (50 % por
  defecto) y cuotas mensuales.
* **Pago** (``RegistrarPagoRequest`` / ``PagoRegistradoRead``): un crédito nuevo
  y el saldo resultante.

Convención de importes
----------------------

Todo el módulo trabaja con ``Decimal`` (``Numeric(10, 2)`` en la base): ni los
cálculos ni los totales pasan por ``float``. Para no romper el contrato del
resto de la API —y del panel de la secretaría, que suma importes en JavaScript—
los *serializadores* publican esos ``Decimal`` como **número JSON** (ver
``field_serializer(..., when_used="json")``): la precisión se cuida en el
servidor y el cliente recibe el mismo tipo de dato que en
``/movimientos-caja``.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, List, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.datetime_utils import to_naive_utc
from app.models.enums import EstadoPlanCuotas, MedioPago, TipoMovimientoCC

#: Importe monetario: hasta 10 dígitos con 2 decimales (``Numeric(10, 2)``).
Monto = Annotated[Decimal, Field(max_digits=10, decimal_places=2)]

#: Importe de un movimiento/pago: siempre mayor que cero.
MontoPositivo = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]

#: Porcentaje de entrega inicial (``50`` = la mitad del tratamiento).
Porcentaje = Annotated[Decimal, Field(gt=0, le=100, max_digits=5, decimal_places=2)]

#: Largo máximo del texto libre que carga la secretaría. Es menor que la columna
#: (``String(200)``) porque el servicio le antepone un prefijo explicativo.
MAX_DESCRIPCION = 120

CERO = Decimal("0.00")


class _EsquemaFinanciero(BaseModel):
    """Base de los esquemas de salida: se construyen desde el ORM."""

    model_config = ConfigDict(from_attributes=True)


class _SolicitudConFecha(BaseModel):
    """Normaliza la ``fecha`` opcional a UTC naive (convención del proyecto).

    La fecha permite registrar un movimiento de días anteriores (una entrega que
    se cobró en el mostrador, una cuota abonada por transferencia el viernes).
    """

    fecha: Optional[datetime] = Field(
        None,
        description="Momento del movimiento; si se omite se usa el del alta.",
    )

    @model_validator(mode="after")
    def _fecha_utc_naive(self):
        """Convierte la fecha recibida a UTC naive (misma convención que el ORM)."""
        fecha = getattr(self, "fecha", None)
        if fecha is not None:
            self.fecha = to_naive_utc(fecha)
        return self


class TransaccionCCRead(_EsquemaFinanciero):
    """Un movimiento del libro mayor de la cuenta."""

    id: UUID
    cuenta_corriente_id: UUID
    tipo_movimiento: TipoMovimientoCC = Field(
        ..., description="``DEBITO`` suma deuda; ``CREDITO`` la cancela."
    )
    monto: Monto
    descripcion: str
    fecha: datetime
    medio_pago: Optional[MedioPago] = Field(
        None,
        description=(
            "Medio del cobro (``EFECTIVO`` o ``TRANSFERENCIA``). Los débitos —y "
            "los pagos cargados antes de existir el campo— llegan en `null`."
        ),
    )

    @field_serializer("monto", when_used="json")
    def _monto_numerico(self, valor: Decimal) -> float:
        """Publica el importe como número JSON (el cálculo sigue en Decimal)."""
        return float(valor)


class PlanCuotasRead(_EsquemaFinanciero):
    """Plan de financiación de un tratamiento."""

    id: UUID
    cuenta_corriente_id: UUID
    monto_total_tratamiento: Monto
    monto_entrega_inicial: Monto
    cantidad_cuotas: int
    monto_por_cuota: Monto
    estado: EstadoPlanCuotas

    @field_serializer(
        "monto_total_tratamiento",
        "monto_entrega_inicial",
        "monto_por_cuota",
        when_used="json",
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class PlanCuotasAbiertoRead(PlanCuotasRead):
    """Plan recién abierto, con la liquidación de la apertura.

    ``monto_financiado`` es lo que se reparte en cuotas y
    ``ajuste_ultima_cuota`` la diferencia de centavos que absorbe la última
    (``500 / 3 = 166.67`` → ``-0.01``). Así la secretaría puede mostrarle al
    paciente el plan exacto, sin sorpresas de redondeo.
    """

    monto_financiado: Monto
    ajuste_ultima_cuota: Monto
    saldo_actual: Monto = Field(
        ..., description="Saldo deudor de la cuenta después de la apertura."
    )
    entrega_inicial_pagada: bool

    @field_serializer(
        "monto_financiado", "ajuste_ultima_cuota", "saldo_actual", when_used="json"
    )
    def _montos_calculados(self, valor: Decimal) -> float:
        return float(valor)


class CuentaCorrienteRead(_EsquemaFinanciero):
    """Estado de la cuenta de un paciente y su historial de movimientos.

    ``existe_cuenta`` distingue a un paciente **sin** cuenta (todavía no tuvo
    ningún movimiento) de uno que la tiene: en ese caso el saldo es ``0.00`` y el
    historial viaja vacío, sin necesidad de un ``404`` ni de escribir en la base
    desde un ``GET``.
    """

    paciente_id: int
    existe_cuenta: bool
    id: Optional[UUID] = None
    saldo_actual: Monto = CERO
    ultima_actualizacion: Optional[datetime] = None
    total_debitos: Monto = CERO
    total_creditos: Monto = CERO
    transacciones: List[TransaccionCCRead] = Field(default_factory=list)
    planes: List[PlanCuotasRead] = Field(default_factory=list)

    @field_serializer(
        "saldo_actual", "total_debitos", "total_creditos", when_used="json"
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class PagoRegistradoRead(_EsquemaFinanciero):
    """Resultado de registrar un pago: el crédito y el saldo que queda."""

    transaccion: TransaccionCCRead
    cuenta_corriente_id: UUID
    saldo_actual: Monto
    saldo_a_favor: Monto = Field(
        CERO,
        description="Importe pagado de más (``saldo_actual`` negativo, en positivo).",
    )
    plan_cuotas: Optional[PlanCuotasRead] = Field(
        None, description="Plan al que se imputó el pago, si se indicó uno."
    )

    @field_serializer("saldo_actual", "saldo_a_favor", when_used="json")
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class CuentaCorrienteResumenRead(_EsquemaFinanciero):
    """Fila del informe de cuentas corrientes: un paciente y su situación.

    Reúne lo que la secretaría necesita para cobrar sin abrir paciente por
    paciente: quién es, qué planes tiene, cuánto se le debitó (lo financiado más
    los cargos), cuánto pagó y cuánto debe (``saldo_actual``; si es negativo, el
    paciente tiene saldo a favor).
    """

    paciente_id: int
    paciente_nombre: str
    paciente_dni: Optional[str] = None
    paciente_telefono: Optional[str] = None
    total_debitos: Monto = CERO
    total_creditos: Monto = CERO
    saldo_actual: Monto = CERO
    planes: List[PlanCuotasRead] = Field(default_factory=list)

    @field_serializer(
        "total_debitos", "total_creditos", "saldo_actual", when_used="json"
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class InformeCuentasCorrientesRead(BaseModel):
    """Informe consolidado de todas las cuentas corrientes.

    ``deuda_total`` suma **sólo** los saldos deudores (los saldos a favor no
    compensan la deuda de otro paciente: se informan aparte en
    ``saldo_a_favor_total``). Los totales se calculan en el servidor, en
    ``Decimal``, y viajan como número JSON igual que el resto del módulo.
    """

    generado: datetime
    cantidad_pacientes: int = 0
    cantidad_con_deuda: int = 0
    cantidad_planes: int = 0
    total_debitado: Monto = CERO
    total_cobrado: Monto = CERO
    deuda_total: Monto = CERO
    saldo_a_favor_total: Monto = CERO
    cuentas: List[CuentaCorrienteResumenRead] = Field(default_factory=list)

    @field_serializer(
        "total_debitado",
        "total_cobrado",
        "deuda_total",
        "saldo_a_favor_total",
        when_used="json",
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class AbrirPlanCuotasRequest(_SolicitudConFecha):
    """Apertura de un tratamiento financiado (``POST /finanzas/plan-cuotas``).

    El sistema calcula la entrega inicial (``porcentaje_entrega_inicial``, 50 %
    por defecto) y el valor de cada cuota sobre el saldo restante.
    """

    paciente_id: int = Field(..., gt=0, description="Paciente que financia.")
    monto_total: MontoPositivo = Field(
        ..., description="Importe total del tratamiento (lo que se debita)."
    )
    cantidad_cuotas: int = Field(
        ..., ge=1, le=120, description="Cuotas mensuales para el saldo restante."
    )
    porcentaje_entrega_inicial: Porcentaje = Field(
        Decimal("50.00"),
        description="Porcentaje que se entrega al inicio (por defecto el 50 %).",
    )
    pago_entrega_inicial: bool = Field(
        True,
        description=(
            "``true`` si la entrega inicial se cobra en este momento: se registra "
            "un ``CREDITO`` inmediato y el saldo deudor se reduce."
        ),
    )
    medio_pago: MedioPago = Field(
        MedioPago.EFECTIVO,
        description=(
            "Medio con el que se cobra la entrega inicial (``EFECTIVO`` o "
            "``TRANSFERENCIA``). Se ignora si `pago_entrega_inicial` es `false`."
        ),
    )
    descripcion: Optional[str] = Field(
        None,
        max_length=MAX_DESCRIPCION,
        description="Motivo o tratamiento (p. ej. «Ortodoncia, 18 meses»).",
    )

    @field_validator("descripcion", mode="before")
    @classmethod
    def _descripcion_opcional(cls, valor):
        """Un texto vacío se guarda como ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class RegistrarPagoRequest(_SolicitudConFecha):
    """Pago posterior del paciente (``POST /finanzas/pagar``)."""

    paciente_id: int = Field(..., gt=0)
    monto: MontoPositivo = Field(
        ..., description="Importe abonado (cuota mensual o pago a cuenta)."
    )
    descripcion: Optional[str] = Field(
        None, max_length=MAX_DESCRIPCION, description="Concepto del pago."
    )
    plan_cuotas_id: Optional[UUID] = Field(
        None,
        description=(
            "Plan al que se imputa el pago. Si se omite, el crédito baja el saldo "
            "general de la cuenta."
        ),
    )
    medio_pago: MedioPago = Field(
        MedioPago.EFECTIVO,
        description=(
            "Medio con el que pagó el paciente (``EFECTIVO`` o ``TRANSFERENCIA``). "
            "La caja del día lo usa para separar lo ingresado en efectivo de lo "
            "transferido."
        ),
    )

    @field_validator("descripcion", mode="before")
    @classmethod
    def _descripcion_opcional(cls, valor):
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class PlanCuotasEstadoUpdate(BaseModel):
    """Cambio manual del estado de un plan: ``ACTIVO`` ↔ ``RECOBRABLE``.

    ``FINALIZADO`` **no** se puede fijar a mano: lo determina el servicio cuando
    la cuenta queda saldada (si no, se podría cerrar un plan con deuda viva).
    """

    estado: EstadoPlanCuotas

    @field_validator("estado")
    @classmethod
    def _no_finalizado_a_mano(cls, valor: EstadoPlanCuotas) -> EstadoPlanCuotas:
        if valor is EstadoPlanCuotas.FINALIZADO:
            raise ValueError(
                "FINALIZADO se calcula automáticamente al saldar la cuenta."
            )
        return valor


# ---------------------------------------------------------------------------
# Depuración: listado global y borrado de movimientos del libro mayor
# ---------------------------------------------------------------------------


class TransaccionCCListadoRead(_EsquemaFinanciero):
    """Movimiento del libro mayor con el paciente al que pertenece.

    Es la fila del módulo de borrado: a diferencia de ``TransaccionCCRead``
    (que vive dentro de la cuenta de un paciente) acá viajan ``paciente_id`` y
    ``paciente_nombre`` para poder listar todos los movimientos del consultorio
    en una sola tabla y saber de quién es cada uno antes de eliminarlo.
    """

    id: UUID
    cuenta_corriente_id: UUID
    paciente_id: int
    paciente_nombre: str
    paciente_dni: Optional[str] = None
    tipo_movimiento: TipoMovimientoCC = Field(
        ..., description="``DEBITO`` suma deuda; ``CREDITO`` la cancela."
    )
    monto: Monto
    descripcion: str
    fecha: datetime
    medio_pago: Optional[MedioPago] = Field(
        None, description="Medio del cobro; `null` en los débitos y en los pagos viejos."
    )

    @field_serializer("monto", when_used="json")
    def _monto_numerico(self, valor: Decimal) -> float:
        return float(valor)


class TransaccionEliminadaRead(_EsquemaFinanciero):
    """Resultado de borrar un movimiento del libro mayor.

    Además de los datos del movimiento borrado, informa el **saldo recalculado**
    de la cuenta (``saldo_actual``, con ``saldo_a_favor`` en positivo cuando el
    paciente queda con crédito) y si la cuenta se eliminó por haber quedado sin
    movimientos ni planes.
    """

    id: UUID
    paciente_id: int
    paciente_nombre: str
    cuenta_corriente_id: UUID
    tipo_movimiento: TipoMovimientoCC
    monto: Monto
    descripcion: str
    fecha: datetime
    medio_pago: Optional[MedioPago] = Field(
        None, description="Medio del cobro; `null` en los débitos."
    )
    saldo_actual: Monto = CERO
    saldo_a_favor: Monto = CERO
    cuenta_eliminada: bool = Field(
        False,
        description=(
            "``true`` si era el último movimiento de la cuenta y, sin planes "
            "abiertos, se borró también la cuenta corriente del paciente."
        ),
    )

    @field_serializer("monto", "saldo_actual", "saldo_a_favor", when_used="json")
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


# ---------------------------------------------------------------------------
# Caja del día: cobros de cuentas corrientes del período
# ---------------------------------------------------------------------------


class CobroCuentaCorrienteRead(_EsquemaFinanciero):
    """Un cobro de cuenta corriente: entrega inicial, cuota o pago a cuenta.

    Son los ingresos que **no** pasan por ``movimientos_caja`` (la caja del día
    sólo guarda el cierre de cada visita). Se listan en la pestaña *Caja del día*
    para que el total del día incluya la plata cobrada por cuentas corrientes.
    """

    id: UUID
    paciente_id: int
    paciente_nombre: str
    paciente_dni: Optional[str] = None
    monto: Monto
    descripcion: str
    fecha: datetime
    #: Nunca es ``None``: los cobros cargados antes de existir el campo se
    #: informan como ``EFECTIVO`` (era el único medio que el sistema suponía).
    medio_pago: MedioPago = Field(
        MedioPago.EFECTIVO, description="``EFECTIVO`` o ``TRANSFERENCIA``."
    )

    @field_serializer("monto", when_used="json")
    def _monto_numerico(self, valor: Decimal) -> float:
        return float(valor)


class CobrosCuentaCorrienteRead(BaseModel):
    """Cobros de cuentas corrientes del período, con el total por medio de pago.

    Los totales se suman en el servidor (y no en el panel) para que no dependan
    de la paginación del listado: ``total`` es lo que ingresó por cuentas
    corrientes en el rango consultado.
    """

    desde: datetime
    hasta: datetime
    cantidad: int = 0
    total: Monto = CERO
    total_efectivo: Monto = CERO
    total_transferencia: Monto = CERO
    cobros: List[CobroCuentaCorrienteRead] = Field(default_factory=list)

    @field_serializer(
        "total", "total_efectivo", "total_transferencia", when_used="json"
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)
