"""Endpoints del Módulo 3: facturación, caja y liquidaciones.

Rutas (prefijo ``/api/v1``):

* **Cierre de la visita**: ``POST /turnos/{id}/facturar`` (crea el movimiento de
  caja) y ``POST /turnos/{id}/presupuesto`` (calcula el cartel del mostrador sin
  escribir nada). ``GET /turnos/{id}/prestaciones-disponibles`` devuelve cada
  práctica con el arancel que le corresponde al paciente del turno, y
  ``GET /turnos/{id}/facturacion`` informa si el turno ya se cobró.
* **Caja**: ``GET /movimientos-caja`` (caja diaria con filtros),
  ``GET /movimientos-caja/resumen`` (totales), ``GET /movimientos-caja/{id}`` y
  los cierres individuales ``POST /movimientos-caja/{id}/cobrar`` y
  ``POST /movimientos-caja/{id}/debitar``.
* **Liquidación de obras sociales**: ``GET /liquidaciones/obras-sociales``
  (presentación del período con el estado de cada orden) y
  ``POST /liquidaciones/obras-sociales`` (genera la presentación del mes).
* **Liquidación de profesionales**: ``GET /liquidaciones/profesionales``
  (producción y honorarios del mes por odontólogo).
* **Reportes en PDF**: ``GET /liquidaciones/obras-sociales/pdf`` y
  ``GET /liquidaciones/profesionales/pdf`` devuelven el mismo reporte que las
  rutas anteriores pero como archivo ``application/pdf`` listo para imprimir o
  adjuntar (se arman con :mod:`app.services.reporte_pdf_service`).
"""

from datetime import date as Date
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.datetime_utils import rango_del_dia, utcnow
from app.db.session import get_db
from app.models.enums import EstadoPagoMovimiento
from app.models.movimiento_caja import MovimientoCaja
from app.schemas.facturacion import (
    CobrarMovimientoRequest,
    DebitarMovimientoRequest,
    FacturacionTurnoRead,
    FacturarTurnoRequest,
    LiquidacionObraSocialRequest,
    LiquidacionProfesionalesRead,
    MovimientoCajaRead,
    PresupuestoCobroRead,
    PresentacionObraSocialRead,
    PrestacionesDisponiblesRead,
    ResumenCajaRead,
)
from app.services import (
    facturacion_service,
    liquidacion_service,
    reporte_pdf_service,
)

# Sin prefijo propio: las rutas de facturación cuelgan del turno
# (``/turnos/…``), la caja vive en ``/movimientos-caja`` y las liquidaciones en
# ``/liquidaciones/…``.
router = APIRouter(tags=["Facturación y caja"])


def _respuesta_pdf(contenido: bytes, nombre_archivo: str) -> Response:
    """Devuelve el PDF como descarga (nombre de archivo en ASCII, sin acentos)."""
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )


def _rango(desde: Optional[Date], hasta: Optional[Date]):
    """Traduce dos fechas ``AAAA-MM-DD`` al rango de instantes UTC naive.

    Se usan fechas (y no *datetime*) porque la caja diaria y las liquidaciones se
    consultan por día calendario. Si no se informan, el rango es el **día de hoy**
    según el reloj del servidor.
    """
    hoy = utcnow().date()
    inicio = rango_del_dia(desde or hoy)[0]
    fin = rango_del_dia(hasta or desde or hoy)[1]
    return inicio, fin


@router.post(
    "/turnos/{turno_id}/facturar",
    response_model=MovimientoCajaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cerrar la visita y facturarla",
    description=(
        "Factura el turno con las prácticas realizadas: calcula cuánto paga la "
        "obra social del paciente y cuánto se cobra de coseguro en el mostrador, y "
        "guarda el movimiento de caja. Un turno se factura **una sola vez** "
        "(**409** si ya tiene movimiento) y un turno cancelado no se puede facturar "
        "(**422**). Si el paciente tiene cobertura pero falta el arancel de una "
        "práctica, responde **422** salvo que se envíe "
        "``permitir_particular: true`` (se cobra a valor de referencia)."
    ),
)
def facturar_turno(
    turno_id: int, payload: FacturarTurnoRequest, db: Session = Depends(get_db)
) -> MovimientoCaja:
    return facturacion_service.facturar_turno(db, turno_id, payload)


