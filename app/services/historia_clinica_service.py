"""Servicio de historia clínica y evoluciones médicas (Módulo 2).

Responsabilidades:

* **Ficha automática**: la historia clínica de un paciente se crea sola con su
  primera evolución (:func:`obtener_o_crear_historia`) y su ``numero_ficha`` se
  deriva del ``paciente_id`` (``HC-000123``), de modo que sea único y estable.
* **Alta de evoluciones** (:func:`crear_evolucion`): valida que el paciente y el
  odontólogo existan, arma la ficha si hace falta y guarda la atención con sus
  adjuntos (radiografías) en una sola transacción.
* **Consulta**: línea de tiempo siempre en orden **cronológico inverso** (de la
  atención más nueva a la más vieja) y detalle de la ficha
  (:func:`detalle_historia`).

**No hay funciones de edición ni de baja**: las evoluciones son registros
inmutables (ver :mod:`app.models.evolucion_medica`, donde además los eventos del
ORM bloquean cualquier ``UPDATE``/``DELETE``).
"""

import logging
from typing import Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_utils import utcnow
from app.core.exceptions import RecursoNoEncontradoError
from app.models.archivo_adjunto import ArchivoAdjunto
from app.models.doctor import Doctor
from app.models.evolucion_medica import EvolucionMedica
from app.models.historia_clinica import HistoriaClinica
from app.models.paciente import Paciente
from app.schemas.antecedentes_salud import AntecedentesSaludRead
from app.schemas.historia_clinica import (
    EvolucionMedicaCreate,
    EvolucionMedicaRead,
    HistoriaClinicaDetalle,
)

logger = logging.getLogger("app.services.historia_clinica")

# Prefijo del número de ficha visible en la carpeta del paciente.
PREFIJO_FICHA = "HC"


def numero_ficha_de(paciente_id: int) -> str:
    """Número de ficha derivado del paciente (``HC-000123``).

    Es **determinístico**: la misma persona siempre tiene la misma ficha, así que
    la consulta de sólo lectura puede anunciarlo antes de que exista la fila
    (ver :func:`detalle_historia`) sin correr el riesgo de informar otro número.
    """
    return f"{PREFIJO_FICHA}-{int(paciente_id):06d}"


def _obtener_paciente(db: Session, paciente_id: int) -> Paciente:
    """Devuelve el paciente o lanza ``RecursoNoEncontradoError`` (HTTP 404)."""
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(f"No existe un paciente con id={paciente_id}.")
    return paciente


def obtener_historia(db: Session, paciente_id: int) -> Optional[HistoriaClinica]:
    """Devuelve la ficha del paciente (``None`` si todavía no tiene ninguna)."""
    return db.execute(
        select(HistoriaClinica).where(HistoriaClinica.paciente_id == paciente_id)
    ).scalars().first()


def obtener_o_crear_historia(db: Session, paciente_id: int) -> HistoriaClinica:
    """Devuelve la ficha del paciente, creándola si es su primera evolución.

    Si dos altas simultáneas intentan crear la ficha a la vez, el índice único
    ``uq_historia_paciente`` frena a la segunda: se traduce en una relectura de la
    ficha que creó la otra transacción (en lugar de un error al usuario).
    """
    paciente = _obtener_paciente(db, paciente_id)
    historia = obtener_historia(db, paciente.id)
    if historia is not None:
        return historia

    historia = HistoriaClinica(
        paciente_id=paciente.id, numero_ficha=numero_ficha_de(paciente.id)
    )
    db.add(historia)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.warning("Ficha clínica concurrente para el paciente %s.", paciente.id)
        historia = obtener_historia(db, paciente_id)
        if historia is None:  # pragma: no cover - condición de carrera extrema
            raise
    return historia


