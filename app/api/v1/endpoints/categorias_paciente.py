"""Endpoints del catálogo de categorías de pacientes.

La pantalla de pacientes administra con estos endpoints las categorías que se
ofrecen al calificar una ficha (por ejemplo «Sin categorizar», «Excelente» y
«Malo», más las que agregue la secretaría):

* **Listar** (``GET``): todas las categorías o sólo las activas (``?activas=``).
* **Crear** (``POST``): el código es único → ``409`` si se repite.
* **Reemplazar / modificar** (``PUT``/``PATCH``): ``PUT`` manda todos los campos
  (lo usa el modal del panel) y ``PATCH`` sólo los enviados (por ejemplo
  ``{activa: true}`` para reactivar o ``{codigo: "NUEVO"}`` para renombrar; en
  ese caso se actualizan en bloque los pacientes que la tenían).
* **Dar de baja** (``DELETE``): **borrado lógico** (``activa = false``).

La categoría ``SIN_CATEGORIZAR`` es la que usa el esquema por defecto: no se
puede desactivar ni cambiarle el código (``422``).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.categoria_paciente import CategoriaPaciente
from app.schemas.categoria_paciente import (
    CategoriaPacienteCreate,
    CategoriaPacienteRead,
    CategoriaPacienteUpdate,
)
from app.services import categoria_paciente_service

router = APIRouter(prefix="/categorias-paciente", tags=["Pacientes"])


@router.get(
    "",
    response_model=List[CategoriaPacienteRead],
    summary="Listar las categorías de pacientes",
)
def listar_categorias(
    activas: Optional[bool] = Query(
        default=None,
        description=(
            "``true`` sólo las categorías que se ofrecen en el formulario, "
            "``false`` sólo las dadas de baja. Si se omite, todas."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[CategoriaPaciente]:
    return list(categoria_paciente_service.listar_categorias(db, activas=activas))


@router.post(
    "",
    response_model=CategoriaPacienteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una categoría de paciente",
    description=(
        "Agrega una categoría al catálogo. El código se normaliza a mayúsculas "
        "y no puede repetirse (**409**). Con ``alerta: true`` el panel pide "
        "observaciones y avisa antes de asignar un turno a un paciente de esa "
        "categoría."
    ),
)
def crear_categoria(
    payload: CategoriaPacienteCreate, db: Session = Depends(get_db)
) -> CategoriaPaciente:
    return categoria_paciente_service.crear_categoria(db, payload)


@router.get(
    "/{categoria_id}",
    response_model=CategoriaPacienteRead,
    summary="Obtener una categoría de paciente",
)
def obtener_categoria(
    categoria_id: int, db: Session = Depends(get_db)
) -> CategoriaPaciente:
    return categoria_paciente_service.obtener_categoria(db, categoria_id)


@router.put(
    "/{categoria_id}",
    response_model=CategoriaPacienteRead,
    summary="Reemplazar una categoría de paciente",
    description="Reemplaza todos los campos (misma validación que el alta).",
)
def reemplazar_categoria(
    categoria_id: int, payload: CategoriaPacienteCreate, db: Session = Depends(get_db)
) -> CategoriaPaciente:
    return categoria_paciente_service.actualizar_categoria(
        db, categoria_id, CategoriaPacienteUpdate(**payload.model_dump())
    )


@router.patch(
    "/{categoria_id}",
    response_model=CategoriaPacienteRead,
    summary="Modificar una categoría de paciente",
    description=(
        "Cambia sólo los campos enviados (por ejemplo ``nombre``, ``alerta``, "
        "``activa`` o ``codigo``). Si cambia el código, los pacientes que tenían "
        "la categoría se actualizan en bloque."
    ),
)
def modificar_categoria(
    categoria_id: int, payload: CategoriaPacienteUpdate, db: Session = Depends(get_db)
) -> CategoriaPaciente:
    return categoria_paciente_service.actualizar_categoria(db, categoria_id, payload)


@router.delete(
    "/{categoria_id}",
    response_model=CategoriaPacienteRead,
    summary="Dar de baja una categoría de paciente",
    description=(
        "**Baja lógica**: deja de ofrecerse en el formulario, pero se conserva "
        "para los pacientes que ya la tenían. Se reactiva con "
        "``PATCH {activa: true}``. La categoría por defecto no puede darse de "
        "baja (**422**)."
    ),
)
def desactivar_categoria(
    categoria_id: int, db: Session = Depends(get_db)
) -> CategoriaPaciente:
    return categoria_paciente_service.desactivar_categoria(db, categoria_id)
