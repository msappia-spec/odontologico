"""Servicio de WhatsApp sobre WAHA (WhatsApp HTTP API).

Contiene tres responsabilidades separadas:

1. **Envío saliente** de recordatorios (tarea programada y botones del panel),
   que se puede *simular* o *ejecutar* con ``POST {WAHA_URL}/api/sendText``.
2. **Envío saliente de texto libre** (``enviar_mensaje_texto``), que reutiliza el
   FAQ Bot (``whatsapp_bot_service``) para contestar los mensajes del paciente.
3. **Lectura del webhook** de WAHA: extrae el teléfono y el texto y los deriva
   al bot, que decide si es una confirmación de turno o una opción del menú.
"""

import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.anamnesis_token import construir_enlace_anamnesis
from app.core.config import settings
from app.core.datetime_utils import rango_del_dia, to_naive_utc, utcnow
from app.core.exceptions import (
    ConflictoError,
    TurnoNoEncontradoError,
    ValidacionError,
    WhatsAppEnvioError,
)
# Formato de teléfonos, fechas y enlaces wa.me: vive en ``app/core`` para que el
# servicio de anamnesis también lo use. Se reexporta acá para no romper los
# imports históricos (``from app.services.whatsapp_service import ...``).
from app.core.whatsapp_utils import (  # noqa: F401
    DIAS_SEMANA,
    MESES,
    construir_chat_id_waha,
    construir_enlace_click_to_chat,
    es_identificador_lid,
    formatear_fecha_recordatorio,
    normalizar_destinatario_waha,
    normalizar_telefono_waha,
    normalizar_telefono_whatsapp,
    numero_de_jid,
    profesional_con_articulo,
    solo_digitos,
)

from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.historia_clinica import HistoriaClinica
from app.models.paciente import Paciente
from app.models.turno import Turno
from app.schemas.whatsapp import (
    RecordatorioEnviado,
    RecordatorioMarcado,
    RecordatorioPendiente,
    RecordatorioResultado,
    WAHAWebhookPayload,
)
from app.services import whatsapp_conversacion_service
from app.services.anamnesis_service import necesita_completar_anamnesis

logger = logging.getLogger("app.services.whatsapp")

#: Pausa antes de cada envío real. WAHA recomienda esperar para no saturar la
#: sesión de WhatsApp y reducir el riesgo de bloqueos por ráfaga.
PAUSA_ENVIO_WAHA = 1.5


# ===========================================================================
#  1) ENVÍO SALIENTE DE RECORDATORIOS
# ===========================================================================


def _opciones_recordatorio():
    """Carga las relaciones que necesita el recordatorio en una sola consulta.

    ``necesita_completar_anamnesis`` mira ``paciente.historia_clinica`` y su
    declaración de salud. Se cargan con ``selectinload`` para no disparar una
    consulta por turno ni acceder a una relación fuera de la sesión.
    """
    return (
        selectinload(Turno.paciente)
        .selectinload(Paciente.historia_clinica)
        .selectinload(HistoriaClinica.antecedentes_salud),
        selectinload(Turno.doctor),
    )


def _enlace_anamnesis_pendiente(turno: Turno) -> str:
    """Enlace público de la ficha de salud, sólo si el paciente aún no la envió."""
    if not necesita_completar_anamnesis(turno):
        return ""
    return construir_enlace_anamnesis(turno.id, turno.paciente_id)


def _agregar_enlace_anamnesis(texto: str, turno: Turno) -> str:
    """Suma al recordatorio el enlace de la ficha cuando está pendiente."""
    enlace = _enlace_anamnesis_pendiente(turno)
    if not enlace:
        return texto
    return f"{texto}\n\n{TEXTO_ANAMNESIS_PENDIENTE.format(enlace=enlace)}"


def construir_payload_recordatorio(turno: Turno) -> dict:
    """Arma el mensaje de texto del recordatorio para WAHA.

    WAHA no usa plantillas ni botones: el recordatorio es texto libre con la
    instrucción de responder ``1`` / ``si`` para confirmar o ``2`` / ``no`` para
    cancelar. El teléfono se normaliza al formato argentino ``549...``.
    """
    telefono = turno.paciente.telefono if turno.paciente else ""
    return construir_payload_texto(telefono, construir_mensaje_recordatorio(turno))


