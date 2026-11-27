"""Servicio de credenciales biométricas (WebAuthn / passkeys).

Mantiene las consultas y las reglas de persistencia fuera de los endpoints:

* listar los dispositivos de un usuario (para el perfil y para excluirlos al
  registrar uno nuevo);
* buscar una credencial por su ``credential_id`` (login con huella);
* guardar el alta y traducir la carrera de IDs duplicados a un 409;
* actualizar el contador anti-clonación y borrar un dispositivo perdido.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictoError, RecursoNoEncontradoError
from app.models.credencial_biometrica import CredencialBiometrica


def listar_credenciales(
    db: Session, usuario_id: uuid.UUID
) -> List[CredencialBiometrica]:
    """Devuelve los dispositivos del usuario, del más nuevo al más viejo."""
    consulta = (
        select(CredencialBiometrica)
        .where(CredencialBiometrica.usuario_id == usuario_id)
        .order_by(CredencialBiometrica.fecha_registro.desc())
    )
    return list(db.execute(consulta).scalars().all())


def ids_de_credenciales(db: Session, usuario_id: uuid.UUID) -> list[str]:
    """IDs canónicos de las credenciales del usuario (para ``allowCredentials``)."""
    return [credencial.credential_id for credencial in listar_credenciales(db, usuario_id)]


def contar_credenciales(db: Session, usuario_id: uuid.UUID) -> int:
    """Cantidad de dispositivos registrados por el usuario."""
    return len(listar_credenciales(db, usuario_id))


def buscar_por_credential_id(
    db: Session, credential_id: str
) -> Optional[CredencialBiometrica]:
    """Busca una credencial por su ID canónico (base64url sin relleno)."""
    consulta = select(CredencialBiometrica).where(
        CredencialBiometrica.credential_id == credential_id
    )
    return db.execute(consulta).scalars().first()


def guardar_credencial(
    db: Session,
    *,
    usuario_id: uuid.UUID,
    credential_id: str,
    public_key: bytes,
    sign_count: int,
    nombre_dispositivo: str,
) -> CredencialBiometrica:
    """Persiste una credencial recién verificada.

    El ``credential_id`` es único en toda la base: si ya existe (otro usuario o
    un doble submit del mismo alta) se responde 409 en lugar de duplicar la
    llave. El ``IntegrityError`` cubre dos altas simultáneas.
    """
    if buscar_por_credential_id(db, credential_id) is not None:
        raise ConflictoError(
            "Este dispositivo ya está registrado. Probá con otro o eliminá el "
            "registro anterior desde tu perfil."
        )

    credencial = CredencialBiometrica(
        usuario_id=usuario_id,
        credential_id=credential_id,
        public_key=public_key,
        sign_count=max(0, int(sign_count or 0)),
        nombre_dispositivo=nombre_dispositivo or "Dispositivo",
    )
    db.add(credencial)
    try:
        db.commit()
    except IntegrityError as exc:  # pragma: no cover - carrera improbable
        db.rollback()
        raise ConflictoError(
            "Este dispositivo ya está registrado."
        ) from exc
    db.refresh(credencial)
    return credencial


def actualizar_contador(
    db: Session, credencial: CredencialBiometrica, nuevo_contador: int
) -> CredencialBiometrica:
    """Guarda el ``sign_count`` que devolvió el autenticador."""
    credencial.sign_count = max(0, int(nuevo_contador or 0))
    db.commit()
    db.refresh(credencial)
    return credencial


def eliminar_credencial(
    db: Session, usuario_id: uuid.UUID, credencial_id: uuid.UUID
) -> None:
    """Borra un dispositivo **de ese usuario** (404 si no existe o es ajeno)."""
    credencial = db.get(CredencialBiometrica, credencial_id)
    if credencial is None or credencial.usuario_id != usuario_id:
        raise RecursoNoEncontradoError("Credencial biométrica no encontrada.")
    db.delete(credencial)
    db.commit()
