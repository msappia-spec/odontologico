"""Lectura del **PDF de aranceles** del Colegio de Odontólogos de Córdoba.

El PDF oficial (``ARANCELES-GENERALES-23.pdf``) está armado como un listado a
tres bandas (código, práctica, honorario) y la posición de las columnas cambia
entre páginas, así que no se puede leer por coordenadas. En cambio se extrae el
texto con :mod:`pymupdf` y se reconstruye cada práctica con estas reglas:

* una línea que empieza con un código (``101``, ``40101``, ``11.02`` o
  ``130700``) abre una fila nueva;
* la descripción puede venir en esa misma línea o en la línea siguiente (el
  código ``11.02`` del PDF, por ejemplo, no entra en la primera columna);
* los importes aparecen después de la descripción, a veces en su propia línea
  (``$ 16.106``) y a veces pegados al final del texto
  (``...) $ 418.744``). Si hay más de un importe se usa el último, que es el
  honorario del profesional;
* las líneas de capítulo, los encabezados de tabla y los números de página no
  abren filas porque no cumplen el patrón de código + descripción.

El texto del PDF trae las ligaduras tipográficas del Calibri incrustado
(``ﬁ``, ``ﬂ``, ``ti``) y algunas letras acentuadas mapeadas a posiciones
equivocadas. Todas se corrigen con :data:`CORRECCIONES`, sin adivinar: son los
caracteres que efectivamente aparecen en el documento del Colegio.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

import pymupdf

from app.core.exceptions import ValidacionError

# Tamaño máximo del PDF (el oficial pesa ~2 MB). Se valida antes de abrirlo
# para no cargar en memoria un archivo que no corresponde.
MAX_BYTES_PDF = 10 * 1024 * 1024

# El PDF está firmado con ``%PDF-``; sirve como control rápido de formato.
_FIRMA_PDF = b"%PDF-"

# Caracteres mal mapeados por el Calibri incrustado del PDF. Se reemplazan por
# su texto real (las ligaduras primero, porque ocupan una sola posición).
CORRECCIONES = str.maketrans(
    {
        "\u019f": "ti",  # ligadura "ti"
        "\u0130": "fí",  # ligadura "fi" con acento
        "\u01a1": "tia",  # ligadura "tia"
        "\u00ec": "í",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\u00a7": "a",
        "\u0371": "c",
        "\u0378": "l",
        "\u037f": "s",
    }
)

# Código al inicio de la línea: 101, 40101, 130700 o el 11.02 del implante.
_RE_CODIGO = re.compile(r"^\s*(\d{2,6}(?:\.\d{2})?)(?:\s+(.*))?$")

# Importe pegado a la descripción. Se aceptan las dos formas del PDF:
# ``$ 1.567.109`` y ``1.567.109 $``. Los miles van con punto y los centavos con
# coma (formato es-AR).
_RE_MONTO = re.compile(
    r"\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)"
    r"|(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?)\s*\$"
)

# Línea que sólo trae un importe (sin el signo ``$``), como ``526.237``.
_RE_SOLO_MONTO = re.compile(
    r"^\s*\$?\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?)\s*\$?\s*$"
)


@dataclass
class ArancelPdf:
    """Una práctica leída del PDF, todavía sin relacionar con la base."""

    codigo: str
    descripcion: str
    valor: Optional[float]
    pagina: int
    linea: int


@dataclass
class _FilaPdf:
    """Fila en construcción mientras se recorre el texto del PDF."""

    codigo: str
    descripcion: str
    pagina: int
    linea: int
    esperando_descripcion: bool
    montos: List[float] = field(default_factory=list)


def limpiar_linea(linea: str) -> str:
    """Corrige el mapeo del PDF y normaliza los espacios de una línea."""
    return " ".join(linea.translate(CORRECCIONES).split())


def _importes_de(linea: str) -> List[float]:
    """Devuelve todos los importes que aparecen en la línea."""
    importes: List[float] = []
    for coincidencia in _RE_MONTO.finditer(linea):
        texto = coincidencia.group(1) or coincidencia.group(2)
        importes.append(float(texto.replace(".", "").replace(",", ".")))
    return importes


def _importe_suelto(linea: str) -> Optional[float]:
    """Devuelve el importe de una línea que sólo trae un número, si aplica."""
    coincidencia = _RE_SOLO_MONTO.match(linea)
    if coincidencia is None:
        return None
    return float(coincidencia.group(1).replace(".", "").replace(",", "."))


def _descripcion_valida(texto: str) -> bool:
    """¿La línea puede ser la descripción de una práctica?

    Las descripciones del PDF empiezan con mayúscula o con un ordinal
    (``1º PLACA...``). Así se descartan los encabezados de capítulo y los
    fragmentos sueltos como ``(12`` que podrían quedar entre columnas.
    """
    if not texto:
        return False
    if texto[0].isupper():
        return True
    return texto[0].isdigit() and len(texto) > 1 and texto[1] in "°º"


def _limpiar_descripcion(texto: str) -> str:
    """Quita los importes que quedaron dentro del texto y lo recorta."""
    texto = _RE_MONTO.sub("", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" -:;,")
    return texto[:200].rstrip()


def parsear_texto_aranceles(texto: str) -> List[ArancelPdf]:
    """Convierte el texto extraído del PDF en la lista de prácticas.

    Es una función pura (sin archivos ni base de datos) para poder probar las
    reglas del parser con un texto representativo. No lanza errores por filas
    incompletas: una práctica sin honorario queda con ``valor=None``.
    """
    filas: List[_FilaPdf] = []
    actual: Optional[_FilaPdf] = None
    numero = 0
    for pagina, bloque in enumerate(texto.split("\f"), start=1):
        for cruda in bloque.splitlines():
            numero += 1
            linea = limpiar_linea(cruda)
            if not linea:
                continue

            coincidencia = _RE_CODIGO.match(linea)
            if coincidencia is not None:
                codigo = coincidencia.group(1)
                descripcion = _limpiar_descripcion(coincidencia.group(2) or "")
                if not descripcion or _descripcion_valida(descripcion):
                    actual = _FilaPdf(
                        codigo=codigo,
                        descripcion=descripcion,
                        pagina=pagina,
                        linea=numero,
                        esperando_descripcion=not descripcion,
                    )
                    actual.montos.extend(_importes_de(linea))
                    filas.append(actual)
                    continue

            importes = _importes_de(linea)
            importe_suelto = _importe_suelto(linea)
            if importe_suelto is not None:
                importes = [importe_suelto]
            if importes:
                if actual is not None:
                    actual.montos.extend(importes)
                continue

            if (
                actual is not None
                and actual.esperando_descripcion
                and _descripcion_valida(linea)
            ):
                actual.descripcion = _limpiar_descripcion(linea)
                actual.esperando_descripcion = False
                continue

            # Cualquier otra línea (capítulo, encabezado de tabla, "A
            # CONVENIR") cierra la fila: los importes que vengan después no se
            # le atribuyen por error.
            actual = None

    return [
        ArancelPdf(
            codigo=fila.codigo,
            descripcion=fila.descripcion,
            valor=fila.montos[-1] if fila.montos else None,
            pagina=fila.pagina,
            linea=fila.linea,
        )
        for fila in filas
        if fila.descripcion
    ]


def leer_aranceles_pdf(contenido: bytes) -> List[ArancelPdf]:
    """Lee un PDF de aranceles y devuelve sus prácticas.

    Lanza :class:`ValidacionError` (HTTP **422**) si el archivo está vacío, no
    es un PDF, supera :data:`MAX_BYTES_PDF` o no contiene prácticas con código
    y honorario reconocibles.
    """
    if not contenido:
        raise ValidacionError(
            "El archivo está vacío: subí el PDF de aranceles del Colegio."
        )
    if len(contenido) > MAX_BYTES_PDF:
        raise ValidacionError(
            f"El PDF supera los {MAX_BYTES_PDF // (1024 * 1024)} MB: "
            "subí sólo el listado de aranceles."
        )
    if not contenido.lstrip().startswith(_FIRMA_PDF):
        raise ValidacionError(
            "El archivo no es un PDF: subí el listado de aranceles en formato .pdf."
        )

    try:
        with pymupdf.open(stream=contenido, filetype="pdf") as documento:
            texto = "\f".join(pagina.get_text("text") for pagina in documento)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError) as exc:
        raise ValidacionError(
            "No se pudo abrir el PDF: verificá que no esté dañado o protegido "
            "con contraseña."
        ) from exc

    filas = parsear_texto_aranceles(texto)
    if not filas:
        raise ValidacionError(
            "No se encontraron prácticas en el PDF: tiene que ser el listado de "
            "aranceles del Colegio (código, práctica y honorario)."
        )
    if not any(fila.valor is not None for fila in filas):
        raise ValidacionError(
            "El PDF no trae honorarios reconocibles: revisá que sea el listado "
            "de aranceles y no una tabla de referencia sin precios."
        )
    return filas
