"""Servicio de facturación mixta, caja y liquidación de obras sociales (Módulo 3).

Cómo funciona el circuito
-------------------------

1. **Cierre de la visita** (:func:`facturar_turno`). Al terminar la atención, la
   secretaría elige las prácticas realizadas y el servicio resuelve cada una
   contra el **arancel** de la obra social del paciente:

   * la parte que cubre la cobertura se acumula en ``monto_pendiente_os`` (lo que
     se le presenta a la OS);
   * la parte del paciente (coseguro) se acumula en ``monto_efectivo`` (lo que se
     cobra en el mostrador).

   Un paciente **sin** cobertura se factura a ``valor_base`` (particular) y el
   movimiento queda saldado (``COBRADO``) porque no hay nada que presentar.
   Si el paciente **tiene** cobertura pero falta el arancel de una práctica, la
   facturación se rechaza con **422** (no se inventan importes): se carga el
   arancel o se factura la visita con ``permitir_particular``.

2. **Liquidación de obras sociales** (:func:`generar_presentacion`). Cierra el
   período de una cobertura: agrupa los movimientos ``PENDIENTE`` del mes y los
   pasa a ``EN_PROCESO_LIQUIDACION``, devolviendo la presentación con el detalle
   y los totales para exportar. Al acreditarse el depósito bancario, la
   secretaría marca cada orden como ``COBRADO`` (:func:`cobrar_movimiento`) o
   ``DEBITADO_OS`` (:func:`debitar_movimiento`) —individualmente o de a lotes
   desde el panel—, y puede informar el importe realmente depositado para dejar
   registrada la diferencia.

3. **Consulta** (:func:`listar_movimientos`, :func:`resumen_caja`): la caja
   diaria del consultorio y el estado de cada orden presentada.

Convenciones: los importes se redondean a dos decimales con
:mod:`app.core.money` y las fechas son UTC naive (convención del proyecto), por
eso el período de un mes se arma con ``periodo_mensual``.
"""

import calendar
import logging
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_utils import utcnow
from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    TurnoYaFacturadoError,
    ValidacionError,
)
from app.core.money import multiplicar_monto, redondear_monto, sumar_montos
from app.models.enums import EstadoPagoMovimiento, EstadoTurno
from app.models.movimiento_caja import DetalleMovimientoCaja, MovimientoCaja
from app.models.paciente import Paciente
from app.models.turno import Turno
from app.schemas.facturacion import (
    CobrarMovimientoRequest,
    DebitarMovimientoRequest,
    FacturarTurnoRequest,
    MovimientoCajaRead,
    PresupuestoCobroRead,
    PrestacionFacturadaCalculadaRead,
    PrestacionFacturadaCreate,
    PrestacionesDisponiblesRead,
    PrestacionDisponibleRead,
    PresentacionObraSocialRead,
    ResumenCajaRead,
)
from app.services import arancel_service, obra_social_service, prestacion_service

logger = logging.getLogger("app.services.facturacion")

# Origen del importe de una práctica (lo expone la ayuda de carga del formulario).
ORIGEN_ARANCEL = "ARANCEL"
ORIGEN_VALOR_BASE = "VALOR_BASE"
ORIGEN_SIN_ARANCEL = "SIN_ARANCEL"


def periodo_mensual(anio: int, mes: int) -> Tuple[datetime, datetime]:
    """Primer y último instante (UTC naive) del mes calendario indicado."""
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    desde = datetime(anio, mes, 1, 0, 0, 0)
    hasta = datetime(anio, mes, ultimo_dia, 23, 59, 59, 999999)
    return desde, hasta


def numero_presentacion(obra_social_id: int, anio: int, mes: int) -> str:
    """Número de presentación del período (``PRES-OS3-202611``).

    Es **determinístico**: la misma cobertura y el mismo mes dan siempre el mismo
    número, así que la secretaría puede identificarlo en el circuito de la OS sin
    necesidad de una tabla de presentaciones.
    """
    return f"PRES-OS{int(obra_social_id)}-{int(anio):04d}{int(mes):02d}"


def _obtener_turno(db: Session, turno_id: int) -> Turno:
    """Devuelve el turno o lanza ``RecursoNoEncontradoError`` (HTTP 404)."""
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise RecursoNoEncontradoError(f"No existe un turno con id={turno_id}.")
    return turno


