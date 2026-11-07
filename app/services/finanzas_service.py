"""Servicio de cuentas corrientes y financiación de pacientes (Módulo 5).

Cómo funciona el circuito
-------------------------

1. **Apertura del plan** (:func:`abrir_plan_cuotas`). Cuando el paciente acepta
   un tratamiento financiado, el servicio:

   * calcula la **entrega inicial** (porcentaje sobre el total, 50 % por
     defecto) y el **valor de cada cuota** sobre el saldo restante, siempre en
     ``Decimal`` con redondeo medio hacia arriba (``ROUND_HALF_UP``);
   * genera un ``DEBITO`` por el **monto total** del tratamiento (la deuda nace
     completa);
   * si la entrega se cobra en ese momento, genera además un ``CREDITO``
     inmediato que baja el saldo deudor;
   * guarda el ``PlanCuotas`` con su estado ``ACTIVO``.

2. **Pagos posteriores** (:func:`registrar_pago`). Cada pago es un ``CREDITO``
   nuevo: el libro mayor es *append-only*. El pago puede imputarse a un plan
   (cuota mensual) o quedar como pago a cuenta del saldo general, y se guarda
   con su **medio de pago** (efectivo o transferencia) para poder cerrar la caja
   del día por medio.

3. **Consulta** (:func:`obtener_cuenta_corriente`). Devuelve el saldo y el
   historial ordenado por fecha descendente. Es de sólo lectura: si el paciente
   todavía no tuvo movimientos, responde ``existe_cuenta = false`` con saldo
   ``0.00`` (no se crea la cuenta desde un ``GET``).

4. **Caja del día** (:func:`cobros_del_periodo`). Los créditos del período
   —entregas iniciales, cuotas y pagos a cuenta— se listan con su medio de pago
   junto a los cierres de visita de la pestaña *Caja del día*, para que el total
   del día incluya lo cobrado por cuentas corrientes y no sólo el mostrador.

Atomicidad y consistencia
-------------------------

* Todas las escrituras pasan por :func:`transaccion`, un *context manager* que
  hace ``commit`` al salir bien y ``rollback`` **completo** si algo falla: el
  movimiento y el saldo nunca quedan desincronizados.
* El saldo **no** se incrementa a ciegas: :func:`_recalcular_saldo` lo
  reconstruye sumando el libro mayor (``suma(DEBITO) - suma(CREDITO)``) dentro
  de la misma transacción, así un reintento o un ajuste manual no lo corrompe.
* En PostgreSQL la cuenta se lee con ``SELECT … FOR UPDATE``
  (:func:`_cuenta_de`) para serializar dos cobros simultáneos del mismo
  paciente; SQLite (desarrollo y pruebas) ignora el bloqueo porque ya
  serializa las escrituras.
* La cuenta es **una por paciente**: el índice único
  ``uq_cuenta_corriente_paciente`` es la última defensa ante dos altas
  simultáneas (se traduce a ``409``).

Separación de conceptos: este módulo no escribe nada de la historia clínica; la
clínica es inmutable y la contabilidad es transaccional.
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterator, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_utils import utcnow
from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    ValidacionError,
)
from app.models.cuenta_corriente import (
    SALDO_CERO,
    CuentaCorriente,
    PlanCuotas,
    TransaccionCC,
)
from app.models.enums import EstadoPlanCuotas, MedioPago, TipoMovimientoCC
from app.models.paciente import Paciente
from app.schemas.finanzas import (
    AbrirPlanCuotasRequest,
    CobroCuentaCorrienteRead,
    CobrosCuentaCorrienteRead,
    CuentaCorrienteRead,
    CuentaCorrienteResumenRead,
    InformeCuentasCorrientesRead,
    PagoRegistradoRead,
    PlanCuotasAbiertoRead,
    PlanCuotasRead,
    RegistrarPagoRequest,
    TransaccionCCListadoRead,
    TransaccionCCRead,
    TransaccionEliminadaRead,
)
from app.services import paciente_service

logger = logging.getLogger("app.services.finanzas")

#: Unidad mínima de los importes (pesos con dos decimales).
CENTAVOS = Decimal("0.01")

#: Cien por ciento, para pasar el porcentaje de entrega a fracción.
CIEN = Decimal("100")

#: Descripción que se guarda cuando la secretaría no escribe un concepto.
CONCEPTO_TRATAMIENTO = "Tratamiento odontológico"
CONCEPTO_PAGO_CUOTA = "Pago de cuota"
CONCEPTO_PAGO_A_CUENTA = "Pago a cuenta"


def _decimal(valor) -> Decimal:
    """Convierte cualquier importe a ``Decimal`` con dos decimales (medio arriba).

    Acepta ``Decimal``, ``int``, ``float`` (de la base) o ``str`` y **nunca**
    devuelve un ``float``: es el único punto por donde entra un importe al
    módulo financiero.
    """
    if valor is None:
        return SALDO_CERO
    return Decimal(str(valor)).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def _porcentaje_texto(valor: Decimal) -> str:
    """Formatea un porcentaje sin ceros de más (``50.00`` → ``50``, ``100.00`` → ``100``)."""
    texto = format(_decimal(valor).normalize(), "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto


@contextmanager
def transaccion(db: Session) -> Iterator[Session]:
    """Unidad de trabajo ACID de las operaciones financieras.

    Al salir por el camino feliz confirma **todo** (movimiento + saldo + estado
    del plan) en un único ``COMMIT``; ante cualquier excepción —del servicio, de
    la base o del esquema— revierte la transacción completa y vuelve a lanzar el
    error, de modo que no queden movimientos huérfanos ni saldos a medio
    actualizar.
    """

    try:
        yield db
        db.commit()
    except Exception as error:  # noqa: BLE001 - el rollback debe ser total
        db.rollback()
        logger.warning("Rollback de la transacción financiera: %s", error)
        raise


def _cuenta_de(
    db: Session, paciente_id: int, *, bloquear: bool = False
) -> Optional[CuentaCorriente]:
    """Cuenta corriente del paciente, o ``None`` si todavía no tiene una.

    Con ``bloquear=True`` se pide ``SELECT … FOR UPDATE``: en PostgreSQL la fila
    queda tomada hasta el ``commit``/``rollback``, así dos cobros simultáneos del
    mismo paciente se serializan y ninguno pisa el saldo del otro.
    """
    stmt = select(CuentaCorriente).where(CuentaCorriente.paciente_id == paciente_id)
    if bloquear:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def _obtener_o_crear_cuenta(db: Session, paciente: Paciente) -> CuentaCorriente:
    """Devuelve la cuenta del paciente y la abre si es su primer movimiento.

    Debe llamarse **dentro** de :func:`transaccion`: el ``flush`` escribe la fila
    (y su ``UUID``) antes de los movimientos que la referencian.
    """
    cuenta = _cuenta_de(db, paciente.id, bloquear=True)
    if cuenta is not None:
        return cuenta

    cuenta = CuentaCorriente(
        paciente_id=paciente.id,
        saldo_actual=SALDO_CERO,
        ultima_actualizacion=utcnow(),
    )
    db.add(cuenta)
    try:
        db.flush()
    except IntegrityError as exc:
        # Otra petición abrió la cuenta entre la consulta y el INSERT: el índice
        # único `uq_cuenta_corriente_paciente` lo impide (condición de carrera).
        raise ConflictoError(
            "Ya existe una cuenta corriente para este paciente: reintente la operación."
        ) from exc
    logger.info("Cuenta corriente abierta: paciente_id=%s", paciente.id)
    return cuenta


def _registrar_movimiento(
    db: Session,
    cuenta: CuentaCorriente,
    tipo: TipoMovimientoCC,
    monto: Decimal,
    descripcion: str,
    fecha,
    medio_pago: Optional[MedioPago] = None,
) -> TransaccionCC:
    """Agrega un movimiento al libro mayor (sin tocar todavía el saldo).

    ``medio_pago`` sólo viaja en los ``CREDITO`` (es un cobro): los ``DEBITO``
    lo dejan en ``None`` porque una deuda no se paga con nada todavía.
    """
    movimiento = TransaccionCC(
        cuenta_corriente_id=cuenta.id,
        tipo_movimiento=tipo,
        monto=_decimal(monto),
        # La columna es ``String(200)``: se recorta por si el texto libre es largo.
        descripcion=descripcion[:200],
        fecha=fecha,
        medio_pago=medio_pago if tipo is TipoMovimientoCC.CREDITO else None,
    )
    db.add(movimiento)
    return movimiento


def _recalcular_saldo(db: Session, cuenta: CuentaCorriente) -> Decimal:
    """Recalcula ``saldo_actual`` sumando el libro mayor y lo persiste.

    Se apoya en la definición contable del saldo (``suma(DEBITO) - suma(CREDITO)``)
    en lugar de incrementar el valor anterior: si alguna vez quedara una
    diferencia por una carga manual, el siguiente movimiento la corrige.
    """
    db.flush()  # los movimientos nuevos deben entrar en la suma
    filas = db.execute(
        select(TransaccionCC.tipo_movimiento, func.sum(TransaccionCC.monto))
        .where(TransaccionCC.cuenta_corriente_id == cuenta.id)
        .group_by(TransaccionCC.tipo_movimiento)
    ).all()
    totales = {TipoMovimientoCC(tipo): _decimal(monto) for tipo, monto in filas}

    saldo = _decimal(
        totales.get(TipoMovimientoCC.DEBITO, SALDO_CERO)
        - totales.get(TipoMovimientoCC.CREDITO, SALDO_CERO)
    )
    cuenta.saldo_actual = saldo
    cuenta.ultima_actualizacion = utcnow()
    db.flush()
    return saldo


def _sincronizar_planes(db: Session, cuenta: CuentaCorriente) -> None:
    """Ajusta el estado de los planes según el saldo **real** de la cuenta.

    * saldo cancelado (``<= 0``) → ``FINALIZADO`` (lo calcula el sistema);
    * saldo deudor sobre un plan finalizado (p. ej. porque se registró un cargo
      nuevo) → vuelve a ``ACTIVO``;
    * ``RECOBRABLE`` se respeta: es una decisión de la secretaría (el plan sigue
      en cobranza mientras haya deuda).
    """
    db.flush()
    planes = (
        db.execute(
            select(PlanCuotas).where(PlanCuotas.cuenta_corriente_id == cuenta.id)
        )
        .scalars()
        .all()
    )
    for plan in planes:
        if cuenta.saldo_actual <= 0:
            if plan.estado is not EstadoPlanCuotas.FINALIZADO:
                plan.estado = EstadoPlanCuotas.FINALIZADO
                logger.info("Plan %s finalizado: la cuenta quedó saldada", plan.id)
        elif plan.estado is EstadoPlanCuotas.FINALIZADO:
            plan.estado = EstadoPlanCuotas.ACTIVO
    db.flush()


def _obtener_plan(db: Session, plan_id: UUID) -> PlanCuotas:
    """Devuelve el plan indicado o lanza ``RecursoNoEncontradoError`` (404)."""
    plan = db.get(PlanCuotas, plan_id)
    if plan is None:
        raise RecursoNoEncontradoError(f"No existe un plan de cuotas con id={plan_id}.")
    return plan



# ---------------------------------------------------------------------------
# API pública del servicio
# ---------------------------------------------------------------------------


def obtener_cuenta_corriente(db: Session, paciente_id: int) -> CuentaCorrienteRead:
    """Estado de la cuenta de un paciente y su historial de movimientos.

    Es una operación de **sólo lectura**: no abre la cuenta si no existe (un
    ``GET`` no debe escribir en la base). En ese caso responde
    ``existe_cuenta = False`` con saldo ``0.00`` y listas vacías, así el panel
    distingue «todavía no tuvo movimientos» de «no existe el paciente» (que sí es
    un ``404``, lanzado por :func:`app.services.paciente_service.obtener_paciente`).

    El saldo se reconstruye sumando el libro mayor y se contrasta con la foto
    guardada en la cuenta: si alguna vez difieren —una carga manual, un script—
    queda registrado en el log, y la respuesta informa el valor **real**.
    """
    paciente = paciente_service.obtener_paciente(db, paciente_id)
    cuenta = _cuenta_de(db, paciente.id)
    if cuenta is None:
        logger.info("Paciente %s consultado sin cuenta corriente", paciente.id)
        return CuentaCorrienteRead(paciente_id=paciente.id, existe_cuenta=False)

    transacciones: List[TransaccionCC] = list(cuenta.transacciones)
    total_debitos = _decimal(
        sum(
            (
                _decimal(mov.monto)
                for mov in transacciones
                if mov.tipo_movimiento is TipoMovimientoCC.DEBITO
            ),
            SALDO_CERO,
        )
    )
    total_creditos = _decimal(
        sum(
            (
                _decimal(mov.monto)
                for mov in transacciones
                if mov.tipo_movimiento is TipoMovimientoCC.CREDITO
            ),
            SALDO_CERO,
        )
    )
    saldo = _decimal(total_debitos - total_creditos)
    if saldo != _decimal(cuenta.saldo_actual):
        logger.warning(
            "Saldo desincronizado en la cuenta %s (paciente_id=%s): "
            "guardado=%s, libro mayor=%s",
            cuenta.id,
            cuenta.paciente_id,
            cuenta.saldo_actual,
            saldo,
        )

    return CuentaCorrienteRead(
        paciente_id=cuenta.paciente_id,
        existe_cuenta=True,
        id=cuenta.id,
        saldo_actual=saldo,
        ultima_actualizacion=cuenta.ultima_actualizacion,
        total_debitos=total_debitos,
        total_creditos=total_creditos,
        transacciones=[
            TransaccionCCRead.model_validate(mov) for mov in transacciones
        ],
        planes=[PlanCuotasRead.model_validate(plan) for plan in cuenta.planes],
    )


def informe_cuentas_corrientes(db: Session) -> InformeCuentasCorrientesRead:
    """Informe consolidado de **todas** las cuentas corrientes de los pacientes.

    Es una consulta de **sólo lectura** pensada para el botón «Informe» del panel
    y para la cobranza: devuelve una fila por paciente con cuenta —los pacientes
    sin movimientos no aparecen porque no tienen plan, pago ni deuda—, ordenadas
    por nombre, con lo que se le debitó, lo que pagó, lo que debe y sus planes de
    cuotas.

    Los importes se agregan en la base (``SUM`` por cuenta y tipo de movimiento)
    y se convierten con :func:`_decimal`: el informe nunca suma ``float``. El
    saldo se reconstruye desde el libro mayor —la misma definición contable de
    :func:`obtener_cuenta_corriente`— y, si difiere de la foto guardada en la
    cuenta, queda registrado en el log (se informa el valor real).

    ``deuda_total`` suma **sólo** los saldos deudores: el saldo a favor de un
    paciente no compensa la deuda de otro, se informa aparte en
    ``saldo_a_favor_total``.
    """
    filas = db.execute(
        select(CuentaCorriente, Paciente)
        .join(Paciente, Paciente.id == CuentaCorriente.paciente_id)
        .order_by(Paciente.nombre)
    ).all()

    # Agregados por cuenta: una consulta para los movimientos y otra para los
    # planes (nunca una consulta por paciente).
    totales: dict = {}
    planes_por_cuenta: dict = {}
    if filas:
        ids = [cuenta.id for cuenta, _ in filas]
        movimientos = db.execute(
            select(
                TransaccionCC.cuenta_corriente_id,
                TransaccionCC.tipo_movimiento,
                func.sum(TransaccionCC.monto),
            )
            .where(TransaccionCC.cuenta_corriente_id.in_(ids))
            .group_by(
                TransaccionCC.cuenta_corriente_id, TransaccionCC.tipo_movimiento
            )
        ).all()
        for cuenta_id, tipo, monto in movimientos:
            totales.setdefault(cuenta_id, {})[TipoMovimientoCC(tipo)] = _decimal(monto)

        planes = db.execute(
            select(PlanCuotas)
            .where(PlanCuotas.cuenta_corriente_id.in_(ids))
            .order_by(PlanCuotas.monto_total_tratamiento.desc())
        ).scalars()
        for plan in planes:
            planes_por_cuenta.setdefault(plan.cuenta_corriente_id, []).append(plan)

    cuentas: List[CuentaCorrienteResumenRead] = []
    total_debitado = SALDO_CERO
    total_cobrado = SALDO_CERO
    deuda_total = SALDO_CERO
    saldo_a_favor_total = SALDO_CERO
    cantidad_con_deuda = 0
    cantidad_planes = 0
    for cuenta, paciente in filas:
        montos = totales.get(cuenta.id, {})
        debitos = montos.get(TipoMovimientoCC.DEBITO, SALDO_CERO)
        creditos = montos.get(TipoMovimientoCC.CREDITO, SALDO_CERO)
        saldo = _decimal(debitos - creditos)
        if saldo != _decimal(cuenta.saldo_actual):
            logger.warning(
                "Saldo desincronizado en la cuenta %s (paciente_id=%s): "
                "guardado=%s, libro mayor=%s",
                cuenta.id,
                cuenta.paciente_id,
                cuenta.saldo_actual,
                saldo,
            )

        planes_cuenta = planes_por_cuenta.get(cuenta.id, [])
        cuentas.append(
            CuentaCorrienteResumenRead(
                paciente_id=paciente.id,
                paciente_nombre=paciente.nombre,
                paciente_dni=paciente.dni,
                paciente_telefono=paciente.telefono,
                total_debitos=debitos,
                total_creditos=creditos,
                saldo_actual=saldo,
                planes=[
                    PlanCuotasRead.model_validate(plan) for plan in planes_cuenta
                ],
            )
        )

        total_debitado += debitos
        total_cobrado += creditos
        cantidad_planes += len(planes_cuenta)
        if saldo > 0:
            cantidad_con_deuda += 1
            deuda_total += saldo
        elif saldo < 0:
            saldo_a_favor_total += -saldo

    logger.info(
        "Informe de cuentas corrientes: %s cuenta(s), %s con deuda, deuda total %s",
        len(cuentas),
        cantidad_con_deuda,
        _decimal(deuda_total),
    )
    return InformeCuentasCorrientesRead(
        generado=utcnow(),
        cantidad_pacientes=len(cuentas),
        cantidad_con_deuda=cantidad_con_deuda,
        cantidad_planes=cantidad_planes,
        total_debitado=_decimal(total_debitado),
        total_cobrado=_decimal(total_cobrado),
        deuda_total=_decimal(deuda_total),
        saldo_a_favor_total=_decimal(saldo_a_favor_total),
        cuentas=cuentas,
    )


def abrir_plan_cuotas(
    db: Session, data: AbrirPlanCuotasRequest
) -> PlanCuotasAbiertoRead:
    """Abre un tratamiento financiado y registra su liquidación inicial.

    Cálculo (todo en ``Decimal``, redondeo ``ROUND_HALF_UP`` a dos decimales):

    * ``entrega_inicial = total * porcentaje / 100``;
    * ``monto_financiado = total - entrega_inicial``;
    * ``monto_por_cuota = monto_financiado / cantidad_cuotas`` (redondeado);
    * ``ajuste_ultima_cuota = monto_financiado - monto_por_cuota * cuotas``, la
      diferencia de centavos que cierra el plan (p. ej. ``-0.01``).

    Movimientos: un ``DEBITO`` por el **total** (la deuda nace completa) y, si
    ``pago_entrega_inicial`` es verdadero, un ``CREDITO`` por la entrega en la
    misma transacción. El saldo resultante se recalcula y se devuelve.

    Raises:
        RecursoNoEncontradoError: el paciente no existe (404).
        ValidacionError: el porcentaje deja el plan sin monto financiable (422).
    """
    paciente = paciente_service.obtener_paciente(db, data.paciente_id)

    total = _decimal(data.monto_total)
    entrega = _decimal(total * _decimal(data.porcentaje_entrega_inicial) / CIEN)
    financiado = _decimal(total - entrega)
    if financiado <= 0:
        # Quien paga todo en el mostrador no necesita un plan: el valor de cada
        # cuota sería 0 y el plan no tendría sentido.
        raise ValidacionError(
            "La entrega inicial cubre el total del tratamiento: "
            "no hay importe para financiar en cuotas."
        )

    cuota = _decimal(financiado / Decimal(data.cantidad_cuotas))
    fecha = data.fecha or utcnow()
    detalle = (data.descripcion or CONCEPTO_TRATAMIENTO).strip()
    entrega_pagada = bool(data.pago_entrega_inicial and entrega > 0)

    with transaccion(db):
        cuenta = _obtener_o_crear_cuenta(db, paciente)
        _registrar_movimiento(
            db,
            cuenta,
            TipoMovimientoCC.DEBITO,
            total,
            f"Tratamiento financiado: {detalle}",
            fecha,
        )
        if entrega_pagada:
            porcentaje = _porcentaje_texto(_decimal(data.porcentaje_entrega_inicial))
            _registrar_movimiento(
                db,
                cuenta,
                TipoMovimientoCC.CREDITO,
                entrega,
                f"Entrega inicial ({porcentaje}%): {detalle}",
                fecha,
                medio_pago=data.medio_pago,
            )

        plan = PlanCuotas(
            cuenta_corriente_id=cuenta.id,
            monto_total_tratamiento=total,
            monto_entrega_inicial=entrega,
            cantidad_cuotas=data.cantidad_cuotas,
            monto_por_cuota=cuota,
            estado=EstadoPlanCuotas.ACTIVO,
        )
        db.add(plan)
        db.flush()

        saldo = _recalcular_saldo(db, cuenta)
        _sincronizar_planes(db, cuenta)

    logger.info(
        "Plan de cuotas abierto: paciente_id=%s plan=%s total=%s cuotas=%sx%s",
        paciente.id,
        plan.id,
        total,
        data.cantidad_cuotas,
        cuota,
    )
    return PlanCuotasAbiertoRead(
        **PlanCuotasRead.model_validate(plan).model_dump(),
        monto_financiado=_decimal(plan.monto_financiado),
        ajuste_ultima_cuota=_decimal(plan.ajuste_ultima_cuota),
        saldo_actual=saldo,
        entrega_inicial_pagada=entrega_pagada,
    )


def registrar_pago(db: Session, data: RegistrarPagoRequest) -> PagoRegistradoRead:
    """Registra un pago del paciente (``CREDITO``) y devuelve el saldo nuevo.

    El libro mayor es *append-only*: un pago nunca modifica movimientos
    anteriores. Si se indica ``plan_cuotas_id`` el pago se imputa a ese plan
    (cuota mensual) y se valida que pertenezca a la cuenta del paciente; si no,
    queda como pago a cuenta del saldo general.

    Pagar de más es válido: el saldo queda negativo (a favor del paciente) y se
    informa en ``saldo_a_favor``. Si la cuenta se salda, los planes pasan a
    ``FINALIZADO`` automáticamente dentro de la misma transacción.

    Raises:
        RecursoNoEncontradoError: el paciente o el plan no existen (404).
        ValidacionError: el plan indicado es de otro paciente (422).
    """
    paciente = paciente_service.obtener_paciente(db, data.paciente_id)
    monto = _decimal(data.monto)
    fecha = data.fecha or utcnow()
    plan: Optional[PlanCuotas] = None

    with transaccion(db):
        cuenta = _obtener_o_crear_cuenta(db, paciente)
        if data.plan_cuotas_id is not None:
            plan = _obtener_plan(db, data.plan_cuotas_id)
            if plan.cuenta_corriente_id != cuenta.id:
                raise ValidacionError(
                    "El plan de cuotas indicado no pertenece a este paciente."
                )
            descripcion = data.descripcion or CONCEPTO_PAGO_CUOTA
        else:
            descripcion = data.descripcion or CONCEPTO_PAGO_A_CUENTA

        movimiento = _registrar_movimiento(
            db,
            cuenta,
            TipoMovimientoCC.CREDITO,
            monto,
            descripcion,
            fecha,
            medio_pago=data.medio_pago,
        )
        saldo = _recalcular_saldo(db, cuenta)
        _sincronizar_planes(db, cuenta)

    saldo_a_favor = _decimal(-saldo) if saldo < 0 else SALDO_CERO
    logger.info(
        "Pago registrado: paciente_id=%s movimiento=%s monto=%s saldo=%s",
        paciente.id,
        movimiento.id,
        monto,
        saldo,
    )
    return PagoRegistradoRead(
        transaccion=TransaccionCCRead.model_validate(movimiento),
        cuenta_corriente_id=cuenta.id,
        saldo_actual=saldo,
        saldo_a_favor=saldo_a_favor,
        plan_cuotas=(
            PlanCuotasRead.model_validate(plan) if plan is not None else None
        ),
    )


def cambiar_estado_plan(
    db: Session, plan_id: UUID, estado: EstadoPlanCuotas
) -> PlanCuotasRead:
    """Pasa un plan a ``RECOBRABLE`` (cobranza) o lo devuelve a ``ACTIVO``.

    ``FINALIZADO`` no se acepta aquí: lo determina :func:`_sincronizar_planes`
    cuando la cuenta queda saldada, para que no se cierre un plan con deuda viva.

    Raises:
        RecursoNoEncontradoError: el plan no existe (404).
        ConflictoError: la cuenta ya está saldada, el plan es ``FINALIZADO`` (409).
    """
    with transaccion(db):
        plan = _obtener_plan(db, plan_id)
        cuenta = db.get(CuentaCorriente, plan.cuenta_corriente_id)
        if cuenta is None:  # pragma: no cover - la clave foránea lo impide
            raise RecursoNoEncontradoError(
                "El plan no tiene una cuenta corriente asociada."
            )
        if _decimal(cuenta.saldo_actual) <= 0:
            raise ConflictoError(
                "La cuenta está saldada: el plan se cierra automáticamente "
                "como FINALIZADO."
            )
        plan.estado = estado
        db.flush()

    logger.info("Plan %s marcado como %s", plan.id, plan.estado.value)
    return PlanCuotasRead.model_validate(plan)


# ---------------------------------------------------------------------------
# Depuración: listado global y borrado de movimientos del libro mayor
# ---------------------------------------------------------------------------


def listar_transacciones(
    db: Session,
    paciente_id: Optional[int] = None,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    tipo_movimiento: Optional[TipoMovimientoCC] = None,
    skip: int = 0,
    limit: Optional[int] = None,
) -> List[TransaccionCCListadoRead]:
    """Movimientos del libro mayor de **todas** las cuentas, con su paciente.

    Es la consulta del módulo de borrado: devuelve, del más nuevo al más viejo,
    cada débito y crédito del consultorio con el nombre y el DNI del paciente,
    para que la secretaría identifique el movimiento antes de eliminarlo.

    Los filtros son acumulativos (``paciente_id``, rango de fechas y tipo de
    movimiento) y ``limit`` lo pone el panel para no traer el libro entero. Es de
    **sólo lectura**: no abre cuentas ni escribe nada.

    Raises:
        RecursoNoEncontradoError: el paciente del filtro no existe (404).
    """
    if paciente_id is not None:
        # Un filtro por un paciente inexistente es un error de la solicitud y no
        # un listado vacío: el panel avisa en lugar de mostrar «sin movimientos».
        paciente_service.obtener_paciente(db, paciente_id)

    stmt = (
        select(TransaccionCC, Paciente)
        .join(
            CuentaCorriente,
            CuentaCorriente.id == TransaccionCC.cuenta_corriente_id,
        )
        .join(Paciente, Paciente.id == CuentaCorriente.paciente_id)
        .order_by(TransaccionCC.fecha.desc(), TransaccionCC.id.desc())
    )
    if paciente_id is not None:
        stmt = stmt.where(CuentaCorriente.paciente_id == paciente_id)
    if desde is not None:
        stmt = stmt.where(TransaccionCC.fecha >= desde)
    if hasta is not None:
        stmt = stmt.where(TransaccionCC.fecha <= hasta)
    if tipo_movimiento is not None:
        stmt = stmt.where(TransaccionCC.tipo_movimiento == tipo_movimiento)

    stmt = stmt.offset(skip)
    if limit is not None:
        stmt = stmt.limit(limit)

    return [
        TransaccionCCListadoRead(
            id=movimiento.id,
            cuenta_corriente_id=movimiento.cuenta_corriente_id,
            paciente_id=paciente.id,
            paciente_nombre=paciente.nombre,
            paciente_dni=paciente.dni,
            tipo_movimiento=movimiento.tipo_movimiento,
            monto=_decimal(movimiento.monto),
            descripcion=movimiento.descripcion,
            fecha=movimiento.fecha,
            medio_pago=movimiento.medio_pago,
        )
        for movimiento, paciente in db.execute(stmt).all()
    ]


def eliminar_transaccion(
    db: Session, transaccion_id: UUID
) -> TransaccionEliminadaRead:
    """Borra un movimiento del libro mayor y deja la cuenta consistente.

    Es la operación de **corrección** del Módulo 5 (módulo de borrado del panel):
    el libro mayor es *append-only* en el circuito normal —un cobro se anula con
    otro movimiento—, pero un movimiento cargado por error (un pago duplicado, un
    plan con el importe equivocado) tiene que poder eliminarse.

    Todo ocurre dentro de :func:`transaccion`, así que el borrado y el saldo nuevo
    se confirman juntos o no se confirma nada:

    1. se toma la cuenta con ``SELECT … FOR UPDATE`` (PostgreSQL) para que dos
       correcciones simultáneas del mismo paciente no se pisen;
    2. se borra el movimiento;
    3. el saldo se **reconstruye** sumando el libro mayor
       (:func:`_recalcular_saldo`): nunca se resta a mano el importe borrado;
    4. los planes se re-sincronizan (:func:`_sincronizar_planes`): si al sacar un
       crédito la cuenta vuelve a tener deuda, el plan finalizado pasa a
       ``ACTIVO``; si el crédito borrado era el que saldaba el plan, éste vuelve
       a quedar en cobranza por su saldo real;
    5. si no queda ningún movimiento **ni** ningún plan, se borra también la
       cuenta: el paciente vuelve a ``existe_cuenta = false`` como si nunca
       hubiera tenido movimientos.

    No se puede deshacer: la respuesta devuelve el movimiento borrado y el saldo
    resultante para que el panel lo muestre.

    Raises:
        RecursoNoEncontradoError: el movimiento (o su cuenta) no existe (404).
    """
    movimiento = db.get(TransaccionCC, transaccion_id)
    if movimiento is None:
        raise RecursoNoEncontradoError(
            f"No existe un movimiento de cuenta corriente con id={transaccion_id}."
        )
    cuenta = db.get(CuentaCorriente, movimiento.cuenta_corriente_id)
    if cuenta is None:  # pragma: no cover - la clave foránea lo impide
        raise RecursoNoEncontradoError(
            "El movimiento no tiene una cuenta corriente asociada."
        )
    paciente = paciente_service.obtener_paciente(db, cuenta.paciente_id)

    # Foto del movimiento antes del ``DELETE``: después ya no estará en la base.
    datos = {
        "id": movimiento.id,
        "paciente_id": paciente.id,
        "paciente_nombre": paciente.nombre,
        "cuenta_corriente_id": cuenta.id,
        "tipo_movimiento": movimiento.tipo_movimiento,
        "monto": _decimal(movimiento.monto),
        "descripcion": movimiento.descripcion,
        "fecha": movimiento.fecha,
        "medio_pago": movimiento.medio_pago,
    }

    with transaccion(db):
        cuenta = _cuenta_de(db, paciente.id, bloquear=True) or cuenta
        db.delete(movimiento)
        saldo = _recalcular_saldo(db, cuenta)
        _sincronizar_planes(db, cuenta)

        restantes = int(
            db.execute(
                select(func.count())
                .select_from(TransaccionCC)
                .where(TransaccionCC.cuenta_corriente_id == cuenta.id)
            ).scalar()
            or 0
        )
        planes = int(
            db.execute(
                select(func.count())
                .select_from(PlanCuotas)
                .where(PlanCuotas.cuenta_corriente_id == cuenta.id)
            ).scalar()
            or 0
        )
        cuenta_eliminada = restantes == 0 and planes == 0
        if cuenta_eliminada:
            # Sin movimientos ni planes la cuenta no significa nada: se borra
            # para que el paciente vuelva a «sin cuenta corriente». Se usa un
            # ``DELETE`` directo: la cascada del ORM volvería a emitir el borrado
            # de los movimientos que ya se borraron más arriba.
            db.execute(
                delete(CuentaCorriente)
                .where(CuentaCorriente.id == cuenta.id)
                .execution_options(synchronize_session=False)
            )
            db.expunge(cuenta)
            saldo = SALDO_CERO

    # La sesión no puede seguir sirviendo la colección con el movimiento ya
    # borrado: al expirarla, el próximo acceso la relee de la base (importante
    # para los scripts y las pruebas, que comparten sesión entre peticiones).
    db.expire_all()

    saldo_a_favor = _decimal(-saldo) if saldo < 0 else SALDO_CERO
    logger.warning(
        "Movimiento de cuenta corriente eliminado: id=%s paciente_id=%s "
        "tipo=%s monto=%s saldo=%s cuenta_eliminada=%s",
        datos["id"],
        datos["paciente_id"],
        datos["tipo_movimiento"],
        datos["monto"],
        saldo,
        cuenta_eliminada,
    )
    return TransaccionEliminadaRead(
        **datos,
        saldo_actual=saldo,
        saldo_a_favor=saldo_a_favor,
        cuenta_eliminada=cuenta_eliminada,
    )


# ---------------------------------------------------------------------------
# Caja del día: lo que ingresó por cuentas corrientes
# ---------------------------------------------------------------------------


def cobros_del_periodo(
    db: Session,
    desde: datetime,
    hasta: datetime,
    medio_pago: Optional[MedioPago] = None,
) -> CobrosCuentaCorrienteRead:
    """Cobros de cuentas corrientes del rango, con el total por medio de pago.

    La caja del día se arma con dos fuentes: los cierres de visita
    (``movimientos_caja``) y **estos** créditos —entregas iniciales de planes,
    cuotas y pagos a cuenta—. Sin esta consulta la caja sólo mostraría el
    mostrador y el día quedaría subvaluado: un paciente que paga su cuota por
    transferencia no aparece en ningún cierre de visita.

    Sólo se listan los ``CREDITO`` (un ``DEBITO`` es deuda, no un ingreso). Los
    cobros cargados antes de existir ``medio_pago`` se informan como
    ``EFECTIVO`` —era el único medio que el sistema suponía—, así el total del
    día sigue cerrando con la caja histórica.

    Args:
        db: sesión de base de datos.
        desde: primer instante del rango (UTC naive, inclusivo).
        hasta: último instante del rango (UTC naive, inclusivo).
        medio_pago: si se informa, filtra el listado y los totales por medio.

    Returns:
        ``CobrosCuentaCorrienteRead`` con las filas (de la más nueva a la más
        vieja) y los totales ``total``, ``total_efectivo`` y
        ``total_transferencia``.
    """
    stmt = (
        select(TransaccionCC, Paciente)
        .join(
            CuentaCorriente,
            CuentaCorriente.id == TransaccionCC.cuenta_corriente_id,
        )
        .join(Paciente, Paciente.id == CuentaCorriente.paciente_id)
        .where(
            TransaccionCC.tipo_movimiento == TipoMovimientoCC.CREDITO,
            TransaccionCC.fecha >= desde,
            TransaccionCC.fecha <= hasta,
        )
        .order_by(TransaccionCC.fecha.desc(), TransaccionCC.id.desc())
    )
    if medio_pago is not None:
        if medio_pago is MedioPago.EFECTIVO:
            # Los cobros viejos (``NULL``) se cuentan como efectivo.
            stmt = stmt.where(
                or_(
                    TransaccionCC.medio_pago == MedioPago.EFECTIVO,
                    TransaccionCC.medio_pago.is_(None),
                )
            )
        else:
            stmt = stmt.where(TransaccionCC.medio_pago == medio_pago)

    cobros = [
        CobroCuentaCorrienteRead(
            id=movimiento.id,
            paciente_id=paciente.id,
            paciente_nombre=paciente.nombre,
            paciente_dni=paciente.dni,
            monto=_decimal(movimiento.monto),
            descripcion=movimiento.descripcion,
            fecha=movimiento.fecha,
            medio_pago=movimiento.medio_pago or MedioPago.EFECTIVO,
        )
        for movimiento, paciente in db.execute(stmt).all()
    ]

    total_efectivo = _decimal(
        sum(
            (
                cobro.monto
                for cobro in cobros
                if cobro.medio_pago is MedioPago.EFECTIVO
            ),
            SALDO_CERO,
        )
    )
    total_transferencia = _decimal(
        sum(
            (
                cobro.monto
                for cobro in cobros
                if cobro.medio_pago is MedioPago.TRANSFERENCIA
            ),
            SALDO_CERO,
        )
    )

    logger.info(
        "Cobros de cuentas corrientes %s..%s: %s movimientos, total=%s "
        "(efectivo=%s, transferencia=%s)",
        desde,
        hasta,
        len(cobros),
        total_efectivo + total_transferencia,
        total_efectivo,
        total_transferencia,
    )
    return CobrosCuentaCorrienteRead(
        desde=desde,
        hasta=hasta,
        cantidad=len(cobros),
        total=_decimal(total_efectivo + total_transferencia),
        total_efectivo=total_efectivo,
        total_transferencia=total_transferencia,
        cobros=cobros,
    )

