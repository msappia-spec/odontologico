"""Servicio de disponibilidad de los odontólogos.

Gestiona las **dos capas** de agenda que el panel administra:

* la **semanal** (``DisponibilidadDoctor``): la rutina habitual del profesional
  —varios rangos por día de la semana—, con altas, reemplazo total y bajas;
* las **excepciones por fecha** (``DisponibilidadEspecifica``): tramos
  ``HABILITADO``/``BLOQUEADO`` que sólo afectan a un día concreto (guardias,
  vacaciones, feriados, un almuerzo más largo) sin tocar la semana.

:func:`rangos_efectivos_del_dia` es el punto de entrada que usa
:mod:`app.services.turno_service` para generar los bloques ofrecidos: decide qué
capa manda para una fecha —si hay **cualquier** excepción cargada, la agenda
semanal se ignora y el día se calcula con los ``HABILITADO`` menos los
``BLOQUEADO``— y devuelve los rangos ya resueltos.
"""

import logging
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import time as Time
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.datetime_utils import dia_semana_de
from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
)
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.disponibilidad_especifica import DisponibilidadEspecifica
from app.models.doctor import Doctor
from app.models.enums import TipoDisponibilidadEspecifica
from app.schemas.disponibilidad import (
    NOMBRES_DIA,
    DisponibilidadBase,
    DisponibilidadCreate,
    DisponibilidadEspecificaCreate,
    ExcepcionFechaBase,
)

logger = logging.getLogger("app.services.disponibilidad")

# De dónde salen los rangos vigentes de una fecha concreta.
ORIGEN_SEMANAL = "SEMANAL"
ORIGEN_ESPECIFICO = "ESPECIFICO"


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


# --------------------------------------------------------------------------- #
# Excepciones por fecha concreta (# DisponibilidadEspecifica)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RangoEfectivo:
    """Rango horario ya resuelto para una fecha, con su procedencia."""

    hora_inicio: Time
    hora_fin: Time
    origen: str = ORIGEN_SEMANAL


@dataclass(frozen=True)
class DisponibilidadEfectivaDia:
    """Resultado de resolver la agenda de un odontólogo en una fecha."""

    origen: str = ORIGEN_SEMANAL
    rangos: List[RangoEfectivo] = field(default_factory=list)
    excepciones: List[DisponibilidadEspecifica] = field(default_factory=list)


def _unir_rangos(rangos: Iterable[Tuple[Time, Time]]) -> List[Tuple[Time, Time]]:
    """Une los tramos solapados y los devuelve ordenados.

    Los tramos **contiguos** (10:00-12:00 y 12:00-14:00) se dejan separados a
    propósito: así cada uno conserva su propio compás de bloques, igual que la
    agenda semanal.
    """
    ordenados = sorted(rangos)
    unidos: List[List[Time]] = []
    for inicio, fin in ordenados:
        if unidos and inicio < unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], fin)
        else:
            unidos.append([inicio, fin])
    return [(inicio, fin) for inicio, fin in unidos]


def _restar_rangos(
    base: Sequence[Tuple[Time, Time]], quitar: Sequence[Tuple[Time, Time]]
) -> List[Tuple[Time, Time]]:
    """Devuelve ``base`` sin las porciones cubiertas por ``quitar``.

    Ambos deben venir ya unidos (:func:`_unir_rangos`) y ordenados. Los tramos
    que quedan partidos por un bloqueo se devuelven como dos tramos distintos.
    """
    restante = list(base)
    for bloque_inicio, bloque_fin in quitar:
        recortado: List[Tuple[Time, Time]] = []
        for inicio, fin in restante:
            if bloque_fin <= inicio or bloque_inicio >= fin:
                recortado.append((inicio, fin))  # sin contacto: queda igual
                continue
            if bloque_inicio > inicio:
                recortado.append((inicio, bloque_inicio))
            if bloque_fin < fin:
                recortado.append((bloque_fin, fin))
        restante = recortado
    return [rango for rango in restante if rango[0] < rango[1]]


