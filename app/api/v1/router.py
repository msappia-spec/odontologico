"""Router agregador de la API v1."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    disponibilidad,
    doctores,
    pacientes,
    tareas,
    turnos,
    whatsapp,
)

api_router = APIRouter()

api_router.include_router(doctores.router)
api_router.include_router(disponibilidad.router)
api_router.include_router(pacientes.router)
api_router.include_router(turnos.router)
api_router.include_router(tareas.router)
api_router.include_router(whatsapp.router)
