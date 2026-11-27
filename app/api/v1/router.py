"""Router agregador de la API v1.

Cada router se incluye con la dependencia ``require_module(...)`` de la sección
del menú que lo consume, de modo que el permiso se aplica a **todas** las rutas
del router sin repetir el ``Depends`` en cada endpoint. `require_module` acepta
varios códigos: la ruta queda autorizada si el usuario tiene al menos uno (sirve
para recursos compartidos, como el catálogo de pacientes que usan la agenda, la
caja y la cuenta corriente).

Quedan **públicas** a propósito:

* ``/health`` y las páginas web (no exponen datos);
* ``POST /whatsapp/webhook`` (lo llama WAHA sin token);
* ``/anamnesis/{token}`` (la ficha pública que recibe el paciente);
* ``/tareas/enviar-recordatorios`` (se autentica con ``CRON_SECRET``).

Las rutas internas de la ficha de salud (``/anamnesis/panel/...``) y los
recordatorios del panel declaran el permiso en el propio decorador.
"""

from fastapi import APIRouter, Depends

from app.api.v1.endpoints import (
    anamnesis,
    aranceles,
    auth,
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
    usuarios,
    whatsapp,
    whatsapp_menu,
)
from app.core.security import require_module

api_router = APIRouter()

# --- Sesión y administración de cuentas --------------------------------------
api_router.include_router(auth.router)
api_router.include_router(usuarios.router)

# --- Operación diaria --------------------------------------------------------
# Los odontólogos los necesitan también la agenda (filtros y horarios), y el
# alta/baja vive en «Registros».
api_router.include_router(
    doctores.router,
    dependencies=[
        Depends(require_module("REGISTROS", "AGENDA_TURNOS", "ADMIN_USUARIOS"))
    ],
)
api_router.include_router(
    disponibilidad.router,
    dependencies=[Depends(require_module("AGENDA_TURNOS"))],
)
# El catálogo de coberturas alimenta la configuración, la facturación y el
# formulario de pacientes.
api_router.include_router(
    obras_sociales.router,
    dependencies=[
        Depends(require_module("CONFIGURACION", "CAJA_PRESUPUESTOS", "REGISTROS"))
    ],
)
# Pacientes: recurso compartido por casi todas las pantallas del panel.
api_router.include_router(
    pacientes.router,
    dependencies=[
        Depends(
            require_module(
                "REGISTROS",
                "AGENDA_TURNOS",
                "FICHAS_SALUD",
                "CAJA_PRESUPUESTOS",
                "CUENTA_CORRIENTE",
                "PROVEEDORES_EGRESOS",
            )
        )
    ],
)
# Catálogo administrable de categorías de pacientes (formulario de pacientes y
# aviso del turno).
api_router.include_router(
    categorias_paciente.router,
    dependencies=[Depends(require_module("REGISTROS", "AGENDA_TURNOS"))],
)
# Módulo 2: historia clínica y evoluciones médicas (sólo GET y POST).
api_router.include_router(
    historias_clinicas.router,
    dependencies=[Depends(require_module("FICHAS_SALUD"))],
)
# Módulo 4: ficha de salud pública (token firmado, sin login de paciente). Las
# rutas ``/panel`` exigen FICHAS_SALUD en su propio decorador; ``/{token}`` queda
# abierta para el paciente.
api_router.include_router(anamnesis.router)
# Módulo 4: odontograma gráfico que completa el profesional (GET y PUT).
api_router.include_router(
    odontogramas.router, dependencies=[Depends(require_module("FICHAS_SALUD"))]
)
# Módulo 4: ficha médica del paciente (cabecera, estado bucal y trabajos).
api_router.include_router(
    fichas_medicas.router, dependencies=[Depends(require_module("FICHAS_SALUD"))]
)
api_router.include_router(
    turnos.router, dependencies=[Depends(require_module("AGENDA_TURNOS"))]
)
# Módulo de presupuestos: cotizaciones de tratamientos con salida en PDF.
api_router.include_router(
    presupuestos.router, dependencies=[Depends(require_module("CAJA_PRESUPUESTOS"))]
)
# Módulo 3: prestaciones, aranceles, facturación, caja y liquidaciones.
api_router.include_router(
    prestaciones.router, dependencies=[Depends(require_module("CAJA_PRESUPUESTOS"))]
)
api_router.include_router(
    aranceles.router, dependencies=[Depends(require_module("CAJA_PRESUPUESTOS"))]
)
# El ``DELETE`` de un cierre de caja exige además BORRAR_MOVIMIENTOS (se declara
# en el decorador del endpoint).
api_router.include_router(
    facturacion.router, dependencies=[Depends(require_module("CAJA_PRESUPUESTOS"))]
)
# Módulo 5: cuenta corriente, planes de cuotas y pagos del paciente. La caja
# también necesita crear entregas y leer saldos.
api_router.include_router(
    finanzas.router,
    dependencies=[Depends(require_module("CUENTA_CORRIENTE", "CAJA_PRESUPUESTOS"))],
)
# Módulo 6: proveedores, gastos recibidos y cuentas por pagar (egresos).
api_router.include_router(
    egresos.router, dependencies=[Depends(require_module("PROVEEDORES_EGRESOS"))]
)
# El cron de Vercel se autentica con CRON_SECRET (no con sesión de usuario).
api_router.include_router(tareas.router)
# El webhook de WAHA es público; los recordatorios del panel declaran
# AGENDA_TURNOS en su decorador.
api_router.include_router(whatsapp.router)
# Menú configurable del FAQ Bot (panel «Bot WhatsApp»).
api_router.include_router(
    whatsapp_menu.router, dependencies=[Depends(require_module("CONFIGURACION"))]
)
