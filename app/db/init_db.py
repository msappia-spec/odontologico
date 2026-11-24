"""Inicialización del esquema de base de datos."""

import logging

from app.db.base import Base  # importa todos los modelos -> registra metadatos
from app.db.migraciones import aplicar_columnas_faltantes, ajustar_categoria_paciente
from app.db.session import SessionLocal, engine
from app.services.categoria_paciente_service import sembrar_categorias_por_defecto
from app.services.whatsapp_menu_service import sembrar_menu_por_defecto

logger = logging.getLogger("app.db")


def init_db() -> None:
    """Crea tablas y completa columnas faltantes (idempotente).

    1. ``create_all`` crea las tablas que no existan; no toca las existentes.
    2. ``aplicar_columnas_faltantes`` agrega con ``ALTER TABLE`` las columnas
       que el modelo incorporó después de crear la base (por ejemplo
       ``doctores.tipo_pago`` en una PostgreSQL anterior al Módulo 3).
    3. ``ajustar_categoria_paciente`` quita en PostgreSQL el ``CHECK`` del enum
       anterior de categorías y ensancha la columna, para que la secretaría
       pueda crear categorías nuevas desde el panel.
    4. Siembra el menú del FAQ Bot y el catálogo de categorías de pacientes con
       los valores históricos **sólo** si las tablas están vacías, para no pisar
       la configuración que haya hecho la secretaría.
    """
    Base.metadata.create_all(bind=engine)
    aplicar_columnas_faltantes(engine)
    ajustar_categoria_paciente(engine)
    with SessionLocal() as db:
        creadas = sembrar_menu_por_defecto(db)
        categorias = sembrar_categorias_por_defecto(db)
    if creadas:
        logger.info(
            "Menú del FAQ Bot inicializado con %s opciones por defecto.", creadas
        )
    if categorias:
        logger.info(
            "Catálogo de categorías de pacientes inicializado con %s valores.",
            categorias,
        )
    logger.info("Esquema de base de datos verificado/creado correctamente.")


def drop_db() -> None:
    """Elimina todas las tablas (útil en entornos de prueba)."""
    Base.metadata.drop_all(bind=engine)
    logger.warning("Esquema de base de datos eliminado (drop_all).")
