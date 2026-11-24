"""Router agregador de la API v1."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    anamnesis,
    aranceles,
    categorias_paciente,
    disponibilidad,
    doctores,
    egresos,
    facturacion,
    fichas_medicas,
    finanzas,
    historias_clinicas,
    obras_sociales,
    odontogramas,
    pacientes,
    presupuestos,
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
# Catálogo administrable de categorías de pacientes (formulario de pacientes).
api_router.include_router(categorias_paciente.router)
# Módulo 2: historia clínica y evoluciones médicas (sólo GET y POST).
api_router.include_router(historias_clinicas.router)
# Módulo 4: ficha de salud pública (token firmado, sin login de paciente).
api_router.include_router(anamnesis.router)
# Módulo 4: odontograma gráfico que completa el profesional (GET y PUT).
api_router.include_router(odontogramas.router)
# Módulo 4: ficha médica del paciente (cabecera, estado bucal y trabajos).
api_router.include_router(fichas_medicas.router)
api_router.include_router(turnos.router)
# Módulo de presupuestos: cotizaciones de tratamientos con salida en PDF.
api_router.include_router(presupuestos.router)
# Módulo 3: prestaciones, aranceles, facturación, caja y liquidaciones.
api_router.include_router(prestaciones.router)
api_router.include_router(aranceles.router)
api_router.include_router(facturacion.router)
# Módulo 5: cuenta corriente, planes de cuotas y pagos del paciente.
api_router.include_router(finanzas.router)
# Módulo 6: proveedores, gastos recibidos y cuentas por pagar (egresos).
api_router.include_router(egresos.router)
api_router.include_router(tareas.router)
api_router.include_router(whatsapp.router)
# Menú configurable del FAQ Bot (panel «Bot WhatsApp»).
api_router.include_router(whatsapp_menu.router)