@router.post(
    "/turnos/{turno_id}/presupuesto",
    response_model=PresupuestoCobroRead,
    summary="Calcular el coseguro antes de cobrar",
    description=(
        "Devuelve el **cartel del mostrador** (cuánto se cobra en efectivo y "
        "cuánto se presenta a la obra social) usando el mismo cálculo que la "
        "facturación, pero **sin escribir** en la base. El formulario de cobro lo "
        "usa para mostrar el importe mientras la secretaría elige las prácticas."
    ),
)
def presupuestar_turno(
    turno_id: int, payload: FacturarTurnoRequest, db: Session = Depends(get_db)
) -> PresupuestoCobroRead:
    return facturacion_service.presupuestar(db, turno_id, payload)


@router.get(
    "/turnos/{turno_id}/prestaciones-disponibles",
    response_model=PrestacionesDisponiblesRead,
    summary="Prácticas facturables de un turno",
    description=(
        "Prácticas activas con el arancel resuelto para la cobertura del paciente "
        "del turno (lo que paga la OS y el coseguro), el estado de facturación del "
        "turno y los datos del paciente. Es la ayuda de carga del formulario de "
        "cobro."
    ),
)
def prestaciones_disponibles(
    turno_id: int, db: Session = Depends(get_db)
) -> PrestacionesDisponiblesRead:
    return facturacion_service.prestaciones_disponibles(db, turno_id)


@router.get(
    "/turnos/{turno_id}/facturacion",
    response_model=FacturacionTurnoRead,
    summary="Estado de facturación de un turno",
    description=(
        "Indica si el turno ya tiene un cierre de caja y, en ese caso, devuelve el "
        "movimiento completo (prácticas, importes y estado del cobro)."
    ),
)
def facturacion_del_turno(
    turno_id: int, db: Session = Depends(get_db)
) -> FacturacionTurnoRead:
    movimiento = facturacion_service.movimiento_de_turno(db, turno_id)
    return FacturacionTurnoRead(
        turno_id=turno_id,
        facturado=movimiento is not None,
        movimiento=(
            MovimientoCajaRead.model_validate(movimiento)
            if movimiento is not None
            else None
        ),
    )


@router.get(
    "/movimientos-caja/resumen",
    response_model=ResumenCajaRead,
    summary="Totales de la caja del día",
    description=(
        "Totales del rango consultado (por defecto, **hoy**): lo cobrado en "
        "efectivo, lo que sigue pendiente de la obra social, lo acreditado y lo "
        "debitado. Es el encabezado de la *Caja del día* del panel."
    ),
)
def resumen_caja(
    desde: Optional[Date] = Query(None, description="Primer día (AAAA-MM-DD)."),
    hasta: Optional[Date] = Query(None, description="Último día (AAAA-MM-DD)."),
    obra_social_id: Optional[int] = Query(None, gt=0),
    doctor_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
) -> ResumenCajaRead:
    inicio, fin = _rango(desde, hasta)
    return facturacion_service.resumen_caja(
        db, inicio, fin, obra_social_id=obra_social_id, doctor_id=doctor_id
    )


