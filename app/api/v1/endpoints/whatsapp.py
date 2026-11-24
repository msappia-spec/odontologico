"""Endpoints de WhatsApp sobre WAHA: webhook de entrada y recordatorios.

El ``POST /whatsapp/webhook`` recibe los eventos de WAHA. Sólo procesa
``event == "message"`` de pacientes (no ``fromMe``, no grupos, no estados):

* ``1`` / ``si`` o ``2`` / ``no`` cuando hay un recordatorio pendiente:
  confirman o cancelan el turno guardado en la conversación.
* cualquier otro texto: lo contesta el **FAQ Bot** (``whatsapp_bot_service``),
  que muestra el menú numerado configurable desde *Bot WhatsApp*
  (``/whatsapp/menu``).

WhatsApp puede entregar el remitente como un identificador de privacidad
(``50783827554549@lid``) en lugar del número. En ese caso el webhook usa el
teléfono real que WAHA publica en ``payload._data.key`` (``remoteJidAlt``,
``senderPn`` o ``participantAlt``) y, si no viniera, contesta al ``@lid``
original: el detalle está en ``whatsapp_service.extraer_mensaje``.

El GET de verificación de Meta ya no existe: WAHA no lo usa. El panel también
ofrece el enlace *Click-to-Chat* de los recordatorios pendientes.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import WhatsAppEnvioError
from app.db.session import get_db
from app.schemas.whatsapp import (
    RecordatorioPendiente,
    WAHAWebhookPayload,
    WebhookProcesadoResponse,
)
from app.services import whatsapp_bot_service, whatsapp_service

logger = logging.getLogger("app.api.whatsapp")

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


def _respuesta_ignorada(
    mensaje_id: str = "", telefono: str = ""
) -> WebhookProcesadoResponse:
    """Respuesta 200 para eventos que WAHA envía pero no hay que contestar."""
    return WebhookProcesadoResponse(
        status="ignorado",
        tipo="IGNORADO",
        mensaje_id_entrante=mensaje_id or None,
        telefono=telefono or None,
    )


def _procesar_texto_del_bot(
    db: Session, telefono: str, texto: str, mensaje_id: str
) -> WebhookProcesadoResponse:
    """Deriva el texto del paciente al bot y arma la respuesta del webhook.

    ``whatsapp_bot_service.responder_mensaje`` decide si hay una confirmación
    pendiente o si corresponde una opción del menú, y **envía** la respuesta
    (real o simulada). El resultado se traduce al contrato del webhook.
    """
    resultado = whatsapp_bot_service.responder_mensaje(db, telefono, texto)

    if resultado.tipo in ("CONFIRMACION", "CANCELACION"):
        return WebhookProcesadoResponse(
            tipo="CONFIRMACION",
            turno_id=resultado.turno_id,
            mensaje_id_entrante=mensaje_id or None,
            boton=(
                "confirmar_turno"
                if resultado.tipo == "CONFIRMACION"
                else "cancelar_turno"
            ),
            estado_turno=resultado.estado_turno,
            estado_whatsapp=resultado.estado_whatsapp,
            telefono=resultado.telefono,
            opcion=resultado.opcion,
            respuesta=resultado.respuesta,
            mensaje_id_saliente=resultado.mensaje_id_saliente,
            atencion_manual=resultado.atencion_manual,
        )

    return WebhookProcesadoResponse(
        tipo="BOT",
        mensaje_id_entrante=mensaje_id or None,
        telefono=resultado.telefono,
        opcion=resultado.opcion,
        respuesta=resultado.respuesta,
        mensaje_id_saliente=resultado.mensaje_id_saliente,
        atencion_manual=resultado.atencion_manual,
    )


@router.post(
    "/webhook",
    response_model=WebhookProcesadoResponse,
    status_code=status.HTTP_200_OK,
    summary="Webhook de entrada de WAHA",
    description=(
        "Procesa los eventos de WAHA. Sólo se atienden los mensajes de pacientes "
        "('event': 'message', 'fromMe': false y remitente distinto de un grupo "
        "o estado); el resto se responde 200 con 'tipo': 'IGNORADO'.\n\n"
        "Si el remitente llega como identificador de privacidad ('...@lid'), se "
        "usa el número real de 'payload._data.key' ('remoteJidAlt', 'senderPn' "
        "o 'participantAlt') y la respuesta sale a ese número; sin él se "
        "contesta al mismo '@lid'.\n\n"
        "* Si hay un recordatorio pendiente para ese teléfono, '1' o 'si' "
        "confirma el turno y '2' o 'no' lo cancela ('tipo': 'CONFIRMACION').\n"
        "* Cualquier otro texto lo contesta el *FAQ Bot* con el menú numerado "
        "configurado en *Bot WhatsApp* ('tipo': 'BOT')."
    ),
)
def recibir_webhook(
    payload: WAHAWebhookPayload,
    db: Session = Depends(get_db),
) -> WebhookProcesadoResponse:
    mensaje = whatsapp_service.extraer_mensaje(payload)
    if mensaje is None:
        return _respuesta_ignorada(
            mensaje_id=(payload.payload.id if payload.payload else "") or "",
            telefono=whatsapp_service.telefono_de_chat(
                payload.payload.from_ if payload.payload else ""
            ),
        )

    telefono, texto, mensaje_id = mensaje
    try:
        return _procesar_texto_del_bot(db, telefono, texto, mensaje_id)
    except WhatsAppEnvioError:
        # Si el problema es el envío saliente, reintentar el webhook sólo
        # duplica los intentos. Se confirma la recepción y queda la
        # conversación disponible para atención humana.
        logger.exception(
            "No se pudo responder el mensaje de %s; requiere atención manual.",
            telefono,
        )
        return WebhookProcesadoResponse(
            tipo="BOT",
            mensaje_id_entrante=mensaje_id or None,
            telefono=telefono,
            atencion_manual=True,
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
