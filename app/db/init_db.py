"""Inicialización del esquema de base de datos."""

import logging

from app.db.base import Base  # importa todos los modelos -> registra metadatos
from app.db.migraciones import aplicar_columnas_faltantes
from app.db.session import SessionLocal, engine
from app.services.whatsapp_menu_service import sembrar_menu_por_defecto

logger = logging.getLogger("app.db")


def init_db() -> None:
    """Crea tablas y completa columnas faltantes (idempotente).

    1. ``create_all`` crea las tablas que no existan; no toca las existentes.
    2. ``aplicar_columnas_faltantes`` agrega con ``ALTER TABLE`` las columnas
       que el modelo incorporó después de crear la base (por ejemplo
       ``doctores.tipo_pago`` en una PostgreSQL anterior al Módulo 3).
    3. Siembra el menú del FAQ Bot con las cuatro opciones históricas **sólo**
       si la tabla está vacía, para no pisar la configuración que haya hecho
       la secretaría desde el panel.
    """
    Base.metadata.create_all(bind=engine)
    aplicar_columnas_faltantes(engine)
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
