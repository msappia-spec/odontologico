"""Limpia las tablas de una base de datos sin borrar el esquema.

Pensado para la base PostgreSQL de **Neon** que usa el despliegue de Vercel.
Vercel no guarda las tablas: la aplicación lee ``DATABASE_URL`` y se conecta a
Neon. Por eso este script se ejecuta desde la máquina local, apuntando a la
misma cadena de producción.

Modos
-----
* ``operativo`` (recomendado): borra sólo los datos de trabajo (pacientes,
  turnos, historias clínicas, evoluciones, antecedentes, adjuntos, caja,
  movimientos y disponibilidad específica). Conserva doctores, obras sociales,
  prestaciones, aranceles, agenda semanal y menú del bot.
* ``todo``: borra las 17 tablas del proyecto y vuelve a sembrar el menú del FAQ
  Bot con sus cuatro opciones históricas para que el panel no quede vacío.

Sin ``--ejecutar`` es un **simulacro**: muestra cuántas filas hay y no escribe
nada. La limpieza se hace en una sola transacción: si algo falla, la base queda
como estaba.

Seguridad
---------
* No usa ``drop_all`` ni elimina tablas/columnas: sólo vacía filas.
* En PostgreSQL usa ``TRUNCATE ... RESTART IDENTITY`` (sin ``CASCADE``) para no
  arrastrar tablas que no forman parte del modo elegido.
* Rechaza por defecto una URL SQLite. La SQLite de Vercel vive en ``/tmp`` y es
  efímera: no se puede limpiar desde afuera. Para pruebas locales hay que pasar
  ``--permitir-sqlite`` de forma explícita.

Uso::

    # Simulacro contra producción (sin borrar nada)
    python -m scripts.limpiar_base --env-file .env.vercel.local

    # Limpiar datos operativos de producción
    python -m scripts.limpiar_base --env-file .env.vercel.local --ejecutar

    # Limpiar todas las tablas
    python -m scripts.limpiar_base --env-file .env.vercel.local --modo todo --ejecutar

    # Prueba local con un SQLite descartable
    python -m scripts.limpiar_base --url sqlite:///./prueba.db --permitir-sqlite --ejecutar

La cadena de producción se guarda en ``.env.vercel.local`` (ignorado por git)
porque el CLI de Vercel no puede exportar las variables de tipo ``Secret``: al
hacer ``vercel env pull`` las reemplaza por ``[SENSITIVE]``. El archivo lo crea
``limpiar_vercel.bat`` pidiendo la cadena con entrada oculta. No se recomienda
pasar ``--url`` en producción porque el secreto queda en el historial de la
consola.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlsplit

from dotenv import dotenv_values
from sqlalchemy import Engine, create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services.whatsapp_menu_service import sembrar_menu_por_defecto

#: Tablas que se vacían en el modo ``operativo``. El orden real de borrado se
#: calcula con ``Base.metadata.sorted_tables`` (primero los hijos).
TABLAS_OPERATIVAS: tuple[str, ...] = (
    "detalles_movimiento_caja",
    "movimientos_caja",
    "archivos_adjuntos",
    "evoluciones_medicas",
    "odontograma_dientes",
    "odontogramas",
    "antecedentes_salud",
    "historias_clinicas",
    "turnos",
    "disponibilidad_especifica",
    "pacientes",
)

#: Variables que pueden contener la cadena de producción. La primera que exista
#: gana. ``DATABASE_URL_UNPOOLED`` es la conexión directa de Neon y evita el
#: pooler; si no está, se usa la misma que lee la aplicación.
CLAVES_URL: tuple[str, ...] = (
    "DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
    "POSTGRES_URL",
    "POSTGRES_URL_NON_POOLING",
)

MODOS: tuple[str, ...] = ("operativo", "todo")



def _normalizar_url(url: str) -> str:
    """Reescribe ``postgresql://`` al driver psycopg 3 que instala el proyecto.

    Mismo criterio que ``app.core.config.Settings._normalizar_driver_postgres``:
    los proveedores administrados entregan la cadena sin ``+psycopg`` y
    SQLAlchemy intentaría importar psycopg2, que no está instalado.
    """
    for prefijo in ("postgres://", "postgresql://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo) :]
    return url


def _crear_engine(url: str) -> Engine:
    """Crea el engine con el mismo criterio que ``app.db.session``."""
    connect_args: dict = {}
    if url.startswith("sqlite"):
        # Permite compartir la conexión entre hilos, como en la app.
        connect_args = {"check_same_thread": False}
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def _describir_url(url: str) -> str:
    """Describe el destino sin mostrar usuario, contraseña ni parámetros."""
    partes = urlsplit(url)
    if partes.scheme.startswith("sqlite"):
        return f"SQLite ({partes.path.lstrip('/') or 'sin archivo'})"
    host = partes.hostname or "(sin host)"
    base = partes.path.lstrip("/") or "(sin nombre)"
    return f"{partes.scheme}://{host}/{base}"


def _url_desde_entorno(env_file: Optional[Path]) -> Optional[str]:
    """Busca la cadena en el entorno o en un archivo ``.env`` explícito.

    El archivo explícito (``--env-file``) tiene prioridad sobre el entorno. Si
    no se pasa archivo, se usan las variables del proceso. Así el ``.env`` local
    (que apunta a SQLite) no se confunde con producción y un ``DATABASE_URL``
    exportado no pisa la cadena de producción indicada a mano.
    """
    valores: dict = {}
    if env_file is not None:
        if not env_file.exists():
            raise FileNotFoundError(f"No existe el archivo {env_file}")
        valores = {
            clave: valor
            for clave, valor in dotenv_values(env_file).items()
            if valor is not None
        }

    for clave in CLAVES_URL:
        # El archivo explícito tiene prioridad sobre el entorno: evita que un
        # DATABASE_URL exportado para la app local desvíe la limpieza a otra
        # base. ``--url`` sigue teniendo prioridad sobre ambos.
        valor = valores.get(clave) or os.environ.get(clave)
        if valor and str(valor).strip():
            return str(valor).strip().strip('"').strip("'")
    return None


def _tablas_para_modo(engine: Engine, modo: str) -> list:
    """Devuelve las tablas del modo, en orden de dependencias.

    Se verifica contra la base real que todas existan. Si falta alguna, se
    aborta antes de borrar nada: una limpieza parcial sería peor que no hacerla.
    """
    existentes = set(inspect(engine).get_table_names())
    nombres_operativos = set(TABLAS_OPERATIVAS)

    if modo == "todo":
        tablas = list(Base.metadata.sorted_tables)
    else:
        tablas = [
            tabla
            for tabla in Base.metadata.sorted_tables
            if tabla.name in nombres_operativos
        ]

    faltantes = [tabla.name for tabla in tablas if tabla.name not in existentes]
    if faltantes:
        raise RuntimeError(
            "Faltan tablas en el destino: "
            + ", ".join(faltantes)
            + ". Abortado sin borrar datos."
        )
    return tablas


def _contar(engine: Engine, tabla) -> int:
    """Cantidad de filas de una tabla."""
    with engine.connect() as conexion:
        return int(
            conexion.execute(select(func.count()).select_from(tabla)).scalar()
            or 0
        )


def _imprimir_conteos(titulo: str, tablas: Sequence, engine: Engine) -> dict:
    """Imprime y devuelve el conteo de cada tabla."""
    print(titulo)
    print("-" * 46)
    conteos: dict[str, int] = {}
    for tabla in tablas:
        conteos[tabla.name] = _contar(engine, tabla)
        print(f"{tabla.name:<30}{conteos[tabla.name]:>10}")
    print("-" * 46)
    print(f"{'TOTAL':<30}{sum(conteos.values()):>10}")
    return conteos


def _vaciar_postgresql(conexion, tablas: Sequence) -> None:
    """Vacía las tablas en una sola sentencia, sin ``CASCADE``.

    No se usa ``CASCADE`` a propósito: si alguna tabla no prevista dependiera de
    las elegidas, PostgreSQL falla y la transacción se revierte, en vez de
    borrar datos de una tabla que el modo debía conservar.
    """
    conexion.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
    conexion.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
    lista = ", ".join(f'"{tabla.name}"' for tabla in tablas)
    conexion.exec_driver_sql(f"TRUNCATE TABLE {lista} RESTART IDENTITY")


def _vaciar_sqlite(conexion, tablas: Sequence) -> None:
    """Borra filas en orden inverso a las dependencias y reinicia los ids."""
    for tabla in reversed(list(tablas)):
        conexion.exec_driver_sql(f'DELETE FROM "{tabla.name}"')

    # SQLite guarda el último id de las columnas AUTOINCREMENT en esta tabla.
    # Sin reiniciarla, el próximo registro seguiría con un id alto.
    if "sqlite_sequence" not in inspect(conexion).get_table_names():
        return
    nombres = [tabla.name for tabla in tablas]
    marcadores = ", ".join("?" for _ in nombres)
    conexion.exec_driver_sql(
        f"DELETE FROM sqlite_sequence WHERE name IN ({marcadores})",
        tuple(nombres),
    )


def _sembrar_menu(engine: Engine) -> int:
    """Vuelve a cargar el menú por defecto si la tabla quedó vacía."""
    fabrica = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    with fabrica() as db:
        return sembrar_menu_por_defecto(db)


def limpiar(
    engine: Engine,
    modo: str,
    *,
    ejecutar: bool,
    sembrar_menu: bool = True,
) -> int:
    """Vacía las tablas del modo elegido y verifica el resultado.

    Devuelve ``0`` si todo quedó vacío (o si fue un simulacro) y ``1`` si la
    verificación posterior encontró filas inesperadas.
    """
    tablas = _tablas_para_modo(engine, modo)
    antes = _imprimir_conteos("Filas antes de limpiar:", tablas, engine)
    if sum(antes.values()) == 0:
        if modo == "todo" and sembrar_menu:
            if ejecutar:
                creadas = _sembrar_menu(engine)
                print(f"Menú del bot resembrado: {creadas} opciones por defecto.")
            else:
                print("El menú del bot se sembraría con 4 opciones por defecto.")
        print("La base ya estaba vacía en las tablas de este modo.")
        return 0

    if not ejecutar:
        print()
        print("Simulacro: no se borró nada.")
        print("Para ejecutar de verdad, repetir con --ejecutar.")
        return 0

    print()
    print("Borrando en una sola transacción...")
    with engine.begin() as conexion:
        if engine.dialect.name == "postgresql":
            _vaciar_postgresql(conexion, tablas)
        else:
            _vaciar_sqlite(conexion, tablas)
    print("Borrado confirmado (commit).")

    if modo == "todo" and sembrar_menu:
        creadas = _sembrar_menu(engine)
        print(f"Menú del bot resembrado: {creadas} opciones por defecto.")

    despues = _imprimir_conteos("Filas después de limpiar:", tablas, engine)
    esperado_menu = 4 if (modo == "todo" and sembrar_menu) else 0
    fallas = [
        f"{nombre}={cantidad}"
        for nombre, cantidad in despues.items()
        if cantidad != (esperado_menu if nombre == "opciones_menu_whatsapp" else 0)
    ]
    if fallas:
        print()
        print("[ERROR] Quedaron filas inesperadas: " + ", ".join(fallas))
        return 1

    if modo == "operativo":
        print()
        print(
            "Se conservaron doctores, obras sociales, prestaciones, aranceles, "
            "agenda semanal y menú del bot."
        )
    print()
    print("Limpieza terminada correctamente.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Punto de entrada del script; devuelve el código de salida."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.limpiar_base",
        description=(
            "Vacía las tablas de la base configurada (por ejemplo la PostgreSQL "
            "de Neon que usa Vercel) sin borrar el esquema. Sin --ejecutar sólo "
            "muestra un simulacro."
        ),
    )
    parser.add_argument(
        "--modo",
        choices=MODOS,
        default="operativo",
        help=(
            "operativo: pacientes, turnos, historias y caja (conserva "
            "catálogos); todo: las 17 tablas y resiembra el menú del bot."
        ),
    )
    parser.add_argument(
        "--ejecutar",
        action="store_true",
        help="Borra de verdad. Sin esta opción sólo se muestran los conteos.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=(
            "Archivo .env del que leer DATABASE_URL. Si se omite, se usa el "
            "entorno del proceso (por ejemplo, si la exportaste a mano)."
        ),
    )
    parser.add_argument(
        "--url",
        default=None,
        help=(
            "Cadena de conexión explícita. Evitarla en producción: queda en el "
            "historial de la consola. Tiene prioridad sobre --env-file."
        ),
    )
    parser.add_argument(
        "--permitir-sqlite",
        action="store_true",
        help=(
            "Permite limpiar un SQLite local. No sirve para Vercel: su SQLite "
            "vive en /tmp y es efímera."
        ),
    )
    parser.add_argument(
        "--sin-sembrar-menu",
        action="store_true",
        help="En modo todo, deja vacío el menú del bot en vez de resembrarlo.",
    )
    args = parser.parse_args(argv)

    url = args.url or _url_desde_entorno(args.env_file)
    if not url:
        parser.error(
            "Falta DATABASE_URL. Usá --env-file .env.vercel.local, exportala "
            "en el entorno o pasá --url."
        )
    url = _normalizar_url(str(url).strip().strip('"').strip("'"))
    if "[SENSITIVE]" in url.upper():
        print()
        print(
            "[ERROR] DATABASE_URL es el placeholder [SENSITIVE] de Vercel. "
            "Reemplazala por la cadena real de Neon en --env-file."
        )
        return 2
    engine = _crear_engine(url)
    try:
        print(f"Destino: {_describir_url(url)}")
        print(f"Modo:    {args.modo}")
        if engine.dialect.name != "postgresql" and not args.permitir_sqlite:
            print()
            print(
                "[ERROR] El destino no es PostgreSQL. La SQLite de Vercel es "
                "efímera y no se puede limpiar desde esta máquina. Configurá "
                "DATABASE_URL con la base de Neon o usá --permitir-sqlite para "
                "una prueba local."
            )
            return 2
        return limpiar(
            engine,
            args.modo,
            ejecutar=args.ejecutar,
            sembrar_menu=not args.sin_sembrar_menu,
        )
    except Exception as exc:  # CLI: mensaje claro en vez de traza completa
        print()
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())

