"""Precarga del nomenclador odontológico de referencia de Córdoba (Módulo 3).

Este script es un *seed* del catálogo de ``Prestacion``: carga los códigos y las
descripciones oficiales de la grilla referencial del Colegio Odontológico de la
Provincia de Córdoba, con un **valor base orientativo** (precio de referencia
para un paciente particular, o cuando la cobertura no tiene arancel cargado).

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.seed_nomenclador_cba

Opciones::

    python -m scripts.seed_nomenclador_cba --actualizar   # refresca los que ya están
    python -m scripts.seed_nomenclador_cba --dry-run      # muestra sin escribir
    python -m scripts.seed_nomenclador_cba --sin-detalle  # sólo el resumen

Conexión
--------
El script **no** abre su propia conexión: usa ``SessionLocal`` de
``app/db/session.py``, es decir la misma ``DATABASE_URL`` que la aplicación
(``.env`` o variable de entorno). Por eso funciona igual en desarrollo local
(SQLite, ``sqlite:///./odontologico.db``) y en producción (PostgreSQL en la
nube): el motor, los tipos y el ``commit`` los resuelve SQLAlchemy. ``init_db()``
garantiza además que la tabla ``prestaciones`` exista (crea las faltantes) antes
de insertar, que es lo que hace falta en una base PostgreSQL recién creada.

Idempotencia
------------
El **código de nomenclador identifica** la práctica (índice único
``uq_prestacion_codigo``). Antes de insertar, el script trae los códigos ya
existentes y compara sin distinguir mayúsculas ni espacios: los que ya están se
**omiten** (no se duplican ni se pisan los precios que la secretaría haya
ajustado desde el panel). Con ``--actualizar`` sí se reescriben la descripción y
el valor base de los existentes, útil para refrescar la grilla después de un
aumento. La carrera de dos ejecuciones simultáneas está cubierta por el índice
único: el ``IntegrityError`` se cuenta como "ya existía", no como error.

Notas sobre los datos
---------------------
* La descripción conserva el **capítulo** del nomenclador (``Capítulo I: …``),
  que es la forma en que la obra social presenta y audita las prácticas.
* Los **montos son orientativos** (pesos argentinos) y sirven para arrancar el
  circuito de facturación: la grilla real de cada colegio/cobertura se ajusta
  desde *Facturación y Caja → Prestaciones y aranceles* (o con ``--actualizar``).
* Este seed **no** toca pacientes, odontólogos ni turnos, y tampoco carga
  aranceles por obra social (eso es el acuerdo con cada cobertura, distinto en
  cada convenio): esos se cargan por cobertura en el panel.
"""

from __future__ import annotations

import argparse
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.money import redondear_monto
from app.db.init_db import init_db
from app.db.session import SessionLocal, engine
from app.models.prestacion import Prestacion

# (código de nomenclador, descripción, valor base orientativo) de la grilla
# referencial del Colegio Odontológico de la Provincia de Córdoba. El código
# identifica la práctica ante la obra social y la descripción conserva el
# capítulo del nomenclador, tal como se presenta en la liquidación.
NOMENCLADOR_CORDOBA: tuple[tuple[str, str, float], ...] = (
    ("10100", "Capítulo I: Consulta, examen, diagnóstico y fichado", 18000.0),
    ("10400", "Capítulo I: Consulta de urgencia (diurna o nocturna)", 26000.0),
    ("20100", "Capítulo II: Obturación con amalgama cavidad simple", 32000.0),
    (
        "21600",
        "Capítulo II: Restauración con resinas compuestas (Composite estético)",
        42000.0,
    ),
    (
        "30100",
        "Capítulo III: Tratamiento unirradicular (Conducto en diente simple)",
        95000.0,
    ),
    (
        "30200",
        "Capítulo III: Tratamiento multirradicular (Conducto dos o más raíces)",
        130000.0,
    ),
    (
        "50100",
        "Capítulo V: Tartrectomía y cepillado mecánico "
        "(Limpieza y eliminación de sarro)",
        38000.0,
    ),
    ("10010", "Capítulo X: Extracción dentaria simple (Cirugía general)", 45000.0),
    (
        "10030",
        "Capítulo X: Extracción de diente retenido (Cirugía compleja)",
        120000.0,
    ),
)

_SEP = "-" * 72


def _normalizar(codigo: str) -> str:
    """Normaliza un código para compararlo (sin espacios ni mayúsculas)."""
    return codigo.strip().lower()


def _pesos(valor: float) -> str:
    """Formatea un importe como ``$ 18.000,00`` (convención es-AR)."""
    texto = f"{valor:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return "$ " + texto.replace("\u00a0", ".")


def _origen_seguro() -> str:
    """Describe la base destino sin exponer usuario ni contraseña."""
    dialecto = engine.dialect.name
    if dialecto.startswith("sqlite"):
        # En SQLite la "URL" es la ruta del archivo: sirve tal cual.
        return f"{dialecto} -> {engine.url.database}"
    return f"{dialecto} -> {engine.url.render_as_string(hide_password=True)}"


def _catalogo_existente(db: Session) -> dict[str, Prestacion]:
    """Devuelve ``{codigo normalizado: Prestacion}`` con **todo** el catálogo.

    Se resuelve con una sola consulta (y no una por código) para no encadenar
    *round-trips* cuando la base está en la nube.
    """
    filas = db.execute(select(Prestacion)).scalars().all()
    return {_normalizar(fila.codigo_nomenclador): fila for fila in filas}


