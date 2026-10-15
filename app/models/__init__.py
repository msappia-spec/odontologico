"""Modelos ORM del dominio (Módulo 1: turnos; Módulo 2: historia clínica)."""

from app.models.archivo_adjunto import ArchivoAdjunto
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.disponibilidad_especifica import DisponibilidadEspecifica
from app.models.doctor import Doctor
from app.models.enums import (
    EstadoTurno,
    EstadoWhatsApp,
    TipoDisponibilidadEspecifica,
)
from app.models.evolucion_medica import EvolucionMedica
from app.models.historia_clinica import HistoriaClinica
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.models.turno import Turno

__all__ = [
    "ArchivoAdjunto",
    "DisponibilidadDoctor",
    "DisponibilidadEspecifica",
    "Doctor",
    "EvolucionMedica",
    "HistoriaClinica",
    "ObraSocial",
    "Paciente",
    "Turno",
    "EstadoTurno",
    "EstadoWhatsApp",
    "TipoDisponibilidadEspecifica",
]
