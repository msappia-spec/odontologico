"""Importa los pacientes del sistema anterior (Access) desde una planilla Excel.

La planilla ``pacientes.xlsx`` que exporta Access trae una fila por paciente con
el **nombre completo** en una sola columna, la obra social por **código**, el
número de afiliado, el plan, el domicilio, el teléfono y la categoría
administrativa. Este script la carga en la tabla ``pacientes`` reutilizando los
servicios de la aplicación:

* crea las **obras sociales** que falten con el catálogo ``mutuales.xlsx``
  (``codMutual`` -> ``nomMutual``); el código ``0``/``PARTICULAR`` deja al
  paciente sin cobertura y un código desconocido también (se informa al final);
* crea las **categorías** que falten con ``categorias.xlsx``; las que dicen
  «MALO» quedan con aviso previo al turno, como el catálogo histórico;
* normaliza el **teléfono**: extrae el número de textos como
  ``+5493512169073LIDIA`` y, cuando no hay ninguno (``0`` o una anotación),
  guarda ``--telefono-sin-dato`` (por defecto ``000000``) para no perder al
  paciente; el resumen informa cuántos quedaron así;
* anexa el **plan** de la obra social al número de afiliado
  (``10-064083-11 (PLAN UNICO)``) porque el modelo no tiene un campo propio;
  se puede desactivar con ``--sin-plan``;
* es **idempotente**: saltea los pacientes cuya combinación nombre + teléfono
  ya existe en la base o ya apareció antes en la misma planilla.

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.importar_pacientes_excel access/pacientes.xlsx --dry-run
    python -m scripts.importar_pacientes_excel access/pacientes.xlsx
    python -m scripts.importar_pacientes_excel access/pacientes.xlsx --limite 50

Opciones::

    --mutuales RUTA         catálogo de obras sociales (por defecto, el
                            ``mutuales.xlsx`` de la misma carpeta)
    --categorias RUTA       catálogo de categorías (por defecto, el
                            ``categorias.xlsx`` de la misma carpeta)
    --telefono-sin-dato T   teléfono para las filas sin número (``000000``)
    --sin-plan              no anexa el plan al número de afiliado
    --limite N              procesa sólo las primeras N filas (para probar)
    --detalle               imprime una línea por paciente creado
    --dry-run               muestra qué haría sin escribir en la base

La base destino es la ``DATABASE_URL`` de la aplicación (el ``.env``): el
mismo script sirve para el PostgreSQL local y para la base de la nube.

Columnas
--------
Se buscan **por nombre** en la primera fila con contenido, así que el orden no
importa y las columnas de más se ignoran. Son obligatorias «nombre» y
«teléfono»; «obra social», «nro afiliado», «plan obra social», «domicilio»,
«categoria» y «dni» son opcionales. El «codigo» del sistema anterior no se
importa: el paciente no tiene un campo para ese identificador y usarlo como
DNI sería incorrecto (no todos los códigos son documentos).
"""

from __future__ import annotations

import argparse
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ValidacionError
from app.core.planilla_aranceles import leer_filas
from app.db.init_db import init_db
from app.db.session import SessionLocal, engine
from app.models.categoria_paciente import CategoriaPaciente
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.schemas.categoria_paciente import CategoriaPacienteCreate
from app.schemas.obra_social import ObraSocialCreate
from app.services import categoria_paciente_service, obra_social_service

logger = logging.getLogger(__name__)

_SEP = "=" * 78

#: Largo máximo del nombre del paciente (``Paciente.nombre``).
MAX_NOMBRE = 120
#: Largo máximo del número de afiliado (``Paciente.numero_afiliado``).
MAX_AFILIADO = 60
#: Largo máximo del domicilio (``Paciente.domicilio``).
MAX_DOMICILIO = 200
#: Largo máximo del teléfono (``Paciente.telefono``).
MAX_TELEFONO = 30
#: Cantidad de pacientes por transacción (evita una por fila en 10.000 filas).
TAMANO_LOTE = 500

