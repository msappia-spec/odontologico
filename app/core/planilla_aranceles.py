"""Lectura y escritura de las planillas de aranceles por obra social (Módulo 3).

La secretaría negocia el precio de cada práctica con la cobertura. Cuando el
convenio llega como una grilla de códigos (cientos de filas), cargarlo a mano no
tiene sentido: este módulo concentra el formato de la planilla y su lectura, **sin
dependencias externas** (sólo biblioteca estándar), para que el importador masivo
funcione igual en el consultorio y en la nube.

Formato
-------
Dos columnas fijas: ``codigo`` y ``precio_cobertura``. Se emite en ``.csv``
(separador ``;`` + BOM, el que Excel abre bien en es-AR) y en ``.xlsx`` (Excel
moderno, armado con ``zipfile`` + XML: no hace falta ``openpyxl``).

Lectura tolerante
-----------------
Se acepta el archivo tal como sale de Excel:

* ``.csv`` con separador ``;``, ``,`` o tabulación, en UTF-8 (con o sin BOM) o
  Windows-1252 (``cp1252``), que es lo que suele guardar Excel es-AR.
* ``.xlsx`` (se detecta por la firma ``PK`` del archivo ZIP y también por la
  extensión). El ``.xls`` viejo (Excel 97-2003, binario) se rechaza con un
  mensaje que explica cómo volver a guardarlo.
* La **cabecera es opcional**: sólo se descarta la primera fila si su primer
  campo dice ``codigo``.
* El **código se compara de forma tolerante** (sin espacios, puntos, guiones ni
  mayúsculas): ``01.01``, ``01-01`` y ``0101`` son el mismo código.
* Los **importes se interpretan por su forma**, porque un archivo no guarda el
  *locale*: ``1.234,56`` y ``1,234.56`` dan 1234.56. Con un único separador,
  ``12.500`` / ``12,500`` se leen como **miles** (12500, la lectura razonable
  para un arancel) y ``120,50`` como **decimales** (120.5). El ``$`` y los
  espacios se ignoran y una celda vacía significa “sin precio” (la fila se omite).

Los errores **no abortan** la importación: cada fila problemática se informa con
su número de línea para que la secretaría corrija la planilla y la vuelva a subir.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from app.core.exceptions import ValidacionError
from app.core.money import redondear_monto

# Nombres de las dos columnas de la plantilla (en minúsculas, tal como se emiten).
COLUMNA_CODIGO = "codigo"
COLUMNA_PRECIO = "precio_cobertura"

# Límites defensivos: una planilla de precios es chica y un archivo enorme
# (o un ZIP con muchas entradas) no debe bloquear el servidor.
MAX_BYTES = 4 * 1024 * 1024
MAX_FILAS = 5000

# Separadores y signos que se descartan antes de interpretar un importe.
_RUIDO_MONTO = ("$", " ", "\u00a0", "\u202f", "\t")

# Espacios de nombres de OOXML (SpreadsheetML).
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_MAIN = f"{{{_NS_MAIN}}}"

# ``A1``/``BC12`` de una celda → letras y número.
_RE_CELDA = re.compile(r"^([A-Z]+)(\d+)$")


@dataclass(frozen=True)
class FilaPlanilla:
    """Una fila de la planilla ya interpretada.

    ``monto`` es ``None`` cuando la fila no trae precio o cuando ``error`` trae el
    motivo por el que no se pudo leer (en ese caso ``codigo`` conserva el texto
    original para poder señalarlo en el reporte).
    """

    linea: int
    codigo: str
    monto: Optional[float] = None
    error: Optional[str] = None



# --------------------------------------------------------------------------- #
# Códigos e importes
# --------------------------------------------------------------------------- #
def canonizar_codigo(valor: object) -> str:
    """Devuelve el código comparable: sin espacios, puntos, guiones ni mayúsculas.

    ``01.01``, ``01-01``, `` 0101 `` y ``0101`` colapsan al mismo texto, así una
    planilla escrita a mano encuentra igual el código del nomenclador.
    """
    texto = str(valor if valor is not None else "").strip().upper()
    return re.sub(r"[^0-9A-Z]", "", texto)


def _con_dos_separadores(texto: str) -> str:
    """Normaliza un importe que trae punto y coma: el último es el decimal."""
    if texto.rfind(",") > texto.rfind("."):
        return texto.replace(".", "").replace(",", ".")
    return texto.replace(",", "")


def _con_un_separador(texto: str, separador: str) -> str:
    """Normaliza un importe con un solo tipo de separador.

    Tres dígitos después del separador se leen como **miles** (``12.500`` →
    12500), que es la lectura razonable para un arancel; cualquier otra cantidad
    se lee como **decimales** (``120,50`` → 120.50).
    """
    entero, _, decimales = texto.rpartition(separador)
    if not entero or not decimales.isdigit():
        raise ValueError("no se reconoce el importe")
    if len(decimales) == 3 and entero.replace(separador, "").isdigit():
        return entero.replace(separador, "") + decimales
    return entero.replace(separador, "") + "." + decimales


def parsear_monto(valor: object) -> Optional[float]:
    """Interpreta el importe de una celda y lo redondea a dos decimales.

    Devuelve ``None`` si la celda está vacía (sin precio) y lanza ``ValueError``
    si el texto no es un importe válido o es negativo.
    """
    texto = str(valor if valor is not None else "")
    for ruido in _RUIDO_MONTO:
        texto = texto.replace(ruido, "")
    if not texto:
        return None
    if texto.startswith("-"):
        raise ValueError("el precio no puede ser negativo")
    if texto.startswith("+"):
        texto = texto[1:]

    if "," in texto and "." in texto:
        normalizado = _con_dos_separadores(texto)
    elif "," in texto:
        normalizado = _con_un_separador(texto, ",")
    elif "." in texto:
        normalizado = _con_un_separador(texto, ".")
    else:
        normalizado = texto

    try:
        numero = Decimal(normalizado)
    except InvalidOperation as exc:
        raise ValueError("no se reconoce el importe") from exc
    if not numero.is_finite() or numero < 0:
        raise ValueError("el precio no puede ser negativo")
    return redondear_monto(numero)


def nombre_archivo_seguro(texto: object, respaldo: str = "aranceles") -> str:
    """Convierte un texto (p. ej. el nombre de la cobertura) en nombre de archivo."""
    plano = unicodedata.normalize("NFKD", str(texto or ""))
    plano = "".join(c for c in plano if not unicodedata.combining(c)).lower()
    limpio = re.sub(r"[^a-z0-9]+", "-", plano).strip("-")
    return limpio or respaldo


# --------------------------------------------------------------------------- #
# Lectura de la planilla (CSV y XLSX, sin dependencias externas)
# --------------------------------------------------------------------------- #
def _decodificar(contenido: bytes) -> str:
    """Decodifica el archivo probando las codificaciones habituales de Excel."""
    for codec in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return contenido.decode(codec)
        except UnicodeDecodeError:
            continue
    # ``latin-1`` no falla nunca, así que este renglón es inalcanzable; se
    # mantiene por claridad (y para no devolver ``None`` si alguien cambia la
    # lista de códecs).
    return contenido.decode("utf-8", errors="replace")


def _delimitador(linea: str) -> str:
    """Adivina el separador de columnas del CSV contando candidatos en la cabecera."""
    candidatos = (";", ",", "\t")
    conteos = {cand: linea.count(cand) for cand in candidatos}
    mejor = max(candidatos, key=lambda cand: conteos[cand])
    return mejor if conteos[mejor] else ";"


def _filas_csv(texto: str) -> List[List[str]]:
    """Separa el texto en filas de celdas (con separador detectado)."""
    primera = next((l for l in texto.splitlines() if l.strip()), "")
    lector = csv.reader(io.StringIO(texto), delimiter=_delimitador(primera))
    return [fila for fila in lector]


def _texto_nodo(nodo: ElementTree.Element) -> str:
    """Concatena todos los ``<t>`` de un nodo (una cadena puede venir en tramos)."""
    return "".join(t.text or "" for t in nodo.iter(f"{_MAIN}t"))


def _indice_columna(referencia: Optional[str]) -> int:
    """Traduce la referencia de una celda (``C4``) al índice de columna (0-based)."""
    coincidencia = _RE_CELDA.match((referencia or "").upper())
    if not coincidencia:
        return 0
    letras = coincidencia.group(1)
    indice = 0
    for letra in letras:
        indice = indice * 26 + (ord(letra) - ord("A") + 1)
    return indice - 1


def _valor_celda(celda: ElementTree.Element, compartidas: Sequence[str]) -> str:
    """Devuelve el texto de una celda considerando los tres tipos de OOXML."""
    tipo = celda.get("t")
    if tipo == "inlineStr":
        return _texto_nodo(celda)
    valor = celda.find(f"{_MAIN}v")
    if valor is None or valor.text is None:
        return ""
    if tipo == "s":  # índice dentro de sharedStrings
        try:
            return compartidas[int(valor.text)]
        except (ValueError, IndexError):
            return ""
    return valor.text


def _compartidos_xlsx(archivo: zipfile.ZipFile) -> List[str]:
    """Devuelve la tabla de cadenas compartidas del libro (vacía si no la trae)."""
    try:
        datos = archivo.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    raiz = ElementTree.fromstring(datos)
    return [_texto_nodo(nodo) for nodo in raiz if nodo.tag == f"{_MAIN}si"]


def _hoja_xlsx(archivo: zipfile.ZipFile) -> bytes:
    """Devuelve el XML de la primera hoja del libro."""
    candidatas = [
        nombre
        for nombre in archivo.namelist()
        if nombre.startswith("xl/worksheets/") and nombre.endswith(".xml")
    ]
    if not candidatas:
        raise ValidacionError(
            "El archivo .xlsx no tiene hojas: volvé a guardarlo desde Excel."
        )
    return archivo.read(sorted(candidatas)[0])


def _filas_xlsx(contenido: bytes) -> List[List[str]]:
    """Lee la primera hoja de un ``.xlsx`` con ``zipfile`` + ``xml`` (stdlib)."""
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as archivo:
            compartidas = _compartidos_xlsx(archivo)
            hoja = ElementTree.fromstring(_hoja_xlsx(archivo))
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValidacionError(
            "No se pudo leer el archivo .xlsx: subí la planilla descargada "
            "desde el panel o guardala de nuevo como .csv."
        ) from exc

    filas: List[List[str]] = []
    for nodo in hoja.iter(f"{_MAIN}row"):
        celdas = list(nodo)
        valores: List[str] = [""] * (max(_indice_columna(c.get("r")) for c in celdas) + 1)
        for celda in celdas:
            valores[_indice_columna(celda.get("r"))] = _valor_celda(celda, compartidas)
        filas.append(valores)
    return filas


def _es_cabecera(valor: str) -> bool:
    """¿La primera celda es el encabezado ``codigo``? (acentos y espacios de más)."""
    return canonizar_codigo(valor).startswith("CODIGO") or valor.strip().lower().startswith(
        ("codigo", "código", "codigos", "códigos")
    )


def _normalizar_filas(crudas: Sequence[Sequence[str]]) -> List[FilaPlanilla]:
    """Convierte las filas crudas en :class:`FilaPlanilla` (con su nº de línea).

    Las filas vacías se saltean; la cabecera sólo se descarta si es la primera
    fila con contenido. Una fila sin el código (o sin la segunda columna) se
    informa como error para poder corregir la planilla; una fila con el código
    pero sin precio vuelve con ``monto = None`` (se omite al importar).
    """
    resultado: List[FilaPlanilla] = []
    for numero, celdas in enumerate(crudas, start=1):
        valores = [str(celda).strip() for celda in celdas]
        if not any(valores):
            continue
        if not resultado and _es_cabecera(valores[0]):
            continue
        if len(valores) < 2 or not valores[0]:
            resultado.append(
                FilaPlanilla(
                    linea=numero,
                    codigo=valores[0] if valores else "",
                    error="faltan las columnas «codigo» y «precio_cobertura»",
                )
            )
            continue
        try:
            monto = parsear_monto(valores[1])
        except ValueError as exc:
            resultado.append(FilaPlanilla(linea=numero, codigo=valores[0], error=str(exc)))
            continue
        resultado.append(FilaPlanilla(linea=numero, codigo=valores[0], monto=monto))

    if not resultado:
        raise ValidacionError(
            "La planilla no tiene filas de datos: tiene que traer las columnas "
            "«codigo» y «precio_cobertura»."
        )
    if len(resultado) > MAX_FILAS:
        raise ValidacionError(
            f"La planilla tiene {len(resultado)} filas: el máximo es {MAX_FILAS}."
        )
    return resultado


def leer_planilla(
    contenido: bytes, nombre_archivo: Optional[str] = None
) -> List[FilaPlanilla]:
    """Interpreta una planilla de aranceles (``.csv`` o ``.xlsx``).

    Lanza :class:`ValidacionError` (HTTP **422**) si el archivo está vacío, es
    demasiado grande, tiene una extensión no soportada o no trae filas de datos.
    Las filas problemáticas **no** cortan la lectura: vuelven con ``error``.
    """
    if not contenido:
        raise ValidacionError(
            "El archivo está vacío: subí la planilla con los códigos y sus precios."
        )
    if len(contenido) > MAX_BYTES:
        raise ValidacionError(
            f"El archivo supera los {MAX_BYTES // (1024 * 1024)} MB: "
            "subí sólo la planilla de aranceles."
        )

    nombre = (nombre_archivo or "").strip().lower()
    extension = nombre.rsplit(".", 1)[-1] if "." in nombre else ""
    if extension == "xls":
        raise ValidacionError(
            "El formato .xls (Excel 97-2003) no se puede leer: abrí la planilla "
            "y guardala como .xlsx o .csv."
        )

    if extension == "xlsx" or contenido[:4] == b"PK\x03\x04":
        crudas = _filas_xlsx(contenido)
    else:
        crudas = _filas_csv(_decodificar(contenido))
    return _normalizar_filas(crudas)


# --------------------------------------------------------------------------- #
# Generación de la plantilla
# --------------------------------------------------------------------------- #
def _celda_xlsx(columna: str, fila: int, valor: object) -> str:
    """Arma una celda de la hoja: texto, número o vacía."""
    referencia = f"{columna}{fila}"
    if valor is None or valor == "":
        return f'<c r="{referencia}"/>'
    if isinstance(valor, bool):
        return (
            f'<c r="{referencia}" t="inlineStr"><is><t>{escape(str(valor))}'
            "</t></is></c>"
        )
    if isinstance(valor, (int, float)):
        return f'<c r="{referencia}"><v>{float(valor):.2f}</v></c>'
    return (
        f'<c r="{referencia}" t="inlineStr"><is><t>{escape(str(valor))}</t></is></c>'
    )


def _fila_xlsx(numero: int, celdas: Sequence[object]) -> str:
    """Arma una ``<row>`` con las dos columnas de la plantilla."""
    columnas = ("A", "B")
    contenido = "".join(
        _celda_xlsx(columna, numero, valor)
        for columna, valor in zip(columnas, celdas)
    )
    return f'<row r="{numero}">{contenido}</row>'


def _cabecera_xml() -> str:
    """Declaración XML que encabeza cada parte del paquete OOXML."""
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def generar_csv(filas: Iterable[Tuple[str, Optional[float]]]) -> bytes:
    """Devuelve la plantilla en CSV (``;`` + BOM): la que Excel abre en es-AR.

    El precio se escribe con coma decimal (``12000,00``), que es lo que Excel
    es-AR interpreta como número; :func:`parsear_monto` lo vuelve a leer igual si
    la planilla se sube sin modificaciones.
    """
    buffer = io.StringIO()
    escritor = csv.writer(
        buffer, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL
    )
    escritor.writerow([COLUMNA_CODIGO, COLUMNA_PRECIO])
    for codigo, monto in filas:
        precio = "" if monto is None else f"{monto:.2f}".replace(".", ",")
        escritor.writerow([codigo, precio])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def generar_xlsx(
    filas: Iterable[Tuple[str, Optional[float]]], hoja: str = "Aranceles"
) -> bytes:
    """Devuelve la plantilla en ``.xlsx`` (Excel moderno), sin dependencias.

    Se arma el paquete OOXML mínimo (contenido, relaciones, libro y hoja) con
    ``zipfile``. El **código se escribe como texto** (``inlineStr``) para que
    Excel no lo convierta en número y pierda los ceros a la izquierda; el precio
    va como número, así se puede sumar o recalcular en la planilla.
    """
    filas_xml = [_fila_xlsx(1, (COLUMNA_CODIGO, COLUMNA_PRECIO))]
    for numero, (codigo, monto) in enumerate(filas, start=2):
        filas_xml.append(_fila_xlsx(numero, (codigo, monto)))

    hoja_xml = (
        _cabecera_xml()
        + f'<worksheet xmlns="{_NS_MAIN}"><sheetData>'
        + "".join(filas_xml)
        + "</sheetData></worksheet>"
    )
    content_types = (
        _cabecera_xml()
        + f'<Types xmlns="{_NS_CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    rels = (
        _cabecera_xml()
        + f'<Relationships xmlns="{_NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{_NS_REL}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        _cabecera_xml()
        + f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}"><sheets>'
        f'<sheet name="{escape(hoja)}" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    )
    workbook_rels = (
        _cabecera_xml()
        + f'<Relationships xmlns="{_NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{_NS_REL}/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as paquete:
        paquete.writestr("[Content_Types].xml", content_types)
        paquete.writestr("_rels/.rels", rels)
        paquete.writestr("xl/workbook.xml", workbook)
        paquete.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        paquete.writestr("xl/worksheets/sheet1.xml", hoja_xml)
    return buffer.getvalue()
