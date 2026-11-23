"""PDF del presupuesto, con el mismo diseño del documento del consultorio.

La plantilla de referencia es ``PRESUPUESTO DRA FRANCIOSI.pdf``: una hoja A4
con el logo circular arriba, los datos del paciente, el título
«PRESUPUESTO», la banda color arena y el detalle de los trabajos con el precio
alineado a la derecha.

El archivo se arma con PyMuPDF (la misma herramienta que usa la Historia
Clínica General) y **no consulta la base**: recibe el ORM ya cargado. Así se
puede probar el documento con objetos en memoria y la generación queda separada
de la lectura.

Detalles que se respetan del original:

* tamaño A4 y márgenes del documento de Canva;
* logo circular del consultorio y color de la banda ``#D3B696``;
* título en el rojo apagado ``#BB5C5C`` y la columna «PRECIO» en gris;
* nombre del paciente en mayúsculas y fecha con mes en castellano;
* código de prestación antes de la descripción (``Cód. 11.02 - ...``) cuando el
  renglón lo tiene;
* descripciones de varias líneas: cada salto se imprime como un renglón nuevo;
* precio con separador de miles argentino (``$550.000``);
* la línea «Forma de Pago : Entrega: $...» debajo de cada trabajo que tenga
  una entrega informada.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Tuple

import pymupdf
from sqlalchemy.orm import Session

from app.core.pdf import nombre_archivo
from app.models.presupuesto import Presupuesto, PresupuestoItem
from app.services import presupuesto_service

#: Logo extraído del PDF original del consultorio.
RUTA_LOGO = (
    Path(__file__).resolve().parents[1] / "resources" / "presupuesto_logo.png"
)

# Tamaño A4 y coordenadas del documento de referencia (en puntos PDF).
ANCHO_A4 = 595.2756
ALTO_A4 = 841.8898
X_IZQ = 50.0712
X_DER = 539.7997

# Logo: rectángulo exacto que ocupa en la primera página del original.
LOGO = pymupdf.Rect(231.7608, 5.4711, 374.0739, 144.7218)

# Paleta del documento.
COLOR_TEXTO = (0.0, 0.0, 0.0)
COLOR_BANDA = (0.82739, 0.713756, 0.588159)
COLOR_TITULO = (0.733333, 0.360784, 0.360784)  # #BB5C5C
COLOR_PRECIO = (0.329412, 0.329412, 0.329412)  # #545454

# Medidas de la tipografía y de los renglones.
TAM_CABECERA = 16.0
TAM_DETALLE = 16.305
TAM_PRECIO = 15.76
TAM_BANDA = 10.0
TAM_PRECIO_BANDA = 13.0
TAM_TITULO = 21.0
INTERLINEA = 15.75
# El original corta el detalle antes de la columna de precios; 345 puntos
# reproducen el mismo quiebre de línea de los dos trabajos de la plantilla.
ANCHO_DETALLE = 345.0
X_PRECIO = 540.2
Y_PRIMER_ITEM = 412.3337
ALTO_BLOQUE = 145.75
MARGEN_INFERIOR = 780.0

# Banda de la primera página y de las páginas de continuación.
BANDA_PRIMERA = pymupdf.Rect(53.4569, 322.1429, 539.7997, 348.7159)
BANDA_CONTINUACION = pymupdf.Rect(X_IZQ, 160.0, X_DER, 186.6)

# Métricas reales de las fuentes estándar. ``get_text_length`` no mide bien las
# vocales acentuadas (las descuenta del ancho), así que se usan los objetos
# ``Font`` de PyMuPDF, que sí respetan el texto completo.
_FUENTE = pymupdf.Font("helv")
_FUENTE_NEGRITA = pymupdf.Font("hebo")

_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


# --------------------------------------------------------------------------- #
# Texto y medidas
# --------------------------------------------------------------------------- #
def _limpiar(valor: object) -> str:
    """Normaliza espacios y saltos de línea de un dato del documento."""
    return " ".join(str(valor or "").split())


def _ancho(texto: str, tamano: float = TAM_DETALLE, negrita: bool = False) -> float:
    """Ancho del texto en puntos con las métricas de la fuente estándar."""
    fuente = _FUENTE_NEGRITA if negrita else _FUENTE
    return fuente.text_length(texto or "", fontsize=tamano)


def _recortar(texto: str, ancho: float, tamano: float, negrita: bool = False) -> str:
    """Recorta con puntos suspensivos si el texto no entra en ``ancho``."""
    if not texto or _ancho(texto, tamano, negrita) <= ancho:
        return texto
    recorte = texto
    while recorte and _ancho(f"{recorte}...", tamano, negrita) > ancho:
        recorte = recorte[:-1]
    return f"{recorte.rstrip()}..." if recorte else ""


def _envolver(texto: str, ancho: float, tamano: float = TAM_DETALLE) -> List[str]:
    """Parte un texto en líneas que entren en ``ancho`` puntos."""
    salida: List[str] = []
    for parrafo in str(texto or "").split("\n"):
        palabras = parrafo.split()
        if not palabras:
            salida.append("")
            continue
        actual = palabras[0]
        for palabra in palabras[1:]:
            prueba = f"{actual} {palabra}"
            if _ancho(prueba, tamano) <= ancho:
                actual = prueba
            else:
                salida.append(actual)
                actual = palabra
        # Una palabra sola más ancha que la columna se corta por caracteres.
        while _ancho(actual, tamano) > ancho and len(actual) > 1:
            corte = len(actual)
            while corte > 1 and _ancho(actual[:corte], tamano) > ancho:
                corte -= 1
            salida.append(actual[:corte])
            actual = actual[corte:]
        salida.append(actual)
    return salida


def _pesos(valor: object) -> str:
    """Importe con el formato del documento: ``$550.000`` / ``$1.234,50``."""
    numero = float(valor or 0)
    if abs(numero - round(numero)) < 0.005:
        return "$" + f"{int(round(numero)):,}".replace(",", ".")
    texto = f"{numero:,.2f}"
    return "$" + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _mes_anio(fecha: date) -> str:
    """Fecha del presupuesto con el mes en castellano (``Septiembre 2026``)."""
    return f"{_MESES[fecha.month - 1].capitalize()} {fecha.year}"


def _fecha_de(presupuesto: Presupuesto) -> date:
    """Fecha segura del documento (nunca deja el PDF sin fecha)."""
    return presupuesto.fecha or date.today()


# --------------------------------------------------------------------------- #
# Dibujo
# --------------------------------------------------------------------------- #
def _escribir(
    pagina: pymupdf.Page,
    x: float,
    y: float,
    texto: object,
    tamano: float = TAM_DETALLE,
    negrita: bool = False,
    color: Tuple[float, float, float] = COLOR_TEXTO,
) -> None:
    """Escribe una línea en la posición exacta del documento."""
    valor = _limpiar(texto)
    if not valor:
        return
    pagina.insert_text(
        (x, y),
        valor,
        fontname="hebo" if negrita else "helv",
        fontsize=tamano,
        color=color,
    )


def _escribir_derecha(
    pagina: pymupdf.Page,
    derecha: float,
    y: float,
    texto: object,
    tamano: float = TAM_DETALLE,
    negrita: bool = False,
    color: Tuple[float, float, float] = COLOR_TEXTO,
) -> None:
    """Escribe un texto alineado a la derecha en ``derecha``."""
    valor = _limpiar(texto)
    if not valor:
        return
    x = derecha - _ancho(valor, tamano, negrita)
    _escribir(pagina, x, y, valor, tamano, negrita, color)


def _logo(pagina: pymupdf.Page, rect: pymupdf.Rect = LOGO) -> None:
    """Inserta el logo circular del consultorio."""
    if not RUTA_LOGO.is_file():
        return
    pagina.insert_image(rect, filename=str(RUTA_LOGO), keep_proportion=True)


def _banda(pagina: pymupdf.Page, rect: pymupdf.Rect) -> None:
    """Dibuja la banda arena con los títulos de las columnas."""
    pagina.draw_rect(rect, color=None, fill=COLOR_BANDA, width=0)
    _escribir(pagina, rect.x0 + 4.0, rect.y0 + 12.6, "DETALLE DE LOS", TAM_BANDA)
    _escribir(pagina, rect.x0 + 4.0, rect.y0 + 23.1, "TRABAJOS", TAM_BANDA)
    _escribir_derecha(
        pagina,
        rect.x1 - 15.0,
        rect.y0 + 19.0,
        "PRECIO",
        TAM_PRECIO_BANDA,
        color=COLOR_PRECIO,
    )


def _pagina_primera(
    documento: pymupdf.Document, presupuesto: Presupuesto
) -> pymupdf.Page:
    """Dibuja la portada del presupuesto y devuelve la página creada."""
    pagina = documento.new_page(width=ANCHO_A4, height=ALTO_A4)
    _logo(pagina)

    paciente = presupuesto.paciente
    etiqueta_nombre = "NOMBRE DEL PACIENTE: "
    x_nombre = X_IZQ + _ancho(etiqueta_nombre, TAM_CABECERA, negrita=True)
    nombre = _recortar(
        _limpiar(paciente.nombre).upper(),
        X_DER - x_nombre,
        TAM_CABECERA,
        negrita=True,
    )
    _escribir(
        pagina,
        X_IZQ,
        172.3,
        f"{etiqueta_nombre}{nombre}",
        TAM_CABECERA,
        negrita=True,
    )

    _escribir(pagina, X_IZQ, 200.3, "Dirección:", TAM_CABECERA, negrita=True)
    direccion = presupuesto.direccion or paciente.domicilio
    x_direccion = _x_valor("Dirección:")
    _escribir(
        pagina,
        x_direccion,
        200.3,
        _recortar(_limpiar(direccion), X_DER - x_direccion, TAM_CABECERA),
        TAM_CABECERA,
    )

    _escribir(pagina, X_IZQ, 227.9, "Fecha:", TAM_CABECERA, negrita=True)
    _escribir(
        pagina,
        _x_valor("Fecha:"),
        227.9,
        _mes_anio(_fecha_de(presupuesto)),
        TAM_CABECERA,
    )

    _escribir(
        pagina, X_IZQ, 255.8, "Forma de pago:", TAM_CABECERA, negrita=True
    )
    x_forma = _x_valor("Forma de pago:")
    _escribir(
        pagina,
        x_forma,
        255.8,
        _recortar(
            _limpiar(presupuesto.forma_pago),
            X_DER - x_forma,
            TAM_CABECERA,
        ),
        TAM_CABECERA,
    )

    _escribir_derecha(
        pagina, X_DER, 307.0, "PRESUPUESTO", TAM_TITULO, color=COLOR_TITULO
    )
    _banda(pagina, BANDA_PRIMERA)
    return pagina


def _pagina_continuacion(
    documento: pymupdf.Document, presupuesto: Presupuesto
) -> pymupdf.Page:
    """Cabecera compacta para las hojas siguientes de un presupuesto largo."""
    pagina = documento.new_page(width=ANCHO_A4, height=ALTO_A4)
    _logo(pagina, pymupdf.Rect(257.6, 24.0, 337.6, 102.4))

    etiqueta_nombre = "NOMBRE DEL PACIENTE: "
    x_nombre = X_IZQ + _ancho(etiqueta_nombre, 10.0, negrita=True)
    nombre = _recortar(
        _limpiar(presupuesto.paciente.nombre).upper(),
        X_DER - x_nombre - 100.0,
        10.0,
        negrita=True,
    )
    _escribir(
        pagina,
        X_IZQ,
        127.0,
        f"{etiqueta_nombre}{nombre}",
        10.0,
        negrita=True,
    )
    _escribir_derecha(
        pagina, X_DER, 127.0, "PRESUPUESTO", 14.0, color=COLOR_TITULO
    )
    _banda(pagina, BANDA_CONTINUACION)
    return pagina


def _texto_detalle(item: PresupuestoItem) -> str:
    """Texto del renglón: código de prestación + descripción."""
    codigo = _limpiar(item.codigo)
    detalle = str(item.detalle or "")
    return f"Cód. {codigo} - {detalle}" if codigo else detalle


def _alto_item(item: PresupuestoItem) -> float:
    """Alto que ocupa un renglón según las líneas de su detalle."""
    lineas = max(
        1, len(_envolver(_texto_detalle(item), ANCHO_DETALLE, TAM_DETALLE))
    )
    if item.entrega is None:
        return max(ALTO_BLOQUE, (lineas - 1) * INTERLINEA + 80.0)
    y_entrega = max(63.0, (lineas - 1) * INTERLINEA + 48.0)
    return max(ALTO_BLOQUE, y_entrega + 35.0)


def _x_valor(etiqueta: str, separacion: float = 6.0) -> float:
    """Posición del valor después de una etiqueta en negrita."""
    return X_IZQ + _ancho(etiqueta, TAM_CABECERA, negrita=True) + separacion


def _dibujar_item(
    pagina: pymupdf.Page, item: PresupuestoItem, y: float
) -> None:
    """Dibuja un trabajo: detalle a la izquierda, precio y entrega."""
    lineas = _envolver(_texto_detalle(item), ANCHO_DETALLE, TAM_DETALLE) or [""]
    for indice, linea in enumerate(lineas):
        _escribir(
            pagina,
            X_IZQ + 3.3858,
            y + indice * INTERLINEA,
            linea,
            TAM_DETALLE,
        )

    _escribir_derecha(
        pagina, X_PRECIO, y + 4.87, _pesos(item.precio), TAM_PRECIO
    )

    if item.entrega is not None:
        y_entrega = max(63.0, (len(lineas) - 1) * INTERLINEA + 48.0)
        _escribir(
            pagina,
            X_IZQ + 3.3858,
            y + y_entrega,
            f"Forma de Pago : Entrega: {_pesos(item.entrega)}",
            TAM_DETALLE,
        )


# --------------------------------------------------------------------------- #
# Documento
# --------------------------------------------------------------------------- #
def construir_pdf(presupuesto: Presupuesto) -> bytes:
    """Arma el PDF completo del presupuesto y devuelve sus bytes.

    Es una función pura sobre el ORM recibido: no consulta la base ni modifica
    el presupuesto. Un detalle muy largo se envuelve en varias líneas y, si no
    entra en la hoja, el documento continúa en una página nueva con una
    cabecera compacta.
    """
    documento = pymupdf.open()
    pagina = _pagina_primera(documento, presupuesto)
    y = Y_PRIMER_ITEM

    items = list(presupuesto.items)
    if not items:
        _escribir(
            pagina,
            X_IZQ + 3.3858,
            y,
            "Sin trabajos cargados.",
            TAM_DETALLE,
        )

    for item in items:
        alto = _alto_item(item)
        if y + alto > MARGEN_INFERIOR and y > Y_PRIMER_ITEM:
            pagina = _pagina_continuacion(documento, presupuesto)
            y = 250.0
        _dibujar_item(pagina, item, y)
        y += alto

    fecha = _fecha_de(presupuesto)
    documento.set_metadata(
        {
            "title": f"Presupuesto - {presupuesto.paciente.nombre}",
            "author": "Franciosi Odontología",
            "subject": f"Presupuesto de {_mes_anio(fecha)}",
            "creator": "Sistema Franciosi Odontología",
        }
    )
    return documento.tobytes(garbage=3, deflate=True)


def generar_pdf(db: Session, presupuesto_id: int) -> Tuple[bytes, str]:
    """Devuelve el PDF y el nombre de archivo de un presupuesto guardado."""
    presupuesto = presupuesto_service.obtener_presupuesto_orm(
        db, presupuesto_id
    )
    contenido = construir_pdf(presupuesto)
    nombre = nombre_archivo(
        "presupuesto",
        presupuesto.paciente.nombre,
        _fecha_de(presupuesto).isoformat(),
    )
    return contenido, f"{nombre}.pdf"
