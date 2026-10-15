"""Agregador de metadatos.

Importar este módulo garantiza que **todos** los modelos estén registrados en
``Base.metadata`` antes de invocar ``create_all``.
"""

from app.db.base_class import Base
from app.models.arancel_obra_social import ArancelObraSocial
from app.models.archivo_adjunto import ArchivoAdjunto
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.disponibilidad_especifica import DisponibilidadEspecifica
from app.models.doctor import Doctor
from app.models.evolucion_medica import EvolucionMedica
from app.models.historia_clinica import HistoriaClinica
from app.models.movimiento_caja import DetalleMovimientoCaja, MovimientoCaja
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.models.prestacion import Prestacion
from app.models.turno import Turno

__all__ = [
    "Base",
    "ArancelObraSocial",
    "ArchivoAdjunto",
    "DetalleMovimientoCaja",
    "DisponibilidadDoctor",
    "DisponibilidadEspecifica",
    "Doctor",
    "EvolucionMedica",
    "HistoriaClinica",
    "MovimientoCaja",
    "ObraSocial",
    "Paciente",
    "Prestacion",
    "Turno",
]
