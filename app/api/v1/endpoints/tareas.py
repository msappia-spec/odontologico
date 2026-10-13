"""Endpoints de tareas programadas (scheduler del Módulo 1)."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.whatsapp import RecordatorioResultado
from app.services import whatsapp_service

logger = logging.getLogger("app.api.tareas")

router = APIRouter(prefix="/tareas", tags=["Tareas programadas"])


@router.post(
    "/enviar-recordatorios",
    response_model=RecordatorioResultado,
    summary="Enviar recordatorios de WhatsApp (tarea programada)",
    description=(
        "Calcula los turnos que ocurren exactamente dentro de N días (por "
        "defecto 3), filtra los que están en estado PENDIENTE / PENDIENTE_ENVIO "
        "y dispara el envío (simulado o real) del recordatorio por WhatsApp."
    ),
)
def enviar_recordatorios_ahora(
    dias: Optional[int] = Query(
        default=None,
        ge=0,
        le=30,
        description="Antelación en días. Si se omite, usa la configuración global.",
    ),
    db: Session = Depends(get_db),
) -> RecordatorioResultado:
    return whatsapp_service.enviar_recordatorios(db, dias=dias)
