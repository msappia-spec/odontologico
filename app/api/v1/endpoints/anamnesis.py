"""Endpoints públicos del Módulo 4: ficha de salud (anamnesis).

Rutas (prefijo ``/api/v1``):

* ``GET  /anamnesis/panel`` — panel interno: turnos próximos con el estado de
  la ficha y el enlace público firmado (declarada **antes** de la ruta con
  token para que ``panel`` no se interprete como credencial).
* ``PUT  /anamnesis/panel/{turno_id}`` — la secretaría carga o corrige la ficha
  desde el panel interno, sin token.
* ``GET  /anamnesis/{token}`` — contexto del turno y estado de la ficha.
* ``POST /anamnesis/{token}`` — envía/actualiza la declaración de salud.

No hay autenticación de paciente: el **token firmado** (HMAC) es la credencial
y sólo permite ver y completar la ficha de ese turno. La página HTML que consume
estas rutas se sirve desde ``/anamnesis/{token}`` (fuera de ``/api/v1``, en
:mod:`app.web.router`) para que el enlace de WhatsApp sea corto. El panel
interno usa el id del turno porque la secretaría ya está dentro del sistema: no
necesita token para cargar la ficha en el mostrador.
"""

from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.antecedentes_salud import (
    AnamnesisGuardadaRead,
    AnamnesisPublicaRead,
    AntecedentesSaludRead,
    DeclaracionSaludCreate,
    FichaSaludPanelRead,
)
from app.services import anamnesis_service

router = APIRouter(prefix="/anamnesis", tags=["Anamnesis"])


@router.get(
    "/panel",
    response_model=List[FichaSaludPanelRead],
    summary="Listar las fichas de salud de los próximos turnos",
    description=(
        "Vista interna para la secretaría y el odontólogo: devuelve los turnos "
        "no cancelados de hoy y de los próximos 'dias' días con el estado de la "
        "ficha (pendiente/completada), la alerta médica y el enlace público "
        "firmado para abrirla o reenviarla por WhatsApp. Con "
        "'solo_pendientes=true' se ocultan las fichas ya completadas."
    ),
)
def listar_fichas_salud(
    dias: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Días a futuro a considerar (entre 1 y 365).",
    ),
    solo_pendientes: bool = Query(
        default=False,
        description="Devuelve únicamente las fichas que todavía no se completaron.",
    ),
    db: Session = Depends(get_db),
) -> List[FichaSaludPanelRead]:
    return anamnesis_service.listar_fichas_panel(
        db, dias=dias, solo_pendientes=solo_pendientes
    )


@router.put(
    "/panel/{turno_id}",
    response_model=AntecedentesSaludRead,
    status_code=status.HTTP_200_OK,
    summary="Cargar la ficha de salud desde el panel interno",
    description=(
        "Permite que la secretaría complete o corrija la declaración de salud "
        "de un turno sin abrir el enlace público (paciente en el mostrador o "
        "por teléfono). Usa la misma validación que el formulario público, crea "
        "la historia clínica si hace falta y devuelve la alerta médica "
        "actualizada. Un turno inexistente responde **404** y un turno "
        "cancelado **409**."
    ),
)
def guardar_ficha_salud_panel(
    turno_id: int,
    payload: DeclaracionSaludCreate,
    db: Session = Depends(get_db),
) -> AntecedentesSaludRead:
    return anamnesis_service.guardar_declaracion_turno(db, turno_id, payload)


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
