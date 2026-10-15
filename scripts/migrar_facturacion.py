"""Migración: tablas y columnas del Módulo 3 (facturación, caja y aranceles).

Contexto
--------
El Módulo 3 suma el catálogo de prácticas, los aranceles por cobertura y el
circuito de facturación/liquidación. La migración es **aditiva**: crea las tablas
nuevas y agrega dos columnas a ``doctores`` (no borra ni reescribe nada).

Tablas nuevas:

* ``prestaciones``: el catálogo de prácticas facturables (``codigo_nomenclador``
  único con el índice ``uq_prestacion_codigo``, descripción y ``valor_base``).
* ``aranceles_obra_social``: cuánto cubre cada cobertura por práctica y cuánto
  queda de coseguro (índice único ``uq_arancel_os_prestacion`` sobre el par
  obra social + prestación).
* ``movimientos_caja``: el cierre económico de cada visita (``monto_efectivo``
  del mostrador, ``monto_pendiente_os`` a presentar, estado del cobro). Índice
  único ``uq_movimiento_caja_turno``: un turno se factura **una sola vez**.
* ``detalles_movimiento_caja``: las prácticas facturadas con sus importes
  congelados (la factura no cambia si luego se edita el arancel).

Columnas nuevas en una tabla existente:

* ``doctores.tipo_pago`` (``VARCHAR(15) NOT NULL DEFAULT 'DUENO'``): modalidad de
  liquidación — dueño, comisión o sueldo. Las filas que ya existían quedan como
  ``DUENO`` (dueño del consultorio), que es el comportamiento histórico.
* ``doctores.valor_pago`` (``FLOAT``, admite ``NULL``): porcentaje o sueldo fijo.

``Base.metadata.create_all`` (que corre al arrancar la app) ya crea las tablas
nuevas, pero **no** agrega columnas a tablas existentes: por eso este script
existe como paso explícito para las bases con datos (con copia de seguridad).

Qué hace (sólo SQLite, el motor del entorno de desarrollo):

1. Crea las tablas faltantes con sus claves foráneas e índices — idempotente.
2. Agrega las columnas faltantes de ``doctores`` (``ALTER TABLE ... ADD COLUMN``).
3. Comprueba que ``pacientes``, ``doctores``, ``turnos`` y ``obras_sociales``
   conserven sus filas.

Antes de escribir guarda una copia de seguridad del archivo ``.db``.

Uso::

    python -m scripts.migrar_facturacion

Equivalente en PostgreSQL (el proyecto aún no usa Alembic): ejecutar el mismo
script apuntando ``DATABASE_URL`` a la base destino —``__table__.create`` genera
el DDL de cada modelo y los ``ALTER TABLE`` de acá también sirven—, o el SQL
equivalente::

    ALTER TABLE doctores ADD COLUMN tipo_pago VARCHAR(15) NOT NULL DEFAULT 'DUENO';
    ALTER TABLE doctores ADD COLUMN valor_pago DOUBLE PRECISION;

    CREATE TABLE prestaciones (
        id SERIAL PRIMARY KEY,
        codigo_nomenclador VARCHAR(30) NOT NULL,
        descripcion VARCHAR(200) NOT NULL,
        valor_base DOUBLE PRECISION NOT NULL DEFAULT 0,
        activa BOOLEAN NOT NULL DEFAULT true
    );
    CREATE UNIQUE INDEX uq_prestacion_codigo ON prestaciones (codigo_nomenclador);

    CREATE TABLE aranceles_obra_social (
        id SERIAL PRIMARY KEY,
        obra_social_id INTEGER NOT NULL REFERENCES obras_sociales (id) ON DELETE CASCADE,
        prestacion_id INTEGER NOT NULL REFERENCES prestaciones (id) ON DELETE CASCADE,
        monto_cubre_os DOUBLE PRECISION NOT NULL DEFAULT 0,
        monto_coseguro_paciente DOUBLE PRECISION NOT NULL DEFAULT 0
    );
    CREATE UNIQUE INDEX uq_arancel_os_prestacion
        ON aranceles_obra_social (obra_social_id, prestacion_id);

    CREATE TABLE movimientos_caja (
        id SERIAL PRIMARY KEY,
        turno_id INTEGER NOT NULL REFERENCES turnos (id) ON DELETE RESTRICT,
        doctor_id INTEGER NOT NULL REFERENCES doctores (id) ON DELETE RESTRICT,
        fecha TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        monto_efectivo DOUBLE PRECISION NOT NULL DEFAULT 0,
        monto_pendiente_os DOUBLE PRECISION NOT NULL DEFAULT 0,
        monto_cobrado_os DOUBLE PRECISION,
        estado_pago VARCHAR(25) NOT NULL DEFAULT 'PENDIENTE',
        observaciones TEXT
    );
    CREATE UNIQUE INDEX uq_movimiento_caja_turno ON movimientos_caja (turno_id);
    CREATE INDEX ix_movimiento_caja_fecha ON movimientos_caja (fecha);

    CREATE TABLE detalles_movimiento_caja (
        id SERIAL PRIMARY KEY,
        movimiento_caja_id INTEGER NOT NULL
            REFERENCES movimientos_caja (id) ON DELETE CASCADE,
        prestacion_id INTEGER NOT NULL REFERENCES prestaciones (id) ON DELETE RESTRICT,
        codigo_nomenclador VARCHAR(30) NOT NULL,
        descripcion VARCHAR(200) NOT NULL,
        cantidad INTEGER NOT NULL DEFAULT 1,
        monto_cubre_os DOUBLE PRECISION NOT NULL DEFAULT 0,
        monto_coseguro_paciente DOUBLE PRECISION NOT NULL DEFAULT 0
    );
    CREATE INDEX ix_detalle_movimiento ON detalles_movimiento_caja (movimiento_caja_id);
"""


