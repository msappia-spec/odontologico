"""Endpoints de ``Paciente``."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.paciente import Paciente
from app.schemas.paciente import PacienteCreate, PacienteRead, PacienteUpdate
from app.services import paciente_service

router = APIRouter(prefix="/pacientes", tags=["Pacientes"])


@router.post(
    "",
    response_model=PacienteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un paciente",
    description=(
        "Registra un paciente. El DNI es opcional pero, si se informa, es único: "
        "un documento ya registrado devuelve 409. Los campos vacíos se guardan "
        "como nulos y el DNI se normaliza (sin puntos ni guiones). También se "
        "aceptan los datos filiatorios de la Historia Clínica General (fecha de "
        "nacimiento, estado civil, nacionalidad, domicilio, profesión, titular, "
        "lugar de trabajo y jerarquía); la edad se calcula, no se envía."
    ),
)
def crear_paciente(payload: PacienteCreate, db: Session = Depends(get_db)) -> Paciente:
    return paciente_service.crear_paciente(db, payload)


@router.get("", response_model=List[PacienteRead], summary="Listar pacientes")
def listar_pacientes(
    telefono: Optional[str] = Query(default=None, description="Filtra por teléfono exacto"),
    dni: Optional[str] = Query(default=None, description="Filtra por DNI exacto"),
    buscar: Optional[str] = Query(
        default=None,
        max_length=120,
        description=(
            "Búsqueda por **nombre o DNI**: trae las fichas cuyo nombre contenga el "
            "texto (sin distinguir mayúsculas ni acentos) o cuyo documento coincida "
            "parcialmente (se ignoran puntos y guiones)."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[Paciente]:
    return list(
        paciente_service.listar_pacientes(
            db, telefono=telefono, dni=dni, buscar=buscar
        )
    )


@router.get(
    "/{paciente_id}",
    response_model=PacienteRead,
    summary="Obtener un paciente",
    description="Devuelve el detalle de un paciente existente según su identificador.",
)
def obtener_paciente(paciente_id: int, db: Session = Depends(get_db)) -> Paciente:
    return paciente_service.obtener_paciente(db, paciente_id)


@router.patch(
    "/{paciente_id}",
    response_model=PacienteRead,
    summary="Actualizar un paciente",
    description=(
        "Actualiza parcialmente los datos del paciente (nombre, teléfono, email, "
        "DNI, obra social, Nº de afiliado y datos filiatorios). Sólo se aplican "
        "los campos enviados y el DNI sigue siendo único entre pacientes. Es la "
        "vía para limpiar un dato filiatorio: la ficha médica, en cambio, nunca "
        "borra lo que ya estaba cargado."
    ),
)
def actualizar_paciente(
    paciente_id: int, payload: PacienteUpdate, db: Session = Depends(get_db)
) -> Paciente:
    return paciente_service.actualizar_paciente(db, paciente_id, payload)

