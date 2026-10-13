"""Migración: ``uq_turno_doctor_fecha_inicio`` pasa de restricción a índice parcial.

Contexto
--------
La primera versión del modelo declaraba ``UniqueConstraint(doctor_id,
fecha_hora_inicio)``, una restricción **global**: un turno ``CANCELADO`` seguía
ocupando su inicio exacto y no se podía volver a agendar ese hueco.

El modelo actual declara un *índice único parcial* que excluye los turnos
``CANCELADO`` (ver ``app/models/turno.py``), coherente con la validación de
negocio ``turno_service._validar_solapamiento`` y con el panel de "Horarios
disponibles" del calendario.

Esta migración, pensada para SQLite (el entorno de desarrollo del módulo),
reconstruye la tabla ``turnos`` y es **idempotente**: si la restricción antigua
no está presente no modifica nada. Antes de escribir guarda una copia de
seguridad del archivo ``.db``.

Uso::

    python -m scripts.migrar_turnos_unique_parcial

Equivalente en PostgreSQL (el proyecto aún no usa Alembic)::

    ALTER TABLE turnos DROP CONSTRAINT uq_turno_doctor_fecha_inicio;
    CREATE UNIQUE INDEX uq_turno_doctor_fecha_inicio
        ON turnos (doctor_id, fecha_hora_inicio)
        WHERE estado_turno <> 'CANCELADO';
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.session import engine
from app.models.turno import Turno

CONSTRAINT_LEGACY = "uq_turno_doctor_fecha_inicio"

COLUMNAS = (
    "id",
    "paciente_id",
    "doctor_id",
    "fecha_hora_inicio",
    "fecha_hora_fin",
    "estado_turno",
    "estado_whatsapp",
    "whatsapp_mensaje_id",
)


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


def _ddl_tabla_turnos(conn) -> str | None:
    """Devuelve el ``CREATE TABLE`` de ``turnos`` (o ``None`` si no existe)."""
    return conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'turnos'"
    ).scalar()


def _tablas_legacy(conn) -> list[str]:
    """Tablas ``_turnos_legacy_*`` que hayan quedado de una corrida anterior."""
    return list(
        conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name LIKE '_turnos_legacy_%' ORDER BY name"
        ).scalars().all()
    )


def _nombres_de_indices(conn, tabla: str) -> list[str]:
    """Índices *nombrados* de ``tabla`` (los ``sqlite_autoindex_*`` no se pueden borrar)."""
    return [
        str(nombre)
        for nombre in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = ? AND sql IS NOT NULL",
            (tabla,),
        ).scalars().all()
    ]


def _contar(conn, tabla: str) -> int:
    """Cantidad de filas de ``tabla``."""
    return int(conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{tabla}"').scalar())


def _asegurar_indices(conn) -> list[str]:
    """Crea los índices declarados en el modelo que falten en ``turnos``."""
    existentes = {
        str(nombre)
        for nombre in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).scalars().all()
    }
    creados: list[str] = []
    for indice in Turno.__table__.indexes:
        if indice.name not in existentes:
            indice.create(bind=conn, checkfirst=True)
            creados.append(str(indice.name))
    return creados


def migrar() -> int:
    """Ejecuta la migración y devuelve un código de salida (0 = correcto).

    Es **resumible**: cada paso detecta el estado real de la base, de modo que
    volver a ejecutarla completa lo que haya quedado a medias (SQLite hace
    *commit* implícito de las sentencias DDL, por lo que la operación no es una
    única transacción).
    """
    ruta = _ruta_sqlite()

    with engine.connect() as conn:
        ddl = _ddl_tabla_turnos(conn)
        legacy = _tablas_legacy(conn)

    if ddl is None and not legacy:
        print("La tabla 'turnos' no existe: se creará con el esquema nuevo al arrancar la app.")
        return 0

    if ddl is not None and CONSTRAINT_LEGACY not in ddl and not legacy:
        print("La tabla 'turnos' ya usa el índice único parcial: nada que migrar.")
        return 0

    if len(legacy) > 1:
        print(f"Se encontraron varias tablas legadas: {legacy}. Se usa la primera.")

    if ruta.exists():
        respaldo = ruta.with_name(f"{ruta.name}.bak-{utcnow():%Y%m%d_%H%M%S}")
        shutil.copy2(ruta, respaldo)
        print(f"Copia de seguridad creada: {respaldo}")

    columnas = ", ".join(COLUMNAS)

    with engine.begin() as conn:
        ddl = _ddl_tabla_turnos(conn)
        legacy = _tablas_legacy(conn)

        # 1) Tabla origen: la legada si existe; si no, ``turnos`` (con la
        #    restricción antigua) que se renombra para liberar el nombre.
        if legacy:
            fuente = legacy[0]
        elif ddl is not None and CONSTRAINT_LEGACY in ddl:
            fuente = f"_turnos_legacy_{utcnow():%Y%m%d_%H%M%S}"
            for nombre in _nombres_de_indices(conn, "turnos"):
                conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{nombre}"')
            conn.exec_driver_sql(f"ALTER TABLE turnos RENAME TO {fuente}")
        else:  # pragma: no cover - cubierto por la verificación previa
            print("No hay nada que migrar.")
            return 0

        # 2) Los índices con nombre viajan con la tabla renombrada y bloquearían
        #    a los de la tabla nueva (los nombres son únicos en todo el archivo).
        for nombre in _nombres_de_indices(conn, fuente):
            conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{nombre}"')

        # 3) Tabla nueva (si quedó a medias una corrida anterior ya existe).
        if _ddl_tabla_turnos(conn) is None:
            Turno.__table__.create(bind=conn)
        creados = _asegurar_indices(conn)

        # 4) Copia de filas (idempotente: no duplica si ya se hizo).
        total_fuente = _contar(conn, fuente)
        if _contar(conn, "turnos") == 0 and total_fuente > 0:
            conn.exec_driver_sql(
                f"INSERT INTO turnos ({columnas}) SELECT {columnas} FROM {fuente}"
            )
        total_nuevo = _contar(conn, "turnos")

        if total_nuevo != total_fuente:
            raise RuntimeError(
                f"Filas en 'turnos' ({total_nuevo}) distintas de las de la tabla "
                f"origen '{fuente}' ({total_fuente}); no se elimina el origen."
            )

        # 5) Recién ahora se descarta la tabla legada.
        conn.exec_driver_sql(f"DROP TABLE {fuente}")

        indice = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (CONSTRAINT_LEGACY,),
        ).scalar()

    print(f"Migración completada: {total_nuevo} turnos preservados.")
    if creados:
        print(f"Índices creados: {', '.join(creados)}")
    print(f"Índice resultante: {indice}")
    return 0



if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(migrar())
