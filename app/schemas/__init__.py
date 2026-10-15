"""Esquemas Pydantic para validación/serialización de la API."""

from app.schemas.disponibilidad import (
    BloqueDisponible,
    DisponibilidadCreate,
    DisponibilidadEspecificaCreate,
    DisponibilidadEspecificaRead,
    DisponibilidadEspecificaReemplazo,
    DisponibilidadRead,
    DisponibilidadReemplazo,
    ExcepcionFechaBase,
    HorariosDisponiblesRead,
    OrigenDisponibilidad,
    RangoEfectivoRead,
)
from app.schemas.doctor import (
    DoctorCreate,
    DoctorRead,
    DoctorReplace,
    DoctorUpdate,
)
from app.schemas.historia_clinica import (
    ArchivoAdjuntoCreate,
    ArchivoAdjuntoRead,
    EvolucionMedicaCreate,
    EvolucionMedicaRead,
    HistoriaClinicaDetalle,
)
from app.schemas.obra_social import (
    ObraSocialCreate,
    ObraSocialRead,
    ObraSocialUpdate,
)
from app.schemas.paciente import PacienteCreate, PacienteRead, PacienteUpdate
from app.schemas.turno import TurnoCreate, TurnoRead, TurnoUpdate
from app.schemas.whatsapp import (
    RecordatorioPendiente,
    RecordatorioResultado,
    WhatsAppWebhookPayload,
    WebhookProcesadoResponse,
)

__all__ = [
    "ArchivoAdjuntoCreate",
    "ArchivoAdjuntoRead",
    "BloqueDisponible",
    "DisponibilidadCreate",
    "DisponibilidadEspecificaCreate",
    "DisponibilidadEspecificaRead",
    "DisponibilidadEspecificaReemplazo",
    "DisponibilidadRead",
    "DisponibilidadReemplazo",
    "DoctorCreate",
    "DoctorRead",
    "DoctorReplace",
    "DoctorUpdate",
    "EvolucionMedicaCreate",
    "EvolucionMedicaRead",
    "ExcepcionFechaBase",
    "HistoriaClinicaDetalle",
    "HorariosDisponiblesRead",
    "OrigenDisponibilidad",
    "RangoEfectivoRead",
    "ObraSocialCreate",
    "ObraSocialRead",
    "ObraSocialUpdate",
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

