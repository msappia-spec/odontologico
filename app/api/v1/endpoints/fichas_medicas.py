"""Endpoints de la ficha médica del paciente (Módulo 4).

Rutas (prefijo ``/api/v1``):

* ``GET /pacientes/{id}/ficha-medica`` — hoja de papel vigente del paciente.
* ``PUT /pacientes/{id}/ficha-medica`` — guarda la hoja completa.

La ficha médica se mantiene **aparte** del odontograma y de la ficha de salud:
reúne la cabecera del papel (vencimiento, grupo familiar, parentesco y
antecedentes de enfermedad), el estado bucal (sarro y enfermedad periodontal) y
el libro «Trabajo realizado» con sus importes Debe/Haber.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ficha_medica import FichaMedicaRead, FichaMedicaUpdate
from app.services import ficha_medica_service

router = APIRouter(tags=["Ficha médica"])


@router.get(
    "/pacientes/{paciente_id}/ficha-medica",
    response_model=FichaMedicaRead,
    summary="Ver la ficha médica de un paciente",
    description=(
        "Devuelve la cabecera de la ficha (vencimiento, grupo familiar, "
        "parentesco y antecedentes de enfermedad), el estado bucal general "
        "(sarro y enfermedad periodontal), el libro «Trabajo realizado» con el "
        "saldo acumulado y los datos filiatorios del paciente. Si el paciente "
        "todavía no tiene ficha responde con ``id`` en ``null``; si no existe, "
        "**404**. Es de sólo lectura: no crea la historia clínica."
    ),
)
def obtener_ficha_medica(
    paciente_id: int, db: Session = Depends(get_db)
) -> FichaMedicaRead:
    return ficha_medica_service.obtener_ficha(db, paciente_id)


@router.put(
    "/pacientes/{paciente_id}/ficha-medica",
    response_model=FichaMedicaRead,
    status_code=status.HTTP_200_OK,
    summary="Guardar la ficha médica de un paciente",
    description=(
        "Reemplaza la ficha del paciente con la cabecera, el estado bucal y los "
        "renglones de trabajo enviados. Crea la historia clínica si es la "
        "primera vez. Los renglones en blanco se descartan y el saldo se "
        "recalcula (Debe - Haber acumulado); un paciente inexistente responde "
        "**404** y los importes negativos o un texto demasiado largo, **422**."
    ),
)
def guardar_ficha_medica(
    paciente_id: int,
    payload: FichaMedicaUpdate,
    db: Session = Depends(get_db),
) -> FichaMedicaRead:
    return ficha_medica_service.guardar_ficha(db, paciente_id, payload)
