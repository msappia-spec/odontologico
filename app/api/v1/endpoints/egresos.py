"""Endpoints del Módulo 6: proveedores, gastos y cuentas por pagar.

Rutas (prefijo ``/api/v1``):

* ``POST /egresos/proveedores`` — alta de un proveedor (mecánico dental,
  insumos o servicios/impuestos) con saldo inicial ``0.00``.
* ``GET  /egresos/proveedores`` — listado alfabético con el saldo actualizado,
  la cantidad de facturas abiertas y la deuda vencida; se filtra por rubro y por
  «sólo con deuda».
* ``GET  /egresos/proveedores/{proveedor_id}`` — detalle con todas sus facturas.
* ``POST /egresos/facturas`` — registra un gasto recibido y **aumenta** el saldo
  del proveedor en el mismo ``COMMIT``.
* ``GET  /egresos/facturas`` — cartera de facturas por vencimiento, filtrable por
  proveedor, estado, paciente y vencidas.
* ``POST /egresos/pagar`` — aplica un pago (efectivo, transferencia o tarjeta):
  baja el saldo de la factura (``PARCIAL``/``PAGADO``) y **resta** el mismo
  importe del saldo del proveedor de forma atómica.
* ``GET  /egresos/cuentas-por-pagar`` — tablero de deuda total, vencida y por
  rubro.

Todos los importes viajan como número JSON y se calculan en ``Decimal`` en el
servidor; el detalle de las reglas contables está en
:mod:`app.services.egreso_service`.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import EstadoPagoFactura, TipoProveedor
from app.schemas.egreso import (
    GastoFacturaCreate,
    GastoFacturaRead,
    PagoEgresoCreate,
    PagoEgresoRegistradoRead,
    ProveedorCreate,
    ProveedorDetalleRead,
    ProveedorRead,
    ResumenEgresosRead,
)
from app.services import egreso_service

router = APIRouter(prefix="/egresos", tags=["Proveedores y egresos"])


@router.post(
    "/proveedores",
    response_model=ProveedorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un proveedor",
    description=(
        "Da de alta un acreedor del consultorio: un **mecánico dental** (las "
        "piezas de los pacientes), un proveedor de **insumos** o uno de "
        "**servicios e impuestos** (alquiler, luz, honorarios). El saldo nace en "
        "`0.00` y sólo lo mueven las facturas y los pagos: no se puede recibir "
        "desde el cliente. Un nombre vacío o un rubro inexistente responde 422."
    ),
)
def crear_proveedor(
    data: ProveedorCreate, db: Session = Depends(get_db)
) -> ProveedorRead:
    return egreso_service.crear_proveedor(db, data)


@router.get(
    "/proveedores",
    response_model=List[ProveedorRead],
    summary="Listar proveedores con sus saldos actualizados",
    description=(
        "Devuelve todos los proveedores en orden alfabético con `saldo_pendiente` "
        "(lo que el consultorio les debe hoy), `deuda_vencida` (la parte cuyas "
        "facturas ya pasaron el vencimiento) y `facturas_abiertas`. Se puede "
        "filtrar por rubro (`tipo_proveedor`) y quedarse sólo con los que tienen "
        "deuda (`solo_con_deuda=true`)."
    ),
)
def listar_proveedores(
    tipo_proveedor: Optional[TipoProveedor] = Query(
        None, description="``MECANICO_DENTAL``, ``INSUMOS`` o ``SERVICIOS_IMPUESTOS``."
    ),
    solo_con_deuda: bool = Query(
        False, description="``true`` deja únicamente los proveedores con saldo."
    ),
    db: Session = Depends(get_db),
) -> List[ProveedorRead]:
    return egreso_service.listar_proveedores(
        db, tipo_proveedor=tipo_proveedor, solo_con_deuda=solo_con_deuda
    )


@router.get(
    "/proveedores/{proveedor_id}",
    response_model=ProveedorDetalleRead,
    summary="Detalle de un proveedor con sus facturas",
    description=(
        "Devuelve el proveedor con su saldo y **todas** sus facturas (de la más "
        "nueva a la más vieja), cada una con sus importes, su estado y sus pagos. "
        "Un proveedor inexistente responde 404."
    ),
)
def obtener_proveedor(
    proveedor_id: UUID, db: Session = Depends(get_db)
) -> ProveedorDetalleRead:
    return egreso_service.obtener_proveedor(db, proveedor_id)

@router.post(
    "/facturas",
    response_model=GastoFacturaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un gasto o factura recibida",
    description=(
        "Carga la factura de un proveedor y **aumenta su saldo** por el "
        "`monto_total` en la misma transacción (si algo falla no queda ni la "
        "factura ni el saldo). La factura nace `PENDIENTE`. Si el proveedor es "
        "`MECANICO_DENTAL` se puede indicar `paciente_id` para vincular la pieza "
        "con el paciente; en otro rubro ese campo responde 422. Sin "
        "`fecha_vencimiento` se toman 30 días desde la emisión."
    ),
)
def registrar_factura(
    data: GastoFacturaCreate, db: Session = Depends(get_db)
) -> GastoFacturaRead:
    return egreso_service.registrar_factura(db, data)


@router.get(
    "/facturas",
    response_model=List[GastoFacturaRead],
    summary="Listar las facturas recibidas",
    description=(
        "Cartera de cuentas por pagar ordenada por **vencimiento** (lo que hay "
        "que pagar primero aparece arriba). Se filtra por proveedor "
        "(`proveedor_id`), estado (`estado_pago`: `PENDIENTE`, `PARCIAL` o "
        "`PAGADO`), paciente (`paciente_id`) y por vencidas (`solo_vencidas=true`, "
        "las que ya pasaron su fecha y todavía tienen saldo). Pagina con "
        "`skip`/`limit` (200 filas por defecto, 500 como máximo)."
    ),
)
def listar_facturas(
    proveedor_id: Optional[UUID] = Query(None, description="Filtra por proveedor."),
    estado_pago: Optional[EstadoPagoFactura] = Query(
        None, description="``PENDIENTE``, ``PARCIAL`` o ``PAGADO``."
    ),
    paciente_id: Optional[int] = Query(
        None, gt=0, description="Facturas de las piezas de un paciente."
    ),
    solo_vencidas: bool = Query(
        False, description="``true`` deja sólo las vencidas con saldo."
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[GastoFacturaRead]:
    return egreso_service.listar_facturas(
        db,
        proveedor_id=proveedor_id,
        estado_pago=estado_pago,
        paciente_id=paciente_id,
        solo_vencidas=solo_vencidas,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/pagar",
    response_model=PagoEgresoRegistradoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Pagar una factura recibida",
    description=(
        "Registra el pago de una factura con su método (`EFECTIVO` por defecto, "
        "`TRANSFERENCIA` o `TARJETA`) y devuelve los saldos resultantes. El pago, "
        "el estado de la factura (`PARCIAL` o `PAGADO`) y el `saldo_pendiente` "
        "del proveedor se confirman en un único `COMMIT`: si falla, no se "
        "descuenta nada. Un pago mayor al saldo de la factura, o sobre una "
        "factura ya paga, responde 422; una factura inexistente, 404."
    ),
)
def registrar_pago(
    data: PagoEgresoCreate, db: Session = Depends(get_db)
) -> PagoEgresoRegistradoRead:
    return egreso_service.registrar_pago(db, data)


@router.get(
    "/cuentas-por-pagar",
    response_model=ResumenEgresosRead,
    summary="Tablero de cuentas por pagar",
    description=(
        "Resume la deuda del consultorio: total facturado y pagado del "
        "histórico, **deuda total** (suma de los saldos de las facturas abiertas, "
        "que coincide con la suma de los `saldo_pendiente` de los proveedores), "
        "**deuda vencida** y el desglose por rubro. Los totales se calculan en "
        "`Decimal` en el servidor."
    ),
)
def resumen_cuentas_por_pagar(db: Session = Depends(get_db)) -> ResumenEgresosRead:
    return egreso_service.resumen_cuentas_por_pagar(db)

