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


class TipoDisponibilidadEspecifica(str, enum.Enum):
    """Efecto de un tramo horario cargado para **una fecha concreta**.

    Se usa en ``DisponibilidadEspecifica`` (excepciones de agenda): un rango
    ``HABILITADO`` suma atención ese día y uno ``BLOQUEADO`` la resta, sin
    tocar la agenda semanal del odontólogo (``DisponibilidadDoctor``).
    """

    HABILITADO = "HABILITADO"
    BLOQUEADO = "BLOQUEADO"