def crear_evolucion(
    db: Session, paciente_id: int, data: EvolucionMedicaCreate
) -> EvolucionMedica:
    """Registra una evolución médica (única operación de escritura del módulo).

    Pasos: valida el paciente (404) y el odontólogo (404), consigue la ficha
    clínica (la crea con la primera evolución) y guarda la atención junto con sus
    adjuntos. No existe ninguna vía de edición o borrado posterior.
    """
    historia = obtener_o_crear_historia(db, paciente_id)

    doctor = db.get(Doctor, data.doctor_id)
    if doctor is None:
        raise RecursoNoEncontradoError(
            f"No existe un doctor con id={data.doctor_id}."
        )

    evolucion = EvolucionMedica(
        historia_clinica_id=historia.id,
        doctor_id=doctor.id,
        fecha_atencion=data.fecha_atencion or utcnow(),
        motivo_consulta=data.motivo_consulta,
        diagnostico=data.diagnostico,
        tratamiento_efectuado=data.tratamiento_efectuado,
        observaciones=data.observaciones,
        archivos=[
            ArchivoAdjunto(
                url_archivo=archivo.url_archivo,
                tipo_archivo=archivo.tipo_archivo,
                fecha_subida=utcnow(),
            )
            for archivo in data.archivos
        ],
    )
    db.add(evolucion)
    db.commit()
    db.refresh(evolucion)
    logger.info(
        "Evolución %s registrada (paciente=%s doctor=%s ficha=%s).",
        evolucion.id,
        paciente_id,
        doctor.id,
        historia.numero_ficha,
    )
    return evolucion


def listar_evoluciones(
    db: Session,
    paciente_id: int,
    skip: int = 0,
    limit: int = 100,
) -> Sequence[EvolucionMedica]:
    """Línea de tiempo del paciente: de la atención **más nueva a la más vieja**.

    El desempate es por ``id`` descendente, así dos atenciones cargadas con la
    misma fecha (el formulario móvil registra el día) conservan el orden en que
    se cargaron. Un paciente **real** sin evoluciones devuelve una lista vacía
    (200) —no es un error consultar una ficha todavía sin atenciones—, pero un
    paciente que no existe responde **404** (así un id mal tipeado no se confunde
    con una ficha nueva).
    """
    _obtener_paciente(db, paciente_id)
    historia = obtener_historia(db, paciente_id)
    if historia is None:
        return []
    stmt = (
        select(EvolucionMedica)
        .where(EvolucionMedica.historia_clinica_id == historia.id)
        .order_by(desc(EvolucionMedica.fecha_atencion), desc(EvolucionMedica.id))
        .offset(skip)
        .limit(limit)
    )
    return db.execute(stmt).scalars().all()


def obtener_evolucion(db: Session, evolucion_id: int) -> EvolucionMedica:
    """Devuelve una evolución puntual o lanza ``RecursoNoEncontradoError``."""
    evolucion = db.get(EvolucionMedica, evolucion_id)
    if evolucion is None:
        raise RecursoNoEncontradoError(
            f"No existe una evolución médica con id={evolucion_id}."
        )
    return evolucion


def detalle_historia(db: Session, paciente_id: int) -> HistoriaClinicaDetalle:
    """Arma la ficha del paciente con su línea de tiempo completa.

    Es una operación de **sólo lectura**: si el paciente todavía no tiene
    evoluciones ni ficha de salud no se crea ninguna fila; se responde con
    ``id = null`` y el ``numero_ficha`` que le corresponderá cuando se cargue la
    primera atención o el paciente complete su anamnesis.

    La declaración de salud viaja dentro del detalle (Módulo 4) con sus campos
    calculados de alerta, para que el odontólogo vea los antecedentes al abrir
    la ficha sin una segunda llamada a la API.
    """
    paciente = _obtener_paciente(db, paciente_id)
    historia = obtener_historia(db, paciente_id)
    evoluciones = listar_evoluciones(db, paciente_id)

    antecedentes_salud = None
    if historia is not None and historia.antecedentes_salud is not None:
        antecedentes_salud = AntecedentesSaludRead.model_validate(
            historia.antecedentes_salud
        )

    return HistoriaClinicaDetalle(
        id=historia.id if historia is not None else None,
        paciente_id=paciente.id,
        paciente_nombre=paciente.nombre,
        paciente_dni=paciente.dni,
        numero_ficha=(
            historia.numero_ficha if historia is not None else numero_ficha_de(paciente.id)
        ),
        total_evoluciones=len(evoluciones),
        evoluciones=[EvolucionMedicaRead.model_validate(ev) for ev in evoluciones],
        antecedentes_salud=antecedentes_salud,
        alerta_medica=bool(antecedentes_salud and antecedentes_salud.alerta_medica),
        motivos_alerta=(
            list(antecedentes_salud.motivos_alerta) if antecedentes_salud else []
        ),
    )

