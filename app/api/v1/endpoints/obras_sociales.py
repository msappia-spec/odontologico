"""Endpoints del catálogo de ``ObraSocial`` (obras sociales y prepagas).

CRUD completo pensado para la pantalla *Gestión de Obras Sociales* del panel:

* **Crear** (``POST``): el nombre es único sin distinguir mayúsculas → ``409``.
* **Listar** (``GET``): ``?activas=true/false`` para separar el catálogo vigente
  del dado de baja y ``?nombre=`` para buscar.
* **Modificar** (``PUT``/``PATCH``): ``PUT`` reemplaza el recurso completo,
  ``PATCH`` cambia sólo los campos enviados (es el que usa el modal del panel).
* **Desactivar** (``DELETE``): **baja lógica** (``activa = false``). La cobertura
  deja de ofrecerse en el desplegable de pacientes, pero se conserva porque los
  pacientes que la tenían siguen apuntándole.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.obra_social import ObraSocial
from app.schemas.obra_social import (
    ObraSocialCreate,
    ObraSocialRead,
    ObraSocialUpdate,
)
from app.services import obra_social_service

router = APIRouter(prefix="/obras-sociales", tags=["Obras Sociales"])


@router.post(
    "",
    response_model=ObraSocialRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una obra social",
    description=(
        "Registra una cobertura en el catálogo. El nombre se guarda sin espacios "
        "sobrantes y es único (sin distinguir mayúsculas): repetirlo responde "
        "**409**."
    ),
)
def crear_obra_social(
    payload: ObraSocialCreate, db: Session = Depends(get_db)
) -> ObraSocial:
    return obra_social_service.crear_obra_social(db, payload)


@router.get("", response_model=List[ObraSocialRead], summary="Listar obras sociales")
def listar_obras_sociales(
    activas: Optional[bool] = Query(
        default=None,
        description=(
            "``true`` sólo las activas (las que ofrece el desplegable de "
            "pacientes), ``false`` sólo las dadas de baja. Si se omite, todas."
        ),
    ),
    nombre: Optional[str] = Query(
        default=None, description="Búsqueda parcial por nombre (sin distinguir mayúsculas)."
    ),
    db: Session = Depends(get_db),
) -> List[ObraSocial]:
    return list(obra_social_service.listar_obras_sociales(db, activas=activas, nombre=nombre))


@router.get(
    "/{obra_social_id}",
    response_model=ObraSocialRead,
    summary="Obtener una obra social",
    description="Devuelve el detalle de una cobertura existente (``404`` si no existe).",
)
def obtener_obra_social(obra_social_id: int, db: Session = Depends(get_db)) -> ObraSocial:
    return obra_social_service.obtener_obra_social(db, obra_social_id)


@router.put(
    "/{obra_social_id}",
    response_model=ObraSocialRead,
    summary="Reemplazar una obra social",
    description=(
        "Reemplaza el estado completo de la cobertura (nombre, código y estado). "
        "Es un **PUT** clásico: los campos omitidos toman su valor por defecto "
        "(``activa: true``). El nombre sigue siendo único (``409``)."
    ),
)
def reemplazar_obra_social(
    obra_social_id: int, payload: ObraSocialCreate, db: Session = Depends(get_db)
) -> ObraSocial:
    return obra_social_service.actualizar_obra_social(
        db, obra_social_id, ObraSocialUpdate(**payload.model_dump())
    )


@router.patch(
    "/{obra_social_id}",
    response_model=ObraSocialRead,
    summary="Actualizar una obra social",
    description=(
        "Actualiza parcialmente la cobertura: sólo se aplican los campos "
        "enviados (por ejemplo ``{\"nombre\": \"Medicus\"}`` o "
        "``{\"activa\": true}`` para reactivarla)."
    ),
)
def actualizar_obra_social(
    obra_social_id: int, payload: ObraSocialUpdate, db: Session = Depends(get_db)
) -> ObraSocial:
    return obra_social_service.actualizar_obra_social(db, obra_social_id, payload)


@router.delete(
    "/{obra_social_id}",
    response_model=ObraSocialRead,
    summary="Desactivar una obra social (borrado lógico)",
    description=(
        "**Baja lógica:** marca ``activa = false`` en lugar de borrar el "
        "registro. La cobertura deja de aparecer en el desplegable del formulario "
        "de pacientes, pero los pacientes que ya la tenían la conservan. Se puede "
        "reactivar con ``PATCH {\"activa\": true}``. Responde **404** si no existe."
    ),
)
def desactivar_obra_social(
    obra_social_id: int, db: Session = Depends(get_db)
) -> ObraSocial:
    return obra_social_service.desactivar_obra_social(db, obra_social_id)
