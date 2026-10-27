"""Endpoints públicos del Módulo 4: ficha de salud (anamnesis).

Rutas (prefijo ``/api/v1``):

* ``GET  /anamnesis/panel`` — panel interno: turnos próximos con el estado de
  la ficha y el enlace público firmado (declarada **antes** de la ruta con
  token para que ``panel`` no se interprete como credencial).
* ``PUT  /anamnesis/panel/{turno_id}`` — la secretaría carga o corrige la ficha
  desde el panel interno, sin token.
* ``GET  /anamnesis/panel/{turno_id}/enlace`` — enlace firmado + mensaje listo
  para mandarle al paciente (sin importar la fecha del turno).
* ``POST /anamnesis/panel/{turno_id}/enviar`` — manda la ficha a mano (devuelve
  el enlace para copiar) o por la WhatsApp Cloud API.
* ``GET  /anamnesis/{token}`` — contexto del turno y estado de la ficha.
* ``POST /anamnesis/{token}`` — envía/actualiza la declaración de salud.

No hay autenticación de paciente: el **token firmado** (HMAC) es la credencial
y sólo permite ver y completar la ficha de ese turno. La página HTML que consume
estas rutas se sirve desde ``/anamnesis/{token}`` (fuera de ``/api/v1``, en
:mod:`app.web.router`) para que el enlace de WhatsApp sea corto. El panel
interno usa el id del turno porque la secretaría ya está dentro del sistema: no
necesita token para cargar la ficha en el mostrador.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.antecedentes_salud import (
    AnamnesisGuardadaRead,
    AnamnesisPublicaRead,
    AntecedentesSaludRead,
    DeclaracionSaludCreate,
    FichaMedicaUpdate,
    FichaSaludEnlaceRead,
    FichaSaludEnvioCreate,
    FichaSaludEnvioRead,
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
    summary="Cargar la ficha médica desde el panel interno",
    description=(
        "Permite que la secretaría o el odontólogo complete o corrija la ficha "
        "médica de un turno sin abrir el enlace público (paciente en el "
        "mostrador o por teléfono). Acepta los antecedentes del paciente y los "
        "bloques que sólo completa el profesional: examen bucal, diagnóstico "
        "presuntivo y plan de tratamiento. Usa la misma validación que el "
        "formulario público, crea la historia clínica si hace falta y devuelve "
        "la alerta médica actualizada. Un turno inexistente responde **404** y "
        "un turno cancelado **409**."
    ),
)
def guardar_ficha_salud_panel(
    turno_id: int,
    payload: FichaMedicaUpdate,
    db: Session = Depends(get_db),
) -> AntecedentesSaludRead:
    return anamnesis_service.guardar_declaracion_turno(db, turno_id, payload)


@router.get(
    "/panel/{turno_id}/enlace",
    response_model=FichaSaludEnlaceRead,
    summary="Obtener el enlace de la ficha para mandarlo al paciente",
    description=(
        "Devuelve el enlace público firmado, el texto sugerido y el enlace "
        "Click-to-Chat (wa.me) para que la secretaría le mande la ficha al "
        "paciente. No filtra por fecha: sirve para cualquier turno no cancelado, "
        "aunque esté fuera de la ventana del panel. El token se firma en el "
        "servidor (el navegador no tiene la clave). Con 'telefono' se puede "
        "apuntar a otro número (por ejemplo el celular desde el que llamó)."
    ),
)
def obtener_enlace_ficha_salud(
    turno_id: int,
    telefono: Optional[str] = Query(
        default=None,
        max_length=40,
        description="Destino alternativo; si se omite se usa el teléfono de la ficha.",
    ),
    db: Session = Depends(get_db),
) -> FichaSaludEnlaceRead:
    return anamnesis_service.obtener_enlace_ficha(db, turno_id, telefono=telefono)


@router.post(
    "/panel/{turno_id}/enviar",
    response_model=FichaSaludEnvioRead,
    status_code=status.HTTP_200_OK,
    summary="Enviar la ficha de salud al paciente",
    description=(
        "Con 'canal=manual' (por defecto) no envía nada: devuelve el enlace y el "
        "texto para copiarlos en WhatsApp Web o en el celular. Con 'canal=api' "
        "envía el mensaje por la WhatsApp Cloud API; si el servidor no tiene "
        "credenciales el envío queda simulado ('simulado=true'). No marca el "
        "recordatorio del turno como enviado: la ficha se completa recién cuando "
        "el paciente envía el formulario. Un turno inexistente responde **404**, "
        "uno cancelado **409** y un canal desconocido **422**."
    ),
)
def enviar_ficha_salud(
    turno_id: int,
    payload: Optional[FichaSaludEnvioCreate] = None,
    db: Session = Depends(get_db),
) -> FichaSaludEnvioRead:
    datos = payload or FichaSaludEnvioCreate()
    return anamnesis_service.enviar_ficha(
        db, turno_id, canal=datos.canal, telefono=datos.telefono
    )


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
