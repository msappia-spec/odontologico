"""Migración: agrega ``dni``, ``obra_social`` y ``numero_afiliado`` a ``pacientes``.

Contexto
--------
El modelo ``Paciente`` (``app/models/paciente.py``) incorporó tres campos
administrativos: el **DNI único** y la cobertura (``obra_social`` y
``numero_afiliado``). ``Base.metadata.create_all`` crea las tablas faltantes pero
**no agrega columnas** a las que ya existen y el proyecto todavía no usa Alembic,
así que las bases creadas con la versión anterior necesitan esta migración.

Qué hace (sólo SQLite, el motor del entorno de desarrollo):

1. ``ALTER TABLE pacientes ADD COLUMN ...`` por cada columna faltante. Es una
   operación **no destructiva**: las filas existentes quedan con ``NULL``.
2. ``CREATE UNIQUE INDEX uq_paciente_dni ON pacientes (dni)`` para replicar la
   unicidad del documento que declara el modelo. Se usa un índice único (y no
   una columna ``UNIQUE``) porque SQLite no admite ``ADD COLUMN`` con restricción
   ``UNIQUE`` y, además, un índice único tolera varios ``NULL`` (los pacientes sin
   documento no colisionan entre sí).

Es **idempotente**: si las columnas y el índice ya existen no modifica nada.
Antes de escribir guarda una copia de seguridad del archivo ``.db``.

Uso::

    python -m scripts.migrar_pacientes_datos

Equivalente en PostgreSQL (el proyecto aún no usa Alembic)::

    ALTER TABLE pacientes ADD COLUMN dni VARCHAR(20);
    ALTER TABLE pacientes ADD COLUMN obra_social VARCHAR(120);
    ALTER TABLE pacientes ADD COLUMN numero_afiliado VARCHAR(60);
    CREATE UNIQUE INDEX uq_paciente_dni ON pacientes (dni);
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.session import engine
from app.models.paciente import Paciente

TABLA = "pacientes"
INDICE = "uq_paciente_dni"
COLUMNAS = ("dni", "obra_social", "numero_afiliado")


def _ruta_sqlite() -> Path:
    """Devuelve el archivo SQLite indicado por ``DATABASE_URL``."""
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise RuntimeError(
            "Esta migración sólo aplica a SQLite. En PostgreSQL ejecutar el "
            "equivalente documentado en el encabezado del script."
        )
    ruta = url.split("sqlite:///")[-1]
    if ruta in ("", ":memory:"):
        raise RuntimeError("DATABASE_URL apunta a una base SQLite en memoria.")
    return Path(ruta)


def _ddl_tabla(conn) -> str | None:
    """Devuelve el ``CREATE TABLE`` de ``pacientes`` (o ``None`` si no existe)."""
    return conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLA,),
    ).scalar()


def _columnas(conn) -> list[str]:
    """Nombres de las columnas actuales de ``pacientes``."""
    return [
        str(fila[1])
        for fila in conn.exec_driver_sql(f"PRAGMA table_info({TABLA})").all()
    ]


def _indices(conn) -> list[str]:
    """Índices *nombrados* de ``pacientes`` (los ``sqlite_autoindex_*`` se omiten)."""
    return [
        str(nombre)
        for nombre in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = ? AND sql IS NOT NULL ORDER BY name",
            (TABLA,),
        ).scalars().all()
    ]


def _contar(conn) -> int:
    """Cantidad de filas de ``pacientes``."""
    return int(conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{TABLA}"').scalar())


def _tipo_columna(nombre: str) -> str:
    """Tipo SQL de la columna, tomado del modelo para no duplicar el DDL."""
    return str(Paciente.__table__.c[nombre].type.compile(engine.dialect))


def migrar() -> int:
    """Aplica la migración y devuelve 0 (código de salida del script)."""
    ruta = _ruta_sqlite()

    with engine.connect() as conn:
        ddl = _ddl_tabla(conn)
        existentes = _columnas(conn)
        indices = _indices(conn)
        filas = _contar(conn)

    if ddl is None:
        print(
            f"La tabla '{TABLA}' no existe: se creará con el esquema nuevo al "
            "arrancar la app."
        )
        return 0

    faltantes = [nombre for nombre in COLUMNAS if nombre not in existentes]
    if not faltantes and INDICE in indices:
        print(
            f"La tabla '{TABLA}' ya tiene {', '.join(COLUMNAS)} y el índice "
            f"{INDICE}: nada que migrar."
        )
        return 0

    if ruta.exists():
        respaldo = ruta.with_name(f"{ruta.name}.bak-{utcnow():%Y%m%d_%H%M%S}")
        shutil.copy2(ruta, respaldo)
        print(f"Copia de seguridad creada: {respaldo}")

    with engine.begin() as conn:
        # Se recalcula dentro de la transacción: SQLite hace *commit* implícito de
        # cada DDL, así que una corrida anterior pudo dejar la migración a medias.
        for nombre in COLUMNAS:
            if nombre in _columnas(conn):
                continue
            tipo = _tipo_columna(nombre)
            conn.exec_driver_sql(
                f'ALTER TABLE {TABLA} ADD COLUMN "{nombre}" {tipo}'
            )
            print(f"Columna agregada: {TABLA}.{nombre} {tipo}")

        if INDICE not in _indices(conn):
            conn.exec_driver_sql(
                f'CREATE UNIQUE INDEX IF NOT EXISTS {INDICE} ON {TABLA} ("dni")'
            )
            print(f"Índice único creado: {INDICE} ({TABLA}.dni)")

        columnas_final = _columnas(conn)
        indices_final = _indices(conn)
        filas_final = _contar(conn)

    if filas_final != filas:
        raise RuntimeError(
            f"Filas en '{TABLA}' ({filas_final}) distintas de las iniciales "
            f"({filas}); revisar la copia de seguridad."
        )

    print(f"Migración completada: {filas_final} pacientes preservados.")
    print(f"Columnas: {', '.join(columnas_final)}")
    print(f"Índices: {', '.join(indices_final) or '(sin índices nombrados)'}")
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(migrar())
