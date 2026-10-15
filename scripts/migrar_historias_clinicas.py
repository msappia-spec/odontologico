"""Migración: tablas del Módulo 2 (historia clínica, evoluciones y adjuntos).

Contexto
--------
El Módulo 2 suma la historia clínica del paciente. Tres tablas nuevas, todas
"colgadas" de lo que ya existía (no modifica ninguna columna de las tablas
viejas, la migración es **puramente aditiva**):

* ``historias_clinicas``: la ficha del paciente (1 a 1), con su ``numero_ficha``
  visible (``HC-000123``). Índices únicos ``uq_historia_paciente`` y
  ``uq_historia_numero_ficha``.
* ``evoluciones_medicas``: cada atención (motivo, diagnóstico, tratamiento,
  observaciones) con el odontólogo que la firmó y su fecha. Índice compuesto
  ``ix_evolucion_historia_fecha`` para la línea de tiempo. **Registro inmutable**:
  la API sólo expone ``POST``/``GET`` y el ORM bloquea sus ``UPDATE``/``DELETE``.
* ``archivos_adjuntos``: radiografías y estudios de cada evolución (se guarda la
  URL del archivo, no el binario). Índice ``ix_adjunto_evolucion``.

``Base.metadata.create_all`` (que corre al arrancar la app) ya crea estas tablas
y sus índices; el script existe para las bases que se quieran migrar
**explícitamente**, dejando copia de seguridad y verificación de datos.

Qué hace (sólo SQLite, el motor del entorno de desarrollo):

1. Crea las tablas faltantes con sus claves foráneas, ``CHECK`` e índices —
   idempotente: no hace nada si ya están todas.
2. Completa los índices nombrados que declare cada modelo si el archivo venía de
   una corrida interrumpida.
3. Comprueba que ``pacientes``, ``doctores`` y ``turnos`` conserven sus filas.

Antes de escribir guarda una copia de seguridad del archivo ``.db``.

Uso::

    python -m scripts.migrar_historias_clinicas

Equivalente en PostgreSQL (el proyecto aún no usa Alembic): ejecutar el mismo
script apuntando ``DATABASE_URL`` a la base destino —``__table__.create`` genera
el DDL de cada modelo—, o el SQL equivalente::

    CREATE TABLE historias_clinicas (
        id SERIAL PRIMARY KEY,
        paciente_id INTEGER NOT NULL REFERENCES pacientes (id) ON DELETE CASCADE,
        numero_ficha VARCHAR(30) NOT NULL
    );
    CREATE UNIQUE INDEX uq_historia_paciente ON historias_clinicas (paciente_id);
    CREATE UNIQUE INDEX uq_historia_numero_ficha ON historias_clinicas (numero_ficha);

    CREATE TABLE evoluciones_medicas (
        id SERIAL PRIMARY KEY,
        historia_clinica_id INTEGER NOT NULL
            REFERENCES historias_clinicas (id) ON DELETE CASCADE,
        doctor_id INTEGER NOT NULL REFERENCES doctores (id) ON DELETE RESTRICT,
        fecha_atencion TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        motivo_consulta TEXT NOT NULL,
        diagnostico TEXT NOT NULL,
        tratamiento_efectuado TEXT NOT NULL,
        observaciones TEXT
    );
    CREATE INDEX ix_evolucion_historia_fecha
        ON evoluciones_medicas (historia_clinica_id, fecha_atencion);

    CREATE TABLE archivos_adjuntos (
        id SERIAL PRIMARY KEY,
        evolucion_id INTEGER NOT NULL
            REFERENCES evoluciones_medicas (id) ON DELETE CASCADE,
        url_archivo VARCHAR(500) NOT NULL,
        tipo_archivo VARCHAR(30) NOT NULL DEFAULT 'RADIOGRAFIA',
        fecha_subida TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_adjunto_evolucion ON archivos_adjuntos (evolucion_id);
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.session import engine
from app.models.archivo_adjunto import ArchivoAdjunto
from app.models.evolucion_medica import EvolucionMedica
from app.models.historia_clinica import HistoriaClinica

# El orden importa: las claves foráneas se crean después de sus padres.
MODELOS = (HistoriaClinica, EvolucionMedica, ArchivoAdjunto)

TABLAS_NUEVAS = tuple(modelo.__tablename__ for modelo in MODELOS)

# Mapa tabla -> modelo, para consultar los índices que declara cada modelo.
TABLA_POR_NOMBRE = {modelo.__tablename__: modelo.__table__ for modelo in MODELOS}

# Tablas que ya existían: se cuentan antes y después para probar que la
# migración no alteró datos.
TABLAS_VERIFICADAS = ("pacientes", "doctores", "turnos")


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
    """Índices *nombrados* de ``tabla`` (los ``sqlite_autoindex_*`` no se listan)."""
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


def _indices_faltantes(conn, tabla: str) -> list[str]:
    """Índices *declarados por el modelo* que todavía no existen en la base."""
    existentes = set(_indices(conn, tabla))
    declarados = TABLA_POR_NOMBRE[tabla].indexes
    return sorted(
        indice.name for indice in declarados if indice.name not in existentes
    )


def migrar() -> int:
    """Crea las tablas del Módulo 2 y verifica el resultado.

    Devuelve el código de salida del proceso (``0`` = correcto).
    """
    ruta = _ruta_sqlite()

    with engine.connect() as conn:
        existentes = _tablas(conn)
        faltantes = [tabla for tabla in TABLAS_NUEVAS if tabla not in existentes]
        indices_pendientes = [
            (tabla, nombre)
            for tabla in TABLAS_NUEVAS
            if tabla in existentes
            for nombre in _indices_faltantes(conn, tabla)
        ]
        iniciales = {tabla: _contar(conn, tabla) for tabla in TABLAS_VERIFICADAS}

    if not faltantes and not indices_pendientes:
        print(
            "Las tablas del Módulo 2 ya están creadas con sus índices: "
            "nada que migrar."
        )
        return 0

    if ruta.exists():
        respaldo = ruta.with_name(f"{ruta.name}.bak-{utcnow():%Y%m%d_%H%M%S}")
        shutil.copy2(ruta, respaldo)
        print(f"Copia de seguridad creada: {respaldo}")

    with engine.begin() as conn:
        # El modelo es la única fuente de verdad del esquema. Se recalcula dentro
        # de la transacción porque SQLite hace *commit* implícito de cada DDL: una
        # corrida anterior pudo dejar la migración a medias.
        for modelo in MODELOS:
            tabla = modelo.__tablename__
            if tabla not in _tablas(conn):
                modelo.__table__.create(bind=conn, checkfirst=True)
                print(f"Tabla creada: {tabla}")
            else:
                print(f"La tabla '{tabla}' ya existía: se completan sus índices.")
            # ``create`` arma también los índices; si el archivo venía de una
            # corrida interrumpida, acá se recuperan uno por uno.
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

    print("Migración completada: historia clínica lista.")
    for tabla, total in finales.items():
        print(f"'{tabla}' intacta: {total} fila(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(migrar())
