"""Endpoints de WhatsApp: webhook de entrada y recordatorios por enlace (wa.me)."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.schemas.whatsapp import (
    RecordatorioPendiente,
    WebhookProcesadoResponse,
    WhatsAppWebhookPayload,
)
from app.services import whatsapp_service

logger = logging.getLogger("app.api.whatsapp")

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


@router.get(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Verificación del webhook (Meta/WhatsApp)",
    description=(
        "Endpoint de verificación exigido por la WhatsApp Cloud API. Devuelve "
        "el 'hub.challenge' cuando el token de verificación coincide."
    ),
)
def verificar_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
):
    # Se compara contra el access token configurado a modo de ejemplo.
    if hub_verify_token and hub_verify_token == settings.whatsapp_access_token:
        return {"hub.challenge": hub_challenge}
    return {"status": "verificación no válida"}


@router.post(
    "/webhook",
    response_model=WebhookProcesadoResponse,
    status_code=status.HTTP_200_OK,
    summary="Webhook de entrada de WhatsApp",
    description=(
        "Procesa la respuesta interactiva (botón Confirmar/Cancelar) enviada "
        "por el paciente. Extrae el id del mensaje contextual, localiza el "
        "turno por 'whatsapp_mensaje_id' y actualiza su estado a CONFIRMADO o "
        "CANCELADO, marcando el estado de WhatsApp como RESPONDIDO."
    ),
)
def recibir_webhook(
    payload: WhatsAppWebhookPayload,
    db: Session = Depends(get_db),
) -> WebhookProcesadoResponse:
    turno, mensaje, boton_id = whatsapp_service.procesar_respuesta_boton(db, payload)

    return WebhookProcesadoResponse(
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
