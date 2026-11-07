"""Endpoints del Módulo 5: cuentas corrientes y financiación de tratamientos.

Rutas (prefijo ``/api/v1``):

* ``GET  /finanzas/cuenta-corriente/paciente/{paciente_id}`` — saldo e historial
  de movimientos del paciente (del más nuevo al más viejo). Si el paciente
  todavía no tuvo movimientos responde ``existe_cuenta = false`` con saldo
  ``0.00``; nunca abre la cuenta desde un ``GET``.
* ``GET  /finanzas/cuentas-corrientes`` — informe consolidado: todos los
  pacientes con cuenta, sus planes, lo que pagaron, lo que deben y la deuda
  total del consultorio.
* ``POST /finanzas/plan-cuotas`` — abre un tratamiento financiado: calcula la
  entrega inicial (50 % por defecto) y las cuotas mensuales, y registra la deuda
  (más el pago de la entrega, si se cobró en el momento).
* ``POST /finanzas/pagar`` — registra un pago (cuota o pago a cuenta) y devuelve
  el saldo resultante.
* ``PATCH /finanzas/plan-cuotas/{plan_id}/estado`` — pasa un plan a
  ``RECOBRABLE`` (cobranza) o lo devuelve a ``ACTIVO``. ``FINALIZADO`` lo pone el
  sistema cuando la cuenta queda saldada.
* ``GET /finanzas/transacciones`` y
  ``DELETE /finanzas/cuenta-corriente/transacciones/{transaccion_id}``: módulo de
  borrado, listan los movimientos de todas las cuentas y eliminan uno,
  recalculando el saldo y el estado de los planes.
* ``GET /finanzas/cobros-cuenta-corriente`` — cobros del período (entregas
  iniciales, cuotas y pagos a cuenta) con el total por medio de pago: es la
  segunda mitad de la *Caja del día* del Módulo 3.

Todos los importes viajan como número JSON y se calculan en ``Decimal`` en el
servidor; el detalle de las reglas contables está en
:mod:`app.services.finanzas_service`.
"""

from datetime import date as Date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.datetime_utils import rango_del_dia, utcnow
from app.db.session import get_db
from app.models.enums import MedioPago, TipoMovimientoCC
from app.schemas.finanzas import (
    AbrirPlanCuotasRequest,
    CobrosCuentaCorrienteRead,
    CuentaCorrienteRead,
    InformeCuentasCorrientesRead,
    PagoRegistradoRead,
    PlanCuotasAbiertoRead,
    PlanCuotasEstadoUpdate,
    PlanCuotasRead,
    RegistrarPagoRequest,
    TransaccionCCListadoRead,
    TransaccionEliminadaRead,
)
from app.services import finanzas_service

router = APIRouter(prefix="/finanzas", tags=["Cuenta corriente y financiación"])


@router.get(
    "/cuenta-corriente/paciente/{paciente_id}",
    response_model=CuentaCorrienteRead,
    summary="Consultar la cuenta corriente de un paciente",
    description=(
        "Devuelve el saldo deudor del paciente y su historial de movimientos "
        "(débitos y créditos) ordenado por fecha descendente, más los planes de "
        "cuotas abiertos. El saldo se reconstruye sumando el libro mayor: "
        "positivo es deuda del paciente y negativo, saldo a favor. Un paciente "
        "sin movimientos devuelve `existe_cuenta` en `false` con saldo `0.00` "
        "(no se crea la cuenta desde una consulta); un paciente inexistente "
        "devuelve 404."
    ),
)
def obtener_cuenta_corriente(
    paciente_id: int, db: Session = Depends(get_db)
) -> CuentaCorrienteRead:
    return finanzas_service.obtener_cuenta_corriente(db, paciente_id)