@router.get(
    "/movimientos-caja",
    response_model=List[MovimientoCajaRead],
    summary="Listar movimientos de caja",
    description=(
        "Lista los cierres de visita (de la más nueva a la más vieja) con los "
        "filtros acumulativos de la caja: rango de días (``desde``/``hasta``), "
        "cobertura (``obra_social_id``), profesional (``doctor_id``), estado del "
        "cobro (``estado_pago``) y turno (``turno_id``)."
    ),
)
def listar_movimientos(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    desde: Optional[Date] = Query(None, description="Primer día (AAAA-MM-DD)."),
    hasta: Optional[Date] = Query(None, description="Último día (AAAA-MM-DD)."),
    obra_social_id: Optional[int] = Query(None, gt=0),
    doctor_id: Optional[int] = Query(None, gt=0),
    estado_pago: Optional[EstadoPagoMovimiento] = Query(
        None, description="PENDIENTE, EN_PROCESO_LIQUIDACION, COBRADO o DEBITADO_OS."
    ),
    turno_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
) -> List[MovimientoCaja]:
    inicio: Optional[datetime] = rango_del_dia(desde)[0] if desde else None
    fin: Optional[datetime] = rango_del_dia(hasta)[1] if hasta else None
    return list(
        facturacion_service.listar_movimientos(
            db,
            skip=skip,
            limit=limit,
            desde=inicio,
            hasta=fin,
            obra_social_id=obra_social_id,
            doctor_id=doctor_id,
            estado_pago=estado_pago,
            turno_id=turno_id,
        )
    )


@router.get(
    "/movimientos-caja/{movimiento_id}",
    response_model=MovimientoCajaRead,
    summary="Obtener un movimiento de caja",
    description="Devuelve el cierre de una visita con sus prácticas (``404`` si no existe).",
)
def obtener_movimiento(
    movimiento_id: int, db: Session = Depends(get_db)
) -> MovimientoCaja:
    return facturacion_service.obtener_movimiento(db, movimiento_id)


@router.post(
    "/movimientos-caja/{movimiento_id}/cobrar",
    response_model=MovimientoCajaRead,
    summary="Marcar el cobro de la obra social",
    description=(
        "Cierra la orden como **cobrada** cuando entra el depósito. Se puede "
        "informar el importe realmente acreditado (``monto_cobrado_os``, útil para "
        "registrar diferencias) y una observación. Es idempotente; un movimiento "
        "debitado no se puede cobrar (**409**)."
    ),
)
def cobrar_movimiento(
    movimiento_id: int,
    payload: CobrarMovimientoRequest,
    db: Session = Depends(get_db),
) -> MovimientoCaja:
    return facturacion_service.cobrar_movimiento(db, movimiento_id, payload)


@router.post(
    "/movimientos-caja/{movimiento_id}/debitar",
    response_model=MovimientoCajaRead,
    summary="Marcar el débito de la obra social",
    description=(
        "Cierra la orden como **debitada** (importe que el consultorio no va a "
        "cobrar) con el **motivo obligatorio**. Si la OS pagó una parte, se "
        "informa ``monto_cobrado_os`` (débito parcial). Un movimiento ya cobrado "
        "no se puede debitar (**409**)."
    ),
)
def debitar_movimiento(
    movimiento_id: int,
    payload: DebitarMovimientoRequest,
    db: Session = Depends(get_db),
) -> MovimientoCaja:
    return facturacion_service.debitar_movimiento(db, movimiento_id, payload)


