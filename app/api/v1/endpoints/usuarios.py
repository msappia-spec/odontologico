"""Administración de usuarios y permisos (sección «Usuarios y Permisos»).

Todo el router exige el módulo ``ADMIN_USUARIOS`` (o ser superadmin). Además de
la autorización por router hay tres reglas de **alcance** que evitan escaladas de
privilegios y bloqueos accidentales:

* sólo un superadmin puede crear o convertir una cuenta en superadmin;
* sólo un superadmin puede administrar (cambiar permisos o blanquear la clave de)
  otra cuenta de superadmin;
* nadie puede desactivarse a sí mismo ni quitarse el último permiso de
  administración, y el sistema nunca queda sin ningún superadmin activo
  (esta última regla vive en :mod:`app.services.usuario_service`).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_module
from app.db.session import get_db
from app.models.usuario import Usuario
from app.schemas.usuario import (
    MensajeRead,
    ResetPasswordRequest,
    UsuarioCreate,
    UsuarioPermisosUpdate,
    UsuarioRead,
)
from app.services import usuario_service

router = APIRouter(
    prefix="/usuarios",
    tags=["Usuarios y permisos"],
    dependencies=[Depends(require_module("ADMIN_USUARIOS"))],
)


def _verificar_alcance(actor: Usuario, objetivo: Usuario) -> None:
    """Impide que un administrador común toque una cuenta de superadmin."""
    if objetivo.es_superadmin and not actor.es_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Sólo un superadmin puede administrar otra cuenta de superadmin."
            ),
        )


@router.get(
    "",
    response_model=list[UsuarioRead],
    summary="Listar el equipo y sus permisos",
    description=(
        "Devuelve todas las cuentas (activas y bloqueadas) con sus módulos y el "
        "odontólogo vinculado. No incluye el hash de la contraseña."
    ),
)
def listar_usuarios(db: Session = Depends(get_db)) -> list[UsuarioRead]:
    return [usuario_service.leer_usuario(u) for u in usuario_service.listar_usuarios(db)]


@router.post(
    "",
    response_model=UsuarioRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un usuario",
    description=(
        "Crea una cuenta con contraseña fuerte (mínimo 8 caracteres, mayúscula, "
        "minúscula y número) y los módulos marcados. El email es único (409 si "
        "ya existe) y la contraseña se guarda como hash bcrypt."
    ),
)
def crear_usuario(
    payload: UsuarioCreate,
    db: Session = Depends(get_db),
    actor: Usuario = Depends(get_current_user),
) -> UsuarioRead:
    if payload.es_superadmin and not actor.es_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sólo un superadmin puede crear otro superadmin.",
        )
    usuario = usuario_service.crear_usuario(db, payload)
    return usuario_service.leer_usuario(usuario)


@router.patch(
    "/{usuario_id}/permisos",
    response_model=UsuarioRead,
    summary="Actualizar módulos o estado de la cuenta",
    description=(
        "Reemplaza la lista de módulos permitidos y/o activa-desactiva la "
        "cuenta. Desactivar bloquea el acceso en el acto (el token deja de "
        "servir). No se puede dejar el sistema sin superadmins activos."
    ),
)
def actualizar_permisos(
    usuario_id: uuid.UUID,
    payload: UsuarioPermisosUpdate,
    db: Session = Depends(get_db),
    actor: Usuario = Depends(get_current_user),
) -> UsuarioRead:
    objetivo = usuario_service.obtener_usuario(db, usuario_id)
    _verificar_alcance(actor, objetivo)

    if payload.es_superadmin is not None and not actor.es_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sólo un superadmin puede cambiar la marca de superadmin.",
        )
    if objetivo.id == actor.id:
        if payload.activo is False:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No podés desactivar tu propia cuenta.",
            )
        admin_final = (
            payload.es_superadmin
            if payload.es_superadmin is not None
            else objetivo.es_superadmin
        )
        if (
            payload.modulos_permitidos is not None
            and not admin_final
            and "ADMIN_USUARIOS" not in payload.modulos_permitidos
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No podés quitarte el permiso «Usuarios y permisos»: "
                    "quedarías sin acceso a esta sección."
                ),
            )

    usuario = usuario_service.actualizar_permisos(
        db,
        objetivo,
        modulos_permitidos=payload.modulos_permitidos,
        activo=payload.activo,
        es_superadmin=payload.es_superadmin,
        doctor_id=payload.doctor_id,
    )
    return usuario_service.leer_usuario(usuario)


@router.patch(
    "/{usuario_id}/reset-password",
    response_model=MensajeRead,
    summary="Blanquear la contraseña de un empleado",
    description=(
        "Reemplaza la contraseña por una nueva (con la misma regla de contraseña "
        "fuerte). El hash anterior deja de valer de inmediato."
    ),
)
def resetear_password(
    usuario_id: uuid.UUID,
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
    actor: Usuario = Depends(get_current_user),
) -> MensajeRead:
    objetivo = usuario_service.obtener_usuario(db, usuario_id)
    _verificar_alcance(actor, objetivo)
    usuario_service.resetear_password(db, objetivo, payload.password)
    return MensajeRead(
        detail=f"La contraseña de {objetivo.nombre_completo} se actualizó."
    )