#: Alias de los encabezados (en minúsculas y sin acentos).
_ENCABEZADOS: Dict[str, Tuple[str, ...]] = {
    "codigo": ("codigo", "cod", "id", "nro", "historia", "historia clinica"),
    "obra_social": ("obra social", "obra", "mutual", "mutualidad", "cobertura"),
    "nombre": (
        "nombre",
        "paciente",
        "nombre y apellido",
        "apellido y nombre",
        "apellidos y nombres",
    ),
    "numero_afiliado": (
        "nro afiliado",
        "numero afiliado",
        "n afiliado",
        "afiliado",
        "nro de afiliado",
    ),
    "plan": ("plan obra social", "plan de obra social", "plan"),
    "domicilio": ("domicilio", "direccion", "calle"),
    "telefono": ("telefono", "tel", "celular", "cel"),
    "categoria": ("categoria",),
    "dni": ("dni", "documento"),
}

#: Códigos de la planilla que significan «sin obra social».
_SIN_OBRA = frozenset({"", "0", "00", "PARTICULAR"})

_RE_ESPACIOS = re.compile(r"\s+")
_RE_PREFIJO_NUMERO = re.compile(r"^[+\d][\d\s\-().]*")
_RE_NO_ALFANUMERICO = re.compile(r"[^A-Z0-9]+")


@dataclass
class FilaPaciente:
    """Un paciente leído de la planilla, antes de escribirlo en la base."""

    linea: int
    nombre: str
    telefono: str
    obra_social_codigo: str = ""
    numero_afiliado: Optional[str] = None
    plan: Optional[str] = None
    domicilio: Optional[str] = None
    categoria: str = categoria_paciente_service.CODIGO_POR_DEFECTO
    dni: Optional[str] = None
    sin_telefono: bool = False
    error: Optional[str] = None


@dataclass
class Resumen:
    """Contadores del proceso, para el informe final."""

    leidos: int = 0
    creados: int = 0
    duplicados: int = 0
    sin_telefono: int = 0
    sin_obra_social: int = 0
    obras_creadas: int = 0
    categorias_creadas: int = 0
    sin_plan: int = 0
    errores: int = 0
    omitidos_limite: int = 0
    obras_desconocidas: Dict[str, int] = field(default_factory=dict)


def _sin_acentos(texto: object) -> str:
    """Pasa un texto a minúsculas y sin acentos (para comparar encabezados)."""
    plano = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in plano if not unicodedata.combining(c)).strip().lower()


def _clave(texto: str) -> str:
    """Clave de comparación de nombres (sin acentos ni espacios de más)."""
    return _RE_ESPACIOS.sub(" ", _sin_acentos(texto))


def _texto(valor: object) -> str:
    """Recorta y colapsa los espacios de una celda."""
    return _RE_ESPACIOS.sub(" ", str(valor or "")).strip()


def _recortar(texto: str, largo: int) -> str:
    """Recorta un texto al largo del campo sin cortar en medio de una palabra."""
    if len(texto) <= largo:
        return texto
    return texto[: largo - 1].rstrip(" ,;:-") + "…"


def _nombre_visible(texto: str) -> str:
    """Pasa un nombre que viene todo en mayúsculas a «Muy bueno»."""
    limpio = _texto(texto)
    return limpio.capitalize() if limpio.isupper() else limpio


def _columnas(fila: Sequence[str]) -> Optional[Dict[str, int]]:
    """Ubica las columnas de la planilla por el **nombre** de su encabezado.

    :returns: ``{campo: índice}``; ``None`` si la fila no es la cabecera
        (falta el nombre, que es el único dato imprescindible).
    """
    mapa: Dict[str, int] = {}
    for indice, celda in enumerate(fila):
        texto = _sin_acentos(celda)
        if not texto:
            continue
        for campo, alias in _ENCABEZADOS.items():
            if campo in mapa:
                continue
            if any(texto == nombre for nombre in alias):
                mapa[campo] = indice
                break
    if "nombre" in mapa:
        return mapa
    return None


