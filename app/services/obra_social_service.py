"""Servicio de gestión del catálogo de obras sociales.

Concentra las reglas de negocio del catálogo que alimenta el desplegable
*Obra social* del formulario de pacientes, en línea con ``paciente_service``:

* El **nombre es único**. La unicidad se defiende en dos capas: una verificación
  previa sin distinguir mayúsculas (mensaje claro, ``409``) y el índice único
  ``uq_obra_social_nombre`` de la base de datos (condición de carrera), traducido
  también a ``ConflictoError``.
* La baja es **lógica**: ``desactivar_obra_social`` marca ``activa = False`` en
  lugar de borrar la fila, porque los pacientes que la tenían siguen
  referenciándola (el nombre se sigue mostrando en su ficha).
"""

import logging
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictoError, RecursoNoEncontradoError
from app.models.obra_social import ObraSocial
from app.schemas.obra_social import ObraSocialCreate, ObraSocialUpdate

logger = logging.getLogger("app.services.obras_sociales")


def obtener_obra_social(db: Session, obra_social_id: int) -> ObraSocial:
    """Devuelve la obra social indicada o lanza ``RecursoNoEncontradoError``."""
    obra_social = db.get(ObraSocial, obra_social_id)
    if obra_social is None:
        raise RecursoNoEncontradoError(
            f"No existe una obra social con id={obra_social_id}."
        )
    return obra_social


def _validar_nombre_libre(
    db: Session, nombre: str, excluir_id: Optional[int] = None
) -> None:
    """Comprueba que no exista otra obra social con el mismo nombre."""
    stmt = select(ObraSocial.id).where(func.lower(ObraSocial.nombre) == nombre.lower())
    if excluir_id is not None:
        stmt = stmt.where(ObraSocial.id != excluir_id)
    existente = db.execute(stmt).scalars().first()
    if existente is not None:
        raise ConflictoError(
            f"Ya existe una obra social con el nombre {nombre} (id={existente})."
        )


def listar_obras_sociales(
    db: Session, activas: Optional[bool] = None, nombre: Optional[str] = None
) -> Sequence[ObraSocial]:
    """Devuelve el catálogo ordenado por nombre, con filtros opcionales.

    :param activas: ``True`` sólo activas, ``False`` sólo dadas de baja,
        ``None`` (por defecto) todas.
    :param nombre: búsqueda parcial por nombre (sin distinguir mayúsculas).
    """
    stmt = select(ObraSocial).order_by(ObraSocial.nombre)
    if activas is not None:
        stmt = stmt.where(ObraSocial.activa == activas)
    if nombre:
        stmt = stmt.where(func.lower(ObraSocial.nombre).contains(nombre.lower()))
    return db.execute(stmt).scalars().all()


def crear_obra_social(db: Session, data: ObraSocialCreate) -> ObraSocial:
    """Crea una obra social validando que el nombre no esté repetido."""
    _validar_nombre_libre(db, data.nombre)

    obra_social = ObraSocial(**data.model_dump())
    db.add(obra_social)
    try:
        db.commit()
    except IntegrityError as exc:  # índice único uq_obra_social_nombre
        db.rollback()
        logger.warning("IntegrityError al crear obra social: %s", exc.orig)
        raise ConflictoError(
            f"Ya existe una obra social con el nombre {data.nombre}."
        ) from exc
    db.refresh(obra_social)
    logger.info("Obra social creada id=%s nombre=%s", obra_social.id, obra_social.nombre)
    return obra_social


def actualizar_obra_social(
    db: Session, obra_social_id: int, data: ObraSocialUpdate
) -> ObraSocial:
    """Actualiza parcialmente una obra social (``PUT``/``PATCH``).

    Sólo se aplican los campos enviados; si llega un ``nombre`` distinto se
    revalida su unicidad contra el resto del catálogo.
    """
    obra_social = obtener_obra_social(db, obra_social_id)
    cambios = data.model_dump(exclude_unset=True)

    if cambios.get("nombre"):
        _validar_nombre_libre(db, cambios["nombre"], excluir_id=obra_social.id)

    for campo, valor in cambios.items():
        setattr(obra_social, campo, valor)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning(
            "IntegrityError al actualizar obra social %s: %s", obra_social_id, exc.orig
        )
        raise ConflictoError(
            f"Ya existe una obra social con el nombre {cambios.get('nombre')}."
        ) from exc
    db.refresh(obra_social)
    logger.info("Obra social actualizada id=%s", obra_social.id)
    return obra_social


def desactivar_obra_social(db: Session, obra_social_id: int) -> ObraSocial:
    """Da de baja la obra social (**borrado lógico**: ``activa = False``).

    La fila se conserva para no romper la referencia de los pacientes que ya la
    tenían, pero deja de ofrecerse en el desplegable del formulario. La operación
    es idempotente: dar de baja dos veces no es un error.
    """
    obra_social = obtener_obra_social(db, obra_social_id)

    if obra_social.activa:
        obra_social.activa = False
        db.commit()
        db.refresh(obra_social)
        logger.info("Obra social desactivada id=%s", obra_social.id)
    else:
        logger.info("Obra social id=%s ya estaba inactiva", obra_social.id)
    return obra_social


def resolver_por_nombre(db: Session, nombre: str) -> ObraSocial:
    """Devuelve la obra social con ese nombre y la crea si no existe.

    Es el puente de compatibilidad para el campo de texto heredado
    ``Paciente.obra_social``: los clientes antiguos (y los ejemplos de ``curl``
    del README) siguen enviando el nombre de la cobertura como texto libre. En
    lugar de rechazar el alta, el servicio **resuelve el nombre contra el
    catálogo** (sin distinguir mayúsculas) y, si no hay coincidencia, registra la
    cobertura como activa para que el dato no se pierda.
    """
    stmt = select(ObraSocial).where(func.lower(ObraSocial.nombre) == nombre.lower())
    existente = db.execute(stmt).scalars().first()
    if existente is not None:
        return existente

    obra_social = ObraSocial(nombre=nombre, activa=True)
    db.add(obra_social)
    db.commit()
    db.refresh(obra_social)
    logger.info(
        "Obra social creada automáticamente desde texto libre: id=%s nombre=%s",
        obra_social.id,
        obra_social.nombre,
    )
    return obra_social
