"""Pruebas de las migraciones aditivas que corren al arrancar la app.

El caso real que motivó esta cobertura: una PostgreSQL creada antes del Módulo 3
no tiene ``doctores.tipo_pago`` y el ORM devuelve HTTP 500 al listar o guardar
profesionales. ``create_all`` no agrega columnas a tablas existentes, así que
``init_db`` debe completarlas sin perder las filas.
"""

import app.db.init_db as init_db_module
from sqlalchemy import create_engine, inspect

from app.db.migraciones import (
    COLUMNAS_NUEVAS,
    _sentencia_alter,
    aplicar_columnas_faltantes,
)


def _crear_base_legacy(ruta):
    """Crea una base con el esquema anterior a la modalidad de pago."""
    engine = create_engine(f"sqlite:///{ruta.as_posix()}", future=True)
    with engine.begin() as conexion:
        conexion.exec_driver_sql(
            """
            CREATE TABLE doctores (
                id INTEGER PRIMARY KEY,
                nombre VARCHAR(120) NOT NULL,
                especialidad VARCHAR(120) NOT NULL,
                activo BOOLEAN NOT NULL DEFAULT 1
            )
            """
        )
        conexion.exec_driver_sql(
            "INSERT INTO doctores (nombre, especialidad, activo) "
            "VALUES ('Dra. Existente', 'Ortodoncia', 1)"
        )
    return engine


def test_agrega_columnas_faltantes_sin_perder_datos(tmp_path):
    """La fila previa queda como dueña y conserva sus datos."""
    engine = _crear_base_legacy(tmp_path / "legacy.db")

    aplicadas = aplicar_columnas_faltantes(engine)

    assert aplicadas == ["doctores.tipo_pago", "doctores.valor_pago"]
    columnas = {c["name"] for c in inspect(engine).get_columns("doctores")}
    assert {"tipo_pago", "valor_pago"} <= columnas

    with engine.connect() as conexion:
        fila = conexion.exec_driver_sql(
            "SELECT nombre, especialidad, tipo_pago, valor_pago FROM doctores"
        ).one()
    assert tuple(fila) == ("Dra. Existente", "Ortodoncia", "DUENO", None)
    engine.dispose()


def test_la_migracion_es_idempotente(tmp_path):
    """Un segundo arranque no repite los ``ALTER TABLE``."""
    engine = _crear_base_legacy(tmp_path / "legacy.db")

    assert aplicar_columnas_faltantes(engine)
    assert aplicar_columnas_faltantes(engine) == []
    engine.dispose()


def test_postgres_usa_add_column_if_not_exists():
    """Dos instancias de Vercel no fallan al migrar la misma columna."""
    columna = next(
        c
        for c in COLUMNAS_NUEVAS
        if c.tabla == "doctores" and c.columna == "tipo_pago"
    )

    sentencia_pg = _sentencia_alter(columna, "postgresql")
    assert "ADD COLUMN IF NOT EXISTS" in sentencia_pg
    assert "VARCHAR(15) NOT NULL DEFAULT 'DUENO'" in sentencia_pg

    sentencia_sqlite = _sentencia_alter(columna, "sqlite")
    assert "IF NOT EXISTS" not in sentencia_sqlite


def test_agrega_los_filiatorios_del_paciente(tmp_path):
    """Una base anterior al encabezado de la Historia Clínica General se migra."""
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'pacientes.db').as_posix()}", future=True
    )
    with engine.begin() as conexion:
        conexion.exec_driver_sql(
            "CREATE TABLE pacientes ("
            "id INTEGER PRIMARY KEY, "
            "nombre VARCHAR(120) NOT NULL, "
            "telefono VARCHAR(30) NOT NULL)"
        )
        conexion.exec_driver_sql(
            "INSERT INTO pacientes (nombre, telefono) "
            "VALUES ('Ana', '+5491100000000')"
        )

    aplicadas = aplicar_columnas_faltantes(engine)

    esperadas = {
        "fecha_nacimiento",
        "estado_civil",
        "nacionalidad",
        "domicilio",
        "profesion",
        "titular",
        "lugar_trabajo",
        "jerarquia",
    }
    assert {f"pacientes.{columna}" for columna in esperadas} <= set(aplicadas)
    columnas = {c["name"] for c in inspect(engine).get_columns("pacientes")}
    assert esperadas <= columnas

    with engine.connect() as conexion:
        fila = conexion.exec_driver_sql(
            "SELECT nombre, fecha_nacimiento, domicilio FROM pacientes"
        ).one()
    assert tuple(fila) == ("Ana", None, None)
    engine.dispose()


def test_init_db_aplica_la_migracion_antes_de_sembrar(monkeypatch):
    """``init_db`` invoca la migración aditiva en cada arranque."""

    class SesionFalsa:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            return False

    engine_falso = object()
    llamados = []
    monkeypatch.setattr(
        init_db_module.Base.metadata, "create_all", lambda bind: None
    )
    monkeypatch.setattr(init_db_module, "engine", engine_falso)
    monkeypatch.setattr(init_db_module, "SessionLocal", SesionFalsa)
    monkeypatch.setattr(
        init_db_module,
        "aplicar_columnas_faltantes",
        lambda engine: llamados.append(engine),
    )
    monkeypatch.setattr(
        init_db_module, "sembrar_menu_por_defecto", lambda db: 0
    )

    init_db_module.init_db()

    assert llamados == [engine_falso]