def obtener_movimiento(db: Session, movimiento_id: int) -> MovimientoCaja:
    """Devuelve el movimiento de caja o lanza ``RecursoNoEncontradoError``."""
    movimiento = db.get(MovimientoCaja, movimiento_id)
    if movimiento is None:
        raise RecursoNoEncontradoError(
            f"No existe un movimiento de caja con id={movimiento_id}."
        )
    return movimiento


def movimiento_de_turno(db: Session, turno_id: int) -> Optional[MovimientoCaja]:
    """Devuelve el cierre de caja de un turno (``None`` si todavía no se facturó)."""
    stmt = select(MovimientoCaja).where(MovimientoCaja.turno_id == turno_id)
    return db.execute(stmt).scalars().first()


def _movimientos_del_periodo(
    db: Session,
    obra_social_id: int,
    desde: datetime,
    hasta: datetime,
    estados: Optional[Sequence[EstadoPagoMovimiento]] = None,
) -> Sequence[MovimientoCaja]:
    """Movimientos de una cobertura dentro del período (opcionalmente por estado).

    El filtro por cobertura es la obra social **del paciente** del turno: si el
    paciente cambia de cobertura más adelante, la factura sigue agrupándose en la
    cobertura con la que se atendió mientras la OS del paciente no cambie. Para
    que la presentación sea estable, el movimiento conserva su detalle y sus
    importes congelados.
    """
    stmt = (
        select(MovimientoCaja)
        .join(MovimientoCaja.turno)
        .join(Turno.paciente)
        .where(
            Paciente.obra_social_id == obra_social_id,
            MovimientoCaja.fecha >= desde,
            MovimientoCaja.fecha <= hasta,
        )
        .order_by(MovimientoCaja.fecha, MovimientoCaja.id)
    )
    if estados:
        stmt = stmt.where(MovimientoCaja.estado_pago.in_(list(estados)))
    return db.execute(stmt).scalars().all()


def listar_movimientos(
    db: Session,
    skip: int = 0,
    limit: Optional[int] = None,
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    obra_social_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    estado_pago: Optional[EstadoPagoMovimiento] = None,
    turno_id: Optional[int] = None,
) -> Sequence[MovimientoCaja]:
    """Lista la caja con filtros acumulativos (fecha, cobertura, doctor, estado).

    Es la consulta que alimenta la **caja diaria** del panel (``desde``/``hasta``
    del día) y el seguimiento de una orden presentada (``estado_pago``).
    """
    stmt = select(MovimientoCaja).order_by(
        MovimientoCaja.fecha.desc(), MovimientoCaja.id.desc()
    )
    if obra_social_id is not None:
        stmt = (
            stmt.join(MovimientoCaja.turno)
            .join(Turno.paciente)
            .where(Paciente.obra_social_id == obra_social_id)
        )
    if desde is not None:
        stmt = stmt.where(MovimientoCaja.fecha >= desde)
    if hasta is not None:
        stmt = stmt.where(MovimientoCaja.fecha <= hasta)
    if doctor_id is not None:
        stmt = stmt.where(MovimientoCaja.doctor_id == doctor_id)
    if estado_pago is not None:
        stmt = stmt.where(MovimientoCaja.estado_pago == estado_pago)
    if turno_id is not None:
        stmt = stmt.where(MovimientoCaja.turno_id == turno_id)

    stmt = stmt.offset(skip)
    if limit is not None:
        stmt = stmt.limit(limit)
    return db.execute(stmt).scalars().all()


