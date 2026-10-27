"""Endpoints del odontograma (Módulo 4: ficha odontológica gráfica).

Rutas (prefijo ``/api/v1``):

* ``GET /pacientes/{id}/odontograma`` — gráfico vigente del paciente.
* ``PUT /pacientes/{id}/odontograma`` — guarda el gráfico completo.

El odontograma se mantiene **separado** de la ficha de salud porque lo completa
el profesional después de revisar al paciente, y se corrige cuantas veces haga
falta (a diferencia de las evoluciones, que son inmutables).
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.odontograma import OdontogramaRead, OdontogramaUpdate
from app.services import odontograma_service

router = APIRouter(tags=["Odontograma"])


@router.get(
    "/pacientes/{paciente_id}/odontograma",
    response_model=OdontogramaRead,
    summary="Ver el odontograma de un paciente",
    description=(
        "Devuelve el gráfico dental con la cabecera (prótesis fija y "
        "removible, coronas, cantidad de dientes existentes), los dientes "
        "pintados y el total por estado. Si el paciente todavía no tiene "
        "odontograma responde con la lista vacía e ``id`` en ``null``; si no "
        "existe, **404**. Es de sólo lectura: no crea la historia clínica."
    ),
)
def obtener_odontograma(
    paciente_id: int, db: Session = Depends(get_db)
) -> OdontogramaRead:
    return odontograma_service.obtener_odontograma(db, paciente_id)


@router.put(
    "/pacientes/{paciente_id}/odontograma",
    response_model=OdontogramaRead,
    status_code=status.HTTP_200_OK,
    summary="Guardar el odontograma de un paciente",
    description=(
        "Reemplaza el gráfico del paciente con los dientes enviados (rojo = "
        "prestación existente, azul = prestación requerida, X = ausente o a "
        "extraer). Crea la historia clínica si es la primera vez. Valida los "
        "números FDI (11-48), los estados y que un diente no venga repetido; un "
        "paciente o un profesional inexistente responde **404**."
    ),
)
def guardar_odontograma(
    paciente_id: int,
    payload: OdontogramaUpdate,
    db: Session = Depends(get_db),
) -> OdontogramaRead:
    return odontograma_service.guardar_odontograma(db, paciente_id, payload)
