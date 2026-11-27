"""Agregador de metadatos.

Importar este módulo garantiza que **todos** los modelos estén registrados en
``Base.metadata`` antes de invocar ``create_all``.
"""

from app.db.base_class import Base
from app.models.antecedentes_salud import AntecedentesSalud
from app.models.arancel_obra_social import ArancelObraSocial
from app.models.archivo_adjunto import ArchivoAdjunto
from app.models.categoria_paciente import CategoriaPaciente
from app.models.conversacion_whatsapp import ConversacionWhatsApp
from app.models.credencial_biometrica import CredencialBiometrica
from app.models.cuenta_corriente import CuentaCorriente, PlanCuotas, TransaccionCC
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.disponibilidad_especifica import DisponibilidadEspecifica
from app.models.doctor import Doctor
from app.models.egreso import GastoFactura, PagoEgreso, Proveedor
from app.models.evolucion_medica import EvolucionMedica
from app.models.ficha_medica import FichaMedica, FichaMedicaTrabajo
from app.models.historia_clinica import HistoriaClinica
from app.models.movimiento_caja import DetalleMovimientoCaja, MovimientoCaja
from app.models.obra_social import ObraSocial
from app.models.opcion_menu_whatsapp import OpcionMenuWhatsApp
from app.models.odontograma import Odontograma, OdontogramaDiente
from app.models.paciente import Paciente
from app.models.presupuesto import Presupuesto, PresupuestoItem
from app.models.prestacion import Prestacion
from app.models.turno import Turno
from app.models.usuario import Usuario

__all__ = [
    "Base",
    "AntecedentesSalud",
    "ArancelObraSocial",
    "ArchivoAdjunto",
    "CategoriaPaciente",
    "ConversacionWhatsApp",
    "CredencialBiometrica",
    "CuentaCorriente",
    "DetalleMovimientoCaja",
    "DisponibilidadDoctor",
    "DisponibilidadEspecifica",
    "Doctor",
    "GastoFactura",
    "EvolucionMedica",
    "FichaMedica",
    "FichaMedicaTrabajo",
    "HistoriaClinica",
    "MovimientoCaja",
    "ObraSocial",
    "OpcionMenuWhatsApp",
    "Odontograma",
    "OdontogramaDiente",
    "Paciente",
    "PagoEgreso",
    "PlanCuotas",
    "Presupuesto",
    "PresupuestoItem",
    "Prestacion",
    "Proveedor",
    "TransaccionCC",
    "Turno",
    "Usuario",
]