@router.get(
    "/cuentas-corrientes",
    response_model=InformeCuentasCorrientesRead,
    summary="Informe de todas las cuentas corrientes",
    description=(
        "Devuelve una fila por paciente con cuenta corriente —ordenadas por "
        "nombre— con sus planes de cuotas, el total debitado, el total cobrado "
        "(lo que pagó) y el saldo: positivo es deuda y negativo, saldo a favor. "
        "Al pie viajan los totales del consultorio: `deuda_total` suma **sólo** "
        "los saldos deudores (el saldo a favor de un paciente no compensa la "
        "deuda de otro, se informa en `saldo_a_favor_total`), más "
        "`total_debitado`, `total_cobrado` y los conteos de pacientes y planes. "
        "Es de sólo lectura: los pacientes sin movimientos no aparecen porque "
        "todavía no tienen plan, pago ni deuda."
    ),
)
def informe_cuentas_corrientes(
    db: Session = Depends(get_db),
) -> InformeCuentasCorrientesRead:
    return finanzas_service.informe_cuentas_corrientes(db)


@router.post(
    "/plan-cuotas",
    response_model=PlanCuotasAbiertoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir un plan de cuotas",
    description=(
        "Financia un tratamiento: debita el **monto total** en la cuenta del "
        "paciente y guarda el plan con la entrega inicial (porcentaje "
        "configurable, 50 % por defecto) y el valor de cada cuota mensual. Si "
        "`pago_entrega_inicial` es verdadero se registra además el crédito de la "
        "entrega en el mismo acto y el saldo deudor se reduce. La respuesta "
        "informa `monto_financiado` y `ajuste_ultima_cuota`, la diferencia de "
        "centavos que absorbe la última cuota (todo se calcula en `Decimal` con "
        "redondeo medio hacia arriba). Un porcentaje que cubra el total devuelve "
        "422 y un paciente inexistente, 404."
    ),
)
def abrir_plan_cuotas(
    payload: AbrirPlanCuotasRequest, db: Session = Depends(get_db)
) -> PlanCuotasAbiertoRead:
    return finanzas_service.abrir_plan_cuotas(db, payload)


@router.post(
    "/pagar",
    response_model=PagoRegistradoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un pago",
    description=(
        "Registra un pago del paciente como un crédito nuevo (el libro mayor es "
        "append-only: nunca se reescribe un movimiento anterior) y devuelve el "
        "saldo actualizado. El pago puede imputarse a un plan de cuotas "
        "(`plan_cuotas_id`) o quedar como pago a cuenta del saldo general. Pagar "
        "de más es válido: el excedente se informa en `saldo_a_favor`. Si la "
        "cuenta queda saldada, los planes pasan a `FINALIZADO` en la misma "
        "transacción. Admite una `fecha` anterior para imputar un cobro de días "
        "atrás."
    ),
)
def registrar_pago(
    payload: RegistrarPagoRequest, db: Session = Depends(get_db)
) -> PagoRegistradoRead:
    return finanzas_service.registrar_pago(db, payload)


@router.patch(
    "/plan-cuotas/{plan_id}/estado",
    response_model=PlanCuotasRead,
    summary="Cambiar el estado de un plan de cuotas",
    description=(
        "Marca el plan como `RECOBRABLE` (pasa a cobranza) o lo devuelve a "
        "`ACTIVO`. `FINALIZADO` no se puede fijar a mano: lo determina el sistema "
        "cuando la cuenta queda saldada (se responde 422). Si la cuenta ya está "
        "saldada, el cambio devuelve 409 y un plan inexistente, 404."
    ),
)
def cambiar_estado_plan(
    plan_id: UUID, payload: PlanCuotasEstadoUpdate, db: Session = Depends(get_db)
) -> PlanCuotasRead:
    return finanzas_service.cambiar_estado_plan(db, plan_id, payload.estado)


