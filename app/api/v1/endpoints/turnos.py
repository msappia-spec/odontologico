"""Endpoints de ``Turno``."""

from datetime import date as Date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.turno import Turno
from app.schemas.turno import TurnoCreate, TurnoRead, TurnoUpdate
from app.services import turno_service

router = APIRouter(prefix="/turnos", tags=["Turnos"])


@router.post(
    "",
    response_model=TurnoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un turno",
    description=(
        "Crea un turno validando que el doctor y el paciente existan, que el "
        "paciente no tenga ya otro turno vigente solapado con otro odontólogo "
        "(HTTP 400) y que el doctor no tenga otro turno solapado en el mismo "
        "rango horario (HTTP 409)."
    ),
)
def crear_turno(payload: TurnoCreate, db: Session = Depends(get_db)) -> Turno:
    return turno_service.crear_turno(db, payload)


@router.get(
    "",
    response_model=List[TurnoRead],
    summary="Listar turnos",
    description=(
        "Lista los turnos ordenados por fecha/hora de inicio. Los filtros son "
        "acumulativos y opcionales: profesional (``doctor_id``), paciente "
        "(``paciente_id``) y **rango de fechas** (``fecha_desde``/``fecha_hasta``, "
        "formato ``AAAA-MM-DD``), que incluye los turnos solapados con esos días. "
        "Un rango invertido responde **422**."
    ),
)
def listar_turnos(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    doctor_id: Optional[int] = Query(None, gt=0),
    paciente_id: Optional[int] = Query(None, gt=0),
    fecha_desde: Optional[Date] = Query(
        None, description="Primer día del rango a consultar (AAAA-MM-DD)."
    ),
    fecha_hasta: Optional[Date] = Query(
        None, description="Último día del rango a consultar (AAAA-MM-DD)."
    ),
    db: Session = Depends(get_db),
) -> List[Turno]:
    return list(
        turno_service.listar_turnos(
            db,
            skip=skip,
            limit=limit,
            doctor_id=doctor_id,
            paciente_id=paciente_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
    )


@router.get(
    "/{turno_id}",
    response_model=TurnoRead,
    summary="Obtener un turno",
    description="Devuelve el detalle de un turno existente según su identificador.",
)
def obtener_turno(turno_id: int, db: Session = Depends(get_db)) -> Turno:
    return turno_service.obtener_turno(db, turno_id)


@router.patch(
    "/{turno_id}",
    response_model=TurnoRead,
    summary="Actualizar un turno",
    description=(
        "Actualiza parcialmente un turno: reprogramar fecha/hora, reasignar "
        "doctor o paciente, confirmar, cancelar o reactivar. Se revalidan el "
        "solapamiento del doctor (HTTP 409) y la ubicuidad del paciente "
        "—que no quede citado a esa hora con otro odontólogo, HTTP 400— salvo "
        "que el turno quede CANCELADO."
    ),
)
def actualizar_turno(
    turno_id: int, payload: TurnoUpdate, db: Session = Depends(get_db)
) -> Turno:
    return turno_service.actualizar_turno(db, turno_id, payload)

