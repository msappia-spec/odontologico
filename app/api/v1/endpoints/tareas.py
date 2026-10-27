"""Endpoints de tareas programadas (scheduler del Módulo 1)."""

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.schemas.whatsapp import RecordatorioResultado
from app.services import whatsapp_service

logger = logging.getLogger("app.api.tareas")

router = APIRouter(prefix="/tareas", tags=["Tareas programadas"])


def validar_secret_cron(authorization: Optional[str] = Header(default=None)) -> None:
    """Autoriza exclusivamente al cron de Vercel a iniciar envíos automáticos."""
    secreto = (settings.cron_secret or "").strip()
    esperado = f"Bearer {secreto}" if secreto else ""
    if not secreto or not authorization or not hmac.compare_digest(authorization, esperado):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autorizado para ejecutar recordatorios.",
        )


def _enviar_recordatorios(
    dias: Optional[int], db: Session
) -> RecordatorioResultado:
    return whatsapp_service.enviar_recordatorios(db, dias=dias)


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
    _: None = Depends(validar_secret_cron),
    db: Session = Depends(get_db),
) -> RecordatorioResultado:
    return _enviar_recordatorios(dias, db)


@router.get(
    "/enviar-recordatorios",
    response_model=RecordatorioResultado,
    summary="Ejecutar recordatorios desde Vercel Cron",
)
def enviar_recordatorios_cron(
    dias: Optional[int] = Query(default=None, ge=0, le=30),
    _: None = Depends(validar_secret_cron),
    db: Session = Depends(get_db),
) -> RecordatorioResultado:
    return _enviar_recordatorios(dias, db)
