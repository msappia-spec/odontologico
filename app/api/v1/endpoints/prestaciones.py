"""Endpoints del catálogo de ``Prestacion`` (prácticas facturables, Módulo 3).

CRUD completo del catálogo que alimenta el formulario de cobro y la tabla de
aranceles:

* **Crear** (``POST``): el código de nomenclador es único sin distinguir
  mayúsculas → ``409``.
* **Listar** (``GET``): ``?activas=true/false`` y ``?buscar=`` (código o
  descripción).
* **Modificar** (``PUT``/``PATCH``): ``PUT`` reemplaza el recurso completo,
  ``PATCH`` cambia sólo los campos enviados.
* **Desactivar** (``DELETE``): **baja lógica** (``activa = false``). La práctica
  deja de ofrecerse al facturar, pero sus aranceles y sus facturas se conservan;
  se reactiva con ``PATCH {"activa": true}``.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.prestacion import Prestacion
from app.schemas.prestacion import (
    PrestacionCreate,
    PrestacionRead,
    PrestacionUpdate,
)
from app.services import prestacion_service

router = APIRouter(prefix="/prestaciones", tags=["Prestaciones"])


@router.post(
    "",
    response_model=PrestacionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una prestación",
    description=(
        "Registra una práctica facturable. El código de nomenclador se guarda en "
        "mayúsculas y es único (sin distinguir mayúsculas): repetirlo responde "
        "**409**. El ``valor_base`` es el precio de referencia cuando el paciente "
        "no tiene cobertura."
    ),
)
def crear_prestacion(
    payload: PrestacionCreate, db: Session = Depends(get_db)
) -> Prestacion:
    return prestacion_service.crear_prestacion(db, payload)


@router.get("", response_model=List[PrestacionRead], summary="Listar prestaciones")
def listar_prestaciones(
    activas: Optional[bool] = Query(
        default=None,
        description=(
            "``true`` sólo las vigentes (las que se ofrecen al facturar), "
            "``false`` sólo las dadas de baja. Si se omite, todas."
        ),
    ),
    buscar: Optional[str] = Query(
        default=None,
        description="Búsqueda parcial por código o descripción.",
    ),
    db: Session = Depends(get_db),
) -> List[Prestacion]:
    return list(
        prestacion_service.listar_prestaciones(db, activas=activas, buscar=buscar)
    )


@router.get(
    "/{prestacion_id}",
    response_model=PrestacionRead,
    summary="Obtener una prestación",
    description="Devuelve el detalle de una práctica existente (``404`` si no existe).",
)
def obtener_prestacion(
    prestacion_id: int, db: Session = Depends(get_db)
) -> Prestacion:
    return prestacion_service.obtener_prestacion(db, prestacion_id)


@router.put(
    "/{prestacion_id}",
    response_model=PrestacionRead,
    summary="Reemplazar una prestación",
    description=(
        "Reemplaza el estado completo de la práctica (código, descripción, valor "
        "de referencia y estado). Es un **PUT** clásico: los campos omitidos toman "
        "su valor por defecto (``activa: true``)."
    ),
)
def reemplazar_prestacion(
    prestacion_id: int, payload: PrestacionCreate, db: Session = Depends(get_db)
) -> Prestacion:
    return prestacion_service.actualizar_prestacion(
        db, prestacion_id, PrestacionUpdate(**payload.model_dump())
    )


@router.patch(
    "/{prestacion_id}",
    response_model=PrestacionRead,
    summary="Actualizar una prestación",
    description=(
        "Actualiza parcialmente la práctica: sólo se aplican los campos enviados "
        "(por ejemplo ``{\"valor_base\": 25000}`` o ``{\"activa\": true}`` para "
        "reactivarla)."
    ),
)
def actualizar_prestacion(
    prestacion_id: int, payload: PrestacionUpdate, db: Session = Depends(get_db)
) -> Prestacion:
    return prestacion_service.actualizar_prestacion(db, prestacion_id, payload)


@router.delete(
    "/{prestacion_id}",
    response_model=PrestacionRead,
    summary="Dar de baja una prestación (borrado lógico)",
    description=(
        "**Baja lógica:** marca ``activa = false`` en lugar de borrar la fila. La "
        "práctica deja de ofrecerse al facturar, pero sus aranceles y las facturas "
        "ya emitidas se conservan. Es idempotente y se revierte con "
        "``PATCH {\"activa\": true}``. Responde **404** si no existe."
    ),
)
def desactivar_prestacion(
    prestacion_id: int, db: Session = Depends(get_db)
) -> Prestacion:
    return prestacion_service.desactivar_prestacion(db, prestacion_id)
