"""Endpoints del Módulo 5: cuentas corrientes y financiación de tratamientos.

Rutas (prefijo ``/api/v1``):

* ``GET  /finanzas/cuenta-corriente/paciente/{paciente_id}`` — saldo e historial
  de movimientos del paciente (del más nuevo al más viejo). Si el paciente
  todavía no tuvo movimientos responde ``existe_cuenta = false`` con saldo
  ``0.00``; nunca abre la cuenta desde un ``GET``.
* ``POST /finanzas/plan-cuotas`` — abre un tratamiento financiado: calcula la
  entrega inicial (50 % por defecto) y las cuotas mensuales, y registra la deuda
  (más el pago de la entrega, si se cobró en el momento).
* ``POST /finanzas/pagar`` — registra un pago (cuota o pago a cuenta) y devuelve
  el saldo resultante.
* ``PATCH /finanzas/plan-cuotas/{plan_id}/estado`` — pasa un plan a
  ``RECOBRABLE`` (cobranza) o lo devuelve a ``ACTIVO``. ``FINALIZADO`` lo pone el
  sistema cuando la cuenta queda saldada.

Todos los importes viajan como número JSON y se calculan en ``Decimal`` en el
servidor; el detalle de las reglas contables está en
:mod:`app.services.finanzas_service`.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.finanzas import (
    AbrirPlanCuotasRequest,
    CuentaCorrienteRead,
    PagoRegistradoRead,
    PlanCuotasAbiertoRead,
    PlanCuotasEstadoUpdate,
    PlanCuotasRead,
    RegistrarPagoRequest,
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
