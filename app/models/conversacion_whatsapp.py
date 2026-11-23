"""Modelo ORM del estado de conversación de WhatsApp por paciente.

WAHA entrega los mensajes como texto libre: a diferencia de los botones de la
Cloud API, la respuesta ``1`` / ``2`` no trae el ``wamid`` del recordatorio. Para
poder confirmar o cancelar el turno correcto hay que recordar **qué** se le envió
a cada número. Esa memoria vive acá, una fila por teléfono:

* ``estado``: ``MENU`` o ``CONFIRMACION_TURNO``.
* ``turno_id``: turno pendiente de confirmación (sólo en el segundo estado).
* ``whatsapp_mensaje_id``: identificador saliente del recordatorio, para auditoría.

El registro sobrevive a reinicios y es único por teléfono, así que dos mensajes
simultáneos del mismo paciente no crean filas duplicadas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.datetime_utils import utcnow
from app.db.base_class import Base
from app.db.types import UTCDateTime
from app.models.enums import EstadoConversacionWhatsApp


class ConversacionWhatsApp(Base):
    """Estado de la conversación de un paciente con el bot."""

    __tablename__ = "conversaciones_whatsapp"
    __table_args__ = (
        # Un solo estado por teléfono: el webhook siempre actualiza la misma fila.
        Index("uq_conversacion_whatsapp_telefono", "telefono", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    telefono: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    estado: Mapped[EstadoConversacionWhatsApp] = mapped_column(
        SAEnum(
            EstadoConversacionWhatsApp,
            name="estado_conversacion_whatsapp",
            native_enum=False,
            validate_strings=True,
            length=30,
        ),
        nullable=False,
        default=EstadoConversacionWhatsApp.MENU,
        server_default=EstadoConversacionWhatsApp.MENU.value,
    )
    turno_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("turnos.id", ondelete="SET NULL"), nullable=True, index=True
    )
    whatsapp_mensaje_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<ConversacionWhatsApp id={self.id} telefono={self.telefono!r} "
            f"estado={self.estado} turno_id={self.turno_id}>"
        )
