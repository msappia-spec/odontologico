"""Servicio de WhatsApp: envío de recordatorios y procesamiento del webhook.

Contiene dos responsabilidades claramente separadas:

1. **Envío saliente** de recordatorios (tarea programada), que se puede
   *simular* o *ejecutar* contra la WhatsApp Cloud API según configuración.
2. **Procesamiento entrante** del webhook con la respuesta de botón del
   paciente, que actualiza el estado del turno.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.datetime_utils import rango_del_dia, to_naive_utc, utcnow
from app.core.exceptions import (
    RespuestaWebhookInvalidaError,
    TurnoNoEncontradoError,
    WhatsAppEnvioError,
)
from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.turno import Turno
from app.schemas.whatsapp import (
    RecordatorioEnviado,
    RecordatorioPendiente,
    RecordatorioResultado,
    WhatsAppButtonReply,
    WhatsAppInboundMessage,
    WhatsAppWebhookPayload,
)

logger = logging.getLogger("app.services.whatsapp")

# Mapeo "id (o título) del botón" -> nuevo estado del turno.
BOTONES_ESTADO: dict[str, EstadoTurno] = {
    "confirmar_turno": EstadoTurno.CONFIRMADO,
    "confirmar": EstadoTurno.CONFIRMADO,
    "cancelar_turno": EstadoTurno.CANCELADO,
    "cancelar": EstadoTurno.CANCELADO,
}


# ===========================================================================
#  1) ENVÍO SALIENTE DE RECORDATORIOS
# ===========================================================================


def construir_payload_recordatorio(turno: Turno) -> dict:
    """Arma el cuerpo JSON del mensaje interactivo (botones Confirmar/Cancelar)."""
    nombre = turno.paciente.nombre if turno.paciente else "paciente"
    doctor = turno.doctor.nombre if turno.doctor else "su profesional"
    inicio = turno.fecha_hora_inicio.strftime("%d/%m/%Y %H:%M")

    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": turno.paciente.telefono if turno.paciente else "",
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": (
                    f"Hola {nombre}, le recordamos su turno con {doctor} "
                    f"el {inicio}. ¿Confirma su asistencia?"
                )
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": "confirmar_turno", "title": "Confirmar"},
                    },
                    {
                        "type": "reply",
                        "reply": {"id": "cancelar_turno", "title": "Cancelar"},
                    },
                ]
            },
        },
    }


def _enviar_http_whatsapp(payload: dict) -> str:
    """Envía el mensaje a la WhatsApp Cloud API y devuelve el ``wamid``."""
    url = (
        f"{settings.whatsapp_api_url.rstrip('/')}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    try:
        respuesta = httpx.post(url, json=payload, headers=headers, timeout=10.0)
        respuesta.raise_for_status()
        data = respuesta.json()
        return data["messages"][0]["id"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise WhatsAppEnvioError(f"Fallo el envío a WhatsApp: {exc}") from exc


def _simular_http_whatsapp(payload: dict) -> str:
    """Simula un ``POST`` HTTP a WhatsApp y devuelve un ``wamid`` ficticio."""
    mensaje_id = f"wamid.SIMULADO.{uuid.uuid4().hex}"
    logger.info(
        "SIMULACION HTTP POST WhatsApp -> to=%s | type=%s | message_id=%s",
        payload.get("to"),
        payload.get("type"),
        mensaje_id,
    )
    return mensaje_id


def enviar_mensaje_recordatorio(turno: Turno) -> str:
    """Envía (o simula) el recordatorio de un turno y devuelve el id del mensaje."""
    telefono = turno.paciente.telefono if turno.paciente else "SIN_TELEFONO"
    logger.info("Enviando WhatsApp a %s", telefono)

    payload = construir_payload_recordatorio(turno)
    if settings.whatsapp_envio_real:
        return _enviar_http_whatsapp(payload)
    return _simular_http_whatsapp(payload)


def _procesar_envio_turno(db: Session, turno: Turno) -> RecordatorioEnviado:
    """Envía el recordatorio de un turno y persiste el resultado del envío."""
    telefono = turno.paciente.telefono if turno.paciente else ""
    doctor = turno.doctor.nombre if turno.doctor else ""

    try:
        mensaje_id = enviar_mensaje_recordatorio(turno)
    except Exception as exc:  # noqa: BLE001 - registramos cualquier fallo del envío
        logger.exception("Fallo el envío del recordatorio del turno id=%s", turno.id)
        turno.estado_whatsapp = EstadoWhatsApp.FALLIDO
        turno.whatsapp_mensaje_id = None
        db.commit()
        return RecordatorioEnviado(
            turno_id=turno.id,
            telefono=telefono,
            doctor=doctor,
            fecha_hora_inicio=turno.fecha_hora_inicio.isoformat(),
            resultado="FALLIDO",
            error=str(exc),
        )

    turno.estado_whatsapp = EstadoWhatsApp.ENVIADO
    turno.whatsapp_mensaje_id = mensaje_id
    db.commit()
    logger.info("Recordatorio enviado: turno id=%s mensaje_id=%s", turno.id, mensaje_id)

    return RecordatorioEnviado(
        turno_id=turno.id,
        telefono=telefono,
        doctor=doctor,
        fecha_hora_inicio=turno.fecha_hora_inicio.isoformat(),
        resultado="ENVIADO",
        whatsapp_mensaje_id=mensaje_id,
    )


def enviar_recordatorios(
    db: Session,
    dias: Optional[int] = None,
    ahora=None,
) -> RecordatorioResultado:
    """Tarea programada: envía recordatorios de los turnos a ``dias`` días.

    Selecciona los turnos cuya ``fecha_hora_inicio`` cae exactamente en el día
    ``hoy + dias`` (00:00:00 - 23:59:59.999999), siempre que estén en estado
    ``PENDIENTE`` / ``PENDIENTE_ENVIO``.

    :param dias: antelación en días (por defecto ``settings.recordatorio_dias``).
    :param ahora: inyectable para pruebas (por defecto ``utcnow()``).
    """
    dias = settings.recordatorio_dias if dias is None else dias
    referencia = utcnow() if ahora is None else to_naive_utc(ahora)

    fecha_objetivo = (referencia + timedelta(days=dias)).date()
    inicio_dia, fin_dia = rango_del_dia(fecha_objetivo)

    logger.info(
        "Buscando recordatorios para el dia %s (dias=%s)",
        fecha_objetivo.isoformat(),
        dias,
    )

    stmt = (
        select(Turno)
        .where(
            Turno.fecha_hora_inicio >= inicio_dia,
            Turno.fecha_hora_inicio <= fin_dia,
            Turno.estado_turno == EstadoTurno.PENDIENTE,
            Turno.estado_whatsapp == EstadoWhatsApp.PENDIENTE_ENVIO,
        )
        .order_by(Turno.fecha_hora_inicio)
    )
    turnos = db.execute(stmt).scalars().all()

    detalles: list[RecordatorioEnviado] = []
    for turno in turnos:
        detalles.append(_procesar_envio_turno(db, turno))

    enviados = sum(1 for d in detalles if d.resultado == "ENVIADO")
    fallidos = len(detalles) - enviados

    return RecordatorioResultado(
        fecha_objetivo=fecha_objetivo.isoformat(),
        dias_antelacion=dias,
        evaluados=len(turnos),
        enviados=enviados,
        fallidos=fallidos,
        detalles=detalles,
    )


# ===========================================================================
#  2) PROCESAMIENTO ENTRANTE DEL WEBHOOK
# ===========================================================================


def _primer_mensaje_con_boton(payload: WhatsAppWebhookPayload) -> WhatsAppInboundMessage:
    """Extrae el primer mensaje que contenga una respuesta de botón."""
    for entry in payload.entry:
        for change in entry.changes:
            for mensaje in change.value.messages or []:
                if mensaje.interactive and mensaje.interactive.button_reply:
                    return mensaje
    raise RespuestaWebhookInvalidaError(
        "El payload no contiene ninguna respuesta de botón (interactive.button_reply)."
    )


def _mapear_boton_a_estado(button_reply: WhatsAppButtonReply) -> EstadoTurno:
    """Traduce el botón presionado al nuevo estado del turno."""
    for clave in (button_reply.id, button_reply.title):
        if clave:
            estado = BOTONES_ESTADO.get(clave.strip().lower())
            if estado is not None:
                return estado
    raise RespuestaWebhookInvalidaError(
        f"Botón no reconocido: id={button_reply.id!r} title={button_reply.title!r}."
    )


def procesar_respuesta_boton(
    db: Session,
    payload: WhatsAppWebhookPayload,
) -> Tuple[Turno, WhatsAppInboundMessage, str]:
    """Procesa la respuesta de botón y actualiza el turno correspondiente.

    Pasos:
      1. Extrae el mensaje con ``interactive.button_reply``.
      2. Toma ``context.id`` (id del mensaje saliente original -> ``wamid``).
      3. Busca el ``Turno`` por ``whatsapp_mensaje_id``.
      4. Mapea el botón a ``CONFIRMADO`` / ``CANCELADO`` y marca el estado de
         WhatsApp como ``RESPONDIDO``.

    :returns: ``(turno_actualizado, mensaje_entrante, boton_id)``.
    """
    mensaje = _primer_mensaje_con_boton(payload)
    button_reply = mensaje.interactive.button_reply  # type: ignore[union-attr]
    assert button_reply is not None  # garantizado por el filtro anterior

    mensaje_contextual_id = mensaje.context.id if mensaje.context else None
    if not mensaje_contextual_id:
        raise RespuestaWebhookInvalidaError(
            "El mensaje no incluye 'context.id'; no es posible correlacionar el turno."
        )

    nuevo_estado = _mapear_boton_a_estado(button_reply)

    turno = db.execute(
        select(Turno).where(Turno.whatsapp_mensaje_id == mensaje_contextual_id)
    ).scalar_one_or_none()

    if turno is None:
        raise TurnoNoEncontradoError(
            f"No se encontró un turno con whatsapp_mensaje_id={mensaje_contextual_id!r}."
        )

    turno.estado_turno = nuevo_estado
    turno.estado_whatsapp = EstadoWhatsApp.RESPONDIDO
    db.commit()
    db.refresh(turno)

    logger.info(
        "Webhook procesado: turno id=%s -> estado_turno=%s (boton=%s)",
        turno.id,
        turno.estado_turno,
        button_reply.id,
    )

    return turno, mensaje, button_reply.id


# ===========================================================================
#  3) RECORDATORIOS POR ENLACE "CLICK-TO-CHAT" (wa.me)
# ===========================================================================
#
#  Alternativa 100 % gratuita al envío automático por la Cloud API: el backend
#  arma el enlace oficial de WhatsApp con el mensaje ya redactado y codificado
#  (URL Encoding) y la secretaría lo abre con un clic desde el panel. El mensaje
#  sale del WhatsApp del consultorio: sin credenciales de Meta, sin plantillas
#  aprobadas y sin costo por mensaje.

#: Texto del recordatorio; se completa con los datos reales del turno.
PLANTILLA_RECORDATORIO = (
    "Hola {paciente}, te recordamos tu turno el día {fecha} a las {hora} con "
    "{profesional}. Por favor, confirmá tu asistencia respondiendo a este "
    "mensaje. ¡Muchas gracias!"
)

#: URL oficial de Click-to-Chat. Marcadores: número (sólo dígitos) y mensaje.
URL_CLICK_TO_CHAT = "https://wa.me/{}?text={}"

#: Nombres en español para la fecha "en letras" (sin depender del locale del SO).
DIAS_SEMANA = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
MESES = (
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


def solo_digitos(valor: Optional[str]) -> str:
    """Devuelve únicamente los dígitos de ``valor`` (``""`` si no hay ninguno)."""
    return "".join(c for c in str(valor or "") if c.isdigit())


def normalizar_telefono_whatsapp(
    telefono: Optional[str], codigo_pais: Optional[str] = None
) -> str:
    """Adapta un teléfono al formato que exige el enlace ``wa.me`` (sólo dígitos).

    Orden de las reglas:

    1. Se conservan sólo los dígitos (se descartan ``+``, espacios, guiones,
       paréntesis y puntos).
    2. El prefijo internacional ``00`` se elimina (``0054911...`` -> ``54911...``).
    3. Se quitan los ceros de larga distancia (``011 2233-4455`` -> ``1122334455``).
    4. Si ya empieza con el código configurado (``549``) o con ese código sin el
       ``9`` de móvil (``54``), se devuelve sin cambios.
    5. En cualquier otro caso se le antepone el código configurado
       (``WHATSAPP_CODIGO_PAIS``, por defecto ``549``: Argentina móvil).

    Ejemplos con la configuración por defecto::

        "+54 9 11 2233-4455" -> "5491122334455"
        "(011) 2233-4455"    -> "5491122334455"
        "5491122334455"      -> "5491122334455"
        ""                   -> ""

    :param telefono: teléfono tal como está cargado en el sistema.
    :param codigo_pais: código a aplicar (por defecto ``settings.whatsapp_codigo_pais``).
    """
    digitos = solo_digitos(telefono)
    if not digitos:
        return ""

    prefijo = solo_digitos(codigo_pais) or solo_digitos(settings.whatsapp_codigo_pais)
    if not prefijo:
        return digitos

    if digitos.startswith("00"):
        digitos = digitos[2:]
    digitos = digitos.lstrip("0")
    if not digitos:
        return ""

    # En Argentina el móvil lleva un "9" después del código de país (549...).
    pais = prefijo[:-1] if prefijo.startswith("549") else prefijo
    if digitos.startswith(prefijo) or digitos.startswith(pais):
        return digitos

    return prefijo + digitos


def formatear_fecha_recordatorio(fecha: datetime) -> str:
    """Devuelve la fecha "en letras" (``viernes 16 de octubre de 2026``)."""
    return (
        f"{DIAS_SEMANA[fecha.weekday()]} {fecha.day} de "
        f"{MESES[fecha.month - 1]} de {fecha.year}"
    )


def profesional_con_articulo(nombre: Optional[str]) -> str:
    """Agrega el artículo al profesional para que el saludo sea natural.

    ``"Dra. Gómez"`` -> ``"la Dra. Gómez"`` y ``"Dr. House"`` -> ``"el Dr. House"``.
    Si el nombre no trae tratamiento (``Dr.``/``Dra.``) se devuelve tal cual, para
    no generar frases como "con el Juan".
    """
    limpio = (nombre or "").strip()
    minus = limpio.lower()
    if minus.startswith(("dra", "doctora")):
        return f"la {limpio}"
    if minus.startswith(("dr", "doctor")):
        return f"el {limpio}"
    return limpio


def construir_mensaje_recordatorio(turno: Turno) -> str:
    """Redacta el texto del recordatorio con los datos del turno.

    :param turno: turno con las relaciones ``paciente`` y ``doctor`` cargadas.
    """
    paciente = (turno.paciente.nombre if turno.paciente else "").strip() or "paciente"
    doctor = profesional_con_articulo(turno.doctor.nombre if turno.doctor else "")
    return PLANTILLA_RECORDATORIO.format(
        paciente=paciente,
        fecha=formatear_fecha_recordatorio(turno.fecha_hora_inicio),
        hora=turno.fecha_hora_inicio.strftime("%H:%M"),
        profesional=doctor or "tu profesional",
    )


def construir_enlace_whatsapp(
    turno: Turno,
    telefono: Optional[str] = None,
    mensaje: Optional[str] = None,
) -> str:
    """Genera el enlace de Click-to-Chat (``wa.me``) para un turno.

    El texto se codifica con ``urllib.parse.quote(..., safe="")`` (URL Encoding),
    de modo que los acentos, el ``¡`` y los espacios viajen como ``%C3%A9`` y
    ``%20`` sin romper la URL.

    :return: la URL lista para abrir en WhatsApp, o ``""`` si el paciente no
        tiene un teléfono válido cargado.
    """
    if telefono is None:
        telefono = turno.paciente.telefono if turno.paciente else ""
    numero = normalizar_telefono_whatsapp(telefono)
    if not numero:
        logger.warning(
            "Turno id=%s sin teléfono válido: no se genera el enlace wa.me.", turno.id
        )
        return ""

    texto = mensaje if mensaje is not None else construir_mensaje_recordatorio(turno)
    return URL_CLICK_TO_CHAT.format(numero, quote(texto, safe=""))


def listar_turnos_pendientes_de_recordatorio(
    db: Session,
    dias: Optional[int] = None,
    ahora: Optional[datetime] = None,
) -> list[Turno]:
    """Turnos que todavía esperan su aviso de WhatsApp, en orden cronológico.

    Se incluyen los turnos cuya ``fecha_hora_inicio`` cae entre ``ahora`` y el
    final del día ``hoy + dias`` (por defecto ``settings.recordatorio_dias``), que
    sigan en ``PENDIENTE_ENVIO`` y no estén cancelados.

    A diferencia de la tarea automática —que sólo mira los turnos aún
    ``PENDIENTE`` de un único día— esta vista es la bandeja de trabajo de la
    secretaría: también incluye los turnos ya confirmados que nunca recibieron
    el recordatorio.

    :param dias: ventana de días a futuro (por defecto ``settings.recordatorio_dias``).
    :param ahora: inyectable para pruebas (por defecto ``utcnow()``).
    """
    dias = settings.recordatorio_dias if dias is None else max(0, int(dias))
    referencia = utcnow() if ahora is None else to_naive_utc(ahora)
    _, fin_ventana = rango_del_dia((referencia + timedelta(days=dias)).date())

    stmt = (
        select(Turno)
        .options(selectinload(Turno.paciente), selectinload(Turno.doctor))
        .where(
            Turno.fecha_hora_inicio >= referencia,
            Turno.fecha_hora_inicio <= fin_ventana,
            Turno.estado_turno != EstadoTurno.CANCELADO,
            Turno.estado_whatsapp == EstadoWhatsApp.PENDIENTE_ENVIO,
        )
        .order_by(Turno.fecha_hora_inicio)
    )
    return list(db.execute(stmt).scalars().all())


def construir_recordatorio_pendiente(
    turno: Turno, ahora: Optional[datetime] = None
) -> RecordatorioPendiente:
    """Convierte un turno en el ítem que consume el panel (con enlace incluido)."""
    referencia = utcnow() if ahora is None else to_naive_utc(ahora)
    telefono = turno.paciente.telefono if turno.paciente else ""
    mensaje = construir_mensaje_recordatorio(turno)

    return RecordatorioPendiente(
        turno_id=turno.id,
        paciente_id=turno.paciente_id,
        paciente=(
            turno.paciente.nombre if turno.paciente else f"Paciente #{turno.paciente_id}"
        ),
        doctor=turno.doctor.nombre if turno.doctor else f"Doctor #{turno.doctor_id}",
        telefono=telefono,
        telefono_whatsapp=normalizar_telefono_whatsapp(telefono),
        fecha_hora_inicio=turno.fecha_hora_inicio,
        fecha_hora_fin=turno.fecha_hora_fin,
        estado_turno=turno.estado_turno,
        estado_whatsapp=turno.estado_whatsapp,
        dias_restantes=max(0, (turno.fecha_hora_inicio.date() - referencia.date()).days),
        mensaje=mensaje,
        enlace_whatsapp=construir_enlace_whatsapp(
            turno, telefono=telefono, mensaje=mensaje
        ),
    )


def listar_recordatorios_click_to_chat(
    db: Session,
    dias: Optional[int] = None,
    ahora: Optional[datetime] = None,
) -> list[RecordatorioPendiente]:
    """Recordatorios pendientes listos para enviar por enlace Click-to-Chat."""
    turnos = listar_turnos_pendientes_de_recordatorio(db, dias=dias, ahora=ahora)
    return [construir_recordatorio_pendiente(turno, ahora=ahora) for turno in turnos]
    return turno, mensaje, button_reply.id
