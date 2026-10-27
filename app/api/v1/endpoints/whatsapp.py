"""Endpoints de WhatsApp: webhook de entrada (botones y FAQ Bot) y recordatorios.

El ``POST /whatsapp/webhook`` es el punto de entrada de la Cloud API de Meta y
atiende los dos tipos de mensaje que puede mandar un paciente:

* ``interactive.button_reply`` — la respuesta a un recordatorio (**Confirmar** /
  **Cancelar**), que actualiza el estado del turno.
* ``text`` — un mensaje escrito a mano ("hola", "1", "¿atienden OSDE?"), que
  contesta el **FAQ Bot** (``whatsapp_bot_service``) con el menú configurable
  desde la vista *Bot WhatsApp* (``/whatsapp/menu``).

El panel también ofrece el enlace *Click-to-Chat* de los recordatorios
pendientes, que no pasa por la API: lo envía la secretaría desde su WhatsApp.
"""

import hashlib
import hmac
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import RespuestaWebhookInvalidaError, WhatsAppEnvioError
from app.db.session import get_db
from app.schemas.whatsapp import (
    RecordatorioPendiente,
    WebhookProcesadoResponse,
    WhatsAppInboundMessage,
    WhatsAppWebhookPayload,
)
from app.services import whatsapp_bot_service, whatsapp_service

logger = logging.getLogger("app.api.whatsapp")

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


@router.get(
    "/webhook",
    response_class=PlainTextResponse,
    status_code=status.HTTP_200_OK,
    summary="Verificación del webhook (Meta/WhatsApp)",
    description=(
        "Endpoint de verificación exigido por la WhatsApp Cloud API. Meta hace "
        "un GET con 'hub.mode=subscribe', el 'hub.verify_token' cargado en el "
        "panel y un 'hub.challenge'; si el token coincide se devuelve el "
        "'hub.challenge' como texto plano (200). Cualquier otro caso es 403."
    ),
)
def verificar_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
) -> Response:
    """Valida el *verify token* del webhook y devuelve el challenge de Meta.

    Meta exige que la respuesta sea el valor de ``hub.challenge`` **tal cual**
    (texto plano, ``200``), no un JSON: si se envuelve en un objeto, la
    verificación del panel falla. El token se compara contra
    ``WHATSAPP_VERIFY_TOKEN`` (nunca contra el access token de la API) y en
    tiempo constante con ``hmac.compare_digest``.
    """
    esperado = (settings.whatsapp_verify_token or "").encode("utf-8")
    recibido = (hub_verify_token or "").encode("utf-8")

    if (
        hub_mode == "subscribe"
        and esperado
        and hub_challenge
        and hmac.compare_digest(recibido, esperado)
    ):
        logger.info("Webhook de WhatsApp verificado por Meta.")
        return PlainTextResponse(hub_challenge, status_code=status.HTTP_200_OK)

    logger.warning(
        "Verificación del webhook rechazada (mode=%r, verify_token=%s).",
        hub_mode,
        "configurado pero distinto" if esperado else "sin configurar",
    )
    return PlainTextResponse(
        "Verificación no válida", status_code=status.HTTP_403_FORBIDDEN
    )


def _procesar_texto_del_bot(
    db: Session, mensaje: WhatsAppInboundMessage
) -> WebhookProcesadoResponse:
    """Deriva el texto del paciente al FAQ Bot y arma la respuesta del webhook.

    Extrae el número del remitente (``from``) y el texto (``text.body``) y llama
    a ``whatsapp_bot_service.responder_mensaje``, que decide y **envía** la
    respuesta (real o simulada). Un mensaje de texto sin remitente o sin cuerpo
    no se puede contestar: se responde ``400`` como cualquier payload inválido.
    """
    telefono = (mensaje.from_ or "").strip()
    texto = (mensaje.text.body or "").strip() if mensaje.text else ""
    if not telefono or not texto:
        raise RespuestaWebhookInvalidaError(
            "El mensaje de texto no trae 'from' y 'text.body'; no se puede responder."
        )

    resultado = whatsapp_bot_service.responder_mensaje(db, telefono, texto)

    return WebhookProcesadoResponse(
        tipo="BOT",
        mensaje_id_entrante=mensaje.id,
        telefono=resultado.telefono,
        opcion=resultado.opcion,
        respuesta=resultado.respuesta,
        mensaje_id_saliente=resultado.mensaje_id_saliente,
        atencion_manual=resultado.atencion_manual,
    )


