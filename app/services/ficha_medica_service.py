"""Servicio de la ficha médica del paciente (Módulo 4).

La ficha médica es la hoja de papel que el consultorio archiva junto al
odontograma: la cabecera del paciente, el estado bucal general (sarro y
enfermedad periodontal) y el libro «Trabajo realizado» con sus importes.

Operaciones:

* :func:`obtener_ficha` — lectura. **No escribe**: un paciente sin historia
  clínica devuelve la ficha vacía (``id`` en ``null``) con sus datos
  filiatorios, igual que el odontograma.
* :func:`guardar_ficha` — alta o corrección. Crea la historia clínica si hace
  falta y **reemplaza** los renglones del libro y la cabecera, así lo guardado es
  exactamente lo que vio el profesional.

El **saldo no se persiste**: se calcula renglón por renglón (``Debe - Haber``
acumulado) al leer, de modo que corregir un importe nunca deja un saldo viejo.
"""

import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.datetime_utils import utcnow
from app.core.exceptions import RecursoNoEncontradoError
from app.models.ficha_medica import FichaMedica, FichaMedicaTrabajo
from app.models.historia_clinica import HistoriaClinica
from app.models.paciente import Paciente
from app.schemas.ficha_medica import (
    FichaMedicaRead,
    FichaMedicaTrabajoRead,
    FichaMedicaUpdate,
)
from app.schemas.paciente import PacienteRead
from app.services.historia_clinica_service import (
    numero_ficha_de,
    obtener_historia,
    obtener_o_crear_historia,
)

logger = logging.getLogger("app.services.ficha_medica")


def _obtener_paciente(db: Session, paciente_id: int) -> Paciente:
    """Devuelve el paciente o lanza ``RecursoNoEncontradoError`` (HTTP 404)."""
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(
            f"No existe un paciente con id={paciente_id}."
        )
    return paciente


def _ficha_de(
    db: Session, historia_clinica_id: int
) -> Optional[FichaMedica]:
    """Ficha de una historia con sus renglones cargados (o ``None``)."""
    return db.execute(
        select(FichaMedica)
        .options(selectinload(FichaMedica.trabajos))
        .where(FichaMedica.historia_clinica_id == historia_clinica_id)
    ).scalars().first()


def _importe(valor: Optional[float]) -> float:
    """Importe redondeado a dos decimales (``None`` cuenta como ``0``)."""
    return round(float(valor or 0.0), 2)


def _armar_read(
    paciente: Paciente,
    historia: Optional[HistoriaClinica],
    ficha: Optional[FichaMedica],
) -> FichaMedicaRead:
    """Arma la respuesta con los saldos acumulados del libro."""
    renglones: List[FichaMedicaTrabajoRead] = []
    total_debe = 0.0
    total_haber = 0.0
    saldo = 0.0

    for fila in (ficha.trabajos if ficha is not None else []):
        debe = _importe(fila.debe)
        haber = _importe(fila.haber)
        total_debe = round(total_debe + debe, 2)
        total_haber = round(total_haber + haber, 2)
        saldo = round(saldo + debe - haber, 2)
        renglones.append(
            FichaMedicaTrabajoRead(
                id=fila.id,
                orden=fila.orden,
                fecha=fila.fecha,
                codigo=fila.codigo,
                pieza=fila.pieza,
                trabajo=fila.trabajo,
                debe=fila.debe,
                haber=fila.haber,
                saldo=saldo,
            )
        )

    return FichaMedicaRead(
        id=ficha.id if ficha is not None else None,
        historia_clinica_id=historia.id if historia is not None else None,
        numero_ficha=(
            historia.numero_ficha
            if historia is not None
            else numero_ficha_de(paciente.id)
        ),
        vencimiento=ficha.vencimiento if ficha is not None else None,
        grupo_familiar=ficha.grupo_familiar if ficha is not None else None,
        parentesco=ficha.parentesco if ficha is not None else None,
        antecedentes_enfermedad=(
            ficha.antecedentes_enfermedad if ficha is not None else None
        ),
        presencia_sarro=ficha.presencia_sarro if ficha is not None else None,
        enfermedad_periodontal=(
            ficha.enfermedad_periodontal if ficha is not None else None
        ),
        actualizado_en=ficha.actualizado_en if ficha is not None else None,
        trabajos=renglones,
        total_debe=total_debe,
        total_haber=total_haber,
        saldo=saldo,
        paciente=PacienteRead.model_validate(paciente),
    )


