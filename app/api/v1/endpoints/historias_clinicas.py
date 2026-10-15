"""Endpoints del Módulo 2: historia clínica y evoluciones médicas.

Regla de inmutabilidad (normativa de salud)
-------------------------------------------
Una evolución médica es un **registro legal**: sólo se puede **crear** (``POST``)
y **leer** (``GET``). Este módulo **no declara —y no debe declarar nunca— rutas
``PUT``, ``PATCH`` ni ``DELETE``** para ``/pacientes/{id}/evoluciones`` ni para
``/evoluciones/{id}``: cualquier intento responde ``405 Method Not Allowed``.
La regla se defiende en tres capas (la API, el servicio y los eventos del ORM)
y `tests/test_historias_clinicas.py` la vigila.

Rutas (prefijo ``/api/v1``):

* ``POST /pacientes/{id}/evoluciones`` — alta de la atención (crea la ficha).
* ``GET  /pacientes/{id}/evoluciones`` — línea de tiempo (más nueva primero).
* ``GET  /pacientes/{id}/historia-clinica`` — ficha + línea de tiempo completa.
* ``GET  /evoluciones/{id}`` — una atención puntual.
"""

from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.evolucion_medica import EvolucionMedica
from app.schemas.historia_clinica import (
    EvolucionMedicaCreate,
    EvolucionMedicaRead,
    HistoriaClinicaDetalle,
)
from app.services import historia_clinica_service

# Sin prefijo propio: las rutas de la ficha cuelgan del paciente (``/pacientes/…``)
# y las de la evolución puntual de ``/evoluciones/…``.
router = APIRouter(tags=["Historias clínicas"])


@router.post(
    "/pacientes/{paciente_id}/evoluciones",
    response_model=EvolucionMedicaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar una evolución médica",
    description=(
        "Registra la atención del odontólogo en la historia clínica del paciente "
        "(la ficha se crea con esta primera evolución y recibe su número, "
        "``HC-000123``). Es la **única operación de escritura** del módulo: una "
        "evolución médica no se puede editar ni eliminar. Responde **404** si el "
        "paciente o el odontólogo no existen y **422** si falta el motivo, el "
        "diagnóstico o el tratamiento."
    ),
)
def crear_evolucion(
    paciente_id: int, payload: EvolucionMedicaCreate, db: Session = Depends(get_db)
) -> EvolucionMedica:
    return historia_clinica_service.crear_evolucion(db, paciente_id, payload)


@router.get(
    "/pacientes/{paciente_id}/evoluciones",
    response_model=List[EvolucionMedicaRead],
    summary="Listar las evoluciones de un paciente",
    description=(
        "Devuelve la línea de tiempo del paciente en orden **cronológico "
        "inverso** (de la atención más nueva a la más vieja), con el odontólogo "
        "que atendió y los archivos adjuntos de cada evolución. Un paciente sin "
        "evoluciones devuelve una lista vacía."
    ),
)
def listar_evoluciones(
    paciente_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[EvolucionMedica]:
    return list(
        historia_clinica_service.listar_evoluciones(
            db, paciente_id, skip=skip, limit=limit
        )
    )


@router.get(
    "/pacientes/{paciente_id}/historia-clinica",
    response_model=HistoriaClinicaDetalle,
    summary="Ver la historia clínica de un paciente",
    description=(
        "Ficha completa del paciente (número de ficha y línea de tiempo) para las "
        "pantallas de consulta: la vista móvil del odontólogo y el Panel Principal "
        "de la secretaría. Es de **sólo lectura**: no crea la ficha si todavía no "
        "existe (en ese caso ``id`` viaja en ``null``) y no admite modificaciones."
    ),
)
def obtener_historia_clinica(
    paciente_id: int, db: Session = Depends(get_db)
) -> HistoriaClinicaDetalle:
    return historia_clinica_service.detalle_historia(db, paciente_id)


@router.get(
    "/evoluciones/{evolucion_id}",
    response_model=EvolucionMedicaRead,
    summary="Obtener una evolución médica",
    description="Devuelve el detalle de una atención registrada (sólo lectura).",
)
def obtener_evolucion(
    evolucion_id: int, db: Session = Depends(get_db)
) -> EvolucionMedica:
    return historia_clinica_service.obtener_evolucion(db, evolucion_id)
