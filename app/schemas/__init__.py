"""Esquemas Pydantic para validación/serialización de la API."""

from app.schemas.disponibilidad import (
    BloqueDisponible,
    DisponibilidadCreate,
    DisponibilidadRead,
    DisponibilidadReemplazo,
    HorariosDisponiblesRead,
)
from app.schemas.doctor import DoctorCreate, DoctorRead
from app.schemas.paciente import PacienteCreate, PacienteRead, PacienteUpdate
from app.schemas.turno import TurnoCreate, TurnoRead, TurnoUpdate
from app.schemas.whatsapp import (
    RecordatorioPendiente,
    RecordatorioResultado,
    WhatsAppWebhookPayload,
    WebhookProcesadoResponse,
)

__all__ = [
    "BloqueDisponible",
    "DisponibilidadCreate",
    "DisponibilidadRead",
    "DisponibilidadReemplazo",
    "DoctorCreate",
    "DoctorRead",
    "HorariosDisponiblesRead",
    "PacienteCreate",
    "PacienteRead",
    "PacienteUpdate",
    "TurnoCreate",
    "TurnoRead",
    "TurnoUpdate",
    "WhatsAppWebhookPayload",
    "WebhookProcesadoResponse",
    "RecordatorioPendiente",
    "RecordatorioResultado",
]