def resumen_caja(
    db: Session,
    desde: datetime,
    hasta: datetime,
    obra_social_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> ResumenCajaRead:
    """Totales de la caja de un rango (lo que entra en efectivo y lo que debe la OS).

    ``monto_pendiente_os`` suma sólo los movimientos **abiertos** (``PENDIENTE`` y
    ``EN_PROCESO_LIQUIDACION``): es la plata que todavía no se acreditó.
    """
    movimientos = listar_movimientos(
        db,
        desde=desde,
        hasta=hasta,
        obra_social_id=obra_social_id,
        doctor_id=doctor_id,
    )
    abiertos = [
        m
        for m in movimientos
        if m.estado_pago
        in (
            EstadoPagoMovimiento.PENDIENTE,
            EstadoPagoMovimiento.EN_PROCESO_LIQUIDACION,
        )
    ]
    cobrados = [
        m for m in movimientos if m.estado_pago == EstadoPagoMovimiento.COBRADO
    ]
    debitados = [
        m for m in movimientos if m.estado_pago == EstadoPagoMovimiento.DEBITADO_OS
    ]

    return ResumenCajaRead(
        desde=desde,
        hasta=hasta,
        cantidad_movimientos=len(movimientos),
        monto_efectivo=sumar_montos(m.monto_efectivo for m in movimientos),
        monto_pendiente_os=sumar_montos(
            m.monto_pendiente_os - (m.monto_cobrado_os or 0) for m in abiertos
        ),
        monto_cobrado_os=sumar_montos(
            (m.monto_cobrado_os if m.monto_cobrado_os is not None else m.monto_pendiente_os)
            for m in cobrados
        ),
        monto_debitado_os=sumar_montos(
            m.monto_pendiente_os - (m.monto_cobrado_os or 0) for m in debitados
        ),
    )


def _resolver_prestaciones(
    db: Session,
    paciente: Paciente,
    pedidas: Sequence[PrestacionFacturadaCreate],
    permitir_particular: bool,
) -> Tuple[List[Dict], List[str]]:
    """Traduce las prácticas pedidas en importes (cubre OS / coseguro).

    Devuelve ``(planes, advertencias)``. Cada plan es un diccionario con la
    práctica, la cantidad y los importes **totales** (unidad × cantidad).

    Reglas:

    * Si la cobertura del paciente tiene arancel para la práctica, se usan sus
      montos (``ARANCEL``).
    * Si el paciente no tiene cobertura, se cobra el ``valor_base`` completo
      (``VALOR_BASE``): es un particular y no hay nada que presentar.
    * Si el paciente tiene cobertura pero falta el arancel, se rechaza con
      **422** (``ValidacionError``) salvo que ``permitir_particular`` sea
      ``True``, en cuyo caso se cobra el ``valor_base`` (``SIN_ARANCEL``) y se
      agrega una advertencia.
    """
    aranceles = arancel_service.mapa_aranceles(db, paciente.obra_social_id)
    planes: List[Dict] = []
    advertencias: List[str] = []
    faltantes: List[str] = []

    for pedida in pedidas:
        prestacion = prestacion_service.obtener_prestacion_activa(
            db, pedida.prestacion_id
        )
        arancel = aranceles.get(prestacion.id)

        if arancel is not None:
            unitario_cubre = arancel.monto_cubre_os
            unitario_coseguro = arancel.monto_coseguro_paciente
            origen = ORIGEN_ARANCEL
        elif paciente.obra_social_id is None:
            unitario_cubre = 0.0
            unitario_coseguro = prestacion.valor_base
            origen = ORIGEN_VALOR_BASE
        elif not permitir_particular:
            faltantes.append(
                f"{prestacion.codigo_nomenclador} ({prestacion.descripcion})"
            )
            continue
        else:
            unitario_cubre = 0.0
            unitario_coseguro = prestacion.valor_base
            origen = ORIGEN_SIN_ARANCEL
            advertencias.append(
                f"{prestacion.codigo_nomenclador} no tiene arancel cargado para la "
                "cobertura: se cobra a valor de referencia como particular."
            )

        planes.append(
            {
                "prestacion": prestacion,
                "cantidad": pedida.cantidad,
                "monto_cubre_os": multiplicar_monto(unitario_cubre, pedida.cantidad),
                "monto_coseguro_paciente": multiplicar_monto(
                    unitario_coseguro, pedida.cantidad
                ),
                "origen": origen,
            }
        )

    if faltantes:
        raise ValidacionError(
            "Falta el arancel de: "
            + ", ".join(faltantes)
            + ". Cargalo en «Prestaciones y aranceles» o facturá la visita con "
            "«permitir_particular» para cobrarlas a valor de referencia."
        )
    return planes, advertencias


def _totales(planes: Sequence[Dict]) -> Tuple[float, float]:
    """Suma los planes y devuelve ``(efectivo del mostrador, saldo de la OS)``."""
    return (
        sumar_montos(plan["monto_coseguro_paciente"] for plan in planes),
        sumar_montos(plan["monto_cubre_os"] for plan in planes),
    )


def _estado_inicial(monto_pendiente_os: float) -> EstadoPagoMovimiento:
    """Estado con el que nace el movimiento: ``PENDIENTE`` si le queda saldo a la OS.

    Un paciente particular (o una atención sin cobertura para esas prácticas) no
    le debe nada a ninguna obra social, así que el movimiento nace ``COBRADO``.
    """
    if monto_pendiente_os > 0:
        return EstadoPagoMovimiento.PENDIENTE
    return EstadoPagoMovimiento.COBRADO


def prestaciones_disponibles(db: Session, turno_id: int) -> PrestacionesDisponiblesRead:
    """Prácticas activas con el arancel que les corresponde según la cobertura.

    Es la ayuda de carga del formulario de cobro: el panel recibe cada práctica
    ya resuelta (lo que paga la OS y lo que se cobra en el mostrador) para que la
    secretaría tilde lo que se hizo y vea el coseguro antes de guardar.
    """
    turno = _obtener_turno(db, turno_id)
    paciente = turno.paciente
    aranceles = arancel_service.mapa_aranceles(db, paciente.obra_social_id)

    items: List[PrestacionDisponibleRead] = []
    for prestacion in prestacion_service.listar_prestaciones(db, activas=True):
        arancel = aranceles.get(prestacion.id)
        if arancel is not None:
            cubre = arancel.monto_cubre_os
            coseguro = arancel.monto_coseguro_paciente
            origen, cubierta = ORIGEN_ARANCEL, True
        elif paciente.obra_social_id is None:
            cubre, coseguro = 0.0, prestacion.valor_base
            origen, cubierta = ORIGEN_VALOR_BASE, False
        else:
            cubre, coseguro = 0.0, prestacion.valor_base
            origen, cubierta = ORIGEN_SIN_ARANCEL, False

        items.append(
            PrestacionDisponibleRead(
                prestacion_id=prestacion.id,
                codigo_nomenclador=prestacion.codigo_nomenclador,
                descripcion=prestacion.descripcion,
                valor_base=prestacion.valor_base,
                cubierta_por_os=cubierta,
                monto_cubre_os=redondear_monto(cubre),
                monto_coseguro_paciente=redondear_monto(coseguro),
                origen=origen,
            )
        )

    obra_social = paciente.obra_social
    return PrestacionesDisponiblesRead(
        turno_id=turno.id,
        paciente_id=paciente.id,
        paciente_nombre=paciente.nombre,
        paciente_dni=paciente.dni,
        obra_social_id=paciente.obra_social_id,
        obra_social_nombre=obra_social.nombre if obra_social is not None else None,
        numero_afiliado=paciente.numero_afiliado,
        doctor_nombre=turno.doctor.nombre,
        turno_fecha_hora_inicio=turno.fecha_hora_inicio,
        facturado=movimiento_de_turno(db, turno.id) is not None,
        prestaciones=items,
    )


def presupuestar(
    db: Session, turno_id: int, data: FacturarTurnoRequest
) -> PresupuestoCobroRead:
    """Calcula el cartel del mostrador **sin** escribir en la base.

    Usa el mismo motor que :func:`facturar_turno` (``_resolver_prestaciones``), de
    modo que el importe que ve la secretaría antes de confirmar es exactamente el
    que va a quedar guardado. Valida las mismas reglas: turno existente y no
    cancelado, y aranceles presentes (o ``permitir_particular``).
    """
    turno = _obtener_turno(db, turno_id)
    if turno.estado_turno == EstadoTurno.CANCELADO:
        raise ValidacionError(
            "No se puede presupuestar una visita cancelada: reactivá el turno primero."
        )
    if movimiento_de_turno(db, turno.id) is not None:
        raise TurnoYaFacturadoError(
            f"El turno {turno.id} ya fue facturado: consultá su movimiento de caja."
        )

    planes, advertencias = _resolver_prestaciones(
        db, turno.paciente, data.prestaciones, data.permitir_particular
    )
    efectivo, pendiente_os = _totales(planes)

    return PresupuestoCobroRead(
        turno_id=turno.id,
        paciente_nombre=turno.paciente.nombre,
        obra_social_nombre=(
            turno.paciente.obra_social.nombre
            if turno.paciente.obra_social is not None
            else None
        ),
        permitir_particular=data.permitir_particular,
        cantidad_prestaciones=sum(plan["cantidad"] for plan in planes),
        monto_efectivo=efectivo,
        monto_pendiente_os=pendiente_os,
        monto_total=sumar_montos([efectivo, pendiente_os]),
        advertencias=advertencias,
        prestaciones=[
            PrestacionFacturadaCalculadaRead(
                prestacion_id=plan["prestacion"].id,
                codigo_nomenclador=plan["prestacion"].codigo_nomenclador,
                descripcion=plan["prestacion"].descripcion,
                cantidad=plan["cantidad"],
                monto_cubre_os=plan["monto_cubre_os"],
                monto_coseguro_paciente=plan["monto_coseguro_paciente"],
                monto_total=sumar_montos(
                    [plan["monto_cubre_os"], plan["monto_coseguro_paciente"]]
                ),
                origen=plan["origen"],
            )
            for plan in planes
        ],
    )


def facturar_turno(
    db: Session, turno_id: int, data: FacturarTurnoRequest
) -> MovimientoCaja:
    """Cierra la visita: crea el movimiento de caja con las prácticas realizadas.

    El cálculo es el de :func:`_resolver_prestaciones`, así que el saldo de la
    obra social y el coseguro del paciente quedan separados desde el primer
    momento. Además:

    * un turno ``PENDIENTE`` pasa a ``CONFIRMADO`` (la visita se realizó);
    * un turno ``CANCELADO`` no se puede facturar (**422**);
    * un turno ya facturado responde **409** (un turno tiene un único cierre: el
      índice ``uq_movimiento_caja_turno`` lo garantiza también en la base);
    * el movimiento nace ``PENDIENTE`` si le queda saldo a la obra social y
      ``COBRADO`` si no hay nada que presentar (paciente particular).
    """
    turno = _obtener_turno(db, turno_id)
    if turno.estado_turno == EstadoTurno.CANCELADO:
        raise ValidacionError(
            "No se puede facturar una visita cancelada: reactivá el turno primero."
        )
    if movimiento_de_turno(db, turno.id) is not None:
        raise TurnoYaFacturadoError(
            f"El turno {turno.id} ya fue facturado (movimiento de caja existente)."
        )

    planes, advertencias = _resolver_prestaciones(
        db, turno.paciente, data.prestaciones, data.permitir_particular
    )
    efectivo, pendiente_os = _totales(planes)

    observaciones = data.observaciones
    if advertencias:
        notas = " · ".join(advertencias)
        observaciones = f"{observaciones} · {notas}" if observaciones else notas

    movimiento = MovimientoCaja(
        turno_id=turno.id,
        doctor_id=turno.doctor_id,
        fecha=data.fecha or utcnow(),
        monto_efectivo=efectivo,
        monto_pendiente_os=pendiente_os,
        # ``None``: todavía no entró nada de la OS. Cuando no hay saldo que
        # presentar (particular) queda en 0: no hay nada que cobrarle a nadie.
        monto_cobrado_os=None if pendiente_os > 0 else 0.0,
        estado_pago=_estado_inicial(pendiente_os),
        observaciones=observaciones,
        detalles=[
            DetalleMovimientoCaja(
                prestacion_id=plan["prestacion"].id,
                codigo_nomenclador=plan["prestacion"].codigo_nomenclador,
                descripcion=plan["prestacion"].descripcion,
                cantidad=plan["cantidad"],
                monto_cubre_os=plan["monto_cubre_os"],
                monto_coseguro_paciente=plan["monto_coseguro_paciente"],
            )
            for plan in planes
        ],
    )
    db.add(movimiento)

    if turno.estado_turno == EstadoTurno.PENDIENTE:
        turno.estado_turno = EstadoTurno.CONFIRMADO

    try:
        db.commit()
    except IntegrityError as exc:  # índice único uq_movimiento_caja_turno
        db.rollback()
        logger.warning(
            "IntegrityError al facturar el turno %s: %s", turno_id, exc.orig
        )
        raise TurnoYaFacturadoError(
            f"El turno {turno_id} ya fue facturado (movimiento de caja existente)."
        ) from exc

    db.refresh(movimiento)
    logger.info(
        "Turno %s facturado: movimiento=%s efectivo=%.2f pendiente_os=%.2f estado=%s",
        turno.id,
        movimiento.id,
        movimiento.monto_efectivo,
        movimiento.monto_pendiente_os,
        movimiento.estado_pago,
    )
    return movimiento


def cobrar_movimiento(
    db: Session, movimiento_id: int, data: CobrarMovimientoRequest
) -> MovimientoCaja:
    """Marca el cobro definitivo de la obra social para un movimiento.

    Es la operación que hace la secretaría cuando **entra el depósito** de la
    prepaga: la orden presentada pasa de ``EN_PROCESO_LIQUIDACION`` a ``COBRADO``.
    Admite informar el importe realmente depositado (puede diferir del presentado
    por una diferencia de arancel) y una observación (nº de depósito, fecha…).

    Es **idempotente**: cobrar algo ya cobrado no es un error. Un movimiento
    debitado no se puede cobrar (esa orden ya se cerró como pérdida).
    """
    movimiento = obtener_movimiento(db, movimiento_id)

    if movimiento.estado_pago == EstadoPagoMovimiento.DEBITADO_OS:
        raise ConflictoError(
            "El movimiento fue debitado por la obra social y no se puede marcar "
            "como cobrado: si la OS finalmente pagó, cargá una facturación nueva."
        )
    if movimiento.estado_pago == EstadoPagoMovimiento.COBRADO:
        logger.info("Movimiento id=%s ya estaba cobrado", movimiento.id)
        return movimiento
    if movimiento.monto_pendiente_os <= 0:
        raise ValidacionError(
            "Este movimiento no tiene saldo a cobrar de la obra social."
        )

    movimiento.monto_cobrado_os = redondear_monto(
        data.monto_cobrado_os
        if data.monto_cobrado_os is not None
        else movimiento.monto_pendiente_os
    )
    if data.observaciones:
        movimiento.observaciones = data.observaciones
    movimiento.estado_pago = EstadoPagoMovimiento.COBRADO
    db.commit()
    db.refresh(movimiento)
    logger.info(
        "Movimiento id=%s cobrado: depositado=%.2f presentado=%.2f",
        movimiento.id,
        movimiento.monto_cobrado_os or 0,
        movimiento.monto_pendiente_os,
    )
    return movimiento


def debitar_movimiento(
    db: Session, movimiento_id: int, data: DebitarMovimientoRequest
) -> MovimientoCaja:
    """Registra el **débito** de la obra social sobre una orden.

    Un débito es un importe que el consultorio deja de cobrar (práctica
    rechazada, afiliado sin cobertura al día, diferencia de arancel): el
    movimiento se cierra como ``DEBITADO_OS`` con el **motivo obligatorio** y, si
    la OS pagó una parte, el importe depositado. Se puede debitar tanto una orden
    presentada como una que todavía está ``PENDIENTE`` (la OS rechazó antes de la
    presentación).

    Un movimiento ya ``COBRADO`` no se puede debitar: primero habría que anular
    el cobro, operación que este módulo no expone (el circuito del dinero ya
    cerró). Responde **409**.
    """
    movimiento = obtener_movimiento(db, movimiento_id)

    if movimiento.estado_pago == EstadoPagoMovimiento.COBRADO:
        raise ConflictoError(
            "El movimiento ya figura cobrado: no se puede debitar. Registrá una "
            "nota en una facturación nueva si la OS descontó el importe."
        )

    movimiento.monto_cobrado_os = redondear_monto(data.monto_cobrado_os or 0)
    movimiento.estado_pago = EstadoPagoMovimiento.DEBITADO_OS
    movimiento.observaciones = data.observaciones
    db.commit()
    db.refresh(movimiento)
    logger.info(
        "Movimiento id=%s debitado: motivo=%s depositado=%.2f",
        movimiento.id,
        data.observaciones,
        movimiento.monto_cobrado_os or 0,
    )
    return movimiento


def _presentacion(
    obra_social,
    movimientos: Sequence[MovimientoCaja],
    anio: int,
    mes: int,
    generada: bool,
) -> PresentacionObraSocialRead:
    """Arma la presentación del período con sus totales ya separados por estado.

    Los importes del resumen son todos derivados de los movimientos (una única
    fuente de verdad): lo presentado, lo cobrado, lo debitado y lo que sigue
    pendiente de acreditación.
    """
    desde, hasta = periodo_mensual(anio, mes)
    abiertos = [
        m
        for m in movimientos
        if m.estado_pago
        in (
            EstadoPagoMovimiento.PENDIENTE,
            EstadoPagoMovimiento.EN_PROCESO_LIQUIDACION,
        )
    ]
    cobrados = [
        m for m in movimientos if m.estado_pago == EstadoPagoMovimiento.COBRADO
    ]
    debitados = [
        m for m in movimientos if m.estado_pago == EstadoPagoMovimiento.DEBITADO_OS
    ]

    return PresentacionObraSocialRead(
        numero_presentacion=numero_presentacion(obra_social.id, anio, mes),
        obra_social_id=obra_social.id,
        obra_social_nombre=obra_social.nombre,
        obra_social_codigo=obra_social.codigo,
        anio=anio,
        mes=mes,
        periodo_desde=desde,
        periodo_hasta=hasta,
        generada=generada,
        cantidad_movimientos=len(movimientos),
        total_presentado=sumar_montos(m.monto_pendiente_os for m in movimientos),
        total_efectivo=sumar_montos(m.monto_efectivo for m in movimientos),
        total_cobrado_os=sumar_montos(
            (
                m.monto_cobrado_os
                if m.monto_cobrado_os is not None
                else m.monto_pendiente_os
            )
            for m in cobrados
        ),
        total_debitado_os=sumar_montos(
            m.monto_pendiente_os - (m.monto_cobrado_os or 0) for m in debitados
        ),
        total_pendiente_os=sumar_montos(
            m.monto_pendiente_os - (m.monto_cobrado_os or 0) for m in abiertos
        ),
        movimientos=[MovimientoCajaRead.model_validate(m) for m in movimientos],
    )


def presentacion_del_periodo(
    db: Session, obra_social_id: int, anio: int, mes: int
) -> PresentacionObraSocialRead:
    """Presentación del período en su estado **actual** (consulta de sólo lectura).

    Devuelve todos los movimientos del mes de esa cobertura con su estado:
    los ``PENDIENTE`` son los que todavía no se presentaron, los
    ``EN_PROCESO_LIQUIDACION`` los que esperan el depósito y los ``COBRADO`` /
    ``DEBITADO_OS`` los ya cerrados. ``generada`` es ``False`` mientras no se
    haya emitido la presentación (no hay ningún movimiento presentado).
    """
    obra_social = obra_social_service.obtener_obra_social(db, obra_social_id)
    desde, hasta = periodo_mensual(anio, mes)
    movimientos = _movimientos_del_periodo(db, obra_social_id, desde, hasta)
    generada = any(
        m.estado_pago != EstadoPagoMovimiento.PENDIENTE for m in movimientos
    )
    return _presentacion(obra_social, movimientos, anio, mes, generada)


def generar_presentacion(
    db: Session, obra_social_id: int, anio: int, mes: int
) -> PresentacionObraSocialRead:
    """Cierra el período de una cobertura: genera la presentación para el cobro.

    Toma los movimientos ``PENDIENTE`` del mes y los pasa a
    ``EN_PROCESO_LIQUIDACION`` (todos a la vez, en una transacción): a partir de
    ahí la secretaría puede exportar el resumen y, cuando entre el depósito,
    marcar cada orden como cobrada o debitada.

    Si no hay nada pendiente en el período responde **422** (no se emite una
    presentación vacía). Volver a generarla después de haberla emitido tampoco
    hace nada: sólo quedan ``PENDIENTE`` las atenciones nuevas que se facturen
    con fecha dentro del mismo mes.
    """
    obra_social = obra_social_service.obtener_obra_social(db, obra_social_id)
    desde, hasta = periodo_mensual(anio, mes)
    pendientes = _movimientos_del_periodo(
        db,
        obra_social_id,
        desde,
        hasta,
        estados=[EstadoPagoMovimiento.PENDIENTE],
    )
    if not pendientes:
        raise ValidacionError(
            f"No hay movimientos pendientes de {obra_social.nombre} en "
            f"{mes:02d}/{anio}: no hay nada para presentar."
        )

    for movimiento in pendientes:
        movimiento.estado_pago = EstadoPagoMovimiento.EN_PROCESO_LIQUIDACION
    db.commit()

    numero = numero_presentacion(obra_social.id, anio, mes)
    logger.info(
        "Presentación %s generada: %s movimiento(s) en proceso de liquidación",
        numero,
        len(pendientes),
    )

    movimientos = _movimientos_del_periodo(db, obra_social_id, desde, hasta)
    return _presentacion(obra_social, movimientos, anio, mes, True)

