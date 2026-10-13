"""Servicio de gestión de turnos (alta, consulta y validación de solapamientos).

Además del CRUD, expone :func:`horarios_disponibles`, que arma los bloques de
turnos que el calendario ofrece para un odontólogo y una fecha: parte los rangos
configurados en ``DisponibilidadDoctor`` (varios tramos por día) y descarta los
bloques que se cruzan con turnos vigentes.
"""

import logging
from datetime import date as Date
from datetime import datetime, time as Time, timedelta
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_utils import rango_del_dia
from app.core.exceptions import (
    PacienteConTurnoSolapadoError,
    RecursoNoEncontradoError,
    TurnoSolapadoError,
    ValidacionError,
)
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.doctor import Doctor
from app.models.enums import EstadoTurno
from app.models.paciente import Paciente
from app.models.turno import Turno
from app.schemas.turno import TurnoCreate, TurnoUpdate
from app.services import disponibilidad_service


logger = logging.getLogger("app.services.turnos")


def _obtener_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise RecursoNoEncontradoError(f"No existe un doctor con id={doctor_id}.")
    return doctor


def _obtener_paciente(db: Session, paciente_id: int) -> Paciente:
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(f"No existe un paciente con id={paciente_id}.")
    return paciente


def _validar_solapamiento(
    db: Session,
    doctor_id: int,
    inicio,
    fin,
    excluir_turno_id: Optional[int] = None,
) -> None:
    """Verifica que el doctor no tenga otro turno que se cruce con ``[inicio, fin]``.

    Se consideran todos los turnos salvo los CANCELADOS. Dos rangos se solapan
    cuando ``existente.inicio < nuevo.fin`` **y** ``existente.fin > nuevo.inicio``.
    """
    stmt = select(Turno.id).where(
        Turno.doctor_id == doctor_id,
        Turno.estado_turno != EstadoTurno.CANCELADO,
        Turno.fecha_hora_inicio < fin,
        Turno.fecha_hora_fin > inicio,
    )
    if excluir_turno_id is not None:
        stmt = stmt.where(Turno.id != excluir_turno_id)

    if db.execute(stmt).first() is not None:
        raise TurnoSolapadoError(
            "El doctor ya tiene un turno que se solapa con el rango solicitado "
            f"({inicio} - {fin})."
        )


def _validar_paciente_ubicuo(
    db: Session,
    paciente_id: int,
    doctor_id: int,
    inicio,
    fin,
    excluir_turno_id: Optional[int] = None,
) -> None:
    """Verifica que el paciente no esté citado en otra agenda a la misma hora.

    Un paciente no puede estar en dos consultorios a la vez: si ya tiene un
    turno vigente (cualquier estado salvo ``CANCELADO``) que se solapa con
    ``[inicio, fin]`` **con otro odontólogo**, la solicitud se rechaza con
    HTTP 400 (``PacienteConTurnoSolapadoError``) y se informa en el cuerpo de la
    respuesta el turno que genera el conflicto (``turno_en_conflicto``).

    El caso del *mismo* odontólogo se excluye a propósito: ya queda cubierto por
    :func:`_validar_solapamiento`, que responde 409 con el diagnóstico de agenda
    del profesional. Se consideran todos los turnos salvo los CANCELADOS, de modo
    que cancelar un turno libera al paciente para ese horario.
    """
    stmt = select(
        Turno.id, Turno.doctor_id, Turno.fecha_hora_inicio, Turno.fecha_hora_fin
    ).where(
        Turno.paciente_id == paciente_id,
        Turno.doctor_id != doctor_id,
        Turno.estado_turno != EstadoTurno.CANCELADO,
        Turno.fecha_hora_inicio < fin,
        Turno.fecha_hora_fin > inicio,
    )
    if excluir_turno_id is not None:
        stmt = stmt.where(Turno.id != excluir_turno_id)

    conflicto = db.execute(stmt).first()
    if conflicto is None:
        return

    logger.warning(
        "Paciente %s ya tiene el turno %s (doctor_id=%s, %s - %s) solapado con "
        "la solicitud %s - %s.",
        paciente_id,
        conflicto.id,
        conflicto.doctor_id,
        conflicto.fecha_hora_inicio,
        conflicto.fecha_hora_fin,
        inicio,
        fin,
    )
    raise PacienteConTurnoSolapadoError(
        extra={
            "turno_en_conflicto": {
                "id": conflicto.id,
                "doctor_id": conflicto.doctor_id,
                "fecha_hora_inicio": conflicto.fecha_hora_inicio.isoformat(),
                "fecha_hora_fin": conflicto.fecha_hora_fin.isoformat(),
            }
        }
    )


