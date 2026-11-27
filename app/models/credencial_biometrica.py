"""Modelo ORM ``CredencialBiometrica`` (WebAuthn / passkeys).

Una credencial biométrica es la **llave pública** que el teléfono o la notebook
del usuario generó al registrar su huella digital o su Face ID. El secreto
privado nunca sale del dispositivo; en la base sólo se guarda:

* ``credential_id``: identificador que el autenticador elige y que el navegador
  devuelve en cada intento de ingreso. Se guarda como **base64url** (sin ``=``)
  porque es el formato del estándar y evita ambigüedades entre SQLite y
  PostgreSQL.
* ``public_key``: clave pública en formato COSE (bytes). Con ella el servidor
  verifica la firma del desafío; no permite suplantar al usuario.
* ``sign_count``: contador de firmas del autenticador. Sirve para detectar una
  credencial clonada (un contador que retrocede es sospechoso).

Decisiones de diseño:

* ``id`` es ``UUID`` (``sqlalchemy.Uuid``), igual que ``usuarios``.
* La relación es **uno-a-muchos**: un médico puede tener su celular y su
  notebook registrados; cualquiera de los dos sirve para entrar.
* ``ON DELETE CASCADE`` en ``usuario_id``: si algún día se borra la cuenta (hoy
  se desactiva), sus credenciales no quedan huérfanas.
* ``fecha_registro`` usa ``UTCDateTime`` (UTC naive), la convención del
  proyecto para las fechas que se muestran en el panel.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import LargeBinary, Uuid

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo tipado/evita el import circular
    from app.models.usuario import Usuario


class CredencialBiometrica(Base):
    """Llave pública de un dispositivo registrado para entrar sin contraseña.

    El registro se hace **con la sesión ya abierta** (``POST
    /auth/webauthn/registro/opciones``): la biometría no reemplaza el alta del
    usuario, sólo evita escribir la clave en el celular. Por eso la credencial
    siempre pertenece a un :class:`~app.models.usuario.Usuario` existente.
    """

    __tablename__ = "credenciales_biometricas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Identificador del autenticador en base64url (sin ``=``). Único: dos
    #: usuarios no pueden compartir la misma llave física. El estándar permite
    #: IDs de hasta 1023 bytes (≈1364 caracteres en base64url); se reserva 2048
    #: para no truncar ningún autenticador real.
    credential_id: Mapped[str] = mapped_column(
        String(2048), nullable=False, unique=True, index=True
    )
    #: Clave pública COSE tal como la entrega el navegador (no es un secreto).
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: Contador de firmas para detectar clonación (0 = no implementado).
    sign_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: Nombre que ve el usuario para reconocer el dispositivo ("Mi celular").
    nombre_dispositivo: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Dispositivo"
    )
    fecha_registro: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    usuario: Mapped["Usuario"] = relationship(
        back_populates="credenciales_biometricas"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<CredencialBiometrica id={self.id} "
            f"usuario_id={self.usuario_id} dispositivo={self.nombre_dispositivo!r}>"
        )
