"""Enumeraciones del dominio (estados de turno y de notificación por WhatsApp)."""

import enum


class EstadoTurno(str, enum.Enum):
    """Estado lógico del turno."""

    PENDIENTE = "PENDIENTE"
    CONFIRMADO = "CONFIRMADO"
    CANCELADO = "CANCELADO"


class EstadoWhatsApp(str, enum.Enum):
    """Estado del flujo de notificación por WhatsApp asociado al turno."""

    PENDIENTE_ENVIO = "PENDIENTE_ENVIO"
    ENVIADO = "ENVIADO"
    FALLIDO = "FALLIDO"
    RESPONDIDO = "RESPONDIDO"