def cargar_nomenclador(
    db: Session,
    *,
    actualizar: bool = False,
    dry_run: bool = False,
    detalle: bool = True,
) -> dict[str, int]:
    """Inserta las prácticas del nomenclador que falten en el catálogo.

    :param db: sesión de SQLAlchemy (la que entrega ``SessionLocal``).
    :param actualizar: reescribe descripción y valor base de las que ya existen.
    :param dry_run: simula la carga sin escribir en la base.
    :param detalle: imprime una línea por código.
    :returns: contadores ``insertadas``, ``actualizadas``, ``existentes``,
        ``errores`` y ``total`` (filas en el catálogo al terminar).
    """
    existentes = _catalogo_existente(db)
    contadores = {"insertadas": 0, "actualizadas": 0, "existentes": 0, "errores": 0}

    for codigo, descripcion, valor_base in NOMENCLADOR_CORDOBA:
        valor = redondear_monto(valor_base)
        practica = existentes.get(_normalizar(codigo))

        if practica is not None:
            if actualizar and (
                practica.descripcion != descripcion
                or redondear_monto(practica.valor_base) != valor
            ):
                practica.descripcion = descripcion
                practica.valor_base = valor
                practica.activa = True
                contadores["actualizadas"] += 1
                if detalle:
                    print(
                        f"  ~ {codigo}  actualizada  (id={practica.id})  "
                        f"{_pesos(valor)}"
                    )
            else:
                contadores["existentes"] += 1
                if detalle:
                    print(
                        f"  = {codigo}  ya existía   (id={practica.id})  "
                        f"{_pesos(practica.valor_base)}"
                    )
            continue

        if dry_run:
            contadores["insertadas"] += 1
            if detalle:
                print(f"  + {codigo}  se insertaría  {_pesos(valor)}  {descripcion}")
            continue

        nueva = Prestacion(
            codigo_nomenclador=codigo,
            descripcion=descripcion,
            valor_base=valor,
            activa=True,
        )
        db.add(nueva)
        try:
            db.commit()
        except IntegrityError:
            # Otra ejecución (o el panel) creó el mismo código en el medio: el
            # índice único uq_prestacion_codigo lo impidió, así que se cuenta
            # como "ya existía" en vez de abortar toda la carga.
            db.rollback()
            contadores["existentes"] += 1
            if detalle:
                print(f"  = {codigo}  ya existía   (cargada en paralelo)")
            continue
        except SQLAlchemyError as exc:  # pragma: no cover - falla de infraestructura
            db.rollback()
            contadores["errores"] += 1
            print(f"  ! {codigo}  ERROR: {exc}")
            continue

        existentes[_normalizar(codigo)] = nueva
        contadores["insertadas"] += 1
        if detalle:
            print(
                f"  + {codigo}  insertada    (id={nueva.id})  "
                f"{_pesos(nueva.valor_base)}  {descripcion}"
            )

    if actualizar and not dry_run:
        db.commit()

    contadores["total"] = len(db.execute(select(Prestacion.id)).scalars().all())
    return contadores


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Punto de entrada: parsea los argumentos, carga y resume el resultado."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed_nomenclador_cba",
        description=(
            "Precarga el nomenclador odontológico de referencia de Córdoba en la "
            "tabla de prestaciones (idempotente: no duplica códigos existentes)."
        ),
    )
    parser.add_argument(
        "--actualizar",
        action="store_true",
        help="reescribe descripción y valor base de las prácticas que ya existen",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="muestra qué haría sin escribir en la base",
    )
    parser.add_argument(
        "--sin-detalle",
        action="store_true",
        help="no imprime una línea por código (sólo la cabecera y el resumen)",
    )
    args = parser.parse_args(argv)

    detalle = not args.sin_detalle
    if args.dry_run:
        modo = "simulación (no escribe en la base)"
    elif args.actualizar:
        modo = "actualiza los que ya existen"
    else:
        modo = "conserva los que ya existen"

    print(_SEP)
    print("Nomenclador odontológico de Córdoba -> catálogo de prestaciones")
    print(_SEP)
    print(f"  Base de datos : {_origen_seguro()}")
    print(f"  Modo          : {modo}")
    print(f"  Prácticas     : {len(NOMENCLADOR_CORDOBA)} códigos en la grilla")
    print(_SEP)

    try:
        if not args.dry_run:
            # Crea la tabla si la base es nueva (por ejemplo, un PostgreSQL recién
            # conectado); en SQLite ya existe desde el primer arranque de la app.
            init_db()
        with SessionLocal() as db:
            contadores = cargar_nomenclador(
                db,
                actualizar=args.actualizar,
                dry_run=args.dry_run,
                detalle=detalle,
            )
    except SQLAlchemyError as exc:  # pragma: no cover - falla de infraestructura
        print(_SEP)
        print(f"  No se pudo conectar o escribir en la base de datos: {exc}")
        return 1

    print(_SEP)
    if args.dry_run:
        print(
            f"  Simulación: {contadores['insertadas']} se insertarían y "
            f"{contadores['existentes']} ya existen. No se escribió nada."
        )
        return 0

    print(f"  Prestaciones insertadas con éxito: {contadores['insertadas']}")
    extras = ""
    if contadores["actualizadas"]:
        extras += f"   ·  Actualizadas: {contadores['actualizadas']}"
    if contadores["errores"]:
        extras += f"   ·  Con error: {contadores['errores']}"
    print(f"  Ya existían (sin cambios):         {contadores['existentes']}{extras}")
    print(f"  Total en el catálogo:              {contadores['total']}")
    print(_SEP)
    print(
        "  Panel: http://127.0.0.1:8000/  ->  "
        "Facturación y Caja > Prestaciones y aranceles"
    )

    return 1 if contadores["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
