"""Esquemas Pydantic para validación/serialización de la API."""

from app.schemas.antecedentes_salud import (
    AnamnesisGuardadaRead,
    AnamnesisPublicaRead,
    AntecedentesSaludRead,
    DeclaracionSaludCreate,
)
from app.schemas.arancel import (
    ArancelObraSocialCreate,
    ArancelObraSocialRead,
    ArancelObraSocialUpdate,
)
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
from app.schemas.facturacion import (
    CobrarMovimientoRequest,
    DebitarMovimientoRequest,
    DetalleMovimientoCajaRead,
    FacturacionTurnoRead,
    FacturarTurnoRequest,
    LiquidacionObraSocialRequest,
    LiquidacionProfesionalDoctorRead,
    LiquidacionProfesionalesRead,
    MovimientoCajaRead,
    PresentacionObraSocialRead,
    PresupuestoCobroRead,
    PrestacionDisponibleRead,
    PrestacionesDisponiblesRead,
    PrestacionFacturadaCalculadaRead,
    PrestacionFacturadaCreate,
    ResumenCajaRead,
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
from app.schemas.prestacion import (
    PrestacionCreate,
    PrestacionRead,
    PrestacionUpdate,
)
from app.schemas.turno import TurnoCreate, TurnoRead, TurnoUpdate
from app.schemas.whatsapp import (
    RecordatorioPendiente,
    RecordatorioResultado,
    WhatsAppWebhookPayload,
    WebhookProcesadoResponse,
)

__all__ = [
    "AnamnesisGuardadaRead",
    "AnamnesisPublicaRead",
    "AntecedentesSaludRead",
    "ArancelObraSocialCreate",
    "ArancelObraSocialRead",
    "ArancelObraSocialUpdate",
    "ArchivoAdjuntoCreate",
    "ArchivoAdjuntoRead",
    "BloqueDisponible",
    "CobrarMovimientoRequest",
    "DebitarMovimientoRequest",
    "DeclaracionSaludCreate",
    "DetalleMovimientoCajaRead",
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
    "FacturacionTurnoRead",
    "FacturarTurnoRequest",
    "HistoriaClinicaDetalle",
    "HorariosDisponiblesRead",
    "LiquidacionObraSocialRequest",
    "LiquidacionProfesionalDoctorRead",
    "LiquidacionProfesionalesRead",
    "MovimientoCajaRead",
    "OrigenDisponibilidad",
    "PresentacionObraSocialRead",
    "PresupuestoCobroRead",
    "PrestacionCreate",
    "PrestacionDisponibleRead",
    "PrestacionesDisponiblesRead",
    "PrestacionFacturadaCalculadaRead",
    "PrestacionFacturadaCreate",
    "PrestacionRead",
    "PrestacionUpdate",
    "RangoEfectivoRead",
    "ResumenCajaRead",
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


