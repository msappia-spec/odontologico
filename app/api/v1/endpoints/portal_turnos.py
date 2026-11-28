"""Endpoints públicos del portal de turnos (``/reservar``).

Este router **no** usa ``get_current_user``: lo consume un paciente desde el
celular, sin cuenta de empleado. Las protecciones son otras:

* :func:`app.core.rate_limit.limitar_portal` en las lecturas y
  :func:`app.core.rate_limit.limitar_operacion_portal` en reservar/cancelar
  (ventana por IP, HTTP 429 con ``Retry-After``);
* respuestas mínimas: la disponibilidad sólo devuelve horas; la búsqueda por
  DNI sólo el nombre de pila, la cobertura y el teléfono enmascarado;
* la reserva revalida el horario bajo bloqueo y el tope de turnos activos vive
  en el servicio.

Las páginas públicas (``/privacidad`` y ``/eliminacion-datos``) y
``POST /whatsapp/webhook`` ya eran públicas; este router se suma a esa lista
acotada del proyecto.
"""

from datetime import date as Date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import dia_semana_de
from app.core.exceptions import ValidacionError
from app.core.rate_limit import limitar_operacion_portal, limitar_portal
from app.db.session import get_db
from app.schemas.disponibilidad import NOMBRES_DIA
from app.schemas.portal_turnos import (
    PortalCancelacionCreate,
    PortalCancelacionRead,
    PortalDisponibilidadRead,
    PortalInicialRead,
    PortalPacienteRead,
    PortalReservaCreate,
    PortalReservaRead,
    normalizar_dni,
)
from app.services import portal_turnos_service

router = APIRouter(prefix="/portal-turnos", tags=["Portal público de turnos"])


@router.get(
    "/inicial",
    response_model=PortalInicialRead,
    dependencies=[Depends(limitar_portal)],
    summary="Catálogos públicos del portal",
    description=(
        "Devuelve únicamente los odontólogos **activos** (id, nombre y "
        "especialidad) y las obras sociales **activas**. No incluye ningún dato "
        "de pacientes."
    ),
)
def inicial(db: Session = Depends(get_db)) -> PortalInicialRead:
    return portal_turnos_service.listar_inicial(db)


@router.get(
    "/disponibilidad",
    response_model=PortalDisponibilidadRead,
    dependencies=[Depends(limitar_portal)],
    summary="Horarios libres de un odontólogo",
    description=(
        "Calcula los bloques libres de ``doctor_id`` en ``fecha`` con la agenda "
        "del Módulo 1 (semanal o excepciones) menos los turnos vigentes. Sólo "
        "devuelve horas (``HH:MM``): nunca el nombre, teléfono ni motivo del "
        "paciente que ocupa otro turno. Las fechas pasadas responden 422 y las "
        "horas ya transcurridas del día actual no se ofrecen."
    ),
)
def disponibilidad(
    doctor_id: int = Query(..., gt=0),
    fecha: Date = Query(..., description="Fecha a consultar (AAAA-MM-DD)."),
    db: Session = Depends(get_db),
) -> PortalDisponibilidadRead:
    horarios = portal_turnos_service.horarios_libres(db, doctor_id, fecha)
    dia_semana = dia_semana_de(fecha)
    return PortalDisponibilidadRead(
        doctor_id=doctor_id,
        fecha=fecha,
        dia=NOMBRES_DIA[dia_semana],
        duracion_minutos=settings.portal_duracion_turno_min,
        horarios=horarios,
    )


@router.get(
    "/buscar-paciente",
    response_model=PortalPacienteRead,
    dependencies=[Depends(limitar_portal)],
    summary="Buscar un DNI para agilizar la reserva",
    description=(
        "Si el DNI existe devuelve **sólo el nombre de pila**, la obra social "
        "actual y el teléfono enmascarado (``***4589``); si no existe responde "
        "``nuevo: true``. Nunca devuelve apellido, teléfono completo, motivo de "
        "consulta ni datos de otros pacientes."
    ),
)
def buscar_paciente(
    dni: str = Query(..., min_length=5, max_length=20),
    db: Session = Depends(get_db),
) -> PortalPacienteRead:
    try:
        documento = normalizar_dni(dni)
    except ValueError as exc:
        raise ValidacionError(str(exc)) from exc
    return portal_turnos_service.buscar_paciente(db, documento)


@router.post(
    "/reservar",
    response_model=PortalReservaRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limitar_operacion_portal)],
    summary="Reservar un turno",
    description=(
        "Da de alta al paciente si el DNI no existe o actualiza su teléfono y "
        "obra social si ya estaba registrado. Impide más de dos turnos futuros "
        "vigentes por DNI (409) y vuelve a comprobar el horario dentro de una "
        "transacción con el odontólogo bloqueado, de modo que dos reservas "
        "simultáneas no puedan quedarse con el mismo bloque. Si WAHA está "
        "configurado envía el WhatsApp de confirmación con un código corto de "
        "cancelación."
    ),
)
def reservar(
    payload: PortalReservaCreate, db: Session = Depends(get_db)
) -> PortalReservaRead:
    return portal_turnos_service.reservar(db, payload)


@router.post(
    "/cancelar",
    response_model=PortalCancelacionRead,
    dependencies=[Depends(limitar_operacion_portal)],
    summary="Cancelar un turno propio",
    description=(
        "Cancela un turno con el DNI del paciente y el código de autogestión "
        "(o el número de turno) del mensaje de confirmación. Sólo se permite "
        "hasta 12 horas antes de la cita; el bloque vuelve a quedar libre en la "
        "agenda del profesional. Si el turno ya estaba cancelado responde 200 "
        "con ``ya_cancelado: true``."
    ),
)
def cancelar(
    payload: PortalCancelacionCreate, db: Session = Depends(get_db)
) -> PortalCancelacionRead:
    return portal_turnos_service.cancelar(db, payload)
