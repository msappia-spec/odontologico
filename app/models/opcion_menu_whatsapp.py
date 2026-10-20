"""Modelo ORM ``OpcionMenuWhatsApp``: menú configurable del FAQ Bot.

La secretaría administra desde el panel las opciones que recibe un paciente
cuando le escribe al WhatsApp del consultorio. Cada fila es un renglón del menú:

* ``numero``: el dígito que el paciente responde (``1``..``9`` y ``0``). Es único
  para que no haya dos opciones que se disparen con la misma tecla.
* ``titulo``: lo que se muestra en el mensaje (p. ej. «Precio de la consulta»).
* ``tipo``: qué hace el bot al recibir ese número. ``TEXTO`` envía la
  ``respuesta`` cargada a mano; los demás tipos arman la respuesta con datos del
  sistema (precio, obras sociales, ubicación) o derivan a una secretaria.
* ``activa``: **borrado lógico**. Una opción dada de baja no se muestra ni se
  atiende, pero se conserva para poder reactivarla.
* ``orden``: orden de aparición en el menú (empate: por número).

Si la tabla está vacía (por ejemplo en una base recién creada o en las pruebas)
el bot usa las cuatro opciones históricas por defecto; ``init_db`` las siembra
en producción la primera vez para que la secretaría las vea en el panel.
"""

from typing import Optional

from sqlalchemy import Boolean, Enum as SAEnum, Index, Integer, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.enums import TipoOpcionMenu


class OpcionMenuWhatsApp(Base):
    """Renglón configurable del menú que contesta el FAQ Bot."""

    __tablename__ = "opciones_menu_whatsapp"
    __table_args__ = (
        # Un dígito no puede repetirse: dos opciones con la misma tecla serían
        # ambiguas. Índice nombrado para poder replicarlo en bases existentes.
        Index("uq_opcion_menu_numero", "numero", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    numero: Mapped[str] = mapped_column(String(1), nullable=False)
    titulo: Mapped[str] = mapped_column(String(80), nullable=False)
    tipo: Mapped[TipoOpcionMenu] = mapped_column(
        SAEnum(
            TipoOpcionMenu,
            name="tipo_opcion_menu_whatsapp",
            native_enum=False,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        default=TipoOpcionMenu.TEXTO,
        server_default=TipoOpcionMenu.TEXTO.value,
    )
    respuesta: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    activa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    orden: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<OpcionMenuWhatsApp id={self.id} numero={self.numero!r} "
            f"titulo={self.titulo!r} tipo={self.tipo} activa={self.activa}>"
        )
