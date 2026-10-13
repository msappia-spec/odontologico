"""Inicialización del esquema de base de datos."""

import logging

from app.db.base import Base  # importa todos los modelos -> registra metadatos
from app.db.session import engine

logger = logging.getLogger("app.db")


def init_db() -> None:
    """Crea las tablas que aún no existan (idempotente)."""
    Base.metadata.create_all(bind=engine)
    logger.info("Esquema de base de datos verificado/creado correctamente.")


def drop_db() -> None:
    """Elimina todas las tablas (útil en entornos de prueba)."""
    Base.metadata.drop_all(bind=engine)
    logger.warning("Esquema de base de datos eliminado (drop_all).")
