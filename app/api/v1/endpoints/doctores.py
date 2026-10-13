"""Endpoints de ``Doctor``."""

from typing import List, Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.doctor import Doctor
from app.schemas.doctor import DoctorCreate, DoctorRead

router = APIRouter(prefix="/doctores", tags=["Doctores"])


@router.post(
    "",
    response_model=DoctorRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un doctor",
)
def crear_doctor(payload: DoctorCreate, db: Session = Depends(get_db)) -> Doctor:
    doctor = Doctor(**payload.model_dump())
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor


@router.get("", response_model=List[DoctorRead], summary="Listar doctores")
def listar_doctores(
    activos: Optional[bool] = None,
    db: Session = Depends(get_db),
) -> List[Doctor]:
    stmt = select(Doctor).order_by(Doctor.nombre)
    if activos is not None:
        stmt = stmt.where(Doctor.activo == activos)
    return list(db.execute(stmt).scalars().all())