def crear_turno(db: Session, data: TurnoCreate) -> Turno:
    """Crea un turno validando existencia de doctor/paciente, agenda del doctor
    y ubicuidad del paciente (no puede estar en otra agenda a la misma hora).
    """
    _obtener_doctor(db, data.doctor_id)
    _obtener_paciente(db, data.paciente_id)
    _validar_paciente_ubicuo(
        db,
        data.paciente_id,
        data.doctor_id,
        data.fecha_hora_inicio,
        data.fecha_hora_fin,
    )
    _validar_solapamiento(
        db, data.doctor_id, data.fecha_hora_inicio, data.fecha_hora_fin
    )

    turno = Turno(
        paciente_id=data.paciente_id,
        doctor_id=data.doctor_id,
        fecha_hora_inicio=data.fecha_hora_inicio,
        fecha_hora_fin=data.fecha_hora_fin,
        estado_turno=data.estado_turno,
        estado_whatsapp=data.estado_whatsapp,
    )
    db.add(turno)
    try:
        db.commit()
    except IntegrityError as exc:
        # Cubre la UniqueConstraint (doctor_id, fecha_hora_inicio) y la
        # CheckConstraint (fecha_hora_fin > fecha_hora_inicio).
        db.rollback()
        logger.warning("IntegrityError al crear turno: %s", exc.orig)
        raise TurnoSolapadoError(
            "Ya existe un turno para ese doctor con la misma fecha/hora de inicio."
        ) from exc
    db.refresh(turno)
    logger.info("Turno creado id=%s doctor_id=%s", turno.id, turno.doctor_id)
    return turno


def listar_turnos(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    doctor_id: Optional[int] = None,
    paciente_id: Optional[int] = None,
) -> Sequence[Turno]:
    """Devuelve turnos con filtros opcionales por doctor y/o paciente."""
    stmt = select(Turno).order_by(Turno.fecha_hora_inicio)
    if doctor_id is not None:
        stmt = stmt.where(Turno.doctor_id == doctor_id)
    if paciente_id is not None:
        stmt = stmt.where(Turno.paciente_id == paciente_id)
    stmt = stmt.offset(skip).limit(limit)
    return db.execute(stmt).scalars().all()


def obtener_turno(db: Session, turno_id: int) -> Turno:
    turno = db.get(Turno, turno_id)
    if turno is None:
        raise RecursoNoEncontradoError(f"No existe un turno con id={turno_id}.")
    return turno


def actualizar_turno(db: Session, turno_id: int, data: TurnoUpdate) -> Turno:
    """Actualiza parcialmente un turno revalidando integridad y solapamientos.

    Reglas aplicadas:

    * El doctor y el paciente nuevos (si se envían) deben existir.
    * El rango resultante —considerando los valores vigentes para los campos
      omitidos— debe cumplir ``fecha_hora_fin > fecha_hora_inicio``.
    * Si el turno no queda CANCELADO, el doctor no puede tener otro turno
      solapado (se excluye el propio turno de la verificación).
    * Si el turno no queda CANCELADO, el paciente tampoco puede quedar citado a
      esa hora en la agenda de **otro** odontólogo (HTTP 400).

    Cualquier campo del turno puede actualizarse, incluido ``estado_whatsapp``,
    lo que permite reiniciar el flujo de recordatorios de un turno reprogramado.
    """
    turno = obtener_turno(db, turno_id)
    cambios = data.model_dump(exclude_unset=True)

    inicio = cambios.get("fecha_hora_inicio", turno.fecha_hora_inicio)
    fin = cambios.get("fecha_hora_fin", turno.fecha_hora_fin)
    doctor_id = cambios.get("doctor_id", turno.doctor_id)
    paciente_id = cambios.get("paciente_id", turno.paciente_id)
    estado_turno = cambios.get("estado_turno", turno.estado_turno)

    if fin <= inicio:
        raise ValidacionError(
            "fecha_hora_fin debe ser estrictamente posterior a fecha_hora_inicio."
        )

    if "doctor_id" in cambios:
        _obtener_doctor(db, doctor_id)
    if "paciente_id" in cambios:
        _obtener_paciente(db, paciente_id)

    if estado_turno != EstadoTurno.CANCELADO:
        _validar_paciente_ubicuo(
            db,
            paciente_id,
            doctor_id,
            inicio,
            fin,
            excluir_turno_id=turno.id,
        )
        _validar_solapamiento(db, doctor_id, inicio, fin, excluir_turno_id=turno.id)

    for campo, valor in cambios.items():
        setattr(turno, campo, valor)

    try:
        db.commit()
    except IntegrityError as exc:
        # UniqueConstraint (doctor_id, fecha_hora_inicio) o CheckConstraint de rango.
        db.rollback()
        logger.warning("IntegrityError al actualizar turno %s: %s", turno_id, exc.orig)
        raise TurnoSolapadoError(
            "Ya existe un turno para ese doctor con la misma fecha/hora de inicio."
        ) from exc
    db.refresh(turno)
    logger.info(
        "Turno actualizado id=%s estado=%s doctor_id=%s",
        turno.id,
        turno.estado_turno,
        turno.doctor_id,
    )
    return turno