def normalizar_telefono(valor: object) -> Optional[str]:
    """Devuelve el teléfono listo para guardar, o ``None`` si no hay número.

    Access mezcla números con anotaciones («NUERA DE R. BRITOS»), ceros de
    relleno y sufijos pegados (``+5493512169073LIDIA``). Se toma el primer
    tramo numérico, se quitan los separadores y se descartan los que no llegan
    al mínimo del esquema (6 caracteres).
    """
    texto = _texto(valor)
    if not texto:
        return None
    coincidencia = _RE_PREFIJO_NUMERO.match(texto)
    if coincidencia is None:
        return None
    numero = _RE_ESPACIOS.sub("", coincidencia.group(0))
    numero = re.sub(r"[().\-]", "", numero)
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) < 6:
        return None
    if len(numero) > MAX_TELEFONO:
        numero = numero[:MAX_TELEFONO]
    return numero


def _codigo_categoria(nombre: str, usados: Set[str]) -> str:
    """Arma un código válido (``[A-Z][A-Z0-9_]*``) a partir del nombre visible."""
    codigo = _RE_NO_ALFANUMERICO.sub("_", _sin_acentos(nombre).upper()).strip("_")
    if not codigo or not codigo[0].isalpha():
        codigo = f"C_{codigo}" if codigo else "SIN_CATEGORIZAR"
    base = codigo[:30]
    codigo = base
    sufijo = 2
    while codigo in usados:
        sufijo_texto = f"_{sufijo}"
        codigo = base[: 30 - len(sufijo_texto)] + sufijo_texto
        sufijo += 1
    return codigo


def _leer_catalogo(ruta: Path) -> List[Tuple[str, str]]:
    """Lee un catálogo de dos columnas (código y nombre) de un ``.xlsx``/``.csv``.

    La cabecera se busca en la primera fila con contenido y se ignoran las
    columnas de más. Devuelve ``[(codigo, nombre), ...]``.
    """
    filas = leer_filas(ruta.read_bytes(), ruta.name)
    resultado: List[Tuple[str, str]] = []
    for fila in filas:
        valores = [_texto(celda) for celda in fila]
        if not any(valores):
            continue
        if len(valores) >= 2 and valores[0].lower() in (
            "codigo",
            "cod",
            "codcategoria",
            "codmutual",
        ):
            continue
        if len(valores) >= 2 and valores[1]:
            resultado.append((valores[0], valores[1]))
    return resultado


def _origen_seguro() -> str:
    """Describe la base destino sin exponer usuario ni contraseña."""
    dialecto = engine.dialect.name
    if dialecto.startswith("sqlite"):
        return f"{dialecto} -> {engine.url.database}"
    return f"{dialecto} -> {engine.url.render_as_string(hide_password=True)}"


def _normalizar_codigo_obra(valor: str) -> str:
    """Normaliza el código de la mutual (``"01"`` y ``"1"`` son el mismo)."""
    texto = _texto(valor)
    return str(int(texto)) if texto.isdigit() else texto.upper()


def cargar_categorias(
    db: Session,
    catalogo: Sequence[Tuple[str, str]],
    resumen: Resumen,
    *,
    dry_run: bool = False,
) -> Dict[str, str]:
    """Crea las categorías que falten y devuelve ``{nombre normalizado: código}``.

    El catálogo histórico (``SIN_CATEGORIZAR``, ``EXCELENTE`` y ``MALO``) ya está
    sembrado; de ``categorias.xlsx`` se agregan las que falten. Las categorías
    «MALO» quedan con ``alerta = true`` (aviso previo al turno), igual que el
    comportamiento histórico. Una categoría existente **no se modifica**: la
    configuración de la secretaría manda.
    """
    if dry_run:
        # No se siembra: la simulación no debe escribir en la base. Si la tabla
        # todavía no existe (base creada antes del catálogo) se usan las
        # categorías históricas en memoria.
        if inspect(db.get_bind()).has_table(CategoriaPaciente.__tablename__):
            categorias = list(db.execute(select(CategoriaPaciente)).scalars())
        else:
            categorias = []
        if not categorias:
            categorias = categoria_paciente_service.categorias_por_defecto()
    else:
        categoria_paciente_service.sembrar_categorias_por_defecto(db)
        categorias = list(db.execute(select(CategoriaPaciente)).scalars())
    mapa = {_clave(categoria.nombre): categoria.codigo for categoria in categorias}
    usados = {categoria.codigo for categoria in categorias}

    for codigo_planilla, nombre in catalogo:
        clave = _clave(nombre)
        if clave in mapa:
            continue
        codigo = _codigo_categoria(nombre, usados)
        usados.add(codigo)
        mapa[clave] = codigo
        resumen.categorias_creadas += 1
        if dry_run:
            continue
        orden = int(codigo_planilla) if codigo_planilla.isdigit() else 0
        categoria_paciente_service.crear_categoria(
            db,
            CategoriaPacienteCreate(
                codigo=codigo,
                nombre=_recortar(_nombre_visible(nombre), 60),
                alerta="MALO" in codigo,
                activa=True,
                orden=min(max(orden, 0), 99),
            ),
        )
    return mapa