def listar_excepciones(
    db: Session,
    doctor_id: int,
    desde: Optional[Date] = None,
    hasta: Optional[Date] = None,
) -> List[DisponibilidadEspecifica]:
    """Excepciones del odontólogo ordenadas por fecha y hora.

    ``desde``/``hasta`` acotan el rango (inclusive) y son opcionales; el panel
    los usa para traer sólo los meses que muestra cada vista del calendario.
    """
    _obtener_doctor(db, doctor_id)
    stmt = select(DisponibilidadEspecifica).where(
        DisponibilidadEspecifica.doctor_id == doctor_id
    )
    if desde is not None:
        stmt = stmt.where(DisponibilidadEspecifica.fecha >= desde)
    if hasta is not None:
        stmt = stmt.where(DisponibilidadEspecifica.fecha <= hasta)
    stmt = stmt.order_by(
        DisponibilidadEspecifica.fecha,
        DisponibilidadEspecifica.hora_inicio,
        DisponibilidadEspecifica.hora_fin,
    )
    return list(db.execute(stmt).scalars().all())


def excepciones_del_dia(
    db: Session, doctor_id: int, fecha: Date
) -> List[DisponibilidadEspecifica]:
    """Excepciones cargadas para ``fecha`` (habilitadas y bloqueadas).

    No valida la existencia del doctor: la usan también los endpoints que ya la
    validaron y el cálculo de bloques del servicio de turnos.
    """
    stmt = (
        select(DisponibilidadEspecifica)
        .where(
            DisponibilidadEspecifica.doctor_id == doctor_id,
            DisponibilidadEspecifica.fecha == fecha,
        )
        .order_by(
            DisponibilidadEspecifica.hora_inicio,
            DisponibilidadEspecifica.hora_fin,
        )
    )
    return list(db.execute(stmt).scalars().all())


def agregar_excepcion(
    db: Session, doctor_id: int, data: DisponibilidadEspecificaCreate
) -> DisponibilidadEspecifica:
    """Agrega un tramo ``HABILITADO`` o ``BLOQUEADO`` a una fecha concreta.

    Sólo se rechaza el solapamiento entre tramos **habilitados** del mismo día
    (``409``): el mismo horario no debería quedar habilitado dos veces. Un
    bloqueo puede pisar cualquier cosa —incluso otro bloqueo— porque representar
    horas cerradas de más de una forma es inofensivo.
    """
    _obtener_doctor(db, doctor_id)

    if data.tipo == TipoDisponibilidadEspecifica.HABILITADO:
        for existente in excepciones_del_dia(db, doctor_id, data.fecha):
            if existente.tipo != TipoDisponibilidadEspecifica.HABILITADO:
                continue
            if _solapan(
                data.hora_inicio,
                data.hora_fin,
                existente.hora_inicio,
                existente.hora_fin,
            ):
                raise ConflictoError(
                    f"El rango {data.hora_inicio:%H:%M}-{data.hora_fin:%H:%M} se "
                    f"solapa con el rango habilitado "
                    f"{existente.hora_inicio:%H:%M}-{existente.hora_fin:%H:%M} del "
                    f"{data.fecha.isoformat()}."
                )

    excepcion = DisponibilidadEspecifica(
        doctor_id=doctor_id,
        fecha=data.fecha,
        hora_inicio=data.hora_inicio,
        hora_fin=data.hora_fin,
        tipo=data.tipo,
    )
    db.add(excepcion)
    db.commit()
    db.refresh(excepcion)
    logger.info(
        "Excepción de agenda agregada id=%s doctor_id=%s fecha=%s %s-%s (%s)",
        excepcion.id,
        doctor_id,
        excepcion.fecha.isoformat(),
        excepcion.hora_inicio.strftime("%H:%M"),
        excepcion.hora_fin.strftime("%H:%M"),
        excepcion.tipo.value,
    )
    return excepcion


