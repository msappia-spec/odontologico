"""Endpoints de disponibilidad semanal de los odontólogos.

Agrupa dos cosas que el panel de secretaría usa juntas:

* el **editor de la agenda semanal** de cada profesional
  (``/doctores/{doctor_id}/disponibilidad``), con altas, borrado y reemplazo
  total de la semana (varios rangos por día);
* los **bloques horarios ofrecidos** para una fecha concreta
  (``/doctores/{doctor_id}/horarios-disponibles``), calculados por
  :func:`app.services.turno_service.horarios_disponibles` a partir de esos
  rangos y de los turnos ya agendados.
"""

from datetime import date as Date
from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.disponibilidad import DisponibilidadDoctor
from app.schemas.disponibilidad import (
    BloqueDisponible,
    DisponibilidadCreate,
    DisponibilidadRead,
    DisponibilidadReemplazo,
    HorariosDisponiblesRead,
    NOMBRES_DIA,
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


@router.get(
    "/doctores/{doctor_id}/horarios-disponibles",
    response_model=HorariosDisponiblesRead,
    summary="Bloques horarios libres de un odontólogo en una fecha",
    description=(
        "Genera los bloques de ``duracion_minutos`` (30 por defecto) que caen "
        "dentro de los rangos habilitados del odontólogo para el día de la "
        "semana de ``fecha`` y descarta los que se cruzan con turnos vigentes. "
        "Un odontólogo sin rangos ese día devuelve ``bloques`` vacío."
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
    rangos, bloques = turno_service.horarios_disponibles(
        db, doctor_id, fecha, duracion_minutos
    )
    return HorariosDisponiblesRead(
        doctor_id=doctor_id,
        fecha=fecha,
        dia_semana=turno_service.dia_semana_de(fecha),
        dia=NOMBRES_DIA[turno_service.dia_semana_de(fecha)],
        duracion_minutos=duracion_minutos,
        rangos=rangos,
        bloques=[BloqueDisponible(hora_inicio=i, hora_fin=f) for i, f in bloques],
    )