def cargar_obras_sociales(
    db: Session,
    catalogo: Sequence[Tuple[str, str]],
    resumen: Resumen,
    *,
    dry_run: bool = False,
) -> Dict[str, Optional[int]]:
    """Crea las obras sociales que falten y devuelve ``{código: id}``.

    El código ``0``/``PARTICULAR`` se mapea a ``None`` (sin cobertura). Si el
    nombre ya existe en el catálogo se reutiliza (sin distinguir mayúsculas), así
    que cargar la planilla dos veces no duplica coberturas.
    """
    existentes: Dict[str, ObraSocial] = {}
    if inspect(db.get_bind()).has_table(ObraSocial.__tablename__):
        existentes = {
            _clave(obra.nombre): obra
            for obra in db.execute(select(ObraSocial)).scalars()
        }
    mapa: Dict[str, Optional[int]] = {}
    for codigo_planilla, nombre in catalogo:
        codigo = _normalizar_codigo_obra(codigo_planilla)
        if codigo in _SIN_OBRA or _texto(nombre).upper() in _SIN_OBRA:
            mapa[codigo] = None
            continue
        clave = _clave(nombre)
        obra = existentes.get(clave)
        if obra is None:
            resumen.obras_creadas += 1
            if dry_run:
                mapa[codigo] = -1
                continue
            obra = obra_social_service.crear_obra_social(
                db,
                ObraSocialCreate(
                    nombre=_recortar(nombre, 120),
                    codigo=codigo[:30] or None,
                    activa=True,
                ),
            )
            existentes[clave] = obra
        mapa[codigo] = obra.id
    return mapa


def _armar_afiliado(
    numero: Optional[str], plan: Optional[str], *, con_plan: bool
) -> Optional[str]:
    """Combina el número de afiliado con el plan (no hay campo para el plan)."""
    numero = (numero or "").strip()
    plan = (plan or "").strip()
    if con_plan and plan:
        texto = f"{numero} ({plan})" if numero else f"Plan: {plan}"
        return _recortar(texto, MAX_AFILIADO)
    return _recortar(numero, MAX_AFILIADO) if numero else None


