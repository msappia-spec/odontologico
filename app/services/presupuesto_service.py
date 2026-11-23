"""Servicio del módulo de presupuestos.

Operaciones:

* :func:`listar_presupuestos` — presupuestos del consultorio, con filtro por
  paciente y búsqueda por nombre.
* :func:`obtener_presupuesto` — lectura de un presupuesto con sus renglones y
  totales.
* :func:`crear_presupuesto` / :func:`actualizar_presupuesto` — alta y
  corrección. El conjunto de renglones reemplaza por completo al anterior.
* :func:`eliminar_presupuesto` — baja física del presupuesto (es un documento
  comercial, no un registro clínico).

Los **totales no se persisten**: se calculan al leer (precio total, entregas y
saldo) con la misma aritmética decimal del Módulo 3, así corregir un renglón
nunca deja un total viejo.
"""

import logging
from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.datetime_utils import utcnow
from app.core.exceptions import RecursoNoEncontradoError
from app.models.paciente import Paciente
from app.models.presupuesto import Presupuesto, PresupuestoItem
from app.schemas.presupuesto import (
    PresupuestoCreate,
    PresupuestoItemRead,
    PresupuestoRead,
)

logger = logging.getLogger("app.services.presupuesto")


def _obtener_paciente(db: Session, paciente_id: int) -> Paciente:
    """Devuelve el paciente o lanza ``RecursoNoEncontradoError`` (HTTP 404)."""
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(
            f"No existe un paciente con id={paciente_id}."
        )
    return paciente


def obtener_presupuesto_orm(
    db: Session, presupuesto_id: int
) -> Presupuesto:
    """Presupuesto ORM con sus renglones cargados (o 404).

    Es la puerta que usa también el generador de PDF: mantiene en un solo lugar
    la consulta y las relaciones que necesita el documento.
    """
    presupuesto = db.execute(
        select(Presupuesto)
        .options(selectinload(Presupuesto.items))
        .where(Presupuesto.id == presupuesto_id)
    ).scalars().first()
    if presupuesto is None:
        raise RecursoNoEncontradoError(
            f"No existe un presupuesto con id={presupuesto_id}."
        )
    return presupuesto


def _armar_read(presupuesto: Presupuesto) -> PresupuestoRead:
    """Convierte el ORM en la respuesta pública con los totales calculados."""
    items = [
        PresupuestoItemRead.model_validate(item) for item in presupuesto.items
    ]
    return PresupuestoRead.con_totales(presupuesto=presupuesto, items=items)


def _aplicar(presupuesto: Presupuesto, data: PresupuestoCreate) -> None:
    """Copia la cabecera y reemplaza los renglones del presupuesto."""
    presupuesto.paciente_id = data.paciente_id
    presupuesto.fecha = data.fecha
    presupuesto.forma_pago = data.forma_pago
    presupuesto.direccion = data.direccion

    presupuesto.items.clear()
    for orden, renglon in enumerate(data.items):
        presupuesto.items.append(
            PresupuestoItem(
                orden=orden,
                codigo=renglon.codigo,
                detalle=renglon.detalle,
                precio=renglon.precio,
                entrega=renglon.entrega,
            )
        )
    # ``onupdate`` del ORM no se dispara en una fila nueva hasta el flush: se
    # marca explícitamente la fecha del último guardado.
    presupuesto.actualizado_en = utcnow()


def listar_presupuestos(
    db: Session,
    paciente_id: Optional[int] = None,
    buscar: Optional[str] = None,
) -> List[PresupuestoRead]:
    """Lista los presupuestos del más nuevo al más viejo.

    ``buscar`` compara el nombre o el DNI del paciente, la forma de pago y el
    código o la descripción de los trabajos, sin distinguir mayúsculas.
    """
    stmt = select(Presupuesto).options(selectinload(Presupuesto.items))
    if paciente_id is not None:
        stmt = stmt.where(Presupuesto.paciente_id == paciente_id)
    if buscar and buscar.strip():
        patron = f"%{buscar.strip()}%"
        stmt = stmt.join(Paciente).where(
            or_(
                Paciente.nombre.ilike(patron),
                Paciente.dni.ilike(patron),
                Presupuesto.forma_pago.ilike(patron),
                Presupuesto.items.any(
                    or_(
                        PresupuestoItem.codigo.ilike(patron),
                        PresupuestoItem.detalle.ilike(patron),
                    )
                ),
            )
        )
    stmt = stmt.order_by(Presupuesto.fecha.desc(), Presupuesto.id.desc())
    presupuestos = db.execute(stmt).scalars().unique().all()
    return [_armar_read(presupuesto) for presupuesto in presupuestos]


def obtener_presupuesto(db: Session, presupuesto_id: int) -> PresupuestoRead:
    """Devuelve un presupuesto completo o lanza 404."""
    return _armar_read(obtener_presupuesto_orm(db, presupuesto_id))


def crear_presupuesto(
    db: Session, data: PresupuestoCreate
) -> PresupuestoRead:
    """Crea un presupuesto y devuelve el documento guardado."""
    paciente = _obtener_paciente(db, data.paciente_id)
    presupuesto = Presupuesto(paciente_id=paciente.id)
    db.add(presupuesto)
    _aplicar(presupuesto, data)
    db.commit()

    # Se vuelve a leer para que la respuesta traiga los ids/orden ya asignados
    # y el paciente resuelto por la relación.
    guardado = obtener_presupuesto_orm(db, presupuesto.id)
    logger.info(
        "Presupuesto creado (id=%s paciente=%s items=%s total=%.2f).",
        guardado.id,
        paciente.id,
        len(guardado.items),
        sum(item.precio or 0 for item in guardado.items),
    )
    return _armar_read(guardado)


def actualizar_presupuesto(
    db: Session, presupuesto_id: int, data: PresupuestoCreate
) -> PresupuestoRead:
    """Reemplaza la cabecera y los renglones de un presupuesto existente."""
    presupuesto = obtener_presupuesto_orm(db, presupuesto_id)
    _obtener_paciente(db, data.paciente_id)
    _aplicar(presupuesto, data)
    db.commit()

    guardado = obtener_presupuesto_orm(db, presupuesto_id)
    logger.info(
        "Presupuesto actualizado (id=%s paciente=%s items=%s).",
        guardado.id,
        guardado.paciente_id,
        len(guardado.items),
    )
    return _armar_read(guardado)


def eliminar_presupuesto(db: Session, presupuesto_id: int) -> None:
    """Elimina el presupuesto y sus renglones (la FK usa ``CASCADE``)."""
    presupuesto = obtener_presupuesto_orm(db, presupuesto_id)
    db.delete(presupuesto)
    db.commit()
    logger.info("Presupuesto eliminado (id=%s).", presupuesto_id)