def reemplazar_excepciones_del_dia(
    db: Session,
    doctor_id: int,
    fecha: Date,
    rangos: Iterable[ExcepcionFechaBase],
) -> List[DisponibilidadEspecifica]:
    """Deja la fecha exactamente con los tramos enviados.

    Es la operación que habilita dos flujos del panel: *personalizar* un día
    (copiar la agenda semanal como ``HABILITADO`` y ajustarla) y *restablecerlo*
    (enviar una lista vacía, con lo que la fecha vuelve a regir por la semana).
    Los solapes entre ``HABILITADO`` los valida
    :class:`~app.schemas.disponibilidad.DisponibilidadEspecificaReemplazo`.
    """
    _obtener_doctor(db, doctor_id)
    nuevos = list(rangos)

    db.execute(
        delete(DisponibilidadEspecifica).where(
            DisponibilidadEspecifica.doctor_id == doctor_id,
            DisponibilidadEspecifica.fecha == fecha,
        )
    )
    for rango in nuevos:
        db.add(
            DisponibilidadEspecifica(
                doctor_id=doctor_id,
                fecha=fecha,
                hora_inicio=rango.hora_inicio,
                hora_fin=rango.hora_fin,
                tipo=rango.tipo,
            )
        )
    db.commit()
    logger.info(
        "Excepciones reemplazadas doctor_id=%s fecha=%s (%s rangos)",
        doctor_id,
        fecha.isoformat(),
        len(nuevos),
    )
    return excepciones_del_dia(db, doctor_id, fecha)


def eliminar_excepcion(db: Session, doctor_id: int, excepcion_id: int) -> None:
    """Elimina una excepción (``404`` si no existe o es de otro odontólogo).

    El tramo se busca con ``id`` **y** ``doctor_id`` en la propia consulta, así
    que la baja sólo puede alcanzar una excepción del odontólogo indicado (nunca
    la de otro profesional). Un id ya eliminado —por ejemplo el panel que
    reintenta la baja de un tramo que se borró en otra pestaña— responde 404 sin
    tocar nada más: la operación es segura de repetir.
    """
    _obtener_doctor(db, doctor_id)
    stmt = select(DisponibilidadEspecifica).where(
        DisponibilidadEspecifica.id == excepcion_id,
        DisponibilidadEspecifica.doctor_id == doctor_id,
    )
    excepcion = db.execute(stmt).scalars().first()
    if excepcion is None:
        raise RecursoNoEncontradoError(
            f"No existe una excepción de disponibilidad con id={excepcion_id} "
            f"para el doctor id={doctor_id}."
        )
    # La fecha se lee antes de borrar: el log necesita el dato ya desligado.
    fecha = excepcion.fecha
    db.delete(excepcion)
    db.commit()
    logger.info(
        "Excepción de agenda eliminada id=%s doctor_id=%s fecha=%s",
        excepcion_id,
        doctor_id,
        fecha.isoformat(),
    )


def rangos_efectivos_del_dia(
    db: Session, doctor_id: int, fecha: Date
) -> DisponibilidadEfectivaDia:
    """Rangos que el odontólogo atiende en ``fecha``, tras aplicar la excepción.

    Regla de precedencia:

    * Si hay **alguna** excepción cargada para esa fecha, la agenda semanal se
      ignora y el día se calcula sólo con las excepciones: la unión de los
      ``HABILITADO`` menos los ``BLOQUEADO``. Así se puede abrir un día que la
      rutina no cubre (domingo de guardia) y cerrar uno que sí.
    * Si no hay excepciones, se devuelven los rangos de la semana para el día de
      la semana correspondiente (comportamiento histórico).
    """
    excepciones = excepciones_del_dia(db, doctor_id, fecha)
    if excepciones:
        habilitados = _unir_rangos(
            (e.hora_inicio, e.hora_fin)
            for e in excepciones
            if e.tipo == TipoDisponibilidadEspecifica.HABILITADO
        )
        bloqueados = _unir_rangos(
            (e.hora_inicio, e.hora_fin)
            for e in excepciones
            if e.tipo == TipoDisponibilidadEspecifica.BLOQUEADO
        )
        return DisponibilidadEfectivaDia(
            origen=ORIGEN_ESPECIFICO,
            rangos=[
                RangoEfectivo(inicio, fin, ORIGEN_ESPECIFICO)
                for inicio, fin in _restar_rangos(habilitados, bloqueados)
            ],
            excepciones=excepciones,
        )

    semanales = rangos_del_dia(db, doctor_id, dia_semana_de(fecha))
    return DisponibilidadEfectivaDia(
        origen=ORIGEN_SEMANAL,
        rangos=[
            RangoEfectivo(rango.hora_inicio, rango.hora_fin, ORIGEN_SEMANAL)
            for rango in semanales
        ],
        excepciones=[],
    )
