"""Router agregador de la API v1."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    anamnesis,
    aranceles,
    disponibilidad,
    doctores,
    facturacion,
    historias_clinicas,
    obras_sociales,
    pacientes,
    prestaciones,
    tareas,
    turnos,
    whatsapp,
    whatsapp_menu,
)

api_router = APIRouter()

api_router.include_router(doctores.router)
api_router.include_router(disponibilidad.router)
api_router.include_router(obras_sociales.router)
api_router.include_router(pacientes.router)
# Módulo 2: historia clínica y evoluciones médicas (sólo GET y POST).
api_router.include_router(historias_clinicas.router)
# Módulo 4: ficha de salud pública (token firmado, sin login de paciente).
api_router.include_router(anamnesis.router)
api_router.include_router(turnos.router)
# Módulo 3: prestaciones, aranceles, facturación, caja y liquidaciones.
api_router.include_router(prestaciones.router)
api_router.include_router(aranceles.router)
api_router.include_router(facturacion.router)
api_router.include_router(tareas.router)
api_router.include_router(whatsapp.router)
# Menú configurable del FAQ Bot (panel «Bot WhatsApp»).
api_router.include_router(whatsapp_menu.router)
