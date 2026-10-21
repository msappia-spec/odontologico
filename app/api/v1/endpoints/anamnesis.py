"""Endpoints públicos del Módulo 4: ficha de salud (anamnesis).

Rutas (prefijo ``/api/v1``):

* ``GET  /anamnesis/{token}`` — contexto del turno y estado de la ficha.
* ``POST /anamnesis/{token}`` — envía/actualiza la declaración de salud.

No hay autenticación de paciente: el **token firmado** (HMAC) es la credencial
y sólo permite ver y completar la ficha de ese turno. La página HTML que consume
estas rutas se sirve desde ``/anamnesis/{token}`` (fuera de ``/api/v1``, en
:mod:`app.web.router`) para que el enlace de WhatsApp sea corto.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.antecedentes_salud import (
    AnamnesisGuardadaRead,
    AnamnesisPublicaRead,
    DeclaracionSaludCreate,
)
from app.services import anamnesis_service

router = APIRouter(prefix="/anamnesis", tags=["Anamnesis"])


@router.get(
    "/{token}",
    response_model=AnamnesisPublicaRead,
    summary="Abrir la ficha de salud de un turno",
    description=(
        "Valida el token firmado que llegó por WhatsApp y devuelve el paciente, "
        "el profesional, la fecha del turno y la declaración de salud ya "
        "enviada (si existe). Un token alterado responde **400** y un turno "
        "inexistente **404**."
    ),
)
def obtener_anamnesis(
    token: str, db: Session = Depends(get_db)
) -> AnamnesisPublicaRead:
    return anamnesis_service.obtener_formulario_publico(db, token)


@router.post(
    "/{token}",
    response_model=AnamnesisGuardadaRead,
    status_code=status.HTTP_200_OK,
    summary="Enviar la ficha de salud",
    description=(
        "Guarda la declaración del paciente, crea su historia clínica si es la "
        "primera vez y marca la ficha como completada. El odontólogo recibe la "
        "alerta médica cuando hay alergias, presión alta, problemas cardíacos, "
        "medicación actual o embarazo. Reenviar con el mismo enlace corrige la "
        "declaración existente."
    ),
)
def guardar_anamnesis(
    token: str,
    payload: DeclaracionSaludCreate,
    db: Session = Depends(get_db),
) -> AnamnesisGuardadaRead:
    return anamnesis_service.guardar_declaracion_publica(db, token, payload)
