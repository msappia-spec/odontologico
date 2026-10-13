"""Endpoints de ``Doctor``.

Incluye la gestión completa del profesional: alta (``POST``), listado y detalle
(``GET``), edición total (``PUT``), edición parcial (``PATCH``) y **baja lógica**
(``DELETE``: marca ``activo = False`` en lugar de borrar la fila, de modo que sus
turnos y su historia se conservan). Los profesionales dados de baja se pueden
seguir consultando con ``GET /doctores?activos=false`` y reactivar con
``PATCH {"activo": true}``.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.doctor import Doctor
from app.schemas.doctor import (
    DoctorCreate,
    DoctorRead,
    DoctorReplace,
    DoctorUpdate,
)
from app.services import doctor_service

router = APIRouter(prefix="/doctores", tags=["Doctores"])


@router.post(
    "",
    response_model=DoctorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un doctor",
)
def crear_doctor(payload: DoctorCreate, db: Session = Depends(get_db)) -> Doctor:
    return doctor_service.crear_doctor(db, payload)


@router.get("", response_model=List[DoctorRead], summary="Listar doctores")
def listar_doctores(
    activos: Optional[bool] = Query(
        default=None,
        description=(
            "Filtra por estado: ``true`` sólo activos, ``false`` sólo los dados "
            "de baja. Si se omite se devuelven todos."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[Doctor]:
    return list(doctor_service.listar_doctores(db, activos=activos))


@router.get(
    "/{doctor_id}",
    response_model=DoctorRead,
    summary="Obtener un doctor",
    description="Devuelve el detalle de un doctor existente según su identificador.",
)
def obtener_doctor(doctor_id: int, db: Session = Depends(get_db)) -> Doctor:
    return doctor_service.obtener_doctor(db, doctor_id)


@router.put(
    "/{doctor_id}",
    response_model=DoctorRead,
    summary="Reemplazar un doctor",
    description=(
        "Reemplaza los datos del profesional (nombre, especialidad y estado). "
        "Es un **PUT** clásico: el payload es el estado completo del recurso, por "
        "lo que un ``activo`` omitido deja al profesional activo. Para cambios "
        "puntuales (por ejemplo sólo el nombre) usar ``PATCH``."
    ),
)
def reemplazar_doctor(
    doctor_id: int, payload: DoctorReplace, db: Session = Depends(get_db)
) -> Doctor:
    return doctor_service.reemplazar_doctor(db, doctor_id, payload)


@router.patch(
    "/{doctor_id}",
    response_model=DoctorRead,
    summary="Actualizar un doctor",
    description=(
        "Actualiza parcialmente los datos del profesional: sólo se aplican los "
        "campos enviados. ``PATCH {\"activo\": false}`` da de baja al odontólogo "
        "(equivale al ``DELETE``) y ``PATCH {\"activo\": true}`` lo reactiva."
    ),
)
def actualizar_doctor(
    doctor_id: int, payload: DoctorUpdate, db: Session = Depends(get_db)
) -> Doctor:
    return doctor_service.actualizar_doctor(db, doctor_id, payload)


@router.delete(
    "/{doctor_id}",
    response_model=DoctorRead,
    summary="Dar de baja un doctor (borrado lógico)",
    description=(
        "**Baja lógica:** marca ``activo = false`` en lugar de borrar el "
        "registro. El odontólogo deja de ofrecerse para nuevos turnos y de "
        "aparecer en las agendas y desplegables, pero sus turnos y su historial "
        "se conservan. La operación es idempotente (repetirla responde igual) y "
        "se puede revertir con ``PATCH {\"activo\": true}``. Responde **404** si "
        "el profesional no existe."
    ),
)
def dar_de_baja_doctor(doctor_id: int, db: Session = Depends(get_db)) -> Doctor:
    return doctor_service.dar_de_baja(db, doctor_id)

