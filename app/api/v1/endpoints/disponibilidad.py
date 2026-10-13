"""Endpoints de disponibilidad de los odontólogos.

Agrupa las tres cosas que el panel de secretaría usa juntas:

* el **editor de la agenda semanal** de cada profesional
  (``/doctores/{doctor_id}/disponibilidad``), con altas, borrado y reemplazo
  total de la semana (varios rangos por día);
* las **excepciones por fecha concreta**
  (``/doctores/{doctor_id}/disponibilidad-especifica``): tramos
  ``HABILITADO``/``BLOQUEADO`` que abren o cierran horas de un día puntual
  (guardias, vacaciones, feriados) sin tocar la semana;
* los **bloques horarios ofrecidos** para una fecha concreta
  (``/doctores/{doctor_id}/horarios-disponibles``), calculados por
  :func:`app.services.turno_service.horarios_disponibles` a partir de la agenda
  vigente —semanal o con la excepción del día aplicada— y de los turnos ya
  agendados.
"""

from datetime import date as Date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.disponibilidad_especifica import DisponibilidadEspecifica
from app.schemas.disponibilidad import (
    BloqueDisponible,
    DisponibilidadCreate,
    DisponibilidadEspecificaCreate,
    DisponibilidadEspecificaRead,
    DisponibilidadEspecificaReemplazo,
    DisponibilidadRead,
    DisponibilidadReemplazo,
    HorariosDisponiblesRead,
    NOMBRES_DIA,
    RangoEfectivoRead,
)
from app.services import disponibilidad_service, turno_service

router = APIRouter(tags=["Disponibilidad"])


@router.get(
    "/doctores/{doctor_id}/disponibilidad",
    response_model=List[DisponibilidadRead],
    summary="Listar la agenda semanal de un odontólogo",
    description=(
        "Devuelve todos los rangos horarios del odontólogo ordenados por día "
        "(0 = domingo … 6 = sábado) y hora de inicio. Puede haber varios rangos "
        "el mismo día (p. ej. mañana y tarde)."
    ),
)
def listar_disponibilidad(
    doctor_id: int, db: Session = Depends(get_db)
) -> List[DisponibilidadDoctor]:
    return disponibilidad_service.listar_rangos(db, doctor_id)


@router.post(
    "/doctores/{doctor_id}/disponibilidad",
    response_model=DisponibilidadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar un rango horario",
    description=(
        "Agrega un tramo de atención para el día de la semana indicado. Si el "
        "rango se solapa con otro ya configurado ese día responde **409**; los "
        "rangos contiguos (08:00-12:00 y 12:00-16:00) se aceptan."
    ),
)
def agregar_disponibilidad(
    doctor_id: int, payload: DisponibilidadCreate, db: Session = Depends(get_db)
) -> DisponibilidadDoctor:
    return disponibilidad_service.agregar_rango(db, doctor_id, payload)


@router.put(
    "/doctores/{doctor_id}/disponibilidad",
    response_model=List[DisponibilidadRead],
    summary="Reemplazar la agenda semanal completa",
    description=(
        "Deja la semana del odontólogo exactamente con los rangos enviados (una "
        "lista vacía lo deja sin atención configurada). Es la operación que usa "
        "el editor semanal del panel. Responde **422** si algún rango está "
        "invertido o si dos rangos del mismo día se solapan."
    ),
)
def reemplazar_disponibilidad(
    doctor_id: int, payload: DisponibilidadReemplazo, db: Session = Depends(get_db)
) -> List[DisponibilidadDoctor]:
    return disponibilidad_service.reemplazar_agenda(db, doctor_id, payload.rangos)


@router.delete(
    "/doctores/{doctor_id}/disponibilidad/{rango_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un rango horario",
    description=(
        "Elimina un rango del odontólogo. Responde **404** si el rango no "
        "existe o pertenece a otro profesional."
    ),
)
def eliminar_disponibilidad(
    doctor_id: int, rango_id: int, db: Session = Depends(get_db)
) -> None:
    disponibilidad_service.eliminar_rango(db, doctor_id, rango_id)


# --------------------------------------------------------------------------- #
# Excepciones de agenda por fecha concreta
# --------------------------------------------------------------------------- #


@router.get(
    "/doctores/{doctor_id}/disponibilidad-especifica",
    response_model=List[DisponibilidadEspecificaRead],
    summary="Listar las excepciones de agenda por fecha",
    description=(
        "Devuelve los tramos ``HABILITADO``/``BLOQUEADO`` cargados para fechas "
        "concretas, ordenados por fecha y hora. ``desde`` y ``hasta`` acotan el "
        "rango (inclusive) y permiten traer sólo el mes que muestra el panel."
    ),
)
def listar_disponibilidad_especifica(
    doctor_id: int,
    desde: Optional[Date] = Query(None, description="Desde esta fecha (AAAA-MM-DD)."),
    hasta: Optional[Date] = Query(None, description="Hasta esta fecha (AAAA-MM-DD)."),
    db: Session = Depends(get_db),
) -> List[DisponibilidadEspecifica]:
    return disponibilidad_service.listar_excepciones(db, doctor_id, desde, hasta)


