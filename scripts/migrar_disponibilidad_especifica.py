"""Migración: tabla ``disponibilidad_especifica`` (excepciones de agenda por fecha).

Contexto
--------
La agenda del odontólogo tenía **una sola capa**, la semanal
(``DisponibilidadDoctor``): la rutina del profesional repetida cada semana. Eso
no alcanza para los días que se salen de la rutina —una guardia de sábado, unas
vacaciones de dos días, un feriado, un almuerzo más largo—, que antes obligaban
a editar la semana y volver a dejarla como estaba.

La tabla ``disponibilidad_especifica`` (``app/models/disponibilidad_especifica.py``)
guarda **tramos ``HABILITADO``/``BLOQUEADO`` de una fecha concreta**. El servicio
de disponibilidad les da *precedencia total*: si el profesional tiene cualquier
excepción cargada para una fecha, ese día se calcula sólo con ellas (los
``HABILITADO`` menos los ``BLOQUEADO``) y la agenda semanal se ignora; si no hay
excepciones, el día sigue rigiéndose por la semana como siempre.

``Base.metadata.create_all`` (que corre al arrancar la app) ya crea esta tabla y
sus índices; el script existe para las bases que se quieran migrar
**explícitamente**, dejando copia de seguridad y verificación de datos.

Qué hace (sólo SQLite, el motor del entorno de desarrollo):

1. Crea la tabla ``disponibilidad_especifica`` con sus dos ``CHECK`` (duración
   positiva y tipo válido) y su clave foránea a ``doctores`` — idempotente: no
   hace nada si ya existe.
2. Verifica/crea los índices que declara el modelo: ``ix_especifica_doctor_fecha``
   (compuesto, el que usan las consultas del día) y los de ``doctor_id``,
   ``fecha`` e ``id``.
3. Comprueba que ``doctores`` y ``turnos`` conserven sus filas: la migración es
   puramente aditiva, no toca datos existentes.

Antes de escribir guarda una copia de seguridad del archivo ``.db``.

Uso::

    python -m scripts.migrar_disponibilidad_especifica

Equivalente en PostgreSQL (el proyecto aún no usa Alembic)::

    CREATE TABLE disponibilidad_especifica (
        id SERIAL PRIMARY KEY,
        doctor_id INTEGER NOT NULL REFERENCES doctores (id) ON DELETE CASCADE,
        fecha DATE NOT NULL,
        hora_inicio TIME NOT NULL,
        hora_fin TIME NOT NULL,
        tipo VARCHAR(20) NOT NULL DEFAULT 'HABILITADO',
        CONSTRAINT ck_especifica_fin_posterior_inicio CHECK (hora_fin > hora_inicio),
        CONSTRAINT ck_especifica_tipo_valido CHECK (tipo IN ('HABILITADO', 'BLOQUEADO'))
    );
    CREATE INDEX ix_disponibilidad_especifica_doctor_id ON disponibilidad_especifica (doctor_id);
    CREATE INDEX ix_disponibilidad_especifica_fecha ON disponibilidad_especifica (fecha);
    CREATE INDEX ix_especifica_doctor_fecha ON disponibilidad_especifica (doctor_id, fecha);
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.session import engine
from app.models.disponibilidad_especifica import DisponibilidadEspecifica

TABLA = DisponibilidadEspecifica.__tablename__

# Nombres tomados del propio modelo para que la verificación no se desincronice
# si se agrega o renombra un índice.
INDICES = tuple(
    sorted(indice.name for indice in DisponibilidadEspecifica.__table__.indexes)
)

# Tablas que ya existían: se cuentan antes y después para probar que la
# migración no alteró datos.
TABLAS_VERIFICADAS = ("doctores", "turnos")


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


def _ddl(conn, tabla: str) -> str | None:
    """``CREATE TABLE`` de ``tabla`` (o ``None`` si no existe)."""
    return conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)
    ).scalar()


def migrar() -> int:
    """Crea la tabla de excepciones por fecha y verifica el resultado.

    Devuelve el código de salida del proceso (``0`` = correcto).
    """
    ruta = _ruta_sqlite()

    with engine.connect() as conn:
        existe = TABLA in _tablas(conn)
        indices = _indices(conn, TABLA) if existe else []
        faltantes = [nombre for nombre in INDICES if nombre not in indices]
        iniciales = {tabla: _contar(conn, tabla) for tabla in TABLAS_VERIFICADAS}

    if existe and not faltantes:
        print(f"La tabla '{TABLA}' ya está creada con sus índices: nada que migrar.")
        return 0

    if ruta.exists():
        respaldo = ruta.with_name(f"{ruta.name}.bak-{utcnow():%Y%m%d_%H%M%S}")
        shutil.copy2(ruta, respaldo)
        print(f"Copia de seguridad creada: {respaldo}")

    with engine.begin() as conn:
        # Se recalcula dentro de la transacción: SQLite hace *commit* implícito de
        # cada DDL, así que una corrida anterior pudo dejar la migración a medias.
        if _ddl(conn, TABLA) is None:
            # El modelo es la única fuente de verdad del esquema.
            DisponibilidadEspecifica.__table__.create(bind=conn, checkfirst=True)
            print(f"Tabla creada: {TABLA}")
        else:
            print(f"La tabla '{TABLA}' ya existía: se completan sus índices.")

        # ``create`` arma también los índices; si el archivo venía de una corrida
        # interrumpida, acá se recuperan uno por uno.
        for indice in DisponibilidadEspecifica.__table__.indexes:
            if indice.name not in _indices(conn, TABLA):
                indice.create(bind=conn, checkfirst=True)
                print(f"Índice creado: {indice.name}")

        columnas = [
            fila[1]
            for fila in conn.exec_driver_sql(f'PRAGMA table_info("{TABLA}")').all()
        ]
        indices_final = _indices(conn, TABLA)
        finales = {tabla: _contar(conn, tabla) for tabla in TABLAS_VERIFICADAS}

    for tabla, inicial in iniciales.items():
        if finales[tabla] != inicial:
            raise RuntimeError(
                f"Las filas de '{tabla}' cambiaron ({inicial} → {finales[tabla]}); "
                "revisar la copia de seguridad."
            )

    print(f"Migración completada: {TABLA} lista.")
    print(f"Columnas: {', '.join(columnas)}")
    print(f"Índices: {', '.join(indices_final) or '(sin índices nombrados)'}")
    for tabla, total in finales.items():
        print(f"'{tabla}' intacta: {total} fila(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(migrar())
