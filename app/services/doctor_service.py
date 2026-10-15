"""Servicio de gestión de doctores (alta, edición y baja lógica).

Reglas de negocio del recurso ``Doctor``:

* **Edición parcial (``PATCH``) y total (``PUT``)**: el servicio actualiza sólo
  los campos enviados. ``activo`` forma parte del payload, así que el mismo
  ``PATCH`` sirve para dar de baja (``{"activo": false}``) y para reactivar
  (``{"activo": true}``).
* **Modalidad de pago (Módulo 3)**: ``tipo_pago`` y ``valor_pago`` se validan
  **contra el estado vigente** del profesional (ver
  :func:`app.core.pagos.validar_modalidad_pago`): un ``PATCH`` que cambie el tipo
  sin informar el valor no puede dejar al odontólogo sin forma de pago.
* **Baja lógica (``DELETE``)**: ``dar_de_baja`` marca ``activo = False`` en lugar
  de borrar la fila. Los turnos del profesional se conservan (la secretaría los
  reprograma o cancela desde el calendario) y el odontólogo deja de ofrecerse en
  las agendas y en los desplegables, porque las consultas de selección y el alta
  de turnos filtran por ``activo`` (ver ``app.services.turno_service``).
"""

import logging
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import RecursoNoEncontradoError, ValidacionError
from app.core.pagos import validar_modalidad_pago
from app.models.doctor import Doctor
from app.models.enums import EstadoTurno, TipoPagoDoctor
from app.models.turno import Turno
from app.schemas.doctor import DoctorCreate, DoctorReplace, DoctorUpdate

logger = logging.getLogger("app.services.doctores")


def _validar_pago(tipo_pago: TipoPagoDoctor, valor_pago: Optional[float]) -> None:
    """Traduce la regla de la modalidad de pago a ``ValidacionError`` (HTTP 422)."""
    try:
        validar_modalidad_pago(tipo_pago, valor_pago)
    except ValueError as exc:
        raise ValidacionError(str(exc)) from exc


def obtener_doctor(
    db: Session, doctor_id: int, solo_activos: bool = False
) -> Doctor:
    """Devuelve el doctor indicado o lanza ``RecursoNoEncontradoError``.

    Con ``solo_activos=True`` (por defecto en las operaciones que **asignan** un
    profesional, como agendar un turno) un odontólogo dado de baja se comporta
    como inexistente para la selección: la respuesta es ``404`` con un mensaje
    que explica que está inactivo.
    """
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise RecursoNoEncontradoError(f"No existe un doctor con id={doctor_id}.")
    if solo_activos and not doctor.activo:
        raise RecursoNoEncontradoError(
            f"El doctor id={doctor_id} está dado de baja y no puede recibir turnos."
        )
    return doctor


def listar_doctores(db: Session, activos: Optional[bool] = None) -> Sequence[Doctor]:
    """Devuelve los doctores ordenados por nombre.

    :param activos: ``True`` sólo activos, ``False`` sólo dados de baja,
        ``None`` (por defecto) todos (la vista *Doctores* del panel los necesita
        para poder reactivar a quien esté inactivo).
    """
    stmt = select(Doctor).order_by(Doctor.nombre)
    if activos is not None:
        stmt = stmt.where(Doctor.activo == activos)
    return db.execute(stmt).scalars().all()


def crear_doctor(db: Session, data: DoctorCreate) -> Doctor:
    """Crea un doctor con los datos completos del payload."""
    _validar_pago(data.tipo_pago, data.valor_pago)
    doctor = Doctor(**data.model_dump())
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    logger.info("Doctor creado id=%s nombre=%s", doctor.id, doctor.nombre)
    return doctor


def reemplazar_doctor(db: Session, doctor_id: int, data: DoctorReplace) -> Doctor:
    """Reemplaza los datos del doctor (``PUT``) con el payload completo."""
    doctor = obtener_doctor(db, doctor_id)
    _validar_pago(data.tipo_pago, data.valor_pago)
    for campo, valor in data.model_dump().items():
        setattr(doctor, campo, valor)
    db.commit()
    db.refresh(doctor)
    logger.info("Doctor reemplazado id=%s nombre=%s", doctor.id, doctor.nombre)
    return doctor


def actualizar_doctor(db: Session, doctor_id: int, data: DoctorUpdate) -> Doctor:
    """Actualiza parcialmente un doctor (``PATCH``): sólo los campos enviados.

    La modalidad de pago se valida con los valores **resultantes** (los enviados
    o los que ya tenía el profesional): ``PATCH {"tipo_pago": "SUELDO"}`` sobre un
    odontólogo sin ``valor_pago`` responde **422** en lugar de dejar su
    liquidación en cero.
    """
    doctor = obtener_doctor(db, doctor_id)
    cambios = data.model_dump(exclude_unset=True)
    _validar_pago(
        cambios.get("tipo_pago", doctor.tipo_pago),
        cambios.get("valor_pago", doctor.valor_pago),
    )
    for campo, valor in cambios.items():
        setattr(doctor, campo, valor)
    db.commit()
    db.refresh(doctor)
    logger.info(
        "Doctor actualizado id=%s campos=%s", doctor.id, sorted(cambios) or "(ninguno)"
    )
    return doctor


def dar_de_baja(db: Session, doctor_id: int) -> Doctor:
    """Da de baja al doctor (**borrado lógico**: ``activo = False``).

    Idempotente: dar de baja dos veces no es un error. Devuelve cuántos turnos
    vigentes (no ``CANCELADO``) quedan agendados sólo en el log, para que quede
    trazado que la baja no los borra: siguen visibles en el calendario para
    reprogramarlos o cancelarlos.
    """
    doctor = obtener_doctor(db, doctor_id)

    if not doctor.activo:
        logger.info("Doctor id=%s ya estaba dado de baja", doctor.id)
        return doctor

    doctor.activo = False
    db.commit()
    db.refresh(doctor)

    vigentes = db.execute(
        select(Turno.id).where(
            Turno.doctor_id == doctor.id,
            Turno.estado_turno != EstadoTurno.CANCELADO,
        )
    ).scalars().all()
    logger.warning(
        "Doctor dado de baja id=%s nombre=%s (%s turnos vigentes conservados)",
        doctor.id,
        doctor.nombre,
        len(vigentes),
    )
    return doctor
