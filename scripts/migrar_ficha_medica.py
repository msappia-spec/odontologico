"""Migración: tablas de la ficha médica del paciente (Módulo 4).

Contexto
--------
La ficha médica digital reproduce las dos caras de la hoja de papel del
consultorio. Suma dos tablas **nuevas** (no modifica ninguna columna de las
tablas viejas, la migración es puramente aditiva):

* ``fichas_medicas``: cabecera de la hoja (vencimiento, grupo familiar,
  parentesco, antecedentes de enfermedad y estado bucal) con un índice único
  ``uq_ficha_medica_historia`` (1 a 1 con la historia clínica).
* ``ficha_medica_trabajos``: renglones del libro «Trabajo realizado» (fecha,
  código, pieza dentaria, práctica, Debe y Haber) con el índice
  ``ix_ficha_trabajo_ficha``.

``Base.metadata.create_all`` (que corre al arrancar la app) ya crea estas tablas
y sus índices; el script existe para las bases que se quieran migrar
**explícitamente**, dejando copia de seguridad y verificación de datos.

Qué hace (sólo SQLite, el motor del entorno de desarrollo):

1. Crea las tablas faltantes con sus claves foráneas e índices — idempotente: no
   hace nada si ya están todas.
2. Completa los índices nombrados que declare cada modelo si el archivo venía de
   una corrida interrumpida.
3. Comprueba que ``pacientes`` e ``historias_clinicas`` conserven sus filas.

Antes de escribir guarda una copia de seguridad del archivo ``.db``.

Uso::

    python -m scripts.migrar_ficha_medica

Equivalente en PostgreSQL (el proyecto aún no usa Alembic): ejecutar el mismo
script apuntando ``DATABASE_URL`` a la base destino —``__table__.create`` genera
el DDL de cada modelo—, o el SQL equivalente::

    CREATE TABLE fichas_medicas (
        id SERIAL PRIMARY KEY,
        historia_clinica_id INTEGER NOT NULL
            REFERENCES historias_clinicas (id) ON DELETE CASCADE,
        vencimiento DATE,
        grupo_familiar VARCHAR(120),
        parentesco VARCHAR(60),
        antecedentes_enfermedad TEXT,
        presencia_sarro BOOLEAN,
        enfermedad_periodontal BOOLEAN,
        actualizado_en TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX uq_ficha_medica_historia ON fichas_medicas (historia_clinica_id);

    CREATE TABLE ficha_medica_trabajos (
        id SERIAL PRIMARY KEY,
        ficha_medica_id INTEGER NOT NULL
            REFERENCES fichas_medicas (id) ON DELETE CASCADE,
        orden INTEGER NOT NULL DEFAULT 0,
        fecha DATE,
        codigo VARCHAR(30),
        pieza VARCHAR(30),
        trabajo TEXT,
        debe DOUBLE PRECISION,
        haber DOUBLE PRECISION
    );
    CREATE INDEX ix_ficha_trabajo_ficha ON ficha_medica_trabajos (ficha_medica_id);
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.session import engine
from app.models.ficha_medica import FichaMedica, FichaMedicaTrabajo

# El orden importa: las claves foráneas se crean después de sus padres.
MODELOS = (FichaMedica, FichaMedicaTrabajo)

TABLAS_NUEVAS = tuple(modelo.__tablename__ for modelo in MODELOS)

# Tablas que ya existían: se cuentan antes y después para probar que la
# migración no alteró datos.
TABLAS_VERIFICADAS = ("pacientes", "historias_clinicas")


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


def _indices(conn, tabla: str) -> list[str]:
    """Índices *nombrados* de ``tabla``."""
    return list(
        conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = ? AND sql IS NOT NULL ORDER BY name",
            (tabla,),
        ).scalars().all()
    )


def _contar(conn, tabla: str) -> int:
    """Cantidad de filas de ``tabla`` (0 si la tabla no existe)."""
    if tabla not in _tablas(conn):
        return 0
    return int(conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{tabla}"').scalar())


def _columnas(conn, tabla: str) -> list[str]:
    """Columnas de ``tabla`` según ``PRAGMA table_info``."""
    return [
        fila[1] for fila in conn.exec_driver_sql(f'PRAGMA table_info("{tabla}")').all()
    ]


def migrar() -> int:
    """Crea las tablas de la ficha médica y verifica el resultado.

    Devuelve el código de salida del proceso (``0`` = correcto).
    """
    ruta = _ruta_sqlite()

    with engine.connect() as conn:
        existentes = _tablas(conn)
        faltantes = [tabla for tabla in TABLAS_NUEVAS if tabla not in existentes]
        indices_pendientes = [
            (modelo.__tablename__, indice.name)
            for modelo in MODELOS
            if modelo.__tablename__ in existentes
            for indice in modelo.__table__.indexes
            if indice.name not in _indices(conn, modelo.__tablename__)
        ]
        iniciales = {tabla: _contar(conn, tabla) for tabla in TABLAS_VERIFICADAS}

    if not faltantes and not indices_pendientes:
        print("Las tablas de la ficha médica ya están creadas: nada que migrar.")
        return 0

    if ruta.exists():
        respaldo = ruta.with_name(f"{ruta.name}.bak-{utcnow():%Y%m%d_%H%M%S}")
        shutil.copy2(ruta, respaldo)
        print(f"Copia de seguridad creada: {respaldo}")

    with engine.begin() as conn:
        for modelo in MODELOS:
            tabla = modelo.__tablename__
            if tabla not in _tablas(conn):
                modelo.__table__.create(bind=conn, checkfirst=True)
                print(f"Tabla creada: {tabla}")
            else:
                print(f"La tabla '{tabla}' ya existía: se completan sus índices.")
            for indice in modelo.__table__.indexes:
                if indice.name not in _indices(conn, tabla):
                    indice.create(bind=conn, checkfirst=True)
                    print(f"Índice creado: {indice.name}")

        finales = {tabla: _contar(conn, tabla) for tabla in TABLAS_VERIFICADAS}

    for tabla, inicial in iniciales.items():
        if finales[tabla] != inicial:
            raise RuntimeError(
                f"Las filas de '{tabla}' cambiaron ({inicial} → {finales[tabla]}); "
                "revisar la copia de seguridad."
            )

    with engine.connect() as conn:
        for tabla in TABLAS_NUEVAS:
            print(f"'{tabla}': columnas {', '.join(_columnas(conn, tabla))}")
            print(f"   índices: {', '.join(_indices(conn, tabla)) or '(sin índices)'}")

    print("Migración completada: ficha médica lista.")
    for tabla, total in finales.items():
        print(f"'{tabla}' intacta: {total} fila(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(migrar())