# --------------------------------------------------------------------------- #
# Caja del día: cobros de cuentas corrientes del período
# --------------------------------------------------------------------------- #
@router.get(
    "/cobros-cuenta-corriente",
    response_model=CobrosCuentaCorrienteRead,
    summary="Cobros de cuentas corrientes del período",
    description=(
        "Devuelve los **créditos** del libro mayor del rango consultado (por "
        "defecto, **hoy**) —entregas iniciales de planes, cuotas y pagos a "
        "cuenta— con el paciente, el concepto y el medio de pago, más los totales "
        "`total`, `total_efectivo` y `total_transferencia`. Es la mitad de la "
        "*Caja del día* que no pasa por los cierres de visita: sin estos cobros "
        "el día quedaría subvaluado. Los pagos cargados antes de existir el campo "
        "se informan como `EFECTIVO` (era el único medio que el sistema suponía). "
        "`medio_pago` filtra el listado y los totales; los débitos nunca aparecen "
        "(son deuda, no ingreso)."
    ),
)
def cobros_cuenta_corriente(
    desde: Optional[Date] = Query(None, description="Primer día (AAAA-MM-DD)."),
    hasta: Optional[Date] = Query(None, description="Último día (AAAA-MM-DD)."),
    medio_pago: Optional[MedioPago] = Query(
        None, description="``EFECTIVO`` o ``TRANSFERENCIA``."
    ),
    db: Session = Depends(get_db),
) -> CobrosCuentaCorrienteRead:
    hoy = utcnow().date()
    inicio = rango_del_dia(desde or hoy)[0]
    fin = rango_del_dia(hasta or desde or hoy)[1]
    return finanzas_service.cobros_del_periodo(
        db, inicio, fin, medio_pago=medio_pago
    )


# --------------------------------------------------------------------------- #
# Depuración: listado global y borrado de movimientos del libro mayor
# --------------------------------------------------------------------------- #
@router.get(
    "/transacciones",
    response_model=List[TransaccionCCListadoRead],
    summary="Listar los movimientos de todas las cuentas corrientes",
    description=(
        "Devuelve los movimientos del libro mayor de **todas** las cuentas, del "
        "más nuevo al más viejo, con el paciente al que pertenecen. Es la tabla "
        "del módulo de borrado del panel: se filtran por paciente (`paciente_id`), "
        "por rango de días (`desde`/`hasta`, fechas `AAAA-MM-DD`) y por tipo "
        "(`DEBITO`/`CREDITO`). Pagina con `skip`/`limit` (200 filas por defecto, "
        "500 como máximo). Es de sólo lectura: no abre cuentas. Un paciente "
        "inexistente en el filtro responde **404**."
    ),
)
def listar_transacciones(
    paciente_id: Optional[int] = Query(None, gt=0),
    desde: Optional[Date] = Query(None, description="Primer día (AAAA-MM-DD)."),
    hasta: Optional[Date] = Query(None, description="Último día (AAAA-MM-DD)."),
    tipo_movimiento: Optional[TipoMovimientoCC] = Query(
        None, description="``DEBITO`` (deuda) o ``CREDITO`` (pago)."
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[TransaccionCCListadoRead]:
    """Listado de movimientos para el módulo de borrado (sólo lectura)."""
    inicio = rango_del_dia(desde)[0] if desde else None
    fin = rango_del_dia(hasta)[1] if hasta else None
    return finanzas_service.listar_transacciones(
        db,
        paciente_id=paciente_id,
        desde=inicio,
        hasta=fin,
        tipo_movimiento=tipo_movimiento,
        skip=skip,
        limit=limit,
    )


@router.delete(
    "/cuenta-corriente/transacciones/{transaccion_id}",
    response_model=TransaccionEliminadaRead,
    summary="Eliminar un movimiento de cuenta corriente",
    description=(
        "Borra **físicamente** un movimiento del libro mayor (un débito o un "
        "crédito cargado por error) y devuelve el movimiento eliminado junto con "
        "el **saldo recalculado**: el saldo se reconstruye sumando el libro mayor, "
        "nunca se resta a mano. Los planes de cuotas se re-sincronizan en la misma "
        "transacción (un plan saldado vuelve a `ACTIVO` si al sacar el pago queda "
        "deuda). Si era el último movimiento y no hay planes, se borra también la "
        "cuenta (`cuenta_eliminada: true`) y el paciente vuelve a quedar «sin "
        "cuenta corriente». No se puede deshacer; un movimiento inexistente "
        "responde **404**."
    ),
)
def eliminar_transaccion(
    transaccion_id: UUID, db: Session = Depends(get_db)
) -> TransaccionEliminadaRead:
    return finanzas_service.eliminar_transaccion(db, transaccion_id)
