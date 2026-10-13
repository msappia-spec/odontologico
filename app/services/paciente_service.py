"""Servicio de gestión de pacientes (alta, consulta y actualización).

Concentra las reglas de negocio del registro de pacientes, en línea con
``turno_service``:

* El **DNI es único** cuando está informado. La unicidad se defiende en dos
  capas: una verificación previa (mensaje claro) y el índice único
  ``uq_paciente_dni`` de la base de datos (condición de carrera), traducido a un
  ``ConflictoError`` (HTTP 409).
* Los campos administrativos vacíos llegan normalizados a ``None`` desde el
  esquema Pydantic (:class:`app.schemas.paciente.PacienteDatosAdministrativos`).
"""

import logging
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictoError, RecursoNoEncontradoError
from app.models.paciente import Paciente
from app.schemas.paciente import PacienteCreate, PacienteUpdate

logger = logging.getLogger("app.services.pacientes")


def obtener_paciente(db: Session, paciente_id: int) -> Paciente:
    """Devuelve el paciente indicado o lanza ``RecursoNoEncontradoError``."""
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(f"No existe un paciente con id={paciente_id}.")
    return paciente


def _validar_dni_unico(
    db: Session, dni: Optional[str], excluir_paciente_id: Optional[int] = None
) -> None:
    """Comprueba que el DNI no esté registrado por otro paciente."""
    if not dni:
        return
    stmt = select(Paciente.id).where(Paciente.dni == dni)
    if excluir_paciente_id is not None:
        stmt = stmt.where(Paciente.id != excluir_paciente_id)
    existente = db.execute(stmt).scalars().first()
    if existente is not None:
        raise ConflictoError(
            f"Ya existe un paciente con el DNI {dni} (id={existente})."
        )


def crear_paciente(db: Session, data: PacienteCreate) -> Paciente:
    """Crea un paciente validando que su DNI (si viene) no esté repetido."""
    _validar_dni_unico(db, data.dni)

    paciente = Paciente(**data.model_dump())
    db.add(paciente)
    try:
        db.commit()
    except IntegrityError as exc:
        # Índice único uq_paciente_dni (dos altas simultáneas con el mismo DNI).
        db.rollback()
        logger.warning("IntegrityError al crear paciente: %s", exc.orig)
        raise ConflictoError(f"Ya existe un paciente con el DNI {data.dni}.") from exc
    db.refresh(paciente)
    logger.info("Paciente creado id=%s", paciente.id)
    return paciente


def listar_pacientes(
    db: Session,
    telefono: Optional[str] = None,
    dni: Optional[str] = None,
) -> Sequence[Paciente]:
    """Devuelve pacientes ordenados por nombre, con filtros exactos opcionales."""
    stmt = select(Paciente).order_by(Paciente.nombre)
    if telefono is not None:
        stmt = stmt.where(Paciente.telefono == telefono)
    if dni is not None:
        stmt = stmt.where(Paciente.dni == dni)
    return db.execute(stmt).scalars().all()


def actualizar_paciente(db: Session, paciente_id: int, data: PacienteUpdate) -> Paciente:
    """Actualiza parcialmente un paciente (``PATCH``).

    Sólo se aplican los campos enviados; si llega un ``dni`` distinto se
    revalida su unicidad contra el resto de los pacientes.
    """
    paciente = obtener_paciente(db, paciente_id)
    cambios = data.model_dump(exclude_unset=True)

    if "dni" in cambios:
        _validar_dni_unico(db, cambios["dni"], excluir_paciente_id=paciente.id)

    for campo, valor in cambios.items():
        setattr(paciente, campo, valor)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning(
            "IntegrityError al actualizar paciente %s: %s", paciente_id, exc.orig
        )
        raise ConflictoError(
            f"Ya existe un paciente con el DNI {cambios.get('dni')}."
        ) from exc
    db.refresh(paciente)
    logger.info("Paciente actualizado id=%s", paciente.id)
    return paciente
