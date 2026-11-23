"""Estado de la conversación de WhatsApp persistido por paciente.

WAHA no permite correlacionar una respuesta de texto con el recordatorio que la
originó (no hay ``context.id`` ni botones). Por eso, cuando se envía un
recordatorio se guarda en ``conversaciones_whatsapp`` qué turno está esperando
respuesta. El webhook consulta esa fila antes de interpretar ``1`` / ``si`` o
``2`` / ``no``.

El servicio es deliberadamente chico: mantiene una fila por teléfono, la crea si
no existe y la vuelve a ``MENU`` cuando la confirmación termina o el turno ya no
es válido.
"""

from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.datetime_utils import utcnow
from app.core.whatsapp_utils import normalizar_telefono_waha
from app.models.conversacion_whatsapp import ConversacionWhatsApp
from app.models.enums import EstadoConversacionWhatsApp, EstadoTurno
from app.models.turno import Turno


def normalizar_telefono_contacto(telefono: Optional[str]) -> str:
    """Clave de la conversación en el formato canónico de WAHA.

    Se usa ``549...`` para que el teléfono guardado en la ficha (que puede estar
    en formato local, con ``0`` y ``15``) coincida con el ``from`` que envía WAHA
    (``549...@c.us``) y la confirmación pendiente se encuentre.
    """
    return normalizar_telefono_waha(telefono)


def obtener_conversacion(
    db: Session, telefono: Optional[str]
) -> Optional[ConversacionWhatsApp]:
    """Busca la conversación del teléfono (``None`` si todavía no existe)."""
    clave = normalizar_telefono_contacto(telefono)
    if not clave:
        return None
    stmt = select(ConversacionWhatsApp).where(
        ConversacionWhatsApp.telefono == clave
    )
    return db.execute(stmt).scalars().first()


def _guardar(db: Session, conversacion: ConversacionWhatsApp) -> ConversacionWhatsApp:
    """Confirma el estado tolerando una carrera de dos mensajes simultáneos.

    El índice único de ``telefono`` es la garantía final: si otro proceso creó la
    fila entre la lectura y el ``commit``, se revierte y se actualiza la fila que
    ganó.
    """
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existente = obtener_conversacion(db, conversacion.telefono)
        if existente is None:
            raise
        existente.estado = conversacion.estado
        existente.turno_id = conversacion.turno_id
        existente.whatsapp_mensaje_id = conversacion.whatsapp_mensaje_id
        existente.actualizado_en = utcnow()
        db.commit()
        conversacion = existente
    db.refresh(conversacion)
    return conversacion


def marcar_confirmacion_pendiente(
    db: Session,
    telefono: Optional[str],
    turno_id: int,
    whatsapp_mensaje_id: Optional[str] = None,
) -> Optional[ConversacionWhatsApp]:
    """Deja al teléfono esperando la confirmación del turno indicado."""
    clave = normalizar_telefono_contacto(telefono)
    if not clave:
        return None

    conversacion = obtener_conversacion(db, clave)
    if conversacion is None:
        conversacion = ConversacionWhatsApp(telefono=clave)
        db.add(conversacion)

    conversacion.estado = EstadoConversacionWhatsApp.CONFIRMACION_TURNO
    conversacion.turno_id = turno_id
    conversacion.whatsapp_mensaje_id = whatsapp_mensaje_id
    conversacion.actualizado_en = utcnow()
    return _guardar(db, conversacion)


def marcar_menu(
    db: Session, telefono: Optional[str]
) -> Optional[ConversacionWhatsApp]:
    """Deja al teléfono en el menú principal (sin confirmación pendiente)."""
    clave = normalizar_telefono_contacto(telefono)
    if not clave:
        return None

    conversacion = obtener_conversacion(db, clave)
    if conversacion is None:
        conversacion = ConversacionWhatsApp(telefono=clave)
        db.add(conversacion)

    conversacion.estado = EstadoConversacionWhatsApp.MENU
    conversacion.turno_id = None
    conversacion.whatsapp_mensaje_id = None
    conversacion.actualizado_en = utcnow()
    return _guardar(db, conversacion)


def cerrar_confirmacion(
    db: Session, telefono: Optional[str]
) -> Optional[ConversacionWhatsApp]:
    """Finaliza la confirmación pendiente y vuelve al menú."""
    return marcar_menu(db, telefono)


def obtener_confirmacion_pendiente(
    db: Session, telefono: Optional[str]
) -> Optional[Tuple[ConversacionWhatsApp, Turno]]:
    """Devuelve la conversación y su turno si hay una confirmación esperando.

    Si el estado apunta a un turno que ya no existe, que ya fue resuelto o que
    no está ``PENDIENTE``, se limpia la conversación y se devuelve ``None``. Así
    un paciente no queda atrapado para siempre en «respondé 1 o 2».
    """
    conversacion = obtener_conversacion(db, telefono)
    if conversacion is None:
        return None
    if conversacion.estado != EstadoConversacionWhatsApp.CONFIRMACION_TURNO:
        return None

    turno = (
        db.get(Turno, conversacion.turno_id)
        if conversacion.turno_id is not None
        else None
    )
    if turno is None or turno.estado_turno != EstadoTurno.PENDIENTE:
        marcar_menu(db, conversacion.telefono)
        return None

    return conversacion, turno
