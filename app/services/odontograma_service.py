"""Servicio del odontograma (Módulo 4: ficha odontológica gráfica).

El odontograma es un gráfico **aparte** de la ficha de salud: el profesional lo
completa después de revisar al paciente, con el mismo criterio de colores que la
Historia Clínica General de papel (rojo = prestación existente, azul =
prestación requerida, X = ausente o a extraer).

Operaciones:

* :func:`obtener_odontograma` — lectura. No crea la historia clínica: un
  paciente que todavía no tiene ficha devuelve un odontograma vacío (``id`` en
  ``null``) en lugar de escribir en la base.
* :func:`guardar_odontograma` — alta o corrección. La historia se crea si hace
  falta (misma regla que la ficha de salud) y el conjunto de dientes enviado
  **reemplaza** al anterior, para que el gráfico guardado sea exactamente el que
  vio el odontólogo.

Un odontograma por historia clínica (``uq_odontograma_historia``): si dos
guardados simultáneos intentan crearlo, el índice único frena al segundo y se
reintenta sobre la fila ya creada.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.datetime_utils import utcnow
from app.core.exceptions import RecursoNoEncontradoError
from app.models.doctor import Doctor
from app.models.odontograma import Odontograma, OdontogramaDiente
from app.models.paciente import Paciente
from app.schemas.odontograma import OdontogramaRead, OdontogramaUpdate
from app.services.historia_clinica_service import (
    obtener_historia,
    obtener_o_crear_historia,
)

logger = logging.getLogger("app.services.odontograma")


def _obtener_paciente(db: Session, paciente_id: int) -> Paciente:
    """Devuelve el paciente o lanza ``RecursoNoEncontradoError`` (HTTP 404)."""
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(
            f"No existe un paciente con id={paciente_id}."
        )
    return paciente


def _odontograma_de(
    db: Session, historia_clinica_id: int
) -> Optional[Odontograma]:
    """Odontograma de una historia con sus dientes cargados (o ``None``)."""
    return db.execute(
        select(Odontograma)
        .options(selectinload(Odontograma.dientes))
        .where(Odontograma.historia_clinica_id == historia_clinica_id)
    ).scalars().first()


def obtener_odontograma(db: Session, paciente_id: int) -> OdontogramaRead:
    """Odontograma vigente del paciente, sin escribir en la base.

    :raises RecursoNoEncontradoError: el paciente no existe.
    """
    paciente = _obtener_paciente(db, paciente_id)
    historia = obtener_historia(db, paciente.id)
    if historia is None:
        return OdontogramaRead()
    odontograma = _odontograma_de(db, historia.id)
    if odontograma is None:
        return OdontogramaRead(historia_clinica_id=historia.id)
    return OdontogramaRead.model_validate(odontograma)


def guardar_odontograma(
    db: Session, paciente_id: int, data: OdontogramaUpdate
) -> OdontogramaRead:
    """Crea o reemplaza el odontograma del paciente.

    Los datos de la cabecera (prótesis, coronas, dientes existentes) se copian
    tal como llegan y la lista de dientes reemplaza por completo a la anterior.

    :raises RecursoNoEncontradoError: el paciente o el profesional no existen.
    """
    paciente = _obtener_paciente(db, paciente_id)
    if data.doctor_id is not None and db.get(Doctor, data.doctor_id) is None:
        raise RecursoNoEncontradoError(
            f"No existe un odontólogo con id={data.doctor_id}."
        )

    odontograma: Optional[Odontograma] = None
    for intento in range(2):
        historia = obtener_o_crear_historia(db, paciente.id)
        odontograma = _odontograma_de(db, historia.id)
        if odontograma is None:
            odontograma = Odontograma(historia_clinica_id=historia.id)
            db.add(odontograma)

        _aplicar_cabecera(odontograma, data)
        odontograma.dientes.clear()
        for item in data.dientes:
            odontograma.dientes.append(
                OdontogramaDiente(
                    diente=item.diente,
                    estado=item.estado,
                    notas=item.notas,
                )
            )

        try:
            db.commit()
        except IntegrityError:
            # Dos guardados a la vez: la otra transacción ya creó la fila.
            db.rollback()
            if intento == 1:  # pragma: no cover - carrera persistente/error
                raise
            logger.warning(
                "Odontograma concurrente para el paciente %s; se reintenta "
                "sobre la fila existente.",
                paciente.id,
            )
            continue
        break

    historia = obtener_o_crear_historia(db, paciente.id)
    guardado = _odontograma_de(db, historia.id)
    assert guardado is not None  # pragma: no cover - recién se guardó
    logger.info(
        "Odontograma guardado (paciente=%s dientes=%s existentes=%s "
        "requeridos=%s ausentes=%s).",
        paciente.id,
        len(guardado.dientes),
        sum(1 for d in guardado.dientes if d.estado == "EXISTENTE"),
        sum(1 for d in guardado.dientes if d.estado == "REQUERIDO"),
        sum(1 for d in guardado.dientes if d.estado == "AUSENTE"),
    )
    return OdontogramaRead.model_validate(guardado)


def _aplicar_cabecera(
    odontograma: Odontograma, data: OdontogramaUpdate
) -> None:
    """Copia la cabecera del payload al ORM (prótesis, coronas y conteos)."""
    odontograma.protesis_fija = data.protesis_fija
    odontograma.protesis_removible = data.protesis_removible
    odontograma.coronas = data.coronas
    odontograma.dientes_existentes = data.dientes_existentes
    odontograma.observaciones = data.observaciones
    if data.doctor_id is not None:
        odontograma.doctor_id = data.doctor_id
    # ``onupdate`` del ORM no se dispara en una fila nueva hasta el flush, así
    # que se marca explícitamente la fecha del último guardado.
    odontograma.actualizado_en = utcnow()
