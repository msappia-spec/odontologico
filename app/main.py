"""Punto de entrada de la aplicación FastAPI (Módulo 1).

Responsabilidades:
  * Construir la instancia ``FastAPI``.
  * Inicializar el esquema de base de datos al arrancar (lifespan).
  * Registrar el router de la API v1 y el manejador global de errores.
  * Configurar CORS para clientes externos (métodos PUT/PATCH/DELETE incluidos).
  * Servir la interfaz web (dashboard de la secretaría) y sus recursos estáticos.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging_config import configurar_logging
from app.db.init_db import init_db
from app.web.router import STATIC_DIR
from app.web.router import router as web_router

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranque y apagado de la aplicación."""
    configurar_logging(level=logging.DEBUG if settings.debug else logging.INFO)
    init_db()
    logger.info("%s v%s iniciada.", settings.app_name, settings.app_version)
    yield
    logger.info("Aplicación finalizada.")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Módulo 1: Gestión de Turnos y Automatización de WhatsApp.\n\n"
        "Incluye la gestión de doctores, pacientes y turnos, la tarea "
        "programada de recordatorios y el webhook de respuestas de WhatsApp."
    ),
    lifespan=lifespan,
)


# --- CORS --------------------------------------------------------------------
# La interfaz web (dashboard) se sirve desde el mismo origen, por lo que no
# necesita CORS; se habilita para que clientes externos (SPA, apps móviles o
# pruebas locales en otro puerto) puedan consumir la API. Los métodos se
# enumeran EXPLÍCITAMENTE porque la respuesta a un *preflight* los declara:
# GET/POST son la lectura y el alta, y PUT/PATCH/DELETE son los que usan la
# reprogramación, la cancelación y la reactivación de turnos. Si un método no
# figura aquí, el navegador bloquea la petición aunque el endpoint exista.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Traduce las excepciones de dominio a respuestas HTTP uniformes."""
    logger.warning("AppError en %s: %s", request.url.path, exc.detail)
    contenido = {"detail": exc.detail}
    if exc.extra:
        contenido.update(exc.extra)
    return JSONResponse(status_code=exc.status_code, content=contenido)


@app.get("/health", tags=["Salud"], summary="Chequeo de estado")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }


# --- Interfaz web (dashboard de la secretaría) -------------------------------
# Archivos estáticos (CSS/JS) y la página principal del dashboard.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(web_router)


# Router principal de la API (prefijo /api/v1)
app.include_router(api_router, prefix=settings.api_v1_prefix)