def leer_pacientes(
    filas: Sequence[Sequence[str]],
    mapa_categorias: Dict[str, str],
    *,
    con_plan: bool = True,
) -> List[FilaPaciente]:
    """Interpreta la planilla y devuelve una fila por paciente.

    Las filas sin nombre vuelven con ``error`` (no cortan la lectura) y las que
    no traen un teléfono usable quedan marcadas con ``sin_telefono`` para que
    quien escribe decida el reemplazo.
    """
    cabecera: Optional[Dict[str, int]] = None
    inicio = 0
    for posicion, fila in enumerate(filas):
        if not any(_texto(celda) for celda in fila):
            continue
        cabecera = _columnas(fila)
        if cabecera is None:
            raise ValidacionError(
                "La primera fila de la planilla tiene que traer el encabezado "
                "«nombre» (y, si existe, «teléfono», «obra social», "
                "«nro afiliado», «plan obra social», «domicilio» y «categoria»)."
            )
        inicio = posicion + 1
        break
    if cabecera is None:
        raise ValidacionError("La planilla no tiene filas: revisá que no esté vacía.")

    def valor(fila: Sequence[str], campo: str) -> str:
        indice = cabecera.get(campo)
        if indice is None or indice >= len(fila):
            return ""
        return _texto(fila[indice])

    pacientes: List[FilaPaciente] = []
    for posicion, fila in enumerate(filas[inicio:], start=inicio + 1):
        nombre = valor(fila, "nombre")
        if not nombre:
            if any(_texto(celda) for celda in fila):
                pacientes.append(
                    FilaPaciente(
                        linea=posicion,
                        nombre="",
                        telefono="",
                        error="la fila no tiene nombre",
                    )
                )
            continue
        telefono = normalizar_telefono(valor(fila, "telefono"))
        categoria_nombre = valor(fila, "categoria") or "SIN CATEGORIZAR"
        plan = valor(fila, "plan")
        pacientes.append(
            FilaPaciente(
                linea=posicion,
                nombre=_recortar(nombre, MAX_NOMBRE),
                telefono=telefono or "",
                obra_social_codigo=_normalizar_codigo_obra(
                    valor(fila, "obra_social")
                ),
                numero_afiliado=_armar_afiliado(
                    valor(fila, "numero_afiliado"), plan, con_plan=con_plan
                ),
                plan=plan or None,
                domicilio=_recortar(valor(fila, "domicilio"), MAX_DOMICILIO) or None,
                categoria=mapa_categorias.get(
                    _clave(categoria_nombre),
                    categoria_paciente_service.CODIGO_POR_DEFECTO,
                ),
                dni=valor(fila, "dni") or None,
                sin_telefono=telefono is None,
            )
        )
    return pacientes


def _clave_paciente(nombre: str, telefono: str) -> Tuple[str, str]:
    """Identificador blando para no duplicar pacientes en una recarga."""
    return (_clave(nombre), re.sub(r"\D", "", telefono or ""))


def _insertar(
    db: Session,
    lote: List[Paciente],
    resumen: Resumen,
    detalle: bool,
) -> None:
    """Inserta un lote y, si falla, aísla la fila culpable para no perder todo."""
    db.add_all(lote)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        for paciente in lote:
            db.add(paciente)
            try:
                db.commit()
            except SQLAlchemyError as exc:
                db.rollback()
                resumen.errores += 1
                print(f"  ! {paciente.nombre}: no se pudo guardar ({exc})")
                continue
            resumen.creados += 1
            if detalle:
                print(f"  + {paciente.nombre} · {paciente.telefono}")
        return

    resumen.creados += len(lote)
    if detalle:
        for paciente in lote:
            print(f"  + {paciente.nombre} · {paciente.telefono}")


def cargar_pacientes(
    db: Session,
    pacientes: Sequence[FilaPaciente],
    mapa_obras: Dict[str, Optional[int]],
    resumen: Resumen,
    *,
    telefono_sin_dato: str,
    dry_run: bool = False,
    detalle: bool = False,
) -> None:
    """Escribe los pacientes nuevos, salteando los ya existentes."""
    existentes: Set[Tuple[str, str]] = set()
    if inspect(db.get_bind()).has_table(Paciente.__tablename__):
        existentes = {
            _clave_paciente(nombre, telefono or "")
            for nombre, telefono in db.execute(
                select(Paciente.nombre, Paciente.telefono)
            )
        }
    vistos: Set[Tuple[str, str]] = set()
    lote: List[Paciente] = []

    for fila in pacientes:
        if fila.error:
            resumen.errores += 1
            print(f"  ! línea {fila.linea}: {fila.error}")
            continue
        resumen.leidos += 1
        if fila.sin_telefono:
            resumen.sin_telefono += 1
        if not fila.plan:
            resumen.sin_plan += 1

        telefono = fila.telefono or telefono_sin_dato
        clave = _clave_paciente(fila.nombre, telefono)
        if clave in existentes or clave in vistos:
            resumen.duplicados += 1
            continue
        vistos.add(clave)

        obra_id = mapa_obras.get(fila.obra_social_codigo)
        if obra_id is None:
            resumen.sin_obra_social += 1
            if fila.obra_social_codigo not in _SIN_OBRA:
                codigo = fila.obra_social_codigo or "(vacío)"
                resumen.obras_desconocidas[codigo] = (
                    resumen.obras_desconocidas.get(codigo, 0) + 1
                )

        if dry_run:
            # La simulación sólo cuenta: no arma 10.000 objetos que no se usan.
            resumen.creados += 1
            continue
        lote.append(
            Paciente(
                nombre=fila.nombre,
                telefono=telefono,
                dni=fila.dni,
                obra_social_id=obra_id if obra_id and obra_id > 0 else None,
                numero_afiliado=fila.numero_afiliado,
                domicilio=fila.domicilio,
                categoria=fila.categoria,
                categoria_observaciones=None,
            )
        )
        if len(lote) >= TAMANO_LOTE:
            _insertar(db, lote, resumen, detalle)
            lote = []

    if lote:
        _insertar(db, lote, resumen, detalle)


