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
* ``GET  /pacientes/{id}/historia-clinica/pdf`` — la Historia Clínica General
  impresa sobre la plantilla de papel (``application/pdf``).
* ``GET  /evoluciones/{id}`` — una atención puntual.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.evolucion_medica import EvolucionMedica
from app.schemas.historia_clinica import (
    EvolucionMedicaCreate,
    EvolucionMedicaRead,
    HistoriaClinicaDetalle,
)
from app.services import ficha_pdf_service, historia_clinica_service

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
    "/pacientes/{paciente_id}/historia-clinica/pdf",
    response_class=Response,
    summary="Imprimir la Historia Clínica General",
    description=(
        "Devuelve la ficha médica como **PDF**, impresa sobre la misma "
        "plantilla de papel del consultorio (Historia Clínica General, dos "
        "páginas). El paciente y lo que declaró van escritos en los renglones y "
        "las casillas SI/NO; el odontograma, el detalle del examen bucal y los "
        "textos que no entran en el formulario salen en una página de anexo. "
        "Se abre en el visor del navegador (``Content-Disposition: inline``) "
        "para imprimir o guardar. El profesional del encabezado se puede elegir "
        "con ``doctor_id``; si se omite, se usa el del odontograma o el de la "
        "última evolución."
    ),
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Historia Clínica General lista para imprimir.",
        }
    },
)
def descargar_historia_clinica_pdf(
    paciente_id: int,
    doctor_id: Optional[int] = Query(
        None,
        gt=0,
        description="Profesional que firma la ficha (por defecto, el de la ficha).",
    ),
    db: Session = Depends(get_db),
) -> Response:
    contenido, nombre = ficha_pdf_service.generar_pdf(
        db, paciente_id, doctor_id=doctor_id
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


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
