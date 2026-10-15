"""Servicio del catálogo de prestaciones (prácticas facturables, Módulo 3).

Reglas de negocio, en línea con ``obra_social_service``:

* El **código de nomenclador es único**. Se defiende en dos capas: una
  verificación previa sin distinguir mayúsculas (mensaje claro, ``409``) y el
  índice único ``uq_prestacion_codigo`` de la base de datos (condición de
  carrera), traducido también a ``ConflictoError``.
* La baja es **lógica**: ``desactivar_prestacion`` marca ``activa = False`` en
  lugar de borrar la fila, porque los aranceles cargados y las facturas ya
  emitidas (``detalles_movimiento_caja``) la referencian. La práctica deja de
  ofrecerse al facturar y se puede reactivar desde el panel.
"""

import logging
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictoError, RecursoNoEncontradoError
from app.models.prestacion import Prestacion
from app.schemas.prestacion import PrestacionCreate, PrestacionUpdate

logger = logging.getLogger("app.services.prestaciones")


def obtener_prestacion(db: Session, prestacion_id: int) -> Prestacion:
    """Devuelve la práctica indicada o lanza ``RecursoNoEncontradoError``."""
    prestacion = db.get(Prestacion, prestacion_id)
    if prestacion is None:
        raise RecursoNoEncontradoError(
            f"No existe una prestación con id={prestacion_id}."
        )
    return prestacion


def obtener_prestacion_activa(db: Session, prestacion_id: int) -> Prestacion:
    """Devuelve la práctica sólo si está vigente (se usa al facturar).

    Una práctica dada de baja se comporta como inexistente para la facturación:
    responde ``404`` con un mensaje que explica que está inactiva, para que la
    secretaría la reactive en lugar de cobrar una práctica retirada.
    """
    prestacion = obtener_prestacion(db, prestacion_id)
    if not prestacion.activa:
        raise RecursoNoEncontradoError(
            f"La prestación id={prestacion_id} ({prestacion.codigo_nomenclador}) "
            "está dada de baja y no se puede facturar."
        )
    return prestacion


def _validar_codigo_libre(
    db: Session, codigo: str, excluir_id: Optional[int] = None
) -> None:
    """Comprueba que no exista otra práctica con el mismo código."""
    stmt = select(Prestacion.id).where(
        func.lower(Prestacion.codigo_nomenclador) == codigo.lower()
    )
    if excluir_id is not None:
        stmt = stmt.where(Prestacion.id != excluir_id)
    existente = db.execute(stmt).scalars().first()
    if existente is not None:
        raise ConflictoError(
            f"Ya existe una prestación con el código {codigo} (id={existente})."
        )


def listar_prestaciones(
    db: Session,
    activas: Optional[bool] = None,
    buscar: Optional[str] = None,
) -> Sequence[Prestacion]:
    """Devuelve el catálogo ordenado por código, con filtros opcionales.

    :param activas: ``True`` sólo vigentes, ``False`` sólo dadas de baja,
        ``None`` (por defecto) todas.
    :param buscar: búsqueda parcial por código o descripción (sin distinguir
        mayúsculas).
    """
    stmt = select(Prestacion).order_by(Prestacion.codigo_nomenclador)
    if activas is not None:
        stmt = stmt.where(Prestacion.activa == activas)
    if buscar:
        texto = f"%{buscar.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Prestacion.codigo_nomenclador).like(texto)
            | func.lower(Prestacion.descripcion).like(texto)
        )
    return db.execute(stmt).scalars().all()



def crear_prestacion(db: Session, data: PrestacionCreate) -> Prestacion:
    """Crea una práctica validando que el código no esté repetido."""
    _validar_codigo_libre(db, data.codigo_nomenclador)

    prestacion = Prestacion(**data.model_dump())
    db.add(prestacion)
    try:
        db.commit()
    except IntegrityError as exc:  # índice único uq_prestacion_codigo
        db.rollback()
        logger.warning("IntegrityError al crear prestación: %s", exc.orig)
        raise ConflictoError(
            f"Ya existe una prestación con el código {data.codigo_nomenclador}."
        ) from exc
    db.refresh(prestacion)
    logger.info(
        "Prestación creada id=%s codigo=%s",
        prestacion.id,
        prestacion.codigo_nomenclador,
    )
    return prestacion


def actualizar_prestacion(
    db: Session, prestacion_id: int, data: PrestacionUpdate
) -> Prestacion:
    """Actualiza parcialmente una práctica (``PUT``/``PATCH``).

    Sólo se aplican los campos enviados; si llega un ``codigo_nomenclador``
    distinto se revalida su unicidad contra el resto del catálogo. ``activa``
    forma parte del payload, así que el mismo ``PATCH`` sirve para dar de baja
    (``{"activa": false}``) y para reactivar.
    """
    prestacion = obtener_prestacion(db, prestacion_id)
    cambios = data.model_dump(exclude_unset=True)

    if cambios.get("codigo_nomenclador"):
        _validar_codigo_libre(
            db, cambios["codigo_nomenclador"], excluir_id=prestacion.id
        )

    for campo, valor in cambios.items():
        setattr(prestacion, campo, valor)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning(
            "IntegrityError al actualizar prestación %s: %s", prestacion_id, exc.orig
        )
        raise ConflictoError(
            "Ya existe una prestación con el código "
            f"{cambios.get('codigo_nomenclador')}."
        ) from exc
    db.refresh(prestacion)
    logger.info("Prestación actualizada id=%s", prestacion.id)
    return prestacion


def desactivar_prestacion(db: Session, prestacion_id: int) -> Prestacion:
    """Da de baja la práctica (**borrado lógico**: ``activa = False``).

    La fila se conserva para no romper los aranceles ni las facturas que la
    referencian, pero la práctica deja de ofrecerse al facturar. Es idempotente.
    """
    prestacion = obtener_prestacion(db, prestacion_id)

    if prestacion.activa:
        prestacion.activa = False
        db.commit()
        db.refresh(prestacion)
        logger.info("Prestación desactivada id=%s", prestacion.id)
    else:
        logger.info("Prestación id=%s ya estaba inactiva", prestacion.id)
    return prestacion