def obtener_ficha(db: Session, paciente_id: int) -> FichaMedicaRead:
    """Ficha médica vigente del paciente, sin escribir en la base.

    :raises RecursoNoEncontradoError: el paciente no existe.
    """
    paciente = _obtener_paciente(db, paciente_id)
    historia = obtener_historia(db, paciente.id)
    if historia is None:
        return _armar_read(paciente, None, None)
    return _armar_read(paciente, historia, _ficha_de(db, historia.id))


def guardar_ficha(
    db: Session, paciente_id: int, data: FichaMedicaUpdate
) -> FichaMedicaRead:
    """Crea o reemplaza la ficha médica del paciente.

    La cabecera se copia tal como llega y el libro de trabajos reemplaza por
    completo al anterior (los renglones en blanco se descartan). El saldo se
    recalcula al leer.

    :raises RecursoNoEncontradoError: el paciente no existe.
    """
    for intento in range(2):
        paciente = _obtener_paciente(db, paciente_id)
        historia = obtener_o_crear_historia(db, paciente.id)
        ficha = _ficha_de(db, historia.id)
        if ficha is None:
            ficha = FichaMedica(historia_clinica_id=historia.id)
            db.add(ficha)

        _aplicar_cabecera(ficha, data)
        ficha.trabajos.clear()
        for renglon in data.trabajos:
            if not renglon.tiene_datos():
                continue
            ficha.trabajos.append(
                FichaMedicaTrabajo(
                    orden=len(ficha.trabajos),
                    fecha=renglon.fecha,
                    codigo=renglon.codigo,
                    pieza=renglon.pieza,
                    trabajo=renglon.trabajo,
                    debe=renglon.debe,
                    haber=renglon.haber,
                )
            )
        # ``onupdate`` del ORM no se dispara en una fila nueva hasta el flush:
        # se marca explícitamente la fecha del último guardado.
        ficha.actualizado_en = utcnow()

        try:
            db.commit()
        except IntegrityError:
            # Dos guardados a la vez: la otra transacción ya creó la fila.
            db.rollback()
            if intento == 1:  # pragma: no cover - carrera persistente/error
                raise
            logger.warning(
                "Ficha médica concurrente para el paciente %s; se reintenta "
                "sobre la fila existente.",
                paciente_id,
            )
            continue
        break

    paciente = _obtener_paciente(db, paciente_id)
    historia = obtener_historia(db, paciente.id)
    assert historia is not None  # pragma: no cover - recién se guardó
    guardada = _ficha_de(db, historia.id)
    assert guardada is not None  # pragma: no cover - recién se guardó
    logger.info(
        "Ficha médica guardada (paciente=%s renglones=%s debe=%.2f haber=%.2f).",
        paciente.id,
        len(guardada.trabajos),
        sum(_importe(fila.debe) for fila in guardada.trabajos),
        sum(_importe(fila.haber) for fila in guardada.trabajos),
    )
    return _armar_read(paciente, historia, guardada)


def _aplicar_cabecera(ficha: FichaMedica, data: FichaMedicaUpdate) -> None:
    """Copia la cabecera del payload al ORM (reemplaza los valores anteriores)."""
    ficha.vencimiento = data.vencimiento
    ficha.grupo_familiar = data.grupo_familiar
    ficha.parentesco = data.parentesco
    ficha.antecedentes_enfermedad = data.antecedentes_enfermedad
    ficha.presencia_sarro = data.presencia_sarro
    ficha.enfermedad_periodontal = data.enfermedad_periodontal
