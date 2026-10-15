"""Reportes PDF del Módulo 3 (obra social y profesionales).

El panel ya exporta CSV para trabajar en Excel; estos PDF son los que se
**imprimen o se adjuntan**: la presentación que se le entrega a la cobertura y
la liquidación que se le da al profesional.

Se arman con :mod:`app.core.pdf` (sin dependencias externas) sobre los mismos
objetos que devuelven :mod:`app.services.facturacion_service` y
:mod:`app.services.liquidacion_service`, así que **el reporte y la pantalla
muestran siempre los mismos números**: acá no se consulta la base ni se
recalcula nada.
"""

from datetime import datetime
from typing import List, Optional, Sequence

from app.core.pdf import Columna, DocumentoPDF, fecha_legible, nombre_archivo
from app.models.enums import EstadoPagoMovimiento
from app.schemas.facturacion import (
    LiquidacionProfesionalesRead,
    MovimientoCajaRead,
    PresentacionObraSocialRead,
)

MESES = (
    "",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

# Mismos textos que muestra el panel (app/static/app.js).
_ESTADO_TEXTO = {
    EstadoPagoMovimiento.PENDIENTE.value: "Sin presentar",
    EstadoPagoMovimiento.EN_PROCESO_LIQUIDACION.value: "Presentada",
    EstadoPagoMovimiento.COBRADO.value: "Cobrada",
    EstadoPagoMovimiento.DEBITADO_OS.value: "Debitada",
}

_TIPO_PAGO_TEXTO = {"DUENO": "Dueño", "COMISION": "Comisión", "SUELDO": "Sueldo fijo"}

# Columnas de cada reporte. Los anchos están elegidos para que los datos que no
# se pueden cortar (fecha, DNI, importes, estado) entren en **una sola línea** en
# A4 vertical: el texto libre (nombre del paciente, observaciones) sí envuelve.
COLUMNAS_PRESENTACION = (
    Columna("Fecha", 1.1),
    Columna("Paciente", 1.75),
    Columna("DNI", 1.1),
    Columna("Prácticas", 1.25),
    Columna("Presentado", 1.35, "derecha"),
    Columna("Efectivo", 1.25, "derecha"),
    Columna("Estado", 1.4),
    Columna("Observaciones", 1.45),
)

COLUMNAS_LIQUIDACION = (
    Columna("Profesional", 2.2),
    Columna("Especialidad", 1.6),
    Columna("Pago", 1.0),
    Columna("Valor", 1.0, "derecha"),
    Columna("Atenc.", 0.85, "derecha"),
    Columna("Efectivo", 1.3, "derecha"),
    Columna("Obra social", 1.35, "derecha"),
    Columna("Producción", 1.3, "derecha"),
    Columna("Honorarios", 1.3, "derecha"),
)


def _texto_enum(valor: object) -> str:
    """Nombre del valor de un enum (acepta también el texto suelto)."""
    return str(getattr(valor, "value", valor) or "")


def _moneda(valor: object) -> str:
    """Importe en el formato del mostrador: ``$ 15.000,00``."""
    try:
        numero = f"{float(valor or 0):,.2f}"  # 15,000.00
    except (TypeError, ValueError):
        numero = "0.00"
    return "$ " + numero.replace(",", "@").replace(".", ",").replace("@", ".")


def _periodo_texto(anio: int, mes: int) -> str:
    """Período del reporte: ``noviembre 2026``."""
    nombre = MESES[mes] if 1 <= mes <= 12 else str(mes)
    return f"{nombre} {anio}".capitalize()


def _practicas(movimiento: MovimientoCajaRead) -> str:
    """Resumen de las prácticas del movimiento: ``1x 20100 · 2x 21600``."""
    return " · ".join(
        f"{detalle.cantidad}x {detalle.codigo_nomenclador}"
        for detalle in movimiento.prestaciones
    )


def _pie(instante: datetime) -> str:
    """Nota al pie de todos los reportes, con la fecha de emisión."""
    return (
        "Sistema Odontológico · emitido el "
        f"{instante.strftime('%d/%m/%Y')} a las {instante.strftime('%H:%M')}"
    )


def _resumen(pdf: DocumentoPDF, pares: Sequence[Sequence[str]]) -> None:
    """Ficha de datos del reporte (etiqueta / valor) en dos columnas."""
    pdf.tabla(
        [Columna("Dato", 1), Columna("Valor", 2.4)],
        pares,
        tamano=9.5,
        encabezado=False,
        cebra=True,
    )



# --------------------------------------------------------------------------- #
# Presentación a la obra social
# --------------------------------------------------------------------------- #
def nombre_archivo_presentacion(presentacion: PresentacionObraSocialRead) -> str:
    """Nombre del PDF (``presentacion-PRES-OS1-202611.pdf``)."""
    return nombre_archivo("presentacion", presentacion.numero_presentacion) + ".pdf"


def pdf_presentacion_obra_social(
    presentacion: PresentacionObraSocialRead, emitido: Optional[datetime] = None
) -> bytes:
    """PDF de la presentación: las órdenes del período y los totales a cobrar."""
    momento = emitido or datetime.now()
    pdf = DocumentoPDF(
        "Presentación a la obra social",
        f"{presentacion.obra_social_nombre} · "
        f"{_periodo_texto(presentacion.anio, presentacion.mes)}",
        _pie(momento),
    )
    _resumen(
        pdf,
        [
            ["Presentación", presentacion.numero_presentacion],
            ["Cobertura", presentacion.obra_social_nombre],
            ["Código", presentacion.obra_social_codigo or "-"],
            [
                "Período",
                f"{_periodo_texto(presentacion.anio, presentacion.mes)} "
                f"({presentacion.periodo_desde:%d/%m/%Y} al "
                f"{presentacion.periodo_hasta:%d/%m/%Y})",
            ],
            ["Emitida", "Sí" if presentacion.generada else "No (borrador)"],
            ["Órdenes", str(presentacion.cantidad_movimientos)],
            ["Emitido el", momento.strftime("%d/%m/%Y %H:%M")],
        ],
    )
    pdf.espacio(10)

    columnas = list(COLUMNAS_PRESENTACION)
    filas = [
        [
            fecha_legible(movimiento.turno_fecha_hora_inicio or movimiento.fecha),
            movimiento.paciente_nombre or "-",
            movimiento.paciente_dni or "-",
            _practicas(movimiento) or "-",
            _moneda(movimiento.monto_pendiente_os),
            _moneda(movimiento.monto_efectivo),
            _ESTADO_TEXTO.get(
                _texto_enum(movimiento.estado_pago),
                _texto_enum(movimiento.estado_pago),
            ),
            movimiento.observaciones or "",
        ]
        for movimiento in presentacion.movimientos
    ]
    if not filas:
        pdf.parrafo(
            "No hay órdenes de esta cobertura en el período seleccionado.", tamano=10
        )
    else:
        pdf.tabla(columnas, filas, tamano=8.5)
    pdf.totales(
        [
            ("Total presentado a la cobertura", _moneda(presentacion.total_presentado)),
            (
                "Coseguros cobrados en el mostrador",
                _moneda(presentacion.total_efectivo),
            ),
            ("Ya depositado por la cobertura", _moneda(presentacion.total_cobrado_os)),
            ("Debitado (no se cobra)", _moneda(presentacion.total_debitado_os)),
            (
                "Saldo pendiente de acreditación",
                _moneda(presentacion.total_pendiente_os),
            ),
        ]
    )
    return pdf.generar()


# --------------------------------------------------------------------------- #
# Liquidación de profesionales
# --------------------------------------------------------------------------- #
def nombre_archivo_liquidacion(liquidacion: LiquidacionProfesionalesRead) -> str:
    """Nombre del PDF (``liquidacion-profesionales-2026-11.pdf``)."""
    return (
        nombre_archivo(
            "liquidacion-profesionales", f"{liquidacion.anio}-{liquidacion.mes:02d}"
        )
        + ".pdf"
    )


def pdf_liquidacion_profesionales(
    liquidacion: LiquidacionProfesionalesRead, emitido: Optional[datetime] = None
) -> bytes:
    """PDF de la liquidación mensual: producción y honorarios por profesional."""
    momento = emitido or datetime.now()
    pdf = DocumentoPDF(
        "Liquidación de profesionales",
        _periodo_texto(liquidacion.anio, liquidacion.mes),
        _pie(momento),
    )
    _resumen(
        pdf,
        [
            ["Período", _periodo_texto(liquidacion.anio, liquidacion.mes)],
            [
                "Producción del",
                f"{liquidacion.periodo_desde:%d/%m/%Y} al "
                f"{liquidacion.periodo_hasta:%d/%m/%Y}",
            ],
            ["Profesionales liquidados", str(len(liquidacion.doctores))],
            ["Emitido el", momento.strftime("%d/%m/%Y %H:%M")],
        ],
    )
    pdf.espacio(10)

    columnas = list(COLUMNAS_LIQUIDACION)
    filas: List[List[str]] = []
    for doctor in liquidacion.doctores:
        tipo = _texto_enum(doctor.tipo_pago)
        valor = ""
        if doctor.valor_pago is not None:
            valor = (
                _moneda(doctor.valor_pago)
                if tipo == "SUELDO"
                else f"{doctor.valor_pago:g}%"
            )
        filas.append(
            [
                doctor.doctor_nombre + ("" if doctor.activo else " (dado de baja)"),
                doctor.doctor_especialidad,
                _TIPO_PAGO_TEXTO.get(tipo, tipo),
                valor,
                str(doctor.cantidad_turnos),
                _moneda(doctor.produccion_efectivo),
                _moneda(doctor.produccion_os),
                _moneda(doctor.produccion_total),
                _moneda(doctor.honorarios),
            ]
        )
    if not filas:
        pdf.parrafo("No hay producción cargada en el período seleccionado.", tamano=10)
    else:
        pdf.tabla(columnas, filas, tamano=8.5)

    pdf.totales(
        [
            ("Producción en efectivo (coseguros)", _moneda(liquidacion.total_efectivo)),
            ("Presentado a las obras sociales", _moneda(liquidacion.total_os)),
            ("Producción total del consultorio", _moneda(liquidacion.total_produccion)),
            ("Honorarios a liquidar", _moneda(liquidacion.total_honorarios)),
        ]
    )

    if liquidacion.doctores:
        pdf.seccion("Cómo se calculó cada liquidación")
        for doctor in liquidacion.doctores:
            detalle = doctor.detalle_honorarios or "Sin detalle del cálculo."
            pdf.parrafo(
                f"{doctor.doctor_nombre}: {detalle} "
                f"(honorarios {_moneda(doctor.honorarios)}).",
                tamano=8.5,
            )
    return pdf.generar()
