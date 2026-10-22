"""Migraciones aditivas de esquema para bases que ya tienen datos.

``Base.metadata.create_all`` crea las tablas que faltan, pero **no agrega
columnas** a una tabla existente. El proyecto todavía no usa Alembic, así que
una base creada con una versión anterior (por ejemplo la PostgreSQL de Neon en
producción) puede quedar con ``doctores`` sin ``tipo_pago``/``valor_pago`` y
responder HTTP 500 en cuanto el ORM intenta leer o guardar un profesional.

Este módulo centraliza esos ``ALTER TABLE ... ADD COLUMN`` y se ejecuta desde
``init_db`` en cada arranque:

* es **idempotente**: inspecciona las columnas y sólo agrega las que faltan;
* es **aditivo**: no borra, renombra ni reescribe datos;
* todas las columnas agregadas son anulables o tienen ``DEFAULT``, por lo que
  las filas existentes quedan válidas;
* funciona tanto en SQLite (desarrollo/pruebas) como en PostgreSQL (Vercel).

En PostgreSQL, ``tipo_pago`` se agrega como ``VARCHAR(15) NOT NULL DEFAULT
'DUENO'``: los profesionales que ya existían quedan como dueños, que es el
comportamiento histórico.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import Engine, inspect

logger = logging.getLogger("app.db")


class ColumnaNueva(NamedTuple):
    """Columna que debe existir y su DDL según el motor de base de datos."""

    tabla: str
    columna: str
    ddl_sqlite: str
    ddl_postgresql: str


# Columnas que se fueron incorporando a tablas que ya existían. El orden no es
# relevante: cada entrada se agrega sólo si la tabla existe y la columna falta.
COLUMNAS_NUEVAS: tuple[ColumnaNueva, ...] = (
    # Módulo 3: modalidad de liquidación del profesional.
    ColumnaNueva(
        "doctores",
        "tipo_pago",
        "VARCHAR(15) NOT NULL DEFAULT 'DUENO'",
        "VARCHAR(15) NOT NULL DEFAULT 'DUENO'",
    ),
    ColumnaNueva("doctores", "valor_pago", "FLOAT", "DOUBLE PRECISION"),
    # Datos administrativos del paciente (DNI y cobertura).
    ColumnaNueva("pacientes", "dni", "VARCHAR(20)", "VARCHAR(20)"),
    ColumnaNueva("pacientes", "obra_social_id", "INTEGER", "INTEGER"),
    ColumnaNueva("pacientes", "numero_afiliado", "VARCHAR(60)", "VARCHAR(60)"),
    # Correlación de la respuesta de WhatsApp con el turno.
    ColumnaNueva(
        "turnos", "whatsapp_mensaje_id", "VARCHAR(128)", "VARCHAR(128)"
    ),
)


def _ddl_para(columna: ColumnaNueva, dialecto: str) -> str:
    """Devuelve el DDL correcto para el dialecto del engine."""
    if dialecto == "postgresql":
        return columna.ddl_postgresql
    return columna.ddl_sqlite


def aplicar_columnas_faltantes(engine: Engine) -> list[str]:
    """Agrega las columnas conocidas que falten en una base existente.

    Devuelve la lista de columnas aplicadas (vacía si el esquema ya estaba al
    día). Los errores de DDL se propagan a propósito: si la base no permite
    migrar, el arranque debe fallar con un mensaje claro en vez de seguir
    sirviendo endpoints que responderán 500.
    """
    inspector = inspect(engine)
    tablas = set(inspector.get_table_names())
    pendientes: list[ColumnaNueva] = []

    for columna in COLUMNAS_NUEVAS:
        if columna.tabla not in tablas:
            # La tabla la crea ``create_all`` con todas sus columnas.
            continue
        existentes = {
            definicion["name"]
            for definicion in inspector.get_columns(columna.tabla)
        }
        if columna.columna not in existentes:
            pendientes.append(columna)

    if not pendientes:
        logger.info("Esquema aditivo al día: no hay columnas para migrar.")
        return []

    dialecto = engine.dialect.name
    aplicadas: list[str] = []
    with engine.begin() as conexion:
        for columna in pendientes:
            conexion.exec_driver_sql(
                f'ALTER TABLE "{columna.tabla}" '
                f'ADD COLUMN "{columna.columna}" '
                f"{_ddl_para(columna, dialecto)}"
            )
            aplicadas.append(f"{columna.tabla}.{columna.columna}")
            logger.info(
                "Columna agregada en %s: %s.%s",
                dialecto,
                columna.tabla,
                columna.columna,
            )

    logger.info(
        "Migración aditiva aplicada (%s columnas): %s",
        len(aplicadas),
        ", ".join(aplicadas),
    )
    return aplicadas