def _imprimir_resumen(resumen: Resumen, *, dry_run: bool) -> None:
    """Muestra los contadores del proceso en el formato de los scripts."""
    print(_SEP)
    if dry_run:
        print(
            f"  Simulación: {resumen.creados} se insertarían y "
            f"{resumen.duplicados} ya existen. No se escribió nada."
        )
    else:
        print(f"  Pacientes creados:                {resumen.creados}")
    print(f"  Pacientes leídos:                  {resumen.leidos}")
    print(f"  Ya existentes (salteados):         {resumen.duplicados}")
    if resumen.sin_telefono:
        print(f"  Sin teléfono (se usó el reemplazo): {resumen.sin_telefono}")
    print(f"  Sin obra social:                   {resumen.sin_obra_social}")
    if resumen.sin_plan:
        print(f"  Sin plan de obra social:           {resumen.sin_plan}")
    if resumen.categorias_creadas:
        print(f"  Categorías nuevas:                 {resumen.categorias_creadas}")
    if resumen.obras_creadas:
        print(f"  Obras sociales nuevas:             {resumen.obras_creadas}")
    if resumen.obras_desconocidas:
        detalle = ", ".join(
            f"{codigo} ({cantidad})"
            for codigo, cantidad in sorted(resumen.obras_desconocidas.items())
        )
        print(f"  Códigos de obra desconocidos:      {detalle}")
    if resumen.errores:
        print(f"  Con error:                         {resumen.errores}")
    print(_SEP)