def _id_mensaje_waha(data: object) -> str:
    """Extrae el identificador del mensaje de una respuesta de WAHA.

    Según la versión, WAHA devuelve ``{"id": "..."}``, un objeto anidado en
    ``_data`` o ``{"message": {"id": ...}}``. Si no viene ninguno se genera un
    identificador local para no perder la trazabilidad del envío.
    """
    if isinstance(data, dict):
        for contenedor in (data, data.get("message"), data.get("_data")):
            if not isinstance(contenedor, dict):
                continue
            identificador = contenedor.get("id") or contenedor.get("messageId")
            if isinstance(identificador, dict):
                identificador = (
                    identificador.get("_serialized") or identificador.get("id")
                )
            if identificador:
                return str(identificador)
    return f"waha.{uuid.uuid4().hex}"


def _detalle_error_waha(respuesta: httpx.Response) -> str:
    """Resume el error que devolvió WAHA (texto o JSON)."""
    try:
        data = respuesta.json()
    except ValueError:
        return (respuesta.text or "").strip() or "sin detalle"
    if isinstance(data, dict):
        error = data.get("error") or data.get("message") or data.get("detail")
        if isinstance(error, dict):
            error = error.get("message") or str(error)
        if error:
            return str(error)
    return str(data)


def _enviar_http_whatsapp(payload: dict) -> str:
    """Envía el mensaje a WAHA con ``POST /api/sendText`` y devuelve su id."""
    url = f"{settings.waha_url_base}/api/sendText"
    headers = {
        "X-Api-Key": settings.waha_api_key or "",
        "Content-Type": "application/json",
    }

    # WAHA recomienda espaciar los envíos para no saturar la sesión.
    time.sleep(PAUSA_ENVIO_WAHA)

    try:
        respuesta = httpx.post(url, json=payload, headers=headers, timeout=10.0)
        respuesta.raise_for_status()
        data = respuesta.json()
    except httpx.HTTPStatusError as exc:
        respuesta_error = exc.response
        raise WhatsAppEnvioError(
            f"WAHA rechazó el mensaje (HTTP {respuesta_error.status_code}): "
            f"{_detalle_error_waha(respuesta_error)}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise WhatsAppEnvioError(f"Falló el envío a WAHA: {exc}") from exc

    return _id_mensaje_waha(data)


def _simular_http_whatsapp(payload: dict) -> str:
    """Simula un ``POST`` HTTP a WAHA y devuelve un identificador ficticio."""
    mensaje_id = f"waha.SIMULADO.{uuid.uuid4().hex}"
    logger.info(
        "SIMULACIÓN HTTP POST WAHA /api/sendText -> chatId=%s | message_id=%s",
        payload.get("chatId"),
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


def construir_payload_texto(
    telefono: str, texto: str, preview_url: bool = False
) -> dict:
    """Arma el cuerpo JSON que espera ``POST /api/sendText`` de WAHA.

    El formato es el mínimo que documenta WAHA::

        {"chatId": "549351...@c.us", "text": "mensaje", "session": "default"}

    ``preview_url`` se mantiene en la firma por compatibilidad con el envío de la
    ficha de salud, pero WAHA decide la vista previa del enlace por su cuenta.
    """
    return {
        "chatId": construir_chat_id_waha(telefono),
        "text": texto,
        "session": settings.waha_session,
    }


def enviar_mensaje_texto(telefono: str, texto: str, preview_url: bool = False) -> str:
    """Envía (o simula) un mensaje de texto al paciente y devuelve su id.

    Usa el envío **real** con ``httpx`` (``POST`` a ``{WAHA_URL}/api/sendText``)
    cuando están configuradas ``WAHA_URL`` y ``WAHA_API_KEY``; si no, **simula**
    el ``POST`` y devuelve un identificador ficticio. Es la función que usa el
    FAQ Bot (``whatsapp_bot_service``) para entregar sus respuestas.

    :param preview_url: se conserva por compatibilidad; WAHA no recibe ese campo.
    """
    logger.info("Enviando WhatsApp (texto) a %s", telefono or "SIN_TELEFONO")

    payload = construir_payload_texto(telefono, texto, preview_url=preview_url)
    if not payload["chatId"]:
        raise WhatsAppEnvioError(
            f"El teléfono {telefono!r} no tiene dígitos utilizables para WhatsApp."
        )
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
            simulado=not settings.whatsapp_envio_real,
            error=str(exc),
        )

    turno.estado_whatsapp = EstadoWhatsApp.ENVIADO
    turno.whatsapp_mensaje_id = mensaje_id
    db.commit()

    # WAHA responde con texto libre: se guarda a qué turno corresponde para que
    # el próximo "1" / "si" o "2" / "no" del paciente sepa qué confirmar.
    telefono = turno.paciente.telefono if turno.paciente else ""
    whatsapp_conversacion_service.marcar_confirmacion_pendiente(
        db, telefono, turno.id, mensaje_id
    )
    logger.info("Recordatorio enviado: turno id=%s mensaje_id=%s", turno.id, mensaje_id)

    return RecordatorioEnviado(
        turno_id=turno.id,
        telefono=telefono,
        doctor=doctor,
        fecha_hora_inicio=turno.fecha_hora_inicio.isoformat(),
        resultado="ENVIADO",
        whatsapp_mensaje_id=mensaje_id,
        simulado=not settings.whatsapp_envio_real,
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
        .options(*_opciones_recordatorio())
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
#  1.b) ENVÍO AUTOMÁTICO DESDE EL PANEL (misma API de WAHA)
# ===========================================================================
#
#  La tarea programada (``enviar_recordatorios``) mira un único día exacto. La
#  pestaña *Recordatorios Pendientes* del panel, en cambio, lista una **ventana**
#  de días y permite mandar el aviso en el momento con ``POST /api/sendText``.
#  Estas dos funciones son el camino que usa esa pantalla:
#
#  * ``enviar_recordatorio_pendiente`` envía un turno puntual por la API.
#  * ``marcar_recordatorio_enviado`` cierra el envío manual (Click-to-Chat): deja
#    el teléfono esperando la respuesta para que el webhook confirme o cancele.


def enviar_recordatorio_pendiente(db: Session, turno_id: int) -> RecordatorioEnviado:
    """Envía (o simula) el recordatorio de un turno puntual desde el panel.

    Reutiliza el mismo camino que la tarea programada
    (``_procesar_envio_turno``): deja el turno en ``ENVIADO``, guarda el
    ``whatsapp_mensaje_id`` que devuelve WAHA y registra en
    ``conversaciones_whatsapp`` que ese teléfono espera la respuesta, de modo
    que el ``1`` / ``si`` o ``2`` / ``no`` del paciente confirme o cancele el
    turno en la base.

    :raises TurnoNoEncontradoError: el turno no existe.
    :raises ConflictoError: el turno está cancelado (no tiene aviso pendiente).
    :raises ValidacionError: el paciente no tiene un teléfono utilizable.
    """
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise TurnoNoEncontradoError(f"No existe el turno id={turno_id}.")
    if turno.estado_turno == EstadoTurno.CANCELADO:
        raise ConflictoError(
            "El turno está cancelado: no tiene un recordatorio pendiente."
        )

    telefono = turno.paciente.telefono if turno.paciente else ""
    if not normalizar_telefono_whatsapp(telefono):
        raise ValidacionError(
            "El paciente no tiene un teléfono válido para WhatsApp: cargá el "
            "número en su ficha o avisale por otro medio."
        )

    return _procesar_envio_turno(db, turno)


def marcar_recordatorio_enviado(db: Session, turno_id: int) -> RecordatorioMarcado:
    """Marca como enviado el recordatorio que la secretaría mandó a mano.

    El enlace Click-to-Chat (``wa.me``) lo abre el panel en el WhatsApp del
    consultorio, así que el backend no ve ese envío. Al pulsar el botón se
    marca el turno en ``ENVIADO`` y —lo importante— se deja el teléfono en
    ``CONFIRMACION_TURNO``: la respuesta ``1`` / ``si`` o ``2`` / ``no`` que el
    paciente mande después llega al webhook de WAHA y confirma o cancela
    **este** turno (``whatsapp_bot_service._responder_confirmacion``).

    Si el turno ya fue respondido (``RESPONDIDO``) no se reabre la
    confirmación: el estado que dejó la respuesta del paciente manda.

    :raises TurnoNoEncontradoError: el turno no existe.
    :raises ConflictoError: el turno está cancelado.
    """
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise TurnoNoEncontradoError(f"No existe el turno id={turno_id}.")
    if turno.estado_turno == EstadoTurno.CANCELADO:
        raise ConflictoError(
            "El turno está cancelado: no tiene un recordatorio pendiente."
        )

    confirmacion_pendiente = False
    if turno.estado_whatsapp != EstadoWhatsApp.RESPONDIDO:
        turno.estado_whatsapp = EstadoWhatsApp.ENVIADO
        db.commit()
        db.refresh(turno)

        telefono = turno.paciente.telefono if turno.paciente else ""
        conversacion = whatsapp_conversacion_service.marcar_confirmacion_pendiente(
            db, telefono, turno.id, turno.whatsapp_mensaje_id
        )
        confirmacion_pendiente = conversacion is not None
    else:
        db.refresh(turno)

    logger.info(
        "Recordatorio enviado desde el panel: turno id=%s estado_whatsapp=%s "
        "confirmacion_pendiente=%s",
        turno.id,
        turno.estado_whatsapp,
        confirmacion_pendiente,
    )
    return RecordatorioMarcado(
        turno_id=turno.id,
        estado_turno=turno.estado_turno,
        estado_whatsapp=turno.estado_whatsapp,
        confirmacion_pendiente=confirmacion_pendiente,
        whatsapp_mensaje_id=turno.whatsapp_mensaje_id,
    )


# ===========================================================================
#  2) PROCESAMIENTO ENTRANTE DEL WEBHOOK
# ===========================================================================


def es_evento_de_mensaje(payload: WAHAWebhookPayload) -> bool:
    """Indica si el evento de WAHA debe procesarse como mensaje del paciente.

    Se descartan:

    * cualquier evento que no sea ``message`` (``session.status``, ``message.ack``
      y el resto de los eventos de WAHA);
    * los mensajes propios del consultorio (``fromMe``), para no responder al
      bot con sus propias respuestas;
    * los mensajes de grupos (``@g.us``) y de estados (``@broadcast``).
    """
    if payload.event != "message" or payload.payload is None:
        return False
    if payload.payload.from_me:
        return False
    remitente = (payload.payload.from_ or "").strip().lower()
    if not remitente:
        return False
    return not remitente.endswith(("@g.us", "@broadcast"))


def telefono_de_chat(chat_id: Optional[str]) -> str:
    """Extrae el teléfono canónico del ``from`` de WAHA (``549...@c.us``).

    Los ``@lid`` son identificadores opacos de WhatsApp (privacidad del
    número): no contienen un teléfono, así que se devuelven tal cual para poder
    contestarle al mismo chat. El número real, cuando viene, lo resuelve
    :func:`numero_real_de_mensaje`.
    """
    crudo = (chat_id or "").strip()
    if not crudo:
        return ""
    if es_identificador_lid(crudo):
        return crudo
    return numero_de_jid(crudo)


def numero_real_de_mensaje(mensaje: WAHAMessagePayload) -> str:
    """Teléfono real del paciente cuando WhatsApp entrega el chat como ``@lid``.

    WAHA publica la clave interna del mensaje en ``payload._data.key``: ahí
    ``remoteJidAlt`` (y, según la versión, ``senderPn`` o ``participantAlt``)
    trae el número de verdad, por ejemplo ``5493515945075@s.whatsapp.net``.

    :return: el teléfono normalizado (``549...``) o ``""`` si no viene.
    """
    datos = mensaje.raw_data
    clave = datos.key if datos is not None else None
    if clave is None:
        return ""
    for candidato in (
        clave.remote_jid_alt,
        clave.sender_pn,
        clave.participant_alt,
    ):
        numero = numero_de_jid(candidato)
        if numero:
            return numero
    return ""


def extraer_mensaje(
    payload: WAHAWebhookPayload,
) -> Optional[Tuple[str, str, str]]:
    """Devuelve ``(teléfono, texto, id_entrante)`` de un evento de WAHA.

    Aplica los filtros de :func:`es_evento_de_mensaje` y descarta los mensajes sin
    remitente o sin texto: no hay nada que contestar y WAHA no debe reintentar.

    Si el ``from`` es un ``@lid`` se usa el número real que viene en
    ``payload._data.key`` (así la respuesta coincide con el teléfono de la
    ficha del paciente); si no estuviera, se devuelve el ``@lid`` original para
    contestarle a ese mismo chat.
    """
    if not es_evento_de_mensaje(payload):
        return None

    mensaje = payload.payload
    assert mensaje is not None  # garantizado por es_evento_de_mensaje
    telefono = telefono_de_chat(mensaje.from_)
    if es_identificador_lid(telefono):
        telefono = numero_real_de_mensaje(mensaje) or telefono
    texto = (mensaje.body or "").strip()
    if not telefono or not texto:
        return None
    return telefono, texto, mensaje.id or ""


# ===========================================================================
#  3) RECORDATORIOS POR ENLACE "CLICK-TO-CHAT" (wa.me)
# ===========================================================================
#
#  Alternativa 100 % gratuita al envío automático por WAHA: el backend arma el
#  enlace oficial de WhatsApp con el mensaje ya redactado y codificado (URL
#  Encoding) y la secretaría lo abre con un clic desde el panel. El mensaje sale
#  del WhatsApp del consultorio, sin credenciales adicionales.

#: Texto del recordatorio; se completa con los datos reales del turno.
PLANTILLA_RECORDATORIO = (
    "Hola {paciente}, te recordamos tu turno el día {fecha} a las {hora} con "
    "{profesional}. Por favor, confirmá tu asistencia respondiendo a este "
    "mensaje. ¡Muchas gracias!"
)

#: Instrucciones de respuesta: WAHA no tiene botones interactivos, así que el
#: paciente contesta con números o palabras.
INSTRUCCIONES_RESPUESTA = (
    "Respondé *1* (o *SI*) para confirmar el turno.\n"
    "Respondé *2* (o *NO*) para cancelarlo."
)

#: Se agrega al recordatorio mientras la ficha de salud siga pendiente. El
#: enlace va solo en su propia línea (igual que ``PLANTILLA_ENVIO_FICHA``): así
#: WhatsApp lo muestra en azul y el paciente puede abrirlo con un toque.
TEXTO_ANAMNESIS_PENDIENTE = (
    "Antes de la consulta, completá tu ficha de salud desde este enlace:\n"
    "\n"
    "{enlace}"
)

# Las utilidades de formato (``solo_digitos``, ``normalizar_telefono_whatsapp``,
# ``formatear_fecha_recordatorio``, ``profesional_con_articulo``, las tablas de
# días/meses y ``URL_CLICK_TO_CHAT``) viven en :mod:`app.core.whatsapp_utils` y
# se importan arriba: el servicio de anamnesis también las necesita para armar
# el mensaje de la ficha, y así no hay dependencia circular entre los servicios.


def construir_mensaje_recordatorio(turno: Turno) -> str:
    """Redacta el texto del recordatorio con los datos del turno.

    Si el paciente todavía no completó su ficha de salud, se agrega al final el
    enlace público firmado para que pueda hacerlo antes de la consulta.

    :param turno: turno con las relaciones ``paciente`` (e historia clínica) y
        ``doctor`` cargadas.
    """
    paciente = (turno.paciente.nombre if turno.paciente else "").strip() or "paciente"
    doctor = profesional_con_articulo(turno.doctor.nombre if turno.doctor else "")
    texto = PLANTILLA_RECORDATORIO.format(
        paciente=paciente,
        fecha=formatear_fecha_recordatorio(turno.fecha_hora_inicio),
        hora=turno.fecha_hora_inicio.strftime("%H:%M"),
        profesional=doctor or "tu profesional",
    )
    texto = f"{texto}\n\n{INSTRUCCIONES_RESPUESTA}"
    return _agregar_enlace_anamnesis(texto, turno)


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
    texto = mensaje if mensaje is not None else construir_mensaje_recordatorio(turno)
    enlace = construir_enlace_click_to_chat(telefono, texto)
    if not enlace:
        logger.warning(
            "Turno id=%s sin teléfono válido: no se genera el enlace wa.me.", turno.id
        )
    return enlace


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
        .options(*_opciones_recordatorio())
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
