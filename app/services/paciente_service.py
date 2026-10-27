"""Servicio de gestión de pacientes (alta, consulta y actualización).

Concentra las reglas de negocio del registro de pacientes, en línea con
``turno_service``:

* El **DNI es único** cuando está informado. La unicidad se defiende en dos
  capas: una verificación previa (mensaje claro) y el índice único
  ``uq_paciente_dni`` de la base de datos (condición de carrera), traducido a un
  ``ConflictoError`` (HTTP 409).
* Los campos administrativos vacíos llegan normalizados a ``None`` desde el
  esquema Pydantic (:class:`app.schemas.paciente.PacienteDatosAdministrativos`).
* La **cobertura** se guarda como clave foránea (``obra_social_id``): el servicio
  traduce el texto heredado ``obra_social`` al catálogo
  (:mod:`app.services.obra_social_service`) y valida que la obra social enviada
  exista (``404`` si no).
"""

import logging
import re
import unicodedata
from typing import Any, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictoError, RecursoNoEncontradoError
from app.models.paciente import Paciente
from app.schemas.paciente import (
    PacienteCreate,
    PacienteDatosFiliatorios,
    PacienteUpdate,
)
from app.services import obra_social_service

logger = logging.getLogger("app.services.pacientes")

#: Filiatorios que la ficha médica comparte con el paciente (Módulo 4). Se
#: derivan del esquema para que un campo nuevo se sume solo.
CAMPOS_FILIATORIOS: tuple[str, ...] = tuple(PacienteDatosFiliatorios.model_fields)


def datos_filiatorios(paciente: Optional[Paciente]) -> dict[str, Any]:
    """Filiatorios vigentes del paciente, listos para un esquema Pydantic.

    La ficha médica los muestra y los escribe; este helper es el único lugar que
    decide qué campos viajan, así el panel, el enlace público y la vista móvil
    devuelven siempre los mismos valores.
    """
    if paciente is None:
        return {}
    return {campo: getattr(paciente, campo, None) for campo in CAMPOS_FILIATORIOS}


# Acentos que se pliegan para buscar por nombre (mismo criterio que el buscador
# del celular: "Suarez" encuentra a "Suárez").
_ACENTOS = (
    ("á", "a"),
    ("é", "e"),
    ("í", "i"),
    ("ó", "o"),
    ("ú", "u"),
    ("ü", "u"),
    ("ñ", "n"),
)


def _sin_acentos(expresion):
    """Devuelve la expresión SQL en minúsculas y sin acentos (portable).

    No todos los motores traen ``unaccent`` (SQLite no lo tiene), así que se
    pliegan los acentos con ``replace`` —funciona igual en SQLite y PostgreSQL—.
    """
    expr = func.lower(expresion)
    for con_acento, sin_acento in _ACENTOS:
        expr = func.replace(expr, con_acento, sin_acento)
    return expr


def _normalizar_busqueda(texto: str) -> str:
    """Normaliza el texto buscado: minúsculas y sin acentos."""
    return (
        unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode().lower()
    )



def obtener_paciente(db: Session, paciente_id: int) -> Paciente:
    """Devuelve el paciente indicado o lanza ``RecursoNoEncontradoError``."""
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(f"No existe un paciente con id={paciente_id}.")
    return paciente


def _resolver_cobertura(db: Session, cambios: dict) -> None:
    """Traduce la cobertura recibida a la clave foránea ``obra_social_id``.

    Reglas (de mayor a menor prioridad):

    * Si el payload trae un ``obra_social_id`` informado se usa tal cual; un id
      inexistente responde ``404``.
    * Si sólo trae el texto heredado ``obra_social``, el nombre se resuelve
      contra el catálogo (se vincula la cobertura existente o se crea una con
      ese nombre).
    * Si la cobertura llega vacía/nula (por cualquiera de las dos vías) el
      paciente queda sin obra social.

    El texto nunca se persiste: ``obra_social`` ya no es una columna.
    """
    enviado_texto = "obra_social" in cambios
    nombre = cambios.pop("obra_social", None)

    if cambios.get("obra_social_id") is not None:
        obra_social_service.obtener_obra_social(db, cambios["obra_social_id"])
        return

    if nombre:
        cambios["obra_social_id"] = obra_social_service.resolver_por_nombre(
            db, nombre
        ).id
    elif enviado_texto or "obra_social_id" in cambios:
        cambios["obra_social_id"] = None



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
    """Crea un paciente validando el DNI único y resolviendo su cobertura."""
    _validar_dni_unico(db, data.dni)

    datos = data.model_dump()
    # ``obra_social`` (texto heredado) se traduce a ``obra_social_id`` antes de
    # construir el ORM: el nombre ya no es una columna del paciente.
    _resolver_cobertura(db, datos)

    paciente = Paciente(**datos)
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
    buscar: Optional[str] = None,
) -> Sequence[Paciente]:
    """Devuelve pacientes ordenados por nombre, con filtros opcionales.

    Filtros:

    * ``telefono`` y ``dni``: coincidencia **exacta** (los usa la integración con
      WhatsApp y los clientes que ya conocen el dato).
    * ``buscar``: búsqueda por **nombre o documento** para las pantallas de
      consulta (historia clínica). El nombre se compara sin distinguir
      mayúsculas ni acentos; el documento se compara parcialmente y sin los
      separadores habituales, así que ``30.123.456`` encuentra a ``30123456``.
    """
    stmt = select(Paciente).order_by(Paciente.nombre)
    if telefono is not None:
        stmt = stmt.where(Paciente.telefono == telefono)
    if dni is not None:
        stmt = stmt.where(Paciente.dni == dni)

    if buscar and buscar.strip():
        texto = buscar.strip()
        buscado = _normalizar_busqueda(texto)
        documento = re.sub(r"[\s.\-]", "", texto)
        criterios = [_sin_acentos(Paciente.nombre).like(f"%{buscado}%")]
        if documento:
            criterios.append(
                func.lower(Paciente.dni).like(f"%{documento.lower()}%")
            )
        stmt = stmt.where(or_(*criterios))

    return db.execute(stmt).scalars().all()


def actualizar_paciente(db: Session, paciente_id: int, data: PacienteUpdate) -> Paciente:
    """Actualiza parcialmente un paciente (``PATCH``).

    Sólo se aplican los campos enviados; si llega un ``dni`` distinto se
    revalida su unicidad contra el resto de los pacientes y la cobertura se
    resuelve contra el catálogo de obras sociales.
    """
    paciente = obtener_paciente(db, paciente_id)
    cambios = data.model_dump(exclude_unset=True)

    if "dni" in cambios:
        _validar_dni_unico(db, cambios["dni"], excluir_paciente_id=paciente.id)

    _resolver_cobertura(db, cambios)

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
