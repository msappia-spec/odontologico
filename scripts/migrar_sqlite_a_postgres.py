"""Migración: copia los datos de la base local (SQLite) a PostgreSQL (Neon).

Contexto
--------
Vercel monta el proyecto en un sistema de archivos de **sólo lectura**: el SQLite
que usa el entorno de desarrollo (``sqlite:///./odontologico.db``) se redirige a
``/tmp`` en la nube y los datos se pierden en cada arranque de la Function (ver
``app/core/config.py``). Para que pacientes, turnos y agendas sobrevivan hay que
usar PostgreSQL (por ejemplo **Neon**, plan gratuito) y copiar allí lo que ya
está cargado en la base local. Este script hace esa copia.

Qué hace
--------
1. Abre un engine SQLAlchemy contra el origen (por defecto el SQLite que indica
   la configuración) y otro contra el destino (``--destino``).
2. Crea en el destino el esquema completo (``Base.metadata.create_all``), así
   sirve tanto para una base recién creada en Neon como para una que ya tiene
   las tablas.
3. Copia **tabla por tabla en orden de dependencias** (``Base.metadata
   .sorted_tables``: obras sociales y doctores antes que pacientes y turnos),
   preservando los ``id`` originales para que las claves foráneas sigan
   apuntando al mismo registro.
4. En PostgreSQL reajusta las **secuencias** (``setval``) para que el próximo
   INSERT que haga la app no choque con un ``id`` ya copiado.
5. Verifica al final que los conteos del destino sean los esperados; si no,
   sale con error.

Modos (``--modo``)
------------------
* ``reemplazar`` (por defecto): borra las filas del destino en orden inverso de
  dependencias y copia el origen. Es lo que se usa para dejar la nube igual al
  estado local.
* ``agregar``: inserta sólo las filas cuya clave primaria no exista en el
  destino; el resto se saltea. Sirve para sumar datos sin tocar lo que ya se
  cargó desde la web.

Sin ``--ejecutar`` el script es un **simulacro** (*dry run*): informa qué haría
y no escribe nada.

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.migrar_sqlite_a_postgres --destino "postgresql://usuario:clave@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require"
    python -m scripts.migrar_sqlite_a_postgres --destino "postgresql://..." --ejecutar
    python -m scripts.migrar_sqlite_a_postgres --destino "postgresql://..." --modo agregar --ejecutar

La cadena puede pegarse **tal cual** la entrega el proveedor (``postgresql://``):
el script la reescribe al driver psycopg 3 con el mismo criterio que
``app/core/config.py``, que es el driver que instala ``requirements.txt``.

Nota sobre fechas: el proyecto guarda ``fecha_hora_inicio``/``fecha_hora_fin`` en
UTC *naive*; al escribirlas en PostgreSQL se interpretan en la zona del servidor,
que en Neon es UTC, de modo que el instante se conserva.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

from sqlalchemy import Engine, create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base

MODOS = ("reemplazar", "agregar")


@dataclass
class Resultado:
    """Conteos de una corrida de la migración (por nombre de tabla)."""

    origen: str
    destino: str
    modo: str
    ejecutado: bool
    origen_filas: dict[str, int] = field(default_factory=dict)
    insertadas: dict[str, int] = field(default_factory=dict)
    salteadas: dict[str, int] = field(default_factory=dict)
    destino_final: dict[str, int] = field(default_factory=dict)
    secuencias: list[str] = field(default_factory=list)

    @property
    def total_insertadas(self) -> int:
        """Total de filas escritas en el destino."""
        return sum(self.insertadas.values())


def _normalizar_url(url: str) -> str:
    """Reescribe ``postgresql://`` a ``postgresql+psycopg://`` (driver v3).

    Mismo criterio que ``Settings._normalizar_driver_postgres``: los proveedores
    administrados (Neon, Supabase, Render) entregan la cadena sin driver y
    SQLAlchemy asumiría ``psycopg2``, que no está instalado.
    """
    for prefijo in ("postgres://", "postgresql://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo) :]
    return url


def _crear_engine(url: str) -> Engine:
    """Crea un engine para el origen o el destino indicado."""
    url = _normalizar_url(url)
    connect_args: dict = {}
    if url.startswith("sqlite"):
        # Igual que ``app.db.session``: permite compartir la conexión entre hilos.
        connect_args = {"check_same_thread": False}
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def _modelos_por_tabla() -> dict[str, type]:
    """Mapea nombre de tabla -> clase ORM usando los mappers de ``Base``."""
    return {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers}


def _sesion(engine: Engine) -> Session:
    """Abre una sesión con el mismo criterio que ``app.db.session``."""
    return sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )()


def _contar(sesion: Session, tabla) -> int:
    """Cantidad de filas de una tabla."""
    return int(sesion.execute(select(func.count()).select_from(tabla)).scalar() or 0)


def _filas(sesion: Session, tabla) -> list[dict]:
    """Todas las filas de la tabla como diccionarios ``columna -> valor``.

    Se seleccionan las **columnas** de la tabla (``select(Table)``) y no la
    entidad ORM: ``select(Entidad).mappings()`` devuelve una sola clave con el
    objeto completo (``{"Doctor": <Doctor ...>}``), inservible para insertar. Con
    las columnas, además, los tipos viajan ya convertidos por SQLAlchemy:
    ``Time`` -> ``datetime.time``, ``DateTime`` -> ``datetime``, ``Boolean`` ->
    ``bool`` y los ``Enum(..., native_enum=False)`` como su cadena de valor.
    """
    consulta = select(tabla).order_by(*tabla.primary_key.columns)
    return [dict(fila) for fila in sesion.execute(consulta).mappings().all()]




def _reajustar_secuencias(engine: Engine, tablas) -> list[str]:
    """Ajusta las secuencias de PostgreSQL al ``MAX(id)`` de cada tabla.

    Sin esto, la app intentaría insertar con el contador de la secuencia (1, 2,
    ...) y chocaría contra los ``id`` ya copiados. En otros dialectos (SQLite
    actualiza su ``sqlite_sequence`` solo) no hace nada.
    """
    if engine.dialect.name != "postgresql":
        return []

    ajustadas: list[str] = []
    with engine.begin() as conn:
        for tabla in tablas:
            secuencia = conn.exec_driver_sql(
                "SELECT pg_get_serial_sequence(%s, 'id')", (tabla.name,)
            ).scalar()
            if not secuencia:
                continue
            max_id = int(
                conn.exec_driver_sql(
                    f'SELECT COALESCE(MAX(id), 0) FROM "{tabla.name}"'
                ).scalar()
                or 0
            )
            # ``is_called=False`` si la tabla quedó vacía: el próximo INSERT
            # recibe el id 1.
            conn.exec_driver_sql(
                "SELECT setval(%s, %s, %s)", (secuencia, max(max_id, 1), max_id > 0)
            )
            ajustadas.append(f"{tabla.name} -> {max_id}")
    return ajustadas


def _claves_existentes(sesion: Session, tabla) -> set[tuple]:
    """Conjunto de claves primarias ya presentes en el destino."""
    consulta = select(*tabla.primary_key.columns)
    return {tuple(fila) for fila in sesion.execute(consulta).all()}


def _clave(fila: dict, columnas_pk) -> tuple:
    """Clave primaria de una fila leída del origen, como tupla."""
    return tuple(fila[columna.name] for columna in columnas_pk)


def migrar(
    destino: str,
    origen: str | None = None,
    *,
    ejecutar: bool = False,
    modo: str = "reemplazar",
) -> Resultado:
    """Copia las tablas del origen al destino y devuelve el resultado.

    ``origen`` por defecto es la base configurada para la app (el SQLite local).
    Sin ``ejecutar=True`` es un simulacro: se informan los conteos y no se
    escribe nada.

    Se crea el esquema del destino si falta y, en PostgreSQL, se reajustan las
    secuencias al ``MAX(id)`` copiado. Al terminar se verifica que los conteos
    del destino sean los esperados; si no coinciden, lanza ``RuntimeError``.
    """
    if modo not in MODOS:
        raise ValueError(f"Modo inválido: {modo!r}. Opciones: {', '.join(MODOS)}.")

    origen = origen or settings.database_url
    if _normalizar_url(origen) == _normalizar_url(destino):
        raise RuntimeError(
            "El origen y el destino son la misma base de datos: no hay nada que "
            "copiar (¿se pasó el mismo --destino?)."
        )

    engine_origen = _crear_engine(origen)
    engine_destino = _crear_engine(destino)

    resultado = Resultado(
        origen=engine_origen.url.render_as_string(hide_password=True),
        destino=engine_destino.url.render_as_string(hide_password=True),
        modo=modo,
        ejecutado=ejecutar,
    )

    # Idempotente: en una base nueva de Neon crea las tablas y en una existente
    # no toca nada (no hay columnas faltantes en este proyecto).
    Base.metadata.create_all(engine_destino)

    modelos = _modelos_por_tabla()
    tablas = list(Base.metadata.sorted_tables)  # orden de dependencias (FKs)

    sesion_origen = _sesion(engine_origen)
    sesion_destino = _sesion(engine_destino)
    try:
        resultado.origen_filas = {
            tabla.name: _contar(sesion_origen, modelos[tabla.name]) for tabla in tablas
        }
        previas_destino = {
            tabla.name: _contar(sesion_destino, modelos[tabla.name]) for tabla in tablas
        }

        if not ejecutar:
            # Simulacro: el destino queda tal como estaba.
            resultado.destino_final = dict(previas_destino)
            return resultado

        if modo == "reemplazar":
            # Orden inverso de dependencias: primero las tablas que apuntan a
            # otras (turnos, disponibilidades) y al final doctores/obras sociales.
            for tabla in reversed(tablas):
                sesion_destino.execute(delete(modelos[tabla.name]))
            sesion_destino.commit()
            previas_destino = {tabla.name: 0 for tabla in tablas}

        for tabla in tablas:
            filas = _filas(sesion_origen, tabla)
            if modo == "agregar" and filas:
                existentes = _claves_existentes(sesion_destino, tabla)
                columnas_pk = list(tabla.primary_key.columns)
                completas = [
                    fila
                    for fila in filas
                    if _clave(fila, columnas_pk) not in existentes
                ]
                resultado.salteadas[tabla.name] = len(filas) - len(completas)
                filas = completas
            if filas:
                # INSERT de **Core** (``tabla.insert()``): el bulk del ORM
                # reinterpreta las claves de los diccionarios y termina armando
                # el INSERT con un subconjunto de columnas, mientras que Core usa
                # exactamente las claves de cada fila.
                sesion_destino.execute(tabla.insert(), filas)
            sesion_destino.commit()
            resultado.insertadas[tabla.name] = len(filas)

        resultado.secuencias = _reajustar_secuencias(engine_destino, tablas)

        resultado.destino_final = {
            tabla.name: _contar(sesion_destino, modelos[tabla.name]) for tabla in tablas
        }
    finally:
        sesion_origen.close()
        sesion_destino.close()
        engine_origen.dispose()
        engine_destino.dispose()

    esperado = {
        tabla.name: previas_destino[tabla.name] + resultado.insertadas.get(tabla.name, 0)
        for tabla in tablas
    }
    if resultado.destino_final != esperado:
        raise RuntimeError(
            f"Conteos inesperados en el destino: {resultado.destino_final} "
            f"(esperado {esperado}). Revisar los datos antes de usar la app."
        )
    return resultado


def _imprimir(resultado: Resultado) -> None:
    """Reporta la corrida en formato tabular (sin exponer la clave de la base)."""
    print(f"Origen : {resultado.origen}")
    print(f"Destino: {resultado.destino}")
    aviso = "" if resultado.ejecutado else "   [SIMULACRO: nada se escribió]"
    print(f"Modo   : {resultado.modo}{aviso}")
    print()
    print(f"{'tabla':<30}{'origen':>8}{'copiadas':>10}{'salteadas':>11}{'destino':>9}")
    print("-" * 68)
    for tabla, filas_origen in resultado.origen_filas.items():
        print(
            f"{tabla:<30}{filas_origen:>8}"
            f"{resultado.insertadas.get(tabla, 0):>10}"
            f"{resultado.salteadas.get(tabla, 0):>11}"
            f"{resultado.destino_final.get(tabla, 0):>9}"
        )
    print("-" * 68)
    print(f"Total de filas copiadas: {resultado.total_insertadas}")
    if resultado.secuencias:
        print("Secuencias reajustadas: " + ", ".join(resultado.secuencias))
    if not resultado.ejecutado:
        print()
        print("Simulacro terminado: repetir con --ejecutar para escribir en el destino.")


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script; devuelve el código de salida."""
    parser = argparse.ArgumentParser(
        description=(
            "Copia los datos de la base local (SQLite) a PostgreSQL, por ejemplo "
            "la base de Neon que usa el despliegue de Vercel."
        )
    )
    parser.add_argument(
        "--destino",
        default=os.getenv("DATABASE_URL_DESTINO"),
        help=(
            "Cadena del destino, tal como la entrega el proveedor "
            "(postgresql://...). También se lee de DATABASE_URL_DESTINO."
        ),
    )
    parser.add_argument(
        "--origen",
        default=None,
        help="Cadena del origen (por defecto, la base configurada para la app).",
    )
    parser.add_argument(
        "--modo",
        choices=MODOS,
        default="reemplazar",
        help=(
            "reemplazar: deja el destino igual al origen (borra y copia); "
            "agregar: sólo inserta las filas que falten."
        ),
    )
    parser.add_argument(
        "--ejecutar",
        action="store_true",
        help="Escribe de verdad en el destino. Sin esta opción sólo se informa.",
    )
    args = parser.parse_args(argv)

    if not args.destino:
        parser.error("Falta --destino (o la variable DATABASE_URL_DESTINO).")

    resultado = migrar(
        args.destino,
        args.origen,
        ejecutar=args.ejecutar,
        modo=args.modo,
    )
    _imprimir(resultado)
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())