from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.session import engine
from app.models.arancel_obra_social import ArancelObraSocial
from app.models.movimiento_caja import DetalleMovimientoCaja, MovimientoCaja
from app.models.prestacion import Prestacion

# El orden importa: las claves foráneas se crean después de sus padres.
MODELOS = (Prestacion, ArancelObraSocial, MovimientoCaja, DetalleMovimientoCaja)

TABLAS_NUEVAS = tuple(modelo.__tablename__ for modelo in MODELOS)

# Mapa tabla -> modelo, para consultar los índices que declara cada modelo.
TABLA_POR_NOMBRE = {modelo.__tablename__: modelo.__table__ for modelo in MODELOS}

# Columnas nuevas sobre tablas que ya existían: (nombre, DDL de la columna).
# El ``DEFAULT`` es lo que deja a los doctores ya cargados como dueños.
COLUMNAS_NUEVAS: dict[str, tuple[tuple[str, str], ...]] = {
    "doctores": (
        ("tipo_pago", "VARCHAR(15) NOT NULL DEFAULT 'DUENO'"),
        ("valor_pago", "FLOAT"),
    )
}

# Tablas que ya existían: se cuentan antes y después para probar que la
# migración no alteró datos.
TABLAS_VERIFICADAS = ("pacientes", "doctores", "turnos", "obras_sociales")


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


def _columnas(conn, tabla: str) -> list[str]:
    """Columnas de ``tabla`` según ``PRAGMA table_info``."""
    return [
        fila[1] for fila in conn.exec_driver_sql(f'PRAGMA table_info("{tabla}")').all()
    ]


def _contar(conn, tabla: str) -> int:
    """Cantidad de filas de ``tabla`` (0 si la tabla no existe)."""
    if tabla not in _tablas(conn):
        return 0
    return int(conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{tabla}"').scalar())


def _indices_faltantes(conn, tabla: str) -> list[str]:
    """Índices *declarados por el modelo* que todavía no existen en la base."""
    existentes = set(_indices(conn, tabla))
    declarados = TABLA_POR_NOMBRE[tabla].indexes
    return sorted(
        indice.name for indice in declarados if indice.name not in existentes
    )


def _columnas_faltantes(conn, tabla: str) -> list[tuple[str, str]]:
    """Columnas nuevas que todavía no tiene ``tabla``: ``[(nombre, DDL), ...]``."""
    if tabla not in _tablas(conn):
        return []
    actuales = set(_columnas(conn, tabla))
    return [
        (nombre, ddl)
        for nombre, ddl in COLUMNAS_NUEVAS.get(tabla, ())
        if nombre not in actuales
    ]


def migrar() -> int:
    """Crea las tablas del Módulo 3, agrega las columnas de pago y verifica.

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
        columnas_pendientes = [
            (tabla, nombre, ddl)
            for tabla in COLUMNAS_NUEVAS
            for nombre, ddl in _columnas_faltantes(conn, tabla)
        ]
        iniciales = {tabla: _contar(conn, tabla) for tabla in TABLAS_VERIFICADAS}

    if not faltantes and not indices_pendientes and not columnas_pendientes:
        print(
            "Las tablas y columnas del Módulo 3 ya están creadas con sus "
            "índices: nada que migrar."
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

        for tabla, columna, ddl in columnas_pendientes:
            conn.exec_driver_sql(f'ALTER TABLE "{tabla}" ADD COLUMN {columna} {ddl}')
            print(f"Columna agregada: {tabla}.{columna}")

        finales = {tabla: _contar(conn, tabla) for tabla in TABLAS_VERIFICADAS}

    for tabla, inicial in iniciales.items():
        if finales[tabla] != inicial:
            raise RuntimeError(
                f"Las filas de '{tabla}' cambiaron ({inicial} → {finales[tabla]}); "
                "revisar la copia de seguridad."
            )

    with engine.connect() as conn:
        for tabla in (*TABLAS_NUEVAS, *COLUMNAS_NUEVAS):
            print(f"'{tabla}': columnas {', '.join(_columnas(conn, tabla))}")
            print(f"   índices: {', '.join(_indices(conn, tabla)) or '(sin índices)'}")

    print("Migración completada: facturación, caja y aranceles listos.")
    for tabla, total in finales.items():
        print(f"'{tabla}' intacta: {total} fila(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(migrar())