def _leer_planilla(ruta: Path, descripcion: str) -> bytes:
    """Lee un archivo de la planilla y traduce el error de disco a ``ValidacionError``."""
    try:
        return ruta.read_bytes()
    except OSError as exc:
        raise ValidacionError(
            f"No se pudo leer {descripcion} «{ruta}»: {exc.strerror or exc}"
        ) from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Punto de entrada del importador de pacientes."""
    parser = argparse.ArgumentParser(
        description="Importa los pacientes del sistema anterior desde pacientes.xlsx.",
    )
    parser.add_argument(
        "archivo", help="planilla de pacientes exportada desde Access (.xlsx o .csv)"
    )
    parser.add_argument(
        "--mutuales",
        help="catálogo de obras sociales (por defecto, mutuales.xlsx junto a la planilla)",
    )
    parser.add_argument(
        "--categorias",
        help="catálogo de categorías (por defecto, categorias.xlsx junto a la planilla)",
    )
    parser.add_argument(
        "--telefono-sin-dato",
        default="000000",
        help="teléfono que se guarda cuando la fila no trae un número (000000)",
    )
    parser.add_argument(
        "--sin-plan",
        action="store_true",
        help="no anexa el plan de la obra social al número de afiliado",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=0,
        help="procesa sólo las primeras N filas (0 = todas; útil para probar)",
    )
    parser.add_argument(
        "--detalle",
        action="store_true",
        help="imprime una línea por paciente creado",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="muestra qué haría sin escribir en la base",
    )
    args = parser.parse_args(argv)

    ruta = Path(args.archivo).expanduser()
    carpeta = ruta.parent
    ruta_mutuales = (
        Path(args.mutuales).expanduser()
        if args.mutuales
        else carpeta / "mutuales.xlsx"
    )
    ruta_categorias = (
        Path(args.categorias).expanduser()
        if args.categorias
        else carpeta / "categorias.xlsx"
    )

    telefono_sin_dato = args.telefono_sin_dato.strip()
    if not 6 <= len(telefono_sin_dato) <= MAX_TELEFONO:
        print(_SEP)
        print("  El teléfono de reemplazo debe tener entre 6 y 30 caracteres.")
        return 1
    if args.limite < 0:
        print(_SEP)
        print("  --limite no puede ser negativo.")
        return 1

    try:
        contenido = _leer_planilla(ruta, "la planilla de pacientes")
        catalogo_mutuales = (
            _leer_catalogo(ruta_mutuales) if ruta_mutuales.is_file() else []
        )
        catalogo_categorias = (
            _leer_catalogo(ruta_categorias) if ruta_categorias.is_file() else []
        )
        filas = leer_filas(contenido, ruta.name)
    except ValidacionError as exc:
        print(_SEP)
        print(f"  {exc.detail}")
        return 1

    if args.dry_run:
        modo = "simulación (no escribe en la base)"
    else:
        modo = "inserta los pacientes que faltan"
    if args.sin_plan:
        modo += " · sin plan en el afiliado"

    print(_SEP)
    print("Pacientes del sistema anterior (Access) -> tabla de pacientes")
    print(_SEP)
    print(f"  Planilla      : {ruta.name} ({len(filas) - 1} filas de datos)")
    print(f"  Base de datos : {_origen_seguro()}")
    print(f"  Modo          : {modo}")
    print(
        f"  Catálogos     : mutuales {'sí' if catalogo_mutuales else 'no'}, "
        f"categorías {'sí' if catalogo_categorias else 'no'}"
    )
    print(_SEP)

    resumen = Resumen()
    try:
        if not args.dry_run:
            # Crea la tabla si la base es nueva y siembra las categorías.
            init_db()
        with SessionLocal() as db:
            mapa_categorias = cargar_categorias(
                db, catalogo_categorias, resumen, dry_run=args.dry_run
            )
            mapa_obras = cargar_obras_sociales(
                db, catalogo_mutuales, resumen, dry_run=args.dry_run
            )
            pacientes = leer_pacientes(
                filas, mapa_categorias, con_plan=not args.sin_plan
            )
            if args.limite:
                resumen.omitidos_limite = max(len(pacientes) - args.limite, 0)
                pacientes = pacientes[: args.limite]
            cargar_pacientes(
                db,
                pacientes,
                mapa_obras,
                resumen,
                telefono_sin_dato=telefono_sin_dato,
                dry_run=args.dry_run,
                detalle=args.detalle,
            )
    except SQLAlchemyError as exc:  # pragma: no cover - falla de infraestructura
        print(_SEP)
        print(f"  No se pudo conectar o escribir en la base de datos: {exc}")
        return 1
    except AppError as exc:  # pragma: no cover - conflicto de unicidad
        print(_SEP)
        print(f"  {exc.detail}")
        return 1

    _imprimir_resumen(resumen, dry_run=args.dry_run)
    if resumen.omitidos_limite:
        print(f"  (Se omitieron {resumen.omitidos_limite} filas por --limite.)")
    if resumen.sin_telefono and not args.dry_run:
        print(
            f"  Nota: las filas sin teléfono se guardaron con «{telefono_sin_dato}»; "
            "completalas desde el panel cuando tengas el número."
        )
    if not args.dry_run:
        print("  Panel: http://127.0.0.1:8000/  ->  Pacientes")
    return 1 if resumen.errores else 0


if __name__ == "__main__":
    raise SystemExit(main())

