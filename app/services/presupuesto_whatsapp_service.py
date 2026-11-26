"""Envío del presupuesto en PDF por WhatsApp.

El panel de la secretaría tiene un botón «WhatsApp» en cada presupuesto
guardado. Antes de mandarlo abre un modal con el **teléfono de la ficha** para
que se pueda corregir (un número viejo, un celular nuevo) y ofrece guardar el
número corregido en el paciente.

El envío tiene dos partes:

1. el **PDF** que ya genera :mod:`app.services.presupuesto_pdf_service`, que
   viaja adjunto con ``POST {WAHA_URL}/api/sendFile``;
2. un **resumen** en el pie del mensaje (fecha, cantidad de trabajos, total,
   entregas, saldo y forma de pago) que se arma con los importes del
   presupuesto, sin volver a consultar la base.

Si faltan ``WAHA_URL``/``WAHA_API_KEY`` el envío se **simula** (queda en el log
con un id ``waha.SIMULADO...``) y la respuesta lo informa con ``simulado``.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ValidacionError
from app.core.money import redondear_monto, sumar_montos
from app.core.whatsapp_utils import MESES, normalizar_telefono_waha
from app.models.presupuesto import Presupuesto
from app.schemas.presupuesto import (
    PresupuestoEnviadoWhatsApp,
    PresupuestoEnvioWhatsApp,
)
from app.services import (
    presupuesto_pdf_service,
    presupuesto_service,
    whatsapp_service,
)

logger = logging.getLogger("app.services.presupuesto_whatsapp")

#: Mínimo de dígitos razonable para un celular (``549`` + área + abonado).
MIN_DIGITOS_TELEFONO = 10


def _pesos(valor: Optional[float]) -> str:
    """Formatea un importe como ``$830.000`` o ``$1.250,50`` (estilo es-AR)."""
    numero = float(valor or 0)
    if abs(numero - round(numero)) < 0.005:
        return "$" + f"{round(numero):,}".replace(",", ".")
    entero, decimales = f"{numero:,.2f}".split(".")
    return "$" + entero.replace(",", ".") + "," + decimales


def _mes_en_letras(fecha) -> str:
    """Devuelve ``septiembre de 2026`` para la fecha del presupuesto."""
    if not fecha:
        return "sin fecha"
    return f"{MESES[fecha.month - 1]} de {fecha.year}"


def construir_mensaje(presupuesto: Presupuesto) -> str:
    """Texto que acompaña al PDF con el resumen del presupuesto.

    Los totales se suman con la misma precisión decimal que usa la API para que
    el mensaje coincida con el pie del documento.
    """
    items = list(presupuesto.items or [])
    total = sumar_montos(item.precio for item in items)
    entregas = sumar_montos(item.entrega for item in items)
    saldo = redondear_monto(total - entregas)
    nombre = (presupuesto.paciente.nombre or "").strip() or "paciente"

    lineas = [
        f"Hola {nombre}, te enviamos el presupuesto del consultorio.",
        "",
        f"Fecha: {_mes_en_letras(presupuesto.fecha)}",
        f"Trabajos: {len(items)}",
        f"Total: {_pesos(total)}",
    ]
    if entregas:
        lineas.append(f"Entregas: {_pesos(entregas)}")
        lineas.append(f"Saldo: {_pesos(saldo)}")
    lineas.extend(
        [
            f"Forma de pago: {presupuesto.forma_pago}",
            "",
            "Cualquier duda, respondé este mensaje.",
        ]
    )
    return "\n".join(lineas)


def _resolver_telefono(
    presupuesto: Presupuesto, telefono: Optional[str]
) -> str:
    """Normaliza el número elegido y valida que sirva para WhatsApp.

    Se usa el número del formulario (el que la secretaría revisó) y, si viene
    vacío, el de la ficha del paciente.
    """
    crudo = (telefono or "").strip() or (presupuesto.paciente.telefono or "")
    normalizado = normalizar_telefono_waha(crudo)
    if len(normalizado) < MIN_DIGITOS_TELEFONO:
        raise ValidacionError(
            "El número de celular no es válido: ingresá el código de área y el "
            "número (por ejemplo, 351 643-2863)."
        )
    return normalizado


def enviar_presupuesto(
    db: Session,
    presupuesto_id: int,
    data: PresupuestoEnvioWhatsApp,
) -> PresupuestoEnviadoWhatsApp:
    """Genera el PDF del presupuesto y lo envía por WhatsApp.

    Un presupuesto inexistente responde **404**, un número sin dígitos
    utilizables **422** y una falla de WAHA **502** (``WhatsAppEnvioError``).
    El teléfono corregido se guarda en la ficha **después** de un envío
    exitoso, y sólo si ``actualizar_telefono`` está activo.
    """
    presupuesto = presupuesto_service.obtener_presupuesto_orm(
        db, presupuesto_id
    )
    telefono = _resolver_telefono(presupuesto, data.telefono)
    mensaje = construir_mensaje(presupuesto)

    contenido, nombre_archivo = presupuesto_pdf_service.generar_pdf(
        db, presupuesto_id
    )
    mensaje_id = whatsapp_service.enviar_archivo_whatsapp(
        telefono,
        contenido,
        nombre_archivo,
        caption=mensaje,
    )

    actualizado = False
    paciente = presupuesto.paciente
    if data.actualizar_telefono and normalizar_telefono_waha(
        paciente.telefono
    ) != telefono:
        paciente.telefono = telefono
        db.commit()
        actualizado = True

    logger.info(
        "Presupuesto %s enviado por WhatsApp a %s (mensaje_id=%s, simulado=%s).",
        presupuesto_id,
        telefono,
        mensaje_id,
        not settings.whatsapp_envio_real,
    )
    return PresupuestoEnviadoWhatsApp(
        presupuesto_id=presupuesto_id,
        telefono=telefono,
        nombre_archivo=nombre_archivo,
        mensaje_id=mensaje_id,
        simulado=not settings.whatsapp_envio_real,
        actualizado_telefono=actualizado,
    )
