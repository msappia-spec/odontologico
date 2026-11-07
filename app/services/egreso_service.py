"""Servicio de proveedores, gastos y cuentas por pagar (Módulo 6).

Cómo funciona el circuito
-------------------------

1. **Alta de una factura recibida** (:func:`registrar_factura`). Cuando llega la
   factura del mecánico dental, de la droguería o de un servicio:

   * se valida que el proveedor exista y se lo **bloquea** (``SELECT … FOR
     UPDATE`` en PostgreSQL) para que dos altas simultáneas no pisen su saldo;
   * si llega ``paciente_id`` se exige que el proveedor sea ``MECANICO_DENTAL``
     (el gasto de una pieza corresponde a un paciente concreto) y que el
     paciente exista;
   * se calcula el vencimiento (30 días por defecto) y se guarda la factura en
     ``PENDIENTE`` con ``monto_pagado = 0``;
   * se **incrementa** el ``saldo_pendiente`` del proveedor por el
     ``monto_total``.

2. **Pago de una factura** (:func:`registrar_pago`). Con la factura y su
   proveedor bloqueados:

   * se rechaza un pago que supere el saldo de la factura (no se admite pagar
     de más: a diferencia de la cuenta del paciente, acá no existe el «saldo a
     favor» con un proveedor);
   * se inserta el ``PagoEgreso`` con su método (``EFECTIVO``,
     ``TRANSFERENCIA`` o ``TARJETA``);
   * se acumula ``monto_pagado`` y el estado pasa a ``PARCIAL`` o ``PAGADO``;
   * se **resta** el mismo importe del ``saldo_pendiente`` del proveedor.

3. **Consultas**: :func:`listar_proveedores` (con saldos al día),
   :func:`obtener_proveedor` (detalle con facturas), :func:`listar_facturas` y
   :func:`resumen_cuentas_por_pagar` (tablero de deuda, total y vencida, por
   rubro).

Atomicidad y control monetario
------------------------------

* Todas las escrituras pasan por :func:`transaccion`, la unidad de trabajo del
  proyecto: ``commit`` al salir bien y ``rollback`` **completo** si algo falla.
  El pago, el estado de la factura y el saldo del proveedor se confirman en un
  único ``COMMIT``; nunca queda un pago aplicado sin descontar la deuda (ni al
  revés). No se usa ``session.begin()`` porque la sesión de ``get_db`` ya tiene
  una transacción abierta: ``begin()`` lanzaría «a transaction is already
  begun». Este *context manager* garantiza exactamente la misma semántica ACID.
* El proveedor se bloquea con ``SELECT … FOR UPDATE`` **y** ``populate_existing``
  (:func:`_bloquear_proveedor`): el lock ordena las escrituras concurrentes y la
  relectura evita que la resta del saldo parta de una copia vieja del *identity
  map* (actualización perdida entre dos pagos a facturas distintas).
* Los importes se calculan **siempre** en ``Decimal`` (``Numeric(10, 2)`` en la
  base). Ningún saldo pasa por ``float``: ``_decimal`` es la única puerta de
  entrada y redondea a centavos con ``ROUND_HALF_UP``.
* Los saldos son **derivados y verificables**: :func:`recalcular_saldo_proveedor`
  reconstruye el saldo sumando las facturas abiertas, de modo que una carga
  manual o un borrado fuera del servicio se puede corregir sin adivinar.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterator, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.datetime_utils import utcnow
from app.core.exceptions import RecursoNoEncontradoError, ValidacionError
from app.models.egreso import SALDO_CERO, GastoFactura, PagoEgreso, Proveedor
from app.models.enums import EstadoPagoFactura, TipoProveedor
from app.schemas.egreso import (
    DIAS_VENCIMIENTO_POR_DEFECTO,
    DeudaPorTipoRead,
    GastoFacturaCreate,
    GastoFacturaRead,
    PagoEgresoCreate,
    PagoEgresoRead,
    PagoEgresoRegistradoRead,
    ProveedorCreate,
    ProveedorDetalleRead,
    ProveedorRead,
    ResumenEgresosRead,
)
from app.services import paciente_service

logger = logging.getLogger("app.services.egresos")

#: Unidad mínima de los importes (pesos con dos decimales).
CENTAVOS = Decimal("0.01")


def _decimal(valor) -> Decimal:
    """Convierte cualquier importe a ``Decimal`` con dos decimales (medio arriba).

    Acepta ``Decimal``, ``int``, ``float`` (de la base) o ``str`` y **nunca**
    devuelve un ``float``: es el único punto por donde entra un importe al
    módulo de egresos.
    """
    if valor is None:
        return SALDO_CERO
    return Decimal(str(valor)).quantize(CENTAVOS, rounding=ROUND_HALF_UP)


@contextmanager
def transaccion(db: Session) -> Iterator[Session]:
    """Unidad de trabajo ACID de las operaciones de egresos.

    Al salir por el camino feliz confirma **todo** (factura/pago + estado +
    saldo del proveedor) en un único ``COMMIT``; ante cualquier excepción —del
    servicio, de la base o del esquema— revierte la transacción completa y
    vuelve a lanzar el error, de modo que no queden pagos huérfanos ni saldos a
    medio actualizar.
    """

    try:
        yield db
        db.commit()
    except Exception as error:  # noqa: BLE001 - el rollback debe ser total
        db.rollback()
        logger.warning("Rollback de la transacción de egresos: %s", error)
        raise

# --------------------------------------------------------------------------- #
# Helpers de lectura, bloqueo y armado de respuestas
# --------------------------------------------------------------------------- #
def _bloquear_fila(db: Session, modelo, pk) -> bool:
    """Pide ``SELECT … FOR UPDATE`` sobre la fila y devuelve si existe.

    Se consulta **sólo la clave** (no el objeto completo) porque las relaciones
    de ``GastoFactura`` viajan con ``lazy="joined"`` y PostgreSQL rechaza
    ``FOR UPDATE`` sobre el lado anulable de un ``LEFT OUTER JOIN``. Una vez
    tomada la fila, la lectura normal del objeto ya ve el estado bloqueado.

    En SQLite (desarrollo y pruebas) la cláusula se ignora: ese motor serializa
    las escrituras de todas formas.
    """
    encontrado = db.execute(
        select(modelo.id).where(modelo.id == pk).with_for_update()
    ).scalar_one_or_none()
    return encontrado is not None


def _bloquear_proveedor(db: Session, proveedor_id: UUID) -> Proveedor:
    """Bloquea la fila del proveedor y devuelve su estado **fresco**.

    ``_bloquear_fila`` sólo toma el lock: si el objeto ya estaba en el
    *identity map* (``GastoFactura.proveedor`` viaja con ``lazy="joined"``), sus
    columnas seguirían siendo las leídas **antes** de esperar el lock. Con dos
    pagos simultáneos a facturas distintas del mismo proveedor, el segundo
    calcularía el saldo sobre el valor viejo y perdería la resta del primero
    («lost update»), aunque la fila estuviera bloqueada.

    ``populate_existing`` obliga a releer las columnas de la fila ya bloqueada,
    de modo que la resta del saldo siempre parte del valor confirmado por la
    última transacción que tomó el lock. En SQLite la cláusula ``FOR UPDATE`` se
    ignora, pero la relectura se aplica igual.

    Raises:
        RecursoNoEncontradoError: el proveedor no existe (404).
    """

    proveedor = db.execute(
        select(Proveedor)
        .where(Proveedor.id == proveedor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if proveedor is None:
        raise RecursoNoEncontradoError("Proveedor no encontrado.")
    return proveedor


def _proveedor(db: Session, proveedor_id: UUID) -> Proveedor:
    """Devuelve el proveedor o lanza ``RecursoNoEncontradoError`` (404)."""
    proveedor = db.execute(
        select(Proveedor).where(Proveedor.id == proveedor_id)
    ).scalar_one_or_none()
    if proveedor is None:
        raise RecursoNoEncontradoError("Proveedor no encontrado.")
    return proveedor


def _factura(db: Session, factura_id: UUID) -> GastoFactura:
    """Devuelve la factura o lanza ``RecursoNoEncontradoError`` (404)."""
    factura = (
        db.execute(select(GastoFactura).where(GastoFactura.id == factura_id))
        .unique()
        .scalar_one_or_none()
    )
    if factura is None:
        raise RecursoNoEncontradoError("Factura de egreso no encontrada.")
    return factura


def _pago_read(pago: PagoEgreso) -> PagoEgresoRead:
    """Esquema de salida de un pago, con el importe en ``Decimal``."""
    return PagoEgresoRead(
        id=pago.id,
        gasto_factura_id=pago.gasto_factura_id,
        monto_pagado=_decimal(pago.monto_pagado),
        metodo_pago=pago.metodo_pago,
        fecha_pago=pago.fecha_pago,
    )


def _factura_read(factura: GastoFactura) -> GastoFacturaRead:
    """Esquema de salida de una factura, con sus saldos y sus pagos."""
    return GastoFacturaRead(
        id=factura.id,
        proveedor_id=factura.proveedor_id,
        proveedor_nombre=factura.proveedor_nombre,
        proveedor_tipo=factura.proveedor_tipo,
        paciente_id=factura.paciente_id,
        paciente_nombre=factura.paciente_nombre,
        concepto=factura.concepto,
        monto_total=_decimal(factura.monto_total),
        monto_pagado=_decimal(factura.monto_pagado),
        saldo_pendiente=_decimal(factura.saldo_pendiente),
        estado_pago=factura.estado_pago,
        fecha_emision=factura.fecha_emision,
        fecha_vencimiento=factura.fecha_vencimiento,
        vencida=factura.vencida,
        pagos=[_pago_read(pago) for pago in factura.pagos],
    )


def _proveedor_read(proveedor: Proveedor) -> ProveedorRead:
    """Esquema de salida de un proveedor con su cartera resumida."""
    return ProveedorRead(
        id=proveedor.id,
        nombre=proveedor.nombre,
        telefono_contacto=proveedor.telefono_contacto,
        tipo_proveedor=proveedor.tipo_proveedor,
        saldo_pendiente=_decimal(proveedor.saldo_pendiente),
        facturas_abiertas=proveedor.facturas_abiertas,
        deuda_vencida=_decimal(proveedor.deuda_vencida),
    )


# --------------------------------------------------------------------------- #
# Proveedores
# --------------------------------------------------------------------------- #
def crear_proveedor(db: Session, data: ProveedorCreate) -> ProveedorRead:
    """Registra un proveedor con saldo inicial cero.

    El saldo **nunca** se recibe del cliente: nace en ``0`` y sólo lo mueven las
    facturas y los pagos, para que no se pueda inflar la deuda a mano.
    """
    with transaccion(db):
        proveedor = Proveedor(
            nombre=data.nombre,
            telefono_contacto=data.telefono_contacto,
            tipo_proveedor=data.tipo_proveedor,
            saldo_pendiente=SALDO_CERO,
        )
        db.add(proveedor)
        db.flush()
        resultado = ProveedorRead(
            id=proveedor.id,
            nombre=proveedor.nombre,
            telefono_contacto=proveedor.telefono_contacto,
            tipo_proveedor=proveedor.tipo_proveedor,
            saldo_pendiente=SALDO_CERO,
            facturas_abiertas=0,
            deuda_vencida=SALDO_CERO,
        )

    logger.info(
        "Proveedor creado: id=%s tipo=%s nombre=%s",
        proveedor.id,
        proveedor.tipo_proveedor,
        proveedor.nombre,
    )
    return resultado


def listar_proveedores(
    db: Session,
    *,
    tipo_proveedor: Optional[TipoProveedor] = None,
    solo_con_deuda: bool = False,
) -> List[ProveedorRead]:
    """Lista los proveedores con el saldo y la deuda vencida al día.

    Ordena alfabéticamente sin distinguir mayúsculas porque es el orden en el
    que la secretaría busca al cargar un gasto. ``solo_con_deuda`` deja
    únicamente a los que tienen ``saldo_pendiente > 0``.
    """
    stmt = select(Proveedor)
    if tipo_proveedor is not None:
        stmt = stmt.where(Proveedor.tipo_proveedor == tipo_proveedor)
    if solo_con_deuda:
        stmt = stmt.where(Proveedor.saldo_pendiente > 0)
    stmt = stmt.order_by(func.lower(Proveedor.nombre), Proveedor.nombre)
    proveedores = db.execute(stmt).scalars().all()
    return [_proveedor_read(proveedor) for proveedor in proveedores]


def obtener_proveedor(db: Session, proveedor_id: UUID) -> ProveedorDetalleRead:
    """Detalle de un proveedor con todas sus facturas (la más nueva primero)."""
    proveedor = _proveedor(db, proveedor_id)
    resumen = _proveedor_read(proveedor)
    return ProveedorDetalleRead(
        **resumen.model_dump(),
        facturas=[_factura_read(factura) for factura in proveedor.facturas],
    )


# --------------------------------------------------------------------------- #
# Facturas recibidas
# --------------------------------------------------------------------------- #
def registrar_factura(db: Session, data: GastoFacturaCreate) -> GastoFacturaRead:
    """Registra un gasto o factura recibida y aumenta la deuda del proveedor.

    Si el proveedor es ``MECANICO_DENTAL`` se puede indicar ``paciente_id`` para
    vincular la pieza con el paciente; en cualquier otro rubro el campo se
    rechaza (``422``) porque una compra de insumos o la luz no son de un
    paciente.

    El vencimiento se toma del payload; si no viene, se calcula con el plazo por
    defecto (:data:`~app.schemas.egreso.DIAS_VENCIMIENTO_POR_DEFECTO`).

    Raises:
        RecursoNoEncontradoError: el proveedor o el paciente no existen (404).
        ValidacionError: ``paciente_id`` en un proveedor que no es mecánico
            dental, o un vencimiento anterior a la emisión (422).
    """
    with transaccion(db):
        # Lock + relectura en una sola consulta: dos altas simultáneas del mismo
        # proveedor suman sobre el saldo confirmado, no sobre una copia vieja.
        proveedor = _bloquear_proveedor(db, data.proveedor_id)

        paciente = None
        if data.paciente_id is not None:
            if proveedor.tipo_proveedor is not TipoProveedor.MECANICO_DENTAL:
                raise ValidacionError(
                    "Sólo las facturas de un mecánico dental pueden asociarse a "
                    f"un paciente (el proveedor es {proveedor.tipo_proveedor.value})."
                )
            paciente = paciente_service.obtener_paciente(db, data.paciente_id)

        emision = data.fecha_emision or utcnow()
        vencimiento = data.fecha_vencimiento or (
            emision + timedelta(days=DIAS_VENCIMIENTO_POR_DEFECTO)
        )
        if vencimiento < emision:
            raise ValidacionError(
                "La fecha de vencimiento no puede ser anterior a la de emisión."
            )

        monto = _decimal(data.monto_total)
        factura = GastoFactura(
            proveedor=proveedor,
            paciente=paciente,
            concepto=data.concepto,
            monto_total=monto,
            monto_pagado=SALDO_CERO,
            fecha_emision=emision,
            fecha_vencimiento=vencimiento,
            estado_pago=EstadoPagoFactura.PENDIENTE,
        )
        db.add(factura)

        # El saldo es la deuda acumulada: se incrementa en la misma transacción
        # en la que nace la factura (nunca se toca fuera de este servicio).
        proveedor.saldo_pendiente = _decimal(proveedor.saldo_pendiente) + monto
        db.flush()

        resultado = _factura_read(factura)

    logger.info(
        "Gasto registrado: factura=%s proveedor=%s monto=%s paciente_id=%s",
        factura.id,
        proveedor.id,
        monto,
        factura.paciente_id,
    )
    return resultado


def listar_facturas(
    db: Session,
    *,
    proveedor_id: Optional[UUID] = None,
    estado_pago: Optional[EstadoPagoFactura] = None,
    paciente_id: Optional[int] = None,
    solo_vencidas: bool = False,
    skip: int = 0,
    limit: int = 200,
) -> List[GastoFacturaRead]:
    """Lista las facturas recibidas, de la que vence antes a la que vence después.

    El orden por vencimiento es el de la cartera de cuentas por pagar: lo primero
    que aparece es lo que hay que pagar primero. ``solo_vencidas`` deja las que
    ya pasaron su fecha y todavía tienen saldo.
    """
    stmt = select(GastoFactura)
    if proveedor_id is not None:
        stmt = stmt.where(GastoFactura.proveedor_id == proveedor_id)
    if estado_pago is not None:
        stmt = stmt.where(GastoFactura.estado_pago == estado_pago)
    if paciente_id is not None:
        stmt = stmt.where(GastoFactura.paciente_id == paciente_id)
    if solo_vencidas:
        stmt = stmt.where(
            GastoFactura.estado_pago != EstadoPagoFactura.PAGADO,
            GastoFactura.fecha_vencimiento < utcnow(),
        )
    stmt = (
        stmt.order_by(
            GastoFactura.fecha_vencimiento, GastoFactura.fecha_emision.desc()
        )
        .offset(skip)
        .limit(limit)
    )
    facturas = db.execute(stmt).unique().scalars().all()
    return [_factura_read(factura) for factura in facturas]


# --------------------------------------------------------------------------- #
# Pagos de egresos
# --------------------------------------------------------------------------- #
def registrar_pago(db: Session, data: PagoEgresoCreate) -> PagoEgresoRegistradoRead:
    """Aplica un pago a una factura y descuenta la deuda del proveedor.

    La operación es **atómica**: el ``PagoEgreso``, el ``monto_pagado`` y el
    estado de la factura, y el ``saldo_pendiente`` del proveedor se confirman en
    el mismo ``COMMIT``. Si el pago supera el saldo de la factura se rechaza con
    ``422`` y **nada** queda escrito (ni el pago ni el saldo).

    Pagar el saldo exacto deja la factura en ``PAGADO``; un pago menor la deja
    en ``PARCIAL`` y se puede completar con otro pago.

    Raises:
        RecursoNoEncontradoError: la factura no existe (404).
        ValidacionError: la factura ya está paga o el importe supera el saldo
            pendiente (422).
    """
    with transaccion(db):
        if not _bloquear_fila(db, GastoFactura, data.gasto_factura_id):
            raise RecursoNoEncontradoError("Factura de egreso no encontrada.")
        factura = _factura(db, data.gasto_factura_id)
        # Lock con relectura: el saldo del proveedor con el que se resta es el
        # confirmado por la última transacción, no el que vino en el ``joined``
        # de la factura (que pudo leerse antes de esperar el bloqueo).
        proveedor = _bloquear_proveedor(db, factura.proveedor_id)

        monto = _decimal(data.monto_pagado)
        saldo_factura = _decimal(factura.saldo_pendiente)
        if saldo_factura <= SALDO_CERO:
            raise ValidacionError(
                "La factura ya está pagada: no queda saldo pendiente por cancelar."
            )
        if monto > saldo_factura:
            raise ValidacionError(
                f"El pago ({monto}) supera el saldo pendiente de la factura "
                f"({saldo_factura})."
            )

        pago = PagoEgreso(
            gasto_factura_id=factura.id,
            monto_pagado=monto,
            metodo_pago=data.metodo_pago,
            fecha_pago=data.fecha_pago or utcnow(),
        )
        db.add(pago)
        # Mantiene la colección en memoria alineada con la fila recién insertada
        # (el esquema de salida devuelve los pagos de la factura).
        factura.pagos.append(pago)

        factura.monto_pagado = _decimal(factura.monto_pagado) + monto
        saldo_restante = _decimal(factura.saldo_pendiente)
        factura.estado_pago = (
            EstadoPagoFactura.PAGADO
            if saldo_restante <= SALDO_CERO
            else EstadoPagoFactura.PARCIAL
        )

        # Nunca negativo: si el saldo del proveedor estuviera desincronizado por
        # una carga manual, el pago lo deja en cero en lugar de violar el CHECK.
        saldo_proveedor = _decimal(proveedor.saldo_pendiente) - monto
        proveedor.saldo_pendiente = max(saldo_proveedor, SALDO_CERO)
        db.flush()

        resultado = PagoEgresoRegistradoRead(
            pago=_pago_read(pago),
            factura_estado_pago=factura.estado_pago,
            factura_saldo_pendiente=max(saldo_restante, SALDO_CERO),
            proveedor_saldo_pendiente=_decimal(proveedor.saldo_pendiente),
        )

    logger.info(
        "Pago de egreso registrado: pago=%s factura=%s monto=%s metodo=%s "
        "estado=%s saldo_proveedor=%s",
        pago.id,
        factura.id,
        monto,
        pago.metodo_pago,
        factura.estado_pago,
        proveedor.saldo_pendiente,
    )
    return resultado


def recalcular_saldo_proveedor(
    db: Session, proveedor_id: UUID, *, confirmar: bool = True
) -> Decimal:
    """Reconstruye el ``saldo_pendiente`` sumando las facturas abiertas.

    Es el equivalente, para el lado de los egresos, del recálculo del libro mayor
    de la cuenta corriente: si una factura se modificó por fuera del servicio
    (una consola de base de datos, un script de importación), este helper deja el
    saldo del proveedor otra vez en su valor contable.

    Con ``confirmar=False`` no hace ``commit``: el llamador lo controla (lo usan
    las pruebas y los procesos que ya tienen su propia unidad de trabajo).

    Raises:
        RecursoNoEncontradoError: el proveedor no existe (404).
    """
    unidad = transaccion(db) if confirmar else _sin_transaccion(db)
    with unidad:
        proveedor = _proveedor(db, proveedor_id)
        facturas = (
            db.execute(
                select(GastoFactura).where(
                    GastoFactura.proveedor_id == proveedor_id
                )
            )
            .unique()
            .scalars()
            .all()
        )
        saldo = sum(
            (
                _decimal(factura.saldo_pendiente)
                for factura in facturas
                if factura.estado_pago is not EstadoPagoFactura.PAGADO
            ),
            SALDO_CERO,
        )
        proveedor.saldo_pendiente = max(saldo, SALDO_CERO)
        db.flush()
        resultado = _decimal(proveedor.saldo_pendiente)

    logger.info(
        "Saldo del proveedor recalculado: id=%s saldo=%s", proveedor_id, resultado
    )
    return resultado


@contextmanager
def _sin_transaccion(db: Session) -> Iterator[Session]:
    """Variante de :func:`transaccion` que deja el ``commit`` en manos del llamador."""
    yield db


# --------------------------------------------------------------------------- #
# Tablero de cuentas por pagar
# --------------------------------------------------------------------------- #
def resumen_cuentas_por_pagar(db: Session) -> ResumenEgresosRead:
    """Tablero de la deuda del consultorio con sus proveedores.

    Devuelve el total facturado y pagado del histórico, la **deuda total**
    (suma de los saldos de las facturas abiertas: coincide con la suma de los
    ``saldo_pendiente`` de los proveedores), la **deuda vencida** y el desglose
    por rubro.

    Se calcula en ``Decimal`` y en Python sobre las facturas abiertas: la
    cartera de un consultorio son cientos de filas y así el redondeo es exacto
    en SQLite y en PostgreSQL, sin depender de cómo sume cada motor un
    ``CASE``/``SUM``.
    """
    ahora = utcnow()
    abiertas = db.execute(
        select(
            Proveedor.tipo_proveedor,
            GastoFactura.monto_total,
            GastoFactura.monto_pagado,
            GastoFactura.fecha_vencimiento,
        )
        .join(GastoFactura, GastoFactura.proveedor_id == Proveedor.id)
        .where(GastoFactura.estado_pago != EstadoPagoFactura.PAGADO)
    ).all()

    deuda_total = SALDO_CERO
    deuda_vencida = SALDO_CERO
    # ``{tipo: [cantidad_facturas, deuda]}``; se arma en Decimal, nunca en float.
    por_tipo: dict[TipoProveedor, list] = {}
    for tipo, monto_total, monto_pagado, vencimiento in abiertas:
        saldo = _decimal(monto_total) - _decimal(monto_pagado)
        deuda_total += saldo
        if vencimiento < ahora:
            deuda_vencida += saldo
        acumulado = por_tipo.setdefault(tipo, [0, SALDO_CERO])
        acumulado[0] += 1
        acumulado[1] += saldo

    historicos = db.execute(
        select(GastoFactura.monto_total, GastoFactura.monto_pagado)
    ).all()
    total_facturado = sum((_decimal(monto) for monto, _ in historicos), SALDO_CERO)
    total_pagado = sum((_decimal(pagado) for _, pagado in historicos), SALDO_CERO)
    cantidad_proveedores = db.execute(
        select(func.count()).select_from(Proveedor)
    ).scalar_one()

    return ResumenEgresosRead(
        generado=ahora,
        cantidad_proveedores=cantidad_proveedores,
        cantidad_facturas_abiertas=len(abiertas),
        total_facturado=_decimal(total_facturado),
        total_pagado=_decimal(total_pagado),
        deuda_total=_decimal(deuda_total),
        deuda_vencida=_decimal(deuda_vencida),
        por_tipo=[
            DeudaPorTipoRead(
                tipo_proveedor=tipo,
                cantidad_facturas=cantidad,
                deuda=_decimal(deuda),
            )
            for tipo, (cantidad, deuda) in sorted(
                por_tipo.items(), key=lambda item: item[0].value
            )
        ],
    )

