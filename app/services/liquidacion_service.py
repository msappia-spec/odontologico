"""Servicio de liquidación de profesionales (Módulo 3).

Calcula, para un mes, cuánto le corresponde cobrar a cada odontólogo según su
``tipo_pago`` y la producción realizada:

* **DUENO**: un porcentaje de su producción (``valor_pago``; sin valor = 100 %).
* **COMISION**: un porcentaje de su producción (``valor_pago`` obligatorio, 0-100).
* **SUELDO**: un importe fijo por mes (``valor_pago``), independiente de la
  producción (que igual se informa en el reporte).

La *producción* de un profesional son sus movimientos de caja del período:
``produccion_efectivo`` (coseguros cobrados en el mostrador) más
``produccion_os`` (lo presentado a las obras sociales). Se liquida la atención
**facturada**, no el estado de cobro de la cobertura: una orden todavía
``PENDIENTE`` ya es producción del odontólogo y la deuda con la prepaga es del
consultorio.
"""

import logging
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.money import porcentaje_de, redondear_monto, sumar_montos
from app.models.doctor import Doctor
from app.models.enums import TipoPagoDoctor
from app.schemas.facturacion import (
    LiquidacionProfesionalDoctorRead,
    LiquidacionProfesionalesRead,
    MovimientoCajaRead,
)
from app.services import doctor_service, facturacion_service

logger = logging.getLogger("app.services.liquidaciones")

# Descripción del cálculo cuando el porcentaje no está configurado.
_PORCENTAJE_COMPLETO = 100.0


def _honorarios(doctor: Doctor, produccion_total: float) -> Tuple[float, str]:
    """Devuelve ``(honorarios, cómo se calculó)`` para el mes liquidado.

    * ``SUELDO``: el fijo de ``valor_pago`` (0 si todavía no se configuró), sin
      importar la producción.
    * ``COMISION``: ``valor_pago`` % de la producción (0 si falta configurar).
    * ``DUENO``: ``valor_pago`` % de la producción, o el **100 %** si no se
      informó (el dueño se queda con toda su producción).
    """
    tipo = doctor.tipo_pago
    valor = doctor.valor_pago

    if tipo == TipoPagoDoctor.SUELDO:
        monto = redondear_monto(valor or 0)
        if valor is None:
            detalle = "Sueldo fijo mensual (sin valor configurado: $0)"
        else:
            detalle = f"Sueldo fijo mensual de {monto:.2f}"
        return monto, detalle

    porcentaje = (
        _PORCENTAJE_COMPLETO if valor is None else float(valor)
    )
    monto = porcentaje_de(produccion_total, porcentaje)
    if tipo == TipoPagoDoctor.DUENO:
        detalle = (
            f"{porcentaje:g}% de la producción (dueño del consultorio)"
            if valor is not None
            else "100% de la producción (dueño del consultorio)"
        )
    else:
        detalle = (
            f"{porcentaje:g}% de la producción (comisión)"
            if valor is not None
            else "Comisión sin valor configurado: $0"
        )
    return monto, detalle


def liquidar_profesionales(
    db: Session,
    anio: int,
    mes: int,
    doctor_id: Optional[int] = None,
) -> LiquidacionProfesionalesRead:
    """Reporte de producción y honorarios del mes (para todos o para uno).

    :param doctor_id: si se informa, se liquida sólo a ese profesional (``404``
        si no existe); si se omite, se incluye a **todos los activos** de modo
        que el reporte mensual muestre el consultorio completo.
    """
    desde, hasta = facturacion_service.periodo_mensual(anio, mes)

    if doctor_id is not None:
        doctores: List[Doctor] = [doctor_service.obtener_doctor(db, doctor_id)]
    else:
        doctores = list(doctor_service.listar_doctores(db, activos=True))

    movimientos = facturacion_service.listar_movimientos(
        db, desde=desde, hasta=hasta, doctor_id=doctor_id
    )

    liquidaciones: List[LiquidacionProfesionalDoctorRead] = []
    for doctor in doctores:
        propios = [m for m in movimientos if m.doctor_id == doctor.id]
        produccion_efectivo = sumar_montos(m.monto_efectivo for m in propios)
        produccion_os = sumar_montos(m.monto_pendiente_os for m in propios)
        produccion_total = sumar_montos([produccion_efectivo, produccion_os])
        honorarios, detalle = _honorarios(doctor, produccion_total)

        liquidaciones.append(
            LiquidacionProfesionalDoctorRead(
                doctor_id=doctor.id,
                doctor_nombre=doctor.nombre,
                doctor_especialidad=doctor.especialidad,
                activo=doctor.activo,
                tipo_pago=doctor.tipo_pago,
                valor_pago=doctor.valor_pago,
                cantidad_movimientos=len(propios),
                cantidad_turnos=len({m.turno_id for m in propios}),
                produccion_efectivo=produccion_efectivo,
                produccion_os=produccion_os,
                produccion_total=produccion_total,
                honorarios=honorarios,
                detalle_honorarios=detalle,
                movimientos=[MovimientoCajaRead.model_validate(m) for m in propios],
            )
        )

    logger.info(
        "Liquidación %02d/%s: %s profesional(es), producción total %.2f",
        mes,
        anio,
        len(liquidaciones),
        sumar_montos(item.produccion_total for item in liquidaciones),
    )

    return LiquidacionProfesionalesRead(
        anio=anio,
        mes=mes,
        periodo_desde=desde,
        periodo_hasta=hasta,
        total_efectivo=sumar_montos(item.produccion_efectivo for item in liquidaciones),
        total_os=sumar_montos(item.produccion_os for item in liquidaciones),
        total_produccion=sumar_montos(
            item.produccion_total for item in liquidaciones
        ),
        total_honorarios=sumar_montos(item.honorarios for item in liquidaciones),
        doctores=liquidaciones,
    )