async def validar_firma_webhook(request: Request) -> None:
    """Comprueba que el POST fue emitido por Meta antes de procesarlo.

    Meta firma los bytes exactos del cuerpo con la *App Secret* y los envía en
    ``X-Hub-Signature-256``. La comparación se hace en tiempo constante para
    que una firma inválida nunca pueda simular una confirmación o cancelación
    de turno.
    """
    secreto = (settings.whatsapp_app_secret or "").strip()
    if not secreto:
        logger.error("WHATSAPP_APP_SECRET no está configurado; webhook rechazado.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook de WhatsApp no configurado de forma segura.",
        )

    cuerpo = await request.body()
    firma_recibida = request.headers.get("X-Hub-Signature-256", "")
    firma_esperada = "sha256=" + hmac.new(
        secreto.encode("utf-8"), cuerpo, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(firma_recibida, firma_esperada):
        logger.warning("Webhook de WhatsApp rechazado: firma inválida.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma de webhook inválida.",
        )


@router.post(
    "/webhook",
    response_model=WebhookProcesadoResponse,
    status_code=status.HTTP_200_OK,
    summary="Webhook de entrada de WhatsApp",
    description=(
        "Procesa los mensajes entrantes del paciente según su tipo:\n\n"
        "* **text** (el paciente escribe): lo contesta el *FAQ Bot* con el menú "
        "configurado en el panel *Bot WhatsApp* y devuelve 'tipo': 'BOT' con la "
        "'opcion' detectada y el texto enviado. Cada tecla ejecuta el tipo de su "
        "opción (texto libre, precio, obras sociales, ubicación o derivar a una "
        "secretaria). Cuando la tecla no existe, no hay opciones activas o el "
        "mensaje no es del menú no se responde: queda el log **'Atención Manual "
        "Requerida'** y 'atencion_manual': true.\n"
        "* **interactive.button_reply** (botones Confirmar/Cancelar del "
        "recordatorio): extrae el id del mensaje contextual, localiza el turno por "
        "'whatsapp_mensaje_id' y lo pasa a CONFIRMADO o CANCELADO, marcando el "
        "estado de WhatsApp como RESPONDIDO ('tipo': 'BOTON').\n\n"
        "En los dos casos responde 200 con el resultado del procesamiento; un "
        "payload que no encaja en ninguno es 400 y un turno inexistente, 404."
    ),
)
async def recibir_webhook(
    request: Request,
    payload: WhatsAppWebhookPayload,
    db: Session = Depends(get_db),
) -> WebhookProcesadoResponse:
    await validar_firma_webhook(request)
    # Un mensaje escrito por el paciente lo contesta el FAQ Bot…
    mensaje_texto = whatsapp_service.mensaje_de_texto(payload)
    if mensaje_texto is not None:
        try:
            return _procesar_texto_del_bot(db, mensaje_texto)
        except WhatsAppEnvioError:
            # Meta reintenta los webhooks que reciben 5xx. Si el problema es el
            # envío saliente, reintentar el evento sólo duplica los intentos y
            # no lo resuelve. Se confirma la recepción, se deja trazabilidad en
            # el log y la conversación queda disponible para atención humana.
            logger.exception(
                "No se pudo responder el mensaje de %s; requiere atención manual.",
                mensaje_texto.from_,
            )
            return WebhookProcesadoResponse(
                tipo="BOT",
                mensaje_id_entrante=mensaje_texto.id,
                telefono=mensaje_texto.from_,
                atencion_manual=True,
            )

    # …y una respuesta de botón actualiza el turno del recordatorio.
    turno, mensaje, boton_id = whatsapp_service.procesar_respuesta_boton(db, payload)

    return WebhookProcesadoResponse(
        tipo="BOTON",
        turno_id=turno.id,
        mensaje_id_entrante=mensaje.id,
        mensaje_id_contextual=turno.whatsapp_mensaje_id or "",
        boton=boton_id,
        estado_turno=turno.estado_turno,
        estado_whatsapp=turno.estado_whatsapp,
    )


@router.get(
    "/recordatorios-pendientes",
    response_model=List[RecordatorioPendiente],
    status_code=status.HTTP_200_OK,
    summary="Recordatorios pendientes para enviar por enlace (wa.me)",
    description=(
        "Devuelve, ordenados cronológicamente, los turnos que ocurren dentro de "
        "los próximos 'dias' días (por defecto 3) y que siguen en estado "
        "'PENDIENTE_ENVIO'. Cada elemento trae el mensaje ya redactado y el "
        "enlace oficial de WhatsApp Click-to-Chat (wa.me) con el texto "
        "codificado, para abrirlo desde el panel con un clic; al abrirlo, el "
        "panel marca el turno como 'ENVIADO' con "
        "PATCH /api/v1/turnos/{turno_id}."
    ),
)
def listar_recordatorios_pendientes(
    dias: int = Query(
        default=settings.recordatorio_dias,
        ge=1,
        le=30,
        description="Días a futuro a considerar (entre 1 y 30).",
    ),
    db: Session = Depends(get_db),
) -> List[RecordatorioPendiente]:
    return whatsapp_service.listar_recordatorios_click_to_chat(db, dias=dias)
