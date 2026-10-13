"""Migración: ``Paciente.obra_social`` (texto) → catálogo ``obras_sociales``.

Contexto
--------
El modelo ``Paciente`` (``app/models/paciente.py``) reemplazó el campo de texto
``obra_social`` por la **clave foránea** ``obra_social_id`` que apunta a la nueva
tabla ``obras_sociales`` (``app/models/obra_social.py``). ``Base.metadata.create_all``
crea las tablas faltantes (``obras_sociales`` y su índice único) pero **no agrega
columnas** a las que ya existen y el proyecto todavía no usa Alembic, así que las
bases creadas con la versión anterior necesitan esta migración.

Qué hace (sólo SQLite, el motor del entorno de desarrollo):

1. Crea la tabla ``obras_sociales`` y su índice único ``uq_obra_social_nombre``
   (idempotente: ``create_all`` sólo crea lo que falta).
2. ``ALTER TABLE pacientes ADD COLUMN obra_social_id INTEGER`` (no destructivo).
3. ``CREATE INDEX ix_pacientes_obra_social_id`` para replicar el índice que
   declara el modelo.
4. **Backfill**: por cada ``obra_social`` de texto distinta crea la cobertura en
   el catálogo (si no existe, comparando sin distinguir mayúsculas) y apunta a
   sus pacientes con ``obra_social_id``. Ningún dato se pierde.
5. Elimina la columna heredada ``obra_social`` **sólo si** el SQLite de Python
   soporta ``DROP COLUMN`` (3.35+). Si no, la deja (es anulable y el ORM ya no la
   mapea, así que no molesta) e informa cómo quitarla.

Es **idempotente** y verifica al final que la cantidad de pacientes no cambió.
Antes de escribir guarda una copia de seguridad del archivo ``.db``.

Uso::

    python -m scripts.migrar_obra_social

Equivalente en PostgreSQL (el proyecto aún no usa Alembic)::

    CREATE TABLE obras_sociales (
        id SERIAL PRIMARY KEY,
        nombre VARCHAR(120) NOT NULL,
        codigo VARCHAR(30),
        activa BOOLEAN NOT NULL DEFAULT TRUE
    );
    CREATE UNIQUE INDEX uq_obra_social_nombre ON obras_sociales (nombre);
    ALTER TABLE pacientes ADD COLUMN obra_social_id INTEGER REFERENCES obras_sociales (id);
    CREATE INDEX ix_pacientes_obra_social_id ON pacientes (obra_social_id);
    -- Backfill: insertar los nombres distintos y actualizar pacientes.obra_social_id
    ALTER TABLE pacientes DROP COLUMN obra_social;
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.base import Base
from app.db.session import engine

TABLA_PACIENTES = "pacientes"
TABLA_OBRAS = "obras_sociales"
COLUMNA_FK = "obra_social_id"
COLUMNA_LEGADA = "obra_social"
INDICE_FK = "ix_pacientes_obra_social_id"
VERSION_DROP_COLUMN = (3, 35)


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


def _tablas(conn) -> set[str]:
    """Nombres de las tablas existentes."""
    return set(
        conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).scalars().all()
    )


def _columnas(conn, tabla: str) -> list[str]:
    """Nombres de las columnas actuales de ``tabla``."""
    return [
        str(fila[1]) for fila in conn.exec_driver_sql(f"PRAGMA table_info({tabla})").all()
    ]


def _indices(conn, tabla: str) -> list[str]:
    """Índices *nombrados* de ``tabla`` (omite los ``sqlite_autoindex_*``)."""
    return [
        str(nombre)
        for nombre in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = ? AND sql IS NOT NULL ORDER BY name",
            (tabla,),
        ).scalars().all()
    ]


def _contar(conn, tabla: str) -> int:
    """Cantidad de filas de ``tabla``."""
    return int(conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{tabla}"').scalar())


def _soporta_drop_column(conn) -> bool:
    """¿El SQLite en uso admite ``ALTER TABLE ... DROP COLUMN``?"""
    partes = str(conn.exec_driver_sql("SELECT sqlite_version()").scalar()).split(".")
    version = tuple(int(p) for p in partes[:3] if p.isdigit())
    return version >= VERSION_DROP_COLUMN


def _backfill(conn) -> tuple[int, int]:
    """Vincula los pacientes con el catálogo a partir del texto heredado.

    Returns:
        Par ``(coberturas_creadas, pacientes_vinculados)``.
    """
    filas = conn.exec_driver_sql(
        f'SELECT id, obra_social FROM "{TABLA_PACIENTES}" '
        f"WHERE {COLUMNA_LEGADA} IS NOT NULL AND TRIM({COLUMNA_LEGADA}) <> ''"
    ).all()

    creadas = 0
    vinculados = 0
    cache: dict[str, int] = {}

    for paciente_id, nombre in filas:
        clave = str(nombre).strip().lower()
        obra_social_id = cache.get(clave)
        if obra_social_id is None:
            existente = conn.exec_driver_sql(
                f'SELECT id FROM "{TABLA_OBRAS}" WHERE lower(nombre) = ?', (clave,)
            ).scalar()
            if existente is None:
                existente = conn.exec_driver_sql(
                    f'INSERT INTO "{TABLA_OBRAS}" (nombre, codigo, activa) VALUES (?, NULL, 1)',
                    (str(nombre).strip(),),
                ).lastrowid
                creadas += 1
                print(f"Obra social creada desde el texto heredado: {str(nombre).strip()!r}")
            obra_social_id = int(existente)
            cache[clave] = obra_social_id

        conn.exec_driver_sql(
            f'UPDATE "{TABLA_PACIENTES}" SET {COLUMNA_FK} = ? WHERE id = ?',
            (obra_social_id, paciente_id),
        )
        vinculados += 1

    return creadas, vinculados


def migrar() -> int:
    """Aplica la migración y devuelve 0 (código de salida del script)."""
    ruta = _ruta_sqlite()

    # 1) La tabla del catálogo y su índice único los crea el propio modelo.
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        if TABLA_PACIENTES not in _tablas(conn):
            print(
                f"La tabla '{TABLA_PACIENTES}' no existe: se creará con el esquema "
                "nuevo (con la clave foránea) al arrancar la app."
            )
            return 0

        columnas = _columnas(conn, TABLA_PACIENTES)
        indices = _indices(conn, TABLA_PACIENTES)
        filas = _contar(conn, TABLA_PACIENTES)
        soporta_drop = _soporta_drop_column(conn)

    ya_migrada = (
        COLUMNA_FK in columnas
        and INDICE_FK in indices
        and COLUMNA_LEGADA not in columnas
    )
    if ya_migrada:
        print(
            f"La tabla '{TABLA_PACIENTES}' ya usa {COLUMNA_FK} y el catálogo "
            f"'{TABLA_OBRAS}' está creado: nada que migrar."
        )
        return 0

    if ruta.exists():
        respaldo = ruta.with_name(f"{ruta.name}.bak-{utcnow():%Y%m%d_%H%M%S}")
        shutil.copy2(ruta, respaldo)
        print(f"Copia de seguridad creada: {respaldo}")

    with engine.begin() as conn:
        # Se recalcula dentro de la transacción: SQLite hace *commit* implícito de
        # cada DDL, así que una corrida anterior pudo dejar la migración a medias.
        if COLUMNA_FK not in _columnas(conn, TABLA_PACIENTES):
            conn.exec_driver_sql(
                f'ALTER TABLE "{TABLA_PACIENTES}" ADD COLUMN {COLUMNA_FK} INTEGER'
            )
            print(f"Columna agregada: {TABLA_PACIENTES}.{COLUMNA_FK} INTEGER")

        if INDICE_FK not in _indices(conn, TABLA_PACIENTES):
            conn.exec_driver_sql(
                f'CREATE INDEX IF NOT EXISTS {INDICE_FK} '
                f'ON "{TABLA_PACIENTES}" ({COLUMNA_FK})'
            )
            print(f"Índice creado: {INDICE_FK} ({TABLA_PACIENTES}.{COLUMNA_FK})")

        if COLUMNA_LEGADA in _columnas(conn, TABLA_PACIENTES):
            creadas, vinculados = _backfill(conn)
            print(
                f"Backfill: {creadas} obra(s) social(es) nueva(s), "
                f"{vinculados} paciente(s) vinculado(s)."
            )

            if soporta_drop:
                conn.exec_driver_sql(
                    f'ALTER TABLE "{TABLA_PACIENTES}" DROP COLUMN {COLUMNA_LEGADA}'
                )
                print(f"Columna heredada eliminada: {TABLA_PACIENTES}.{COLUMNA_LEGADA}")
            else:
                print(
                    f"AVISO: este SQLite no admite DROP COLUMN; la columna "
                    f"'{COLUMNA_LEGADA}' queda (es anulable y el ORM ya no la mapea). "
                    f"Para quitarla, usar SQLite 3.35+ y repetir el script."
                )

        columnas_final = _columnas(conn, TABLA_PACIENTES)
        indices_final = _indices(conn, TABLA_PACIENTES)
        filas_final = _contar(conn, TABLA_PACIENTES)
        obras_final = _contar(conn, TABLA_OBRAS)

    if filas_final != filas:
        raise RuntimeError(
            f"Filas en '{TABLA_PACIENTES}' ({filas_final}) distintas de las iniciales "
            f"({filas}); revisar la copia de seguridad."
        )

    print(f"Migración completada: {filas_final} pacientes preservados.")
    print(f"Catálogo '{TABLA_OBRAS}': {obras_final} cobertura(s) registrada(s).")
    print(f"Columnas: {', '.join(columnas_final)}")
    print(f"Índices: {', '.join(indices_final) or '(sin índices nombrados)'}")
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(migrar())
