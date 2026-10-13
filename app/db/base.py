"""Agregador de metadatos.

Importar este módulo garantiza que **todos** los modelos estén registrados en
``Base.metadata`` antes de invocar ``create_all``.
"""

from app.db.base_class import Base
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.doctor import Doctor
from app.models.paciente import Paciente
from app.models.turno import Turno

__all__ = ["Base", "DisponibilidadDoctor", "Doctor", "Paciente", "Turno"]
