"""Servicio de disponibilidad semanal de los odontólogos.

Gestiona los rangos de ``DisponibilidadDoctor`` (altas, reemplazo total de la
semana, bajas y consultas) y expone :func:`rangos_del_dia`, el punto de entrada
que usa :mod:`app.services.turno_service` para generar los bloques de turnos
disponibles de una fecha.
"""

import logging
from typing import Iterable, List, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
)
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.doctor import Doctor
from app.schemas.disponibilidad import (
    NOMBRES_DIA,
    DisponibilidadBase,
    DisponibilidadCreate,
)

logger = logging.getLogger("app.services.disponibilidad")


def _obtener_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise RecursoNoEncontradoError(f"No existe un doctor con id={doctor_id}.")
    return doctor


def _ordenar(rangos: Sequence[DisponibilidadDoctor]) -> List[DisponibilidadDoctor]:
    """Orden estable por día y hora de inicio (lunes a domingo según el índice)."""
    return sorted(rangos, key=lambda r: (r.dia_semana, r.hora_inicio, r.hora_fin))


def _solapan(inicio_a, fin_a, inicio_b, fin_b) -> bool:
    """Dos rangos se solapan cuando uno empieza antes de que termine el otro."""
    return inicio_a < fin_b and inicio_b < fin_a


def listar_rangos(db: Session, doctor_id: int) -> List[DisponibilidadDoctor]:
    """Devuelve la semana completa del odontólogo ordenada por día y hora."""
    _obtener_doctor(db, doctor_id)
    stmt = (
        select(DisponibilidadDoctor)
        .where(DisponibilidadDoctor.doctor_id == doctor_id)
        .order_by(
            DisponibilidadDoctor.dia_semana,
            DisponibilidadDoctor.hora_inicio,
            DisponibilidadDoctor.hora_fin,
        )
    )
    return list(db.execute(stmt).scalars().all())


def rangos_del_dia(
    db: Session, doctor_id: int, dia_semana: int
) -> List[DisponibilidadDoctor]:
    """Rangos de atención del odontólogo para ``dia_semana`` (0 = domingo).

    No valida la existencia del doctor: la usan tanto los endpoints (que ya la
    validaron) como la generación de bloques del servicio de turnos.
    """
    stmt = (
        select(DisponibilidadDoctor)
        .where(
            DisponibilidadDoctor.doctor_id == doctor_id,
            DisponibilidadDoctor.dia_semana == dia_semana,
        )
        .order_by(DisponibilidadDoctor.hora_inicio)
    )
    return list(db.execute(stmt).scalars().all())


def agregar_rango(
    db: Session, doctor_id: int, data: DisponibilidadCreate
) -> DisponibilidadDoctor:
    """Agrega un rango al odontólogo.

    Si el rango se superpone con otro ya configurado para el mismo día de la
    semana responde ``409`` (``ConflictoError``): un horario no puede quedar
    habilitado dos veces. Los rangos **contiguos** (p. ej. 08:00–12:00 y
    12:00–16:00) se aceptan.
    """
    _obtener_doctor(db, doctor_id)

    for existente in rangos_del_dia(db, doctor_id, data.dia_semana):
        if _solapan(
            data.hora_inicio,
            data.hora_fin,
            existente.hora_inicio,
            existente.hora_fin,
        ):
            raise ConflictoError(
                f"El rango {data.hora_inicio:%H:%M}-{data.hora_fin:%H:%M} se solapa "
                f"con el rango ya configurado "
                f"{existente.hora_inicio:%H:%M}-{existente.hora_fin:%H:%M} del "
                f"{NOMBRES_DIA[data.dia_semana]}."
            )

    rango = DisponibilidadDoctor(
        doctor_id=doctor_id,
        dia_semana=data.dia_semana,
        hora_inicio=data.hora_inicio,
        hora_fin=data.hora_fin,
    )
    db.add(rango)
    db.commit()
    db.refresh(rango)
    logger.info(
        "Disponibilidad agregada id=%s doctor_id=%s dia=%s %s-%s",
        rango.id,
        doctor_id,
        NOMBRES_DIA[rango.dia_semana],
        rango.hora_inicio.strftime("%H:%M"),
        rango.hora_fin.strftime("%H:%M"),
    )
    return rango


def reemplazar_agenda(
    db: Session, doctor_id: int, rangos: Iterable[DisponibilidadBase]
) -> List[DisponibilidadDoctor]:
    """Reemplaza **toda** la semana del odontólogo por ``rangos``.

    Pensado para el editor semanal del panel: la secretaría envía el estado
    completo de la semana y el backend la deja exactamente así (una lista vacía
    deshabilita al profesional). La validación de orden y de solapes dentro del
    payload la hace :class:`~app.schemas.disponibilidad.DisponibilidadReemplazo`
    (``422``).
    """
    _obtener_doctor(db, doctor_id)
    nuevos = list(rangos)

    db.execute(
        delete(DisponibilidadDoctor).where(
            DisponibilidadDoctor.doctor_id == doctor_id
        )
    )
    for rango in nuevos:
        db.add(
            DisponibilidadDoctor(
                doctor_id=doctor_id,
                dia_semana=rango.dia_semana,
                hora_inicio=rango.hora_inicio,
                hora_fin=rango.hora_fin,
            )
        )
    db.commit()
    logger.info(
        "Disponibilidad reemplazada doctor_id=%s (%s rangos)", doctor_id, len(nuevos)
    )
    return listar_rangos(db, doctor_id)


def eliminar_rango(db: Session, doctor_id: int, rango_id: int) -> None:
    """Elimina un rango del odontólogo (``404`` si no existe o es de otro)."""
    _obtener_doctor(db, doctor_id)
    rango = db.get(DisponibilidadDoctor, rango_id)
    if rango is None or rango.doctor_id != doctor_id:
        raise RecursoNoEncontradoError(
            f"No existe un rango de disponibilidad con id={rango_id} "
            f"para el doctor id={doctor_id}."
        )
    db.delete(rango)
    db.commit()
    logger.info("Disponibilidad eliminada id=%s doctor_id=%s", rango_id, doctor_id)
