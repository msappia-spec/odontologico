"""Modelo ORM ``Usuario`` (autenticación y permisos por pantalla).

Un usuario del sistema es una **persona con credenciales** para entrar al panel
de la secretaría. Sus permisos se guardan como una lista de códigos de módulo
(``modulos_permitidos``) que el frontend usa para ocultar las secciones del menú
y el backend para autorizar cada router (`require_module`).

Decisiones de diseño:

* ``id`` es ``UUID`` (``sqlalchemy.Uuid``: nativo en PostgreSQL, ``CHAR(32)`` en
  SQLite), igual que las tablas contables del proyecto.
* ``email`` es único e indexado y **se guarda en minúsculas** (lo normaliza el
  servicio) para que el login no dependa de cómo se escribió.
* ``hashed_password`` guarda **sólo** el hash bcrypt con sal aleatoria; nunca la
  contraseña en texto plano. El largo (255) cubre el hash completo (60) y deja
  margen si en el futuro se migra a otro esquema.
* ``es_superadmin`` saltea todos los permisos; ``activo`` permite bloquear el
  acceso al instante sin perder la auditoría (el usuario sigue existiendo).
* ``doctor_id`` es **opcional** y es la clave con la que se vincula la cuenta con
  un odontólogo del registro. ``doctores.id`` es un entero en este proyecto (no
  UUID), por eso la columna es ``Integer``: la integridad referencial manda sobre
  el tipo teórico.
* ``modulos_permitidos`` es ``JSON`` (lista de strings). En PostgreSQL y SQLite
  se mapea al tipo ``JSON`` nativo; las filas nuevas arrancan con ``[]``.
* ``ultimo_acceso`` es ``UTCDateTime`` (UTC naive, convención del proyecto) y se
  actualiza en cada login exitoso.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, ForeignKey, JSON, String, false, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.credencial_biometrica import CredencialBiometrica
    from app.models.doctor import Doctor


class Usuario(Base):
    """Cuenta de acceso al panel con sus permisos por sección.

    ``modulos_permitidos`` se interpreta así:

    * ``es_superadmin = True`` -> entra a todo, sin importar la lista.
    * en caso contrario, la lista tiene los códigos del catálogo
      :data:`app.core.security.MODULOS` (``PANEL_PRINCIPAL``,
      ``AGENDA_TURNOS``, ...). Un módulo ausente se traduce en HTTP 403 en la
      API y en un ítem oculto en el menú del panel.
    """

    __tablename__ = "usuarios"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(
        String(150), nullable=False, unique=True, index=True
    )
    nombre_completo: Mapped[str] = mapped_column(String(150), nullable=False)
    # Hash bcrypt con sal aleatoria (60 caracteres); nunca texto plano.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    es_superadmin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    # Vínculo opcional con el registro de odontólogos (``doctores.id`` es int).
    # ``SET NULL``: si se borra el profesional, la cuenta sigue existiendo sin
    # quedar apuntando a una fila inexistente.
    doctor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("doctores.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Lista de códigos de módulo habilitados. ``default=list`` se ejecuta por
    # instancia (nunca comparte la misma lista entre usuarios).
    modulos_permitidos: Mapped[List[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    ultimo_acceso: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )

    doctor: Mapped[Optional["Doctor"]] = relationship(back_populates="usuarios")
    #: Passkeys del usuario (celular, notebook). Se borran con la cuenta y el
    #: ``ON DELETE CASCADE`` de la tabla hace lo mismo en la base.
    credenciales_biometricas: Mapped[List["CredencialBiometrica"]] = relationship(
        back_populates="usuario",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<Usuario id={self.id} email={self.email!r} "
            f"superadmin={self.es_superadmin} activo={self.activo}>"
        )
