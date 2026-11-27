"""Servicio de usuarios, credenciales y permisos.

Centraliza las reglas que no son HTTP para que los endpoints (y el script
``scripts/crear_admin.py``) compartan el mismo comportamiento:

* el email se normaliza y es único sin distinguir mayúsculas;
* la contraseña se guarda **sólo** como hash bcrypt;
* no se puede dejar el sistema sin ningún superadmin activo;
* ``doctor_id`` debe apuntar a un odontólogo existente;
* la lectura pública del usuario nunca incluye el hash.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_utils import utcnow
from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    ValidacionError,
)
from app.core.security import hash_password
from app.models.doctor import Doctor
from app.models.usuario import Usuario
from app.schemas.usuario import UsuarioCreate, UsuarioRead


def normalizar_email(email: str) -> str:
    """Devuelve el email en minúsculas y sin espacios (clave del login)."""
    return (email or "").strip().lower()


def contar_superadmins_activos(
    db: Session, excluyendo_id: Optional[uuid.UUID] = None
) -> int:
    """Cuenta los superadmins activos (opcionalmente sin contar uno)."""
    consulta = select(func.count()).select_from(Usuario).where(
        Usuario.es_superadmin.is_(True), Usuario.activo.is_(True)
    )
    if excluyendo_id is not None:
        consulta = consulta.where(Usuario.id != excluyendo_id)
    return int(db.execute(consulta).scalar_one())


def listar_usuarios(db: Session) -> List[Usuario]:
    """Lista el equipo ordenado por nombre (los activos primero)."""
    consulta = select(Usuario).order_by(
        Usuario.activo.desc(), func.lower(Usuario.nombre_completo)
    )
    return list(db.execute(consulta).scalars().all())


def obtener_por_email(db: Session, email: str) -> Optional[Usuario]:
    """Busca un usuario por email normalizado."""
    consulta = select(Usuario).where(Usuario.email == normalizar_email(email))
    return db.execute(consulta).scalars().first()


def obtener_usuario(db: Session, usuario_id: uuid.UUID) -> Usuario:
    """Devuelve el usuario o lanza ``RecursoNoEncontradoError`` (404)."""
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise RecursoNoEncontradoError("Usuario no encontrado.")
    return usuario


def _validar_doctor(db: Session, doctor_id: Optional[int]) -> None:
    """Verifica que el odontólogo vinculado exista."""
    if doctor_id is None:
        return
    if db.get(Doctor, doctor_id) is None:
        raise ValidacionError("El odontólogo indicado no existe.")


def _verificar_email_libre(
    db: Session, email: str, excluyendo_id: Optional[uuid.UUID] = None
) -> None:
    """Lanza 409 si el email ya pertenece a otra cuenta."""
    existente = obtener_por_email(db, email)
    if existente is not None and existente.id != excluyendo_id:
        raise ConflictoError("Ya existe un usuario con ese email.")


def crear_usuario(db: Session, payload: UsuarioCreate) -> Usuario:
    """Crea un usuario con la contraseña ya hasheada.

    El chequeo previo del email da un 409 claro; el ``IntegrityError`` es la red
    de seguridad ante dos altas simultáneas con el mismo email.
    """
    email = normalizar_email(str(payload.email))
    _verificar_email_libre(db, email)
    _validar_doctor(db, payload.doctor_id)

    usuario = Usuario(
        email=email,
        nombre_completo=payload.nombre_completo,
        hashed_password=hash_password(payload.password),
        es_superadmin=payload.es_superadmin,
        activo=payload.activo,
        doctor_id=payload.doctor_id,
        modulos_permitidos=list(payload.modulos_permitidos),
    )
    db.add(usuario)
    try:
        db.commit()
    except IntegrityError as exc:  # pragma: no cover - carrera improbable
        db.rollback()
        raise ConflictoError("Ya existe un usuario con ese email.") from exc
    db.refresh(usuario)
    return usuario


def actualizar_permisos(
    db: Session,
    usuario: Usuario,
    *,
    modulos_permitidos: Optional[List[str]] = None,
    activo: Optional[bool] = None,
    es_superadmin: Optional[bool] = None,
    doctor_id: Optional[int] = None,
) -> Usuario:
    """Aplica cambios de permisos/estado respetando la regla del último admin.

    Si la operación dejaría el sistema **sin ningún superadmin activo** se
    rechaza con 409: sería imposible volver a administrar usuarios desde el
    panel. El chequeo se hace antes de modificar el objeto para no dejar la
    sesión con cambios a medio aplicar.
    """
    deja_de_ser_admin = es_superadmin is False and usuario.es_superadmin
    se_desactiva = activo is False and usuario.activo
    if (deja_de_ser_admin or se_desactiva) and usuario.es_superadmin:
        if contar_superadmins_activos(db, excluyendo_id=usuario.id) == 0:
            raise ConflictoError(
                "No se puede dejar el sistema sin ningún superadmin activo. "
                "Creá o habilitá otro administrador primero."
            )

    if modulos_permitidos is not None:
        usuario.modulos_permitidos = list(modulos_permitidos)
    if activo is not None:
        usuario.activo = activo
    if es_superadmin is not None:
        usuario.es_superadmin = es_superadmin
    if doctor_id is not None:
        _validar_doctor(db, doctor_id)
        usuario.doctor_id = doctor_id
    db.commit()
    db.refresh(usuario)
    return usuario


def resetear_password(db: Session, usuario: Usuario, password: str) -> Usuario:
    """Reemplaza el hash de la contraseña (blanqueo por administrador)."""
    usuario.hashed_password = hash_password(password)
    db.commit()
    db.refresh(usuario)
    return usuario


def registrar_acceso(db: Session, usuario: Usuario) -> Usuario:
    """Actualiza ``ultimo_acceso`` con la hora UTC naive del login."""
    usuario.ultimo_acceso = utcnow()
    db.commit()
    db.refresh(usuario)
    return usuario


def leer_usuario(usuario: Usuario) -> UsuarioRead:
    """Arma el esquema de lectura con el nombre del odontólogo vinculado."""
    datos = UsuarioRead.model_validate(usuario)
    return datos.model_copy(
        update={
            "doctor_nombre": usuario.doctor.nombre if usuario.doctor else None,
            "modulos_permitidos": list(usuario.modulos_permitidos or []),
        }
    )
