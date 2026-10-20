"""Inicialización del esquema de base de datos."""

import logging

from app.db.base import Base  # importa todos los modelos -> registra metadatos
from app.db.session import SessionLocal, engine
from app.services.whatsapp_menu_service import sembrar_menu_por_defecto

logger = logging.getLogger("app.db")


def init_db() -> None:
    """Crea las tablas que aún no existan (idempotente).

    Después siembra el menú del FAQ Bot con las cuatro opciones históricas
    **sólo** si la tabla está vacía, para no pisar la configuración que haya
    hecho la secretaría desde el panel.
    """
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        creadas = sembrar_menu_por_defecto(db)
    if creadas:
        logger.info(
            "Menú del FAQ Bot inicializado con %s opciones por defecto.", creadas
        )
    logger.info("Esquema de base de datos verificado/creado correctamente.")


def drop_db() -> None:
    """Elimina todas las tablas (útil en entornos de prueba)."""
    Base.metadata.drop_all(bind=engine)
    logger.warning("Esquema de base de datos eliminado (drop_all).")