# --------------------------------------------------------------------------- #
# Disponibilidad: bloques de turnos ofrecidos a partir de la agenda semanal
# --------------------------------------------------------------------------- #

# Ancho por defecto de los bloques que ofrece el calendario (30 minutos).
DURACION_BLOQUE_MIN = 30


def dia_semana_de(fecha: Date) -> int:
    """Día de la semana del calendario: **0 = domingo** … 6 = sábado.

    Se usa la misma convención que ``DisponibilidadDoctor.dia_semana`` y que
    ``Date.getDay()`` de JavaScript, de modo que el panel web no tenga que
    convertir nada.
    """
    return (fecha.weekday() + 1) % 7


def _bloques_de_rangos(
    rangos: Sequence[DisponibilidadDoctor], duracion_minutos: int
) -> List[Tuple[Time, Time]]:
    """Parte cada rango habilitado en bloques consecutivos de ``duracion_minutos``.

    Los bloques se generan desde la hora de inicio del rango y avanzan de
    ``duracion_minutos`` en ``duracion_minutos``, de modo que nunca se sale del
    tramo configurado. Si el sobrante final no alcanza para un bloque completo
    se descarta (p. ej. 09:00–13:00 cada 45 min ofrece hasta 12:15).
    """
    bloques: List[Tuple[Time, Time]] = []
    paso = timedelta(minutes=duracion_minutos)
    for rango in rangos:
        momento = datetime.combine(Date.min, rango.hora_inicio)
        limite = datetime.combine(Date.min, rango.hora_fin)
        while momento + paso <= limite:
            bloques.append((momento.time(), (momento + paso).time()))
            momento += paso
    return bloques


def _turnos_vigentes_del_dia(
    db: Session, doctor_id: int, fecha: Date
) -> List[Turno]:
    """Turnos vigentes (distintos de ``CANCELADO``) del doctor en ``fecha``.

    ``fecha_hora_inicio``/``fecha_hora_fin`` se guardan como *naive* en UTC (ver
    ``app.core.datetime_utils``) y el panel web los interpreta como hora local
    del consultorio; por eso el día se delimita con :func:`rango_del_dia`.
    """
    inicio, fin = rango_del_dia(fecha)
    stmt = select(Turno).where(
        Turno.doctor_id == doctor_id,
        Turno.estado_turno != EstadoTurno.CANCELADO,
        Turno.fecha_hora_inicio <= fin,
        Turno.fecha_hora_fin >= inicio,
    )
    return list(db.execute(stmt).scalars().all())


def horarios_disponibles(
    db: Session,
    doctor_id: int,
    fecha: Date,
    duracion_minutos: int = DURACION_BLOQUE_MIN,
) -> Tuple[List[DisponibilidadDoctor], List[Tuple[Time, Time]]]:
    """Bloques que el odontólogo puede atender en ``fecha``.

    El cálculo es **estricto** respecto de la configuración: sólo se ofrecen
    bloques que caigan íntegramente dentro de los rangos de ``DisponibilidadDoctor``
    del día de la semana correspondiente (un doctor sin rangos ese día no ofrece
    ningún horario). De esos bloques se descartan los que se cruzan con turnos
    vigentes —los ``CANCELADO`` vuelven a liberar su horario—.

    Returns:
        Tupla ``(rangos, bloques)``: los rangos configurados para ese día de la
        semana y los bloques libres como pares ``(hora_inicio, hora_fin)``.
    """
    _obtener_doctor(db, doctor_id)

    if duracion_minutos <= 0:
        raise ValidacionError("La duración del bloque debe ser mayor que cero.")

    dia_semana = dia_semana_de(fecha)
    rangos = disponibilidad_service.rangos_del_dia(db, doctor_id, dia_semana)
    candidatos = _bloques_de_rangos(rangos, duracion_minutos)
    if not candidatos:
        return rangos, []

    ocupados = _turnos_vigentes_del_dia(db, doctor_id, fecha)
    libres: List[Tuple[Time, Time]] = []
    for hora_inicio, hora_fin in candidatos:
        inicio = datetime.combine(fecha, hora_inicio)
        fin = datetime.combine(fecha, hora_fin)
        if any(t.fecha_hora_inicio < fin and t.fecha_hora_fin > inicio for t in ocupados):
            continue
        libres.append((hora_inicio, hora_fin))
    return rangos, libres