@router.get(
    "/liquidaciones/obras-sociales",
    response_model=PresentacionObraSocialRead,
    summary="Presentación del período de una obra social",
    description=(
        "Devuelve el período completo de la cobertura (``obra_social_id``, "
        "``anio``, ``mes``) con el estado de cada orden: las ``PENDIENTE`` todavía "
        "no se presentaron, las ``EN_PROCESO_LIQUIDACION`` esperan el depósito y "
        "las ``COBRADO``/``DEBITADO_OS`` ya se cerraron. Incluye el número de "
        "presentación (``PRES-OS3-202611``) y los totales para exportar. Es de "
        "**sólo lectura**: no cambia ningún estado."
    ),
)
def presentacion_obra_social(
    obra_social_id: int = Query(..., gt=0),
    anio: int = Query(..., ge=2000, le=2100),
    mes: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> PresentacionObraSocialRead:
    return facturacion_service.presentacion_del_periodo(db, obra_social_id, anio, mes)


@router.post(
    "/liquidaciones/obras-sociales",
    response_model=PresentacionObraSocialRead,
    summary="Generar la presentación del período",
    description=(
        "**Cierra el período** de la cobertura: toma los movimientos "
        "``PENDIENTE`` del mes, los pasa a ``EN_PROCESO_LIQUIDACION`` (todos en "
        "una transacción) y devuelve la presentación lista para exportar y "
        "presentar ante la prepaga. Si no hay nada pendiente responde **422**."
    ),
)
def generar_presentacion_obra_social(
    payload: LiquidacionObraSocialRequest, db: Session = Depends(get_db)
) -> PresentacionObraSocialRead:
    return facturacion_service.generar_presentacion(
        db, payload.obra_social_id, payload.anio, payload.mes
    )


@router.get(
    "/liquidaciones/profesionales",
    response_model=LiquidacionProfesionalesRead,
    summary="Liquidación de profesionales del mes",
    description=(
        "Reporte de producción del mes por odontólogo: cuánto produjo (coseguros "
        "cobrados + lo presentado a las obras sociales) y cuánto le corresponde "
        "cobrar según su ``tipo_pago`` (dueño, comisión o sueldo). Con "
        "``doctor_id`` se liquida a uno solo; sin él, a todos los activos."
    ),
)
def liquidacion_profesionales(
    anio: int = Query(..., ge=2000, le=2100),
    mes: int = Query(..., ge=1, le=12),
    doctor_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
) -> LiquidacionProfesionalesRead:
    return liquidacion_service.liquidar_profesionales(
        db, anio, mes, doctor_id=doctor_id
    )


# --------------------------------------------------------------------------- #
# Reportes en PDF (mismos datos que los JSON anteriores, listos para imprimir)
# --------------------------------------------------------------------------- #
@router.get(
    "/liquidaciones/obras-sociales/pdf",
    summary="Presentación del período en PDF",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Presentación lista para imprimir o adjuntar.",
        }
    },
    description=(
        "Misma información que ``GET /liquidaciones/obras-sociales`` pero en un "
        "**archivo PDF** para entregar a la cobertura: las órdenes del período "
        "con su estado y los totales (presentado, coseguros, depositado, "
        "debitado y saldo pendiente). Se descarga como adjunto "
        "(``Content-Disposition``); una cobertura inexistente responde **404**."
    ),
)
def presentacion_obra_social_pdf(
    obra_social_id: int = Query(..., gt=0),
    anio: int = Query(..., ge=2000, le=2100),
    mes: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> Response:
    presentacion = facturacion_service.presentacion_del_periodo(
        db, obra_social_id, anio, mes
    )
    return _respuesta_pdf(
        reporte_pdf_service.pdf_presentacion_obra_social(presentacion),
        reporte_pdf_service.nombre_archivo_presentacion(presentacion),
    )


@router.get(
    "/liquidaciones/profesionales/pdf",
    summary="Liquidación de profesionales en PDF",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Liquidación del mes lista para imprimir o adjuntar.",
        }
    },
    description=(
        "Misma información que ``GET /liquidaciones/profesionales`` pero en un "
        "**archivo PDF**: la producción del mes por profesional, sus honorarios, "
        "los totales del consultorio y el detalle de cómo se calculó cada "
        "liquidación. Se descarga como adjunto (``Content-Disposition``)."
    ),
)
def liquidacion_profesionales_pdf(
    anio: int = Query(..., ge=2000, le=2100),
    mes: int = Query(..., ge=1, le=12),
    doctor_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
) -> Response:
    liquidacion = liquidacion_service.liquidar_profesionales(
        db, anio, mes, doctor_id=doctor_id
    )
    return _respuesta_pdf(
        reporte_pdf_service.pdf_liquidacion_profesionales(liquidacion),
        reporte_pdf_service.nombre_archivo_liquidacion(liquidacion),
    )