@router.post(
    "/doctores/{doctor_id}/disponibilidad-especifica",
    response_model=DisponibilidadEspecificaRead,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar un rango a una fecha concreta",
    description=(
        "Agrega un tramo a una fecha puntual: ``HABILITADO`` (por defecto) para "
        "abrir atención que la agenda semanal no cubre y ``BLOQUEADO`` para "
        "recortarla. Si el tramo ``HABILITADO`` se solapa con otro habilitado de "
        "la misma fecha responde **409**."
    ),
)
def agregar_disponibilidad_especifica(
    doctor_id: int,
    payload: DisponibilidadEspecificaCreate,
    db: Session = Depends(get_db),
) -> DisponibilidadEspecifica:
    return disponibilidad_service.agregar_excepcion(db, doctor_id, payload)


@router.put(
    "/doctores/{doctor_id}/disponibilidad-especifica/{fecha}",
    response_model=List[DisponibilidadEspecificaRead],
    summary="Reemplazar la agenda de una fecha concreta",
    description=(
        "Deja la fecha exactamente con los tramos enviados (es la operación que "
        "usa el botón *Personalizar día* del panel, copiando la agenda semanal). "
        "Una lista vacía borra las excepciones de esa fecha, con lo que el día "
        "vuelve a regirse por la agenda semanal. Responde **422** si un tramo "
        "está invertido o si dos ``HABILITADO`` se solapan."
    ),
)
def reemplazar_disponibilidad_especifica(
    doctor_id: int,
    fecha: Date,
    payload: DisponibilidadEspecificaReemplazo,
    db: Session = Depends(get_db),
) -> List[DisponibilidadEspecifica]:
    return disponibilidad_service.reemplazar_excepciones_del_dia(
        db, doctor_id, fecha, payload.rangos
    )


@router.delete(
    "/doctores/{doctor_id}/disponibilidad-especifica/{excepcion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una excepción de fecha",
    description=(
        "Elimina un tramo de una fecha concreta (borrado físico). El tramo se "
        "ubica por ``excepcion_id`` **dentro de la agenda del** ``doctor_id``: si "
        "no existe o pertenece a otro profesional responde **404** y no toca nada. "
        "Un id ya eliminado también responde **404**, de modo que reintentar la "
        "baja es seguro para el panel: el backend nunca borra el tramo de otro. "
        "Al borrar la última excepción de una fecha, ese día vuelve a calcularse "
        "con la agenda semanal."
    ),
)
def eliminar_disponibilidad_especifica(
    doctor_id: int, excepcion_id: int, db: Session = Depends(get_db)
) -> None:
    disponibilidad_service.eliminar_excepcion(db, doctor_id, excepcion_id)


@router.get(
    "/doctores/{doctor_id}/horarios-disponibles",
    response_model=HorariosDisponiblesRead,
    summary="Bloques horarios libres de un odontólogo en una fecha",
    description=(
        "Genera los bloques de ``duracion_minutos`` (30 por defecto) que caen "
        "dentro de los rangos vigentes del odontólogo en ``fecha`` y descarta los "
        "que se cruzan con turnos vigentes. Si la fecha tiene excepciones "
        "cargadas (``disponibilidad-especifica``), el día se calcula sólo con "
        "ellas —``HABILITADO`` menos ``BLOQUEADO``— y ``origen`` devuelve "
        "``ESPECIFICO``; si no, se usa la agenda semanal (``origen`` "
        "``SEMANAL``). Un odontólogo sin rangos ese día devuelve ``bloques`` "
        "vacío."
    ),
)
def horarios_disponibles(
    doctor_id: int,
    fecha: Date = Query(..., description="Fecha a consultar (AAAA-MM-DD)."),
    duracion_minutos: int = Query(
        turno_service.DURACION_BLOQUE_MIN,
        ge=5,
        le=240,
        description="Ancho de cada bloque, en minutos.",
    ),
    db: Session = Depends(get_db),
) -> HorariosDisponiblesRead:
    efectiva, bloques = turno_service.horarios_disponibles(
        db, doctor_id, fecha, duracion_minutos
    )
    dia_semana = turno_service.dia_semana_de(fecha)
    return HorariosDisponiblesRead(
        doctor_id=doctor_id,
        fecha=fecha,
        dia_semana=dia_semana,
        dia=NOMBRES_DIA[dia_semana],
        duracion_minutos=duracion_minutos,
        origen=efectiva.origen,
        rangos=[RangoEfectivoRead.model_validate(rango) for rango in efectiva.rangos],
        excepciones=[
            DisponibilidadEspecificaRead.model_validate(excepcion)
            for excepcion in efectiva.excepciones
        ],
        bloques=[BloqueDisponible(hora_inicio=i, hora_fin=f) for i, f in bloques],
    )
