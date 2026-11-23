"""Actualiza el catálogo de prestaciones con el nomenclador odontológico en Excel.

La grilla oficial del nomenclador se publica como una planilla (``.xlsx`` o
``.csv``) con cuatro columnas: **Capítulo**, **Código**, **Descripción** y
**Arancel ($)**. Este script la carga en la tabla ``prestaciones``: crea las
prácticas que faltan, actualiza descripción y valor base de las que ya están y,
si se pide con ``--desactivar-faltantes``, da de baja lógica las que ya no
figuran en la grilla.

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.importar_nomenclador_excel "C:\\Users\\Marcos\\Downloads\\nomenclador_odontologico.xlsx"

Opciones::

    python -m scripts.importar_nomenclador_excel planilla.xlsx --dry-run
    python -m scripts.importar_nomenclador_excel planilla.xlsx --desactivar-faltantes
    python -m scripts.importar_nomenclador_excel planilla.xlsx --solo-nuevas

    --dry-run               muestra qué haría sin escribir en la base
    --solo-nuevas           inserta las que faltan y no toca las existentes
    --desactivar-faltantes  da de baja lógica las que no están en la planilla
    --sin-detalle           no imprime una línea por código (sólo el resumen)

Columnas
--------
Se buscan **por nombre** en la primera fila con contenido (``Capítulo``,
``Código``, ``Descripción`` y ``Arancel``), así que el orden no importa y las
columnas de más se ignoran. Si no aparecen el código y el arancel, el script
corta **antes** de tocar la base: cargar una planilla equivocada sería peor que
no cargar nada. El ``.xlsx`` se lee con ``zipfile`` + XML (biblioteca estándar,
sin ``openpyxl``), igual que el resto de las planillas del panel.

Aranceles no publicados
-----------------------
La grilla trae textos como ``No publicado``, ``A convenir``, ``No indicado`` o
``Según tratamiento``. Esas prácticas se cargan igual, con ``valor_base = 0``
(el nomenclador no publica precio: lo define la secretaría en el panel), y se
cuentan aparte en el resumen. Si la práctica **ya existía** con un precio
cargado, un arancel no numérico **no** lo pisa.

Conexión
--------
Como el resto de los scripts, usa ``SessionLocal`` (la ``DATABASE_URL`` de la
aplicación): funciona igual contra el SQLite local (``odontologico.db``) y
contra PostgreSQL. ``init_db()`` crea la tabla si la base es nueva.

Idempotencia
------------
El **código de nomenclador identifica** la práctica (índice único
``uq_prestacion_codigo``) y se compara de forma tolerante
(:func:`~app.core.planilla_aranceles.canonizar_codigo`: sin espacios, puntos,
guiones ni mayúsculas), así que volver a correr el mismo archivo no duplica
nada: informa «sin cambios». Un código repetido dentro de la propia planilla se
informa como error y se conserva la primera aparición.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# Permite ejecutar el script también como archivo
# (``python scripts/importar_nomenclador_excel.py``): en ese caso ``sys.path[0]``
# es ``scripts/`` y ``from app.core...`` fallaría con
# ``ModuleNotFoundError: No module named 'app'``. Con el bootstrap, ambas formas
# (``-m scripts...`` y ruta directa) funcionan desde la raíz del proyecto.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402 - va después del bootstrap
from sqlalchemy.exc import IntegrityError, SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.exceptions import AppError, ConflictoError, ValidacionError  # noqa: E402
from app.core.money import redondear_monto  # noqa: E402
from app.core.planilla_aranceles import (  # noqa: E402
    canonizar_codigo,
    leer_filas,
    parsear_monto,
)
from app.db.init_db import init_db  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.prestacion import Prestacion  # noqa: E402

_SEP = "-" * 72

# Encabezados aceptados para cada columna (sin acentos, en minúsculas y sin el
# sufijo de unidades: «Arancel ($)» se reconoce por el prefijo ``arancel``).
_ENCABEZADOS: Dict[str, Tuple[str, ...]] = {
    "capitulo": ("capitulo", "rubro", "grupo", "seccion"),
    "codigo": ("codigo", "cod", "nomenclador"),
    "descripcion": ("descripcion", "detalle", "practica", "prestacion"),
    "arancel": ("arancel", "valor", "precio", "honorario", "importe"),
}

# Textos de la grilla que significan «sin arancel publicado».
_SIN_ARANCEL = frozenset(
    {"no publicado", "no indicado", "a convenir", "sin arancel", "segun tratamiento"}
)

# Límites del modelo ``Prestacion`` (``String(30)`` y ``String(200)``).
MAX_CODIGO = 30
MAX_DESCRIPCION = 200


@dataclass(frozen=True)
class PracticaPlanilla:
    """Una práctica leída de la planilla, antes de escribirla en el catálogo.

    ``valor`` es ``None`` cuando la grilla no publica arancel para esa práctica
    (o cuando la celda está vacía). Si no se pudo interpretar la fila, ``error``
    trae el motivo y la práctica **no** se escribe en la base.
    """

    linea: int
    codigo: str
    descripcion: str
    valor: Optional[float]
    arancel: str = ""
    error: Optional[str] = None


def _sin_acentos(texto: object) -> str:
    """Pasa un texto a minúsculas y sin acentos (para comparar encabezados)."""
    plano = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in plano if not unicodedata.combining(c)).strip().lower()


def _columnas(fila: Sequence[str]) -> Optional[Dict[str, int]]:
    """Ubica las columnas de la planilla por el **nombre** de su encabezado.

    :returns: ``{campo: índice}`` con ``capitulo``, ``codigo``, ``descripcion``
        y/o ``arancel``; ``None`` si la fila no es la cabecera del nomenclador
        (no están el código y el arancel, que son los datos indispensables).
    """
    mapa: Dict[str, int] = {}
    for indice, celda in enumerate(fila):
        texto = _sin_acentos(celda)
        if not texto:
            continue
        for campo, alias in _ENCABEZADOS.items():
            if campo in mapa:
                continue
            if any(texto == nombre or texto.startswith(nombre) for nombre in alias):
                mapa[campo] = indice
                break
    if "codigo" in mapa and "arancel" in mapa:
        return mapa
    return None


def _descripcion(capitulo: str, descripcion: str) -> str:
    """Arma la descripción que se guarda: ``Capítulo: práctica``.

    Igual que el seed del nomenclador, conserva el capítulo porque es la forma
    en que la obra social presenta y audita las prácticas. Se recorta a
    ``String(200)`` sin cortar en medio de una palabra.
    """
    capitulo = " ".join(str(capitulo or "").split())
    descripcion = " ".join(str(descripcion or "").split())
    if not descripcion:
        return ""
    texto = f"{capitulo}: {descripcion}" if capitulo else descripcion
    if len(texto) <= MAX_DESCRIPCION:
        return texto
    return texto[: MAX_DESCRIPCION - 1].rstrip(" ,;:-") + "…"


def _valor_arancel(texto: str) -> Tuple[Optional[float], Optional[str]]:
    """Interpreta la celda del arancel como ``(valor, error)``.

    ``None`` en el valor significa **sin precio**: la celda está vacía o trae un
    texto de la grilla (``No publicado``, ``A convenir``…). Un texto con dígitos
    que no se entiende (por ejemplo ``$ 1.2.3``) vuelve como error para que se
    corrija la planilla, en lugar de cargar un arancel inventado.
    """
    limpio = " ".join(str(texto or "").split())
    if not limpio:
        return None, None
    try:
        return parsear_monto(limpio), None
    except ValueError:
        if _sin_acentos(limpio) in _SIN_ARANCEL or not any(
            caracter.isdigit() for caracter in limpio
        ):
            return None, None
        return None, f"no se entiende el arancel «{limpio}»"


def _pesos(valor: float) -> str:
    """Formatea un importe como ``$ 18.000,00`` (convención es-AR)."""
    texto = f"{valor:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return "$ " + texto.replace("\u00a0", ".")


def _origen_seguro() -> str:
    """Describe la base destino sin exponer usuario ni contraseña."""
    dialecto = engine.dialect.name
    if dialecto.startswith("sqlite"):
        return f"{dialecto} -> {engine.url.database}"
    return f"{dialecto} -> {engine.url.render_as_string(hide_password=True)}"


def leer_practicas(
    contenido: bytes, nombre_archivo: Optional[str] = None
) -> List[PracticaPlanilla]:
    """Interpreta la planilla del nomenclador y devuelve una fila por práctica.

    La cabecera se busca en la primera fila con contenido y cada dato se toma de
    la columna que corresponda **por nombre**, así que el archivo puede venir en
    cualquier orden (o con columnas de más, que se ignoran). Las filas con
    problemas vuelven con ``error`` (no cortan la lectura) y los códigos
    repetidos dentro del propio archivo se informan conservando el primero.

    :raises ValidacionError: si el archivo no tiene la cabecera esperada o no
        trae ninguna práctica debajo.
    """
    filas = leer_filas(contenido, nombre_archivo)

    columnas: Optional[Dict[str, int]] = None
    inicio = 0
    for posicion, fila in enumerate(filas):
        if not any(str(celda).strip() for celda in fila):
            continue
        columnas = _columnas(fila)
        if columnas is None:
            raise ValidacionError(
                "La primera fila de la planilla tiene que traer los encabezados "
                "«Capítulo», «Código», «Descripción» y «Arancel»: revisá que sea "
                "la grilla del nomenclador y volvé a intentar."
            )
        inicio = posicion + 1
        break
    if columnas is None:
        raise ValidacionError(
            "La planilla no tiene filas: revisá que el archivo no esté vacío."
        )

    practicas: List[PracticaPlanilla] = []
    vistos: Dict[str, PracticaPlanilla] = {}
    for posicion, fila in enumerate(filas[inicio:], start=inicio + 1):
        valores = [str(celda).strip() for celda in fila]
        if not any(valores):
            continue

        def celda(campo: str) -> str:
            indice = columnas.get(campo)  # type: ignore[union-attr]
            if indice is None or indice >= len(valores):
                return ""
            return valores[indice]

        codigo = celda("codigo")
        descripcion = _descripcion(celda("capitulo"), celda("descripcion"))
        arancel = celda("arancel")

        if not codigo:
            practicas.append(
                PracticaPlanilla(
                    posicion, "", descripcion, None, arancel, "falta el código"
                )
            )
            continue
        if len(codigo) > MAX_CODIGO:
            practicas.append(
                PracticaPlanilla(
                    posicion,
                    codigo,
                    descripcion,
                    None,
                    arancel,
                    f"el código supera los {MAX_CODIGO} caracteres",
                )
            )
            continue
        if not descripcion:
            practicas.append(
                PracticaPlanilla(
                    posicion, codigo, "", None, arancel, "falta la descripción"
                )
            )
            continue

        valor, error = _valor_arancel(arancel)
        practica = PracticaPlanilla(posicion, codigo, descripcion, valor, arancel, error)
        clave = canonizar_codigo(codigo)
        anterior = vistos.get(clave)
        if anterior is not None:
            practicas.append(
                PracticaPlanilla(
                    posicion,
                    codigo,
                    descripcion,
                    valor,
                    arancel,
                    f"el código {codigo} ya apareció en la línea {anterior.linea}: "
                    "se conserva la primera aparición",
                )
            )
            continue
        vistos[clave] = practica
        practicas.append(practica)

    if not practicas:
        raise ValidacionError(
            "La planilla no tiene prácticas debajo de los encabezados: revisá el "
            "archivo del nomenclador."
        )
    return practicas


def cargar_practicas(
    db: Session,
    practicas: Sequence[PracticaPlanilla],
    *,
    solo_nuevas: bool = False,
    desactivar_faltantes: bool = False,
    dry_run: bool = False,
    detalle: bool = True,
) -> Dict[str, int]:
    """Escribe en el catálogo las prácticas leídas de la planilla.

    :param db: sesión de SQLAlchemy (la que entrega ``SessionLocal``).
    :param practicas: filas devueltas por :func:`leer_practicas`.
    :param solo_nuevas: inserta las que faltan y no toca las existentes (útil
        cuando la secretaría ya ajustó precios a mano desde el panel).
    :param desactivar_faltantes: baja **lógica** de las prácticas que no figuran
        en la planilla (se conservan: sus aranceles y facturas las referencian).
    :param dry_run: simula la carga sin escribir en la base.
    :param detalle: imprime una línea por código.
    :returns: contadores ``nuevas``, ``actualizadas``, ``reactivadas``,
        ``sin_cambios``, ``desactivadas``, ``sin_precio``, ``errores`` y
        ``total`` (filas del catálogo al terminar).
    """
    catalogo: Dict[str, Prestacion] = {
        canonizar_codigo(fila.codigo_nomenclador): fila
        for fila in db.execute(select(Prestacion)).scalars().all()
    }
    contadores = {
        "nuevas": 0,
        "actualizadas": 0,
        "reactivadas": 0,
        "sin_cambios": 0,
        "desactivadas": 0,
        "sin_precio": sum(1 for fila in practicas if fila.valor is None and not fila.error),
        "errores": 0,
        "total": len(catalogo),
    }
    hubo_cambios = False
    en_planilla: Set[str] = set()

    for practica in practicas:
        if practica.error:
            contadores["errores"] += 1
            if detalle:
                print(f"  ! línea {practica.linea}  ERROR: {practica.error}")
            continue

        clave = canonizar_codigo(practica.codigo)
        en_planilla.add(clave)
        existente = catalogo.get(clave)

        if existente is None:
            contadores["nuevas"] += 1
            if not dry_run:
                nueva = Prestacion(
                    codigo_nomenclador=practica.codigo.upper(),
                    descripcion=practica.descripcion,
                    valor_base=redondear_monto(practica.valor or 0),
                    activa=True,
                )
                db.add(nueva)
                catalogo[clave] = nueva
                hubo_cambios = True
            if detalle:
                print(
                    f"  + {practica.codigo:<8} "
                    f"{'se insertaría ' if dry_run else 'insertada    '} "
                    f"{_pesos(practica.valor or 0):>14}  {practica.descripcion}"
                )
            continue

        if solo_nuevas:
            contadores["sin_cambios"] += 1
            continue

        cambia_descripcion = existente.descripcion.strip() != practica.descripcion
        cambia_valor = practica.valor is not None and redondear_monto(
            existente.valor_base
        ) != redondear_monto(practica.valor)
        reactivar = not existente.activa
        if not (cambia_descripcion or cambia_valor or reactivar):
            contadores["sin_cambios"] += 1
            continue

        contadores["actualizadas"] += 1
        if reactivar:
            contadores["reactivadas"] += 1
        if not dry_run:
            if cambia_descripcion:
                existente.descripcion = practica.descripcion
            if cambia_valor:
                existente.valor_base = redondear_monto(practica.valor)
            if reactivar:
                existente.activa = True
            hubo_cambios = True
        if detalle:
            valor = practica.valor if cambia_valor else existente.valor_base
            nota = " (reactivada)" if reactivar else ""
            print(
                f"  ~ {practica.codigo:<8} "
                f"{'se actualizaría ' if dry_run else 'actualizada    '} "
                f"{_pesos(valor or 0):>14}{nota}  {practica.descripcion}"
            )

    # Baja lógica de las prácticas que ya no figuran en la grilla. Es lógica (no
    # se borra la fila) porque los aranceles por cobertura y las facturas ya
    # emitidas la referencian.
    if desactivar_faltantes:
        for clave, existente in catalogo.items():
            if clave in en_planilla or not existente.activa:
                continue
            contadores["desactivadas"] += 1
            if not dry_run:
                existente.activa = False
                hubo_cambios = True
            if detalle:
                print(
                    f"  - {existente.codigo_nomenclador:<8} "
                    f"{'se daría de baja ' if dry_run else 'dada de baja    '} "
                    f"{_pesos(existente.valor_base):>14}  {existente.descripcion}"
                )

    if hubo_cambios:
        try:
            db.flush()  # asigna los id de las nuevas antes de informar
            db.commit()
        except IntegrityError as exc:  # índice único uq_prestacion_codigo
            db.rollback()
            raise ConflictoError(
                "Otra ejecución (o el panel) cargó el mismo código al mismo "
                "tiempo: volvé a intentar la importación."
            ) from exc

    contadores["total"] = db.execute(
        select(func.count()).select_from(Prestacion)
    ).scalar_one()
    return contadores


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Punto de entrada: lee la planilla, carga el catálogo y resume el resultado."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.importar_nomenclador_excel",
        description=(
            "Actualiza la tabla de prestaciones con la planilla del nomenclador "
            "odontológico: crea las prácticas que faltan y refresca descripción "
            "y valor base de las que ya existen (idempotente por código)."
        ),
    )
    parser.add_argument(
        "archivo", help="planilla del nomenclador en .xlsx (o .csv)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="muestra qué haría sin escribir en la base",
    )
    parser.add_argument(
        "--solo-nuevas",
        action="store_true",
        help="inserta las que faltan y no toca las existentes",
    )
    parser.add_argument(
        "--desactivar-faltantes",
        action="store_true",
        help="da de baja lógica las prácticas que no están en la planilla",
    )
    parser.add_argument(
        "--sin-detalle",
        action="store_true",
        help="no imprime una línea por código (sólo la cabecera y el resumen)",
    )
    args = parser.parse_args(argv)

    ruta = Path(args.archivo).expanduser()
    try:
        contenido = ruta.read_bytes()
    except OSError as exc:
        print(_SEP)
        print(f"  No se pudo leer la planilla: {ruta}")
        print(f"  {exc.strerror or exc}")
        return 1

    try:
        practicas = leer_practicas(contenido, ruta.name)
    except ValidacionError as exc:
        print(_SEP)
        print(f"  {exc.detail}")
        return 1

    if args.dry_run:
        modo = "simulación (no escribe en la base)"
    elif args.solo_nuevas:
        modo = "inserta sólo las que faltan"
    else:
        modo = "crea y actualiza el catálogo"
    if args.desactivar_faltantes:
        modo += " · da de baja las que no figuran"

    detalle = not args.sin_detalle
    validas = sum(1 for practica in practicas if not practica.error)

    print(_SEP)
    print("Nomenclador odontológico en Excel -> catálogo de prestaciones")
    print(_SEP)
    print(f"  Planilla      : {ruta.name}")
    print(f"  Base de datos : {_origen_seguro()}")
    print(f"  Modo          : {modo}")
    print(
        f"  Prácticas     : {validas} en la planilla "
        f"({len(practicas) - validas} con error)"
    )
    print(_SEP)

    try:
        if not args.dry_run:
            # Crea la tabla si la base es nueva (por ejemplo, un PostgreSQL
            # recién conectado); en SQLite ya existe desde el primer arranque.
            init_db()
        with SessionLocal() as db:
            contadores = cargar_practicas(
                db,
                practicas,
                solo_nuevas=args.solo_nuevas,
                desactivar_faltantes=args.desactivar_faltantes,
                dry_run=args.dry_run,
                detalle=detalle,
            )
    except SQLAlchemyError as exc:  # pragma: no cover - falla de infraestructura
        print(_SEP)
        print(f"  No se pudo conectar o escribir en la base de datos: {exc}")
        return 1
    except AppError as exc:  # pragma: no cover - conflicto de unicidad
        print(_SEP)
        print(f"  {exc.detail}")
        return 1

    print(_SEP)
    if args.dry_run:
        print(
            f"  Simulación: {contadores['nuevas']} se insertarían, "
            f"{contadores['actualizadas']} se actualizarían y "
            f"{contadores['desactivadas']} se darían de baja. No se escribió nada."
        )
        return 0

    print(f"  Prestaciones nuevas:               {contadores['nuevas']}")
    print(f"  Actualizadas:                      {contadores['actualizadas']}")
    if contadores["reactivadas"]:
        print(f"    · reactivadas:                   {contadores['reactivadas']}")
    if contadores["desactivadas"]:
        print(f"  Dadas de baja (no están en Excel): {contadores['desactivadas']}")
    print(f"  Sin cambios:                       {contadores['sin_cambios']}")
    print(f"  Sin arancel publicado (valor 0):   {contadores['sin_precio']}")
    if contadores["errores"]:
        print(f"  Con error:                         {contadores['errores']}")
    print(f"  Total en el catálogo:              {contadores['total']}")
    print(_SEP)
    print(
        "  Panel: http://127.0.0.1:8000/  ->  "
        "Facturación y Caja > Prestaciones y aranceles"
    )

    return 1 if contadores["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
