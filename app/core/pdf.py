"""Generador mínimo de documentos PDF, sin dependencias externas.

El panel necesita entregar los reportes de facturación como PDF para imprimir o
adjuntar (la presentación ante la obra social y la liquidación de profesionales).
En vez de sumar una librería pesada al despliegue (``reportlab``/``fpdf2``, que
además complican el build de Vercel) se arma el archivo a mano con las fuentes
estándar del formato: **Helvetica** y **Helvetica-Bold** con
``WinAnsiEncoding``. Alcanza para texto y tablas con encabezado repetido, el
resultado pesa unos pocos KB y los acentos del castellano se imprimen bien.

El PDF (versión 1.4) es deliberadamente simple:

* internamente el origen de coordenadas es **arriba a la izquierda** (lo natural
  para dibujar tablas hacia abajo) y se traduce al origen del formato al escribir
  los operadores;
* el contenido **no se comprime**, así que el archivo se puede inspeccionar con
  un editor de texto y las pruebas pueden verificar el texto dibujado;
* el catálogo, las páginas y la tabla ``xref`` se emiten al final, cuando ya se
  sabe cuántas páginas hay (el pie dice "Página X de Y").
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

# Tamaño A4 en puntos (1 pt = 1/72 de pulgada) y margen del contenido.
ANCHO_A4 = 595.28
ALTO_A4 = 841.89
MARGEN = 40.0
# Campos verticales reservados para la cabecera y el pie de cada página.
ALTO_CABECERA = 60.0
ALTO_PIE = 30.0

# --------------------------------------------------------------------------- #
# Métricas de las fuentes (tablas AFM de Helvetica, en milésimas de punto)
# --------------------------------------------------------------------------- #

# ASCII imprimible. Los dígitos van aparte porque todos miden lo mismo.
_ANCHOS_ASCII = {
    32: 278, 33: 278, 34: 355, 35: 556, 36: 556, 37: 889, 38: 667, 39: 191,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    58: 278, 59: 278, 60: 584, 61: 584, 62: 584, 63: 556, 64: 1015,
    65: 667, 66: 667, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778, 72: 722,
    73: 278, 74: 500, 75: 667, 76: 556, 77: 833, 78: 722, 79: 778, 80: 667,
    81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944, 88: 667,
    89: 667, 90: 611, 91: 278, 92: 278, 93: 278, 94: 469, 95: 556, 96: 333,
    97: 556, 98: 556, 99: 500, 100: 556, 101: 556, 102: 278, 103: 556,
    104: 556, 105: 222, 106: 222, 107: 500, 108: 222, 109: 833, 110: 556,
    111: 556, 112: 556, 113: 556, 114: 333, 115: 500, 116: 278, 117: 556,
    118: 500, 119: 722, 120: 500, 121: 500, 122: 500, 123: 334, 124: 260,
    125: 334, 126: 584,
}

_ANCHOS_ASCII_FUERTE = {
    32: 278, 33: 333, 34: 474, 35: 556, 36: 556, 37: 889, 38: 722, 39: 238,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    58: 333, 59: 333, 60: 584, 61: 584, 62: 584, 63: 611, 64: 975,
    65: 722, 66: 722, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778, 72: 722,
    73: 278, 74: 556, 75: 722, 76: 611, 77: 833, 78: 722, 79: 778, 80: 667,
    81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944, 88: 667,
    89: 667, 90: 611, 91: 333, 92: 278, 93: 333, 94: 584, 95: 556, 96: 333,
    97: 556, 98: 611, 99: 556, 100: 611, 101: 556, 102: 333, 103: 611,
    104: 611, 105: 278, 106: 278, 107: 556, 108: 278, 109: 889, 110: 611,
    111: 611, 112: 611, 113: 611, 114: 389, 115: 556, 116: 333, 117: 611,
    118: 556, 119: 778, 120: 556, 121: 556, 122: 500, 123: 389, 124: 280,
    125: 389, 126: 584,
}

# Latin-1 alto (0xA0-0xFF) más los signos de apertura del castellano. Los
# acentos miden como su letra base, que es la convención de las tablas AFM.
_ANCHOS_ALTOS = {
    0xA1: 333, 0xA2: 556, 0xA3: 556, 0xA4: 556, 0xA5: 556, 0xA6: 556,
    0xA7: 556, 0xA8: 333, 0xA9: 737, 0xAA: 370, 0xAB: 556, 0xAC: 584,
    0xAD: 333, 0xAE: 737, 0xAF: 552, 0xB0: 400, 0xB1: 584, 0xB2: 333,
    0xB3: 333, 0xB4: 333, 0xB5: 556, 0xB6: 537, 0xB7: 278, 0xB8: 333,
    0xB9: 333, 0xBA: 365, 0xBB: 556, 0xBC: 834, 0xBD: 834, 0xBE: 834,
    0xBF: 611, 0xC0: 667, 0xC1: 667, 0xC2: 667, 0xC3: 667, 0xC4: 667,
    0xC5: 667, 0xC6: 1000, 0xC7: 722, 0xC8: 667, 0xC9: 667, 0xCA: 667,
    0xCB: 667, 0xCC: 278, 0xCD: 278, 0xCE: 278, 0xCF: 278, 0xD0: 722,
    0xD1: 722, 0xD2: 778, 0xD3: 778, 0xD4: 778, 0xD5: 778, 0xD6: 778,
    0xD7: 584, 0xD8: 778, 0xD9: 722, 0xDA: 722, 0xDB: 722, 0xDC: 722,
    0xDD: 667, 0xDE: 667, 0xDF: 611, 0xE0: 556, 0xE1: 556, 0xE2: 556,
    0xE3: 556, 0xE4: 556, 0xE5: 556, 0xE6: 889, 0xE7: 500, 0xE8: 556,
    0xE9: 556, 0xEA: 556, 0xEB: 556, 0xEC: 278, 0xED: 278, 0xEE: 278,
    0xEF: 278, 0xF0: 556, 0xF1: 556, 0xF2: 556, 0xF3: 556, 0xF4: 556,
    0xF5: 556, 0xF6: 556, 0xF7: 584, 0xF8: 611, 0xF9: 556, 0xFA: 556,
    0xFB: 556, 0xFC: 556, 0xFD: 556, 0xFE: 556, 0xFF: 556,
}

# En negrita basta con las variantes acentuadas que aparecen en los reportes
# (resto: 611, el ancho de la "o"/"n" fuerte, error de menos de 1 punto).
_ANCHOS_ALTOS_FUERTE = {
    0xA1: 333, 0xA8: 333, 0xAA: 370, 0xAB: 556, 0xAC: 584, 0xAE: 737,
    0xAF: 552, 0xB0: 400, 0xB1: 584, 0xB4: 333, 0xB6: 611, 0xB7: 278,
    0xB8: 333, 0xBA: 365, 0xBB: 556, 0xBF: 611, 0xC1: 722, 0xC9: 667,
    0xCD: 278, 0xD1: 722, 0xD3: 778, 0xD7: 584, 0xDA: 722, 0xDC: 722,
    0xE1: 556, 0xE7: 556, 0xE9: 556, 0xED: 278, 0xF1: 611, 0xF3: 611,
    0xFA: 611, 0xFC: 611,
}

# Caracteres que WinAnsi codifica en el rango 0x80-0x9F (el resto de Latin-1
# coincide con su punto de código Unicode, así que no hace falta mapearlos).
_WINANSI_EXTRA = {
    "\u20ac": 0x80, "\u201a": 0x82, "\u0192": 0x83, "\u201e": 0x84,
    "\u2026": 0x85, "\u2020": 0x86, "\u2021": 0x87, "\u02c6": 0x88,
    "\u2030": 0x89, "\u0160": 0x8A, "\u2039": 0x8B, "\u0152": 0x8C,
    "\u017d": 0x8E, "\u2018": 0x91, "\u2019": 0x92, "\u201c": 0x93,
    "\u201d": 0x94, "\u2022": 0x95, "\u2013": 0x96, "\u2014": 0x97,
    "\u02dc": 0x98, "\u2122": 0x99, "\u0161": 0x9A, "\u203a": 0x9B,
    "\u0153": 0x9C, "\u017e": 0x9E, "\u0178": 0x9F,
}

# Símbolos frecuentes que WinAnsi **no** tiene: se aproximan con ASCII para no
# perder el dato en el reporte (mejor "x" que un "?" suelto).
_EQUIVALENTES = {
    "\u2713": "v", "\u2714": "v", "\u2715": "x", "\u2716": "x", "\u00d7": "x",
    "\u2192": "->", "\u21d2": "=>", "\u2265": ">=", "\u2264": "<=",
    "\u2212": "-", "\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u200b": "",
}

_TAMANO_INTERLINEA = 1.35  # interlineado relativo al tamaño de la fuente


def _a_winansi(texto: str) -> bytes:
    """Codifica ``texto`` en WinAnsi (lo que entienden las fuentes del PDF).

    El castellano entra completo; lo que no existe en la codificación (emojis,
    flechas exóticas) se aproxima con ASCII y, en el peor caso, con ``?``.
    """
    salida = bytearray()
    for caracter in texto:
        punto = ord(caracter)
        if 32 <= punto <= 126 or 160 <= punto <= 255:
            salida.append(punto)
            continue
        equivalente = _EQUIVALENTES.get(caracter)
        if equivalente is not None:
            salida.extend(_a_winansi(equivalente))
            continue
        extra = _WINANSI_EXTRA.get(caracter)
        salida.append(extra if extra is not None else 0x3F)  # 0x3F = "?"
    return bytes(salida)


def _ancho_de_byte(codigo: int, negrita: bool) -> float:
    """Ancho de un byte WinAnsi en milésimas de punto."""
    tabla = _ANCHOS_ASCII_FUERTE if negrita else _ANCHOS_ASCII
    if codigo in tabla:
        return float(tabla[codigo])
    if 48 <= codigo <= 57:
        return 556.0  # los dígitos miden igual en ambas fuentes
    altos = _ANCHOS_ALTOS_FUERTE if negrita else _ANCHOS_ALTOS
    if codigo in altos:
        return float(altos[codigo])
    return 611.0 if negrita else 556.0


def ancho_texto(texto: str, negrita: bool = False, tamano: float = 10.0) -> float:
    """Ancho del texto en puntos, con las métricas de la fuente del PDF."""
    milesimas = sum(
        _ancho_de_byte(byte, negrita) for byte in _a_winansi(texto or "")
    )
    return milesimas * tamano / 1000.0


def envolver(
    texto: str, ancho: float, negrita: bool = False, tamano: float = 10.0
) -> List[str]:
    """Parte el texto en líneas que entren en ``ancho`` puntos."""
    lineas: List[str] = []
    for parrafo in (texto or "").split("\n"):
        palabras = parrafo.split()
        if not palabras:
            lineas.append("")
            continue
        actual = palabras[0]
        for palabra in palabras[1:]:
            prueba = f"{actual} {palabra}"
            if ancho_texto(prueba, negrita, tamano) <= ancho:
                actual = prueba
            else:
                lineas.append(actual)
                actual = palabra
        # Una palabra sola más ancha que la columna se corta por caracteres.
        while ancho_texto(actual, negrita, tamano) > ancho and len(actual) > 1:
            corte = len(actual)
            while corte > 1 and ancho_texto(actual[:corte], negrita, tamano) > ancho:
                corte -= 1
            lineas.append(actual[:corte])
            actual = actual[corte:]
        lineas.append(actual)
    return lineas


def recortar(
    texto: str, ancho: float, negrita: bool = False, tamano: float = 10.0
) -> str:
    """Recorta el texto con puntos suspensivos si no entra en ``ancho``."""
    if ancho_texto(texto, negrita, tamano) <= ancho:
        return texto
    recorte = texto
    while recorte and ancho_texto(recorte + "...", negrita, tamano) > ancho:
        recorte = recorte[:-1]
    return f"{recorte}..."


def _escapar(texto: str) -> bytes:
    """Escapa los caracteres especiales de una cadena PDF ``( ... )``."""
    return (
        _a_winansi(texto)
        .replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
    )


@dataclass(frozen=True)
class Columna:
    """Columna de una tabla: título, proporción del ancho útil y alineación."""

    titulo: str
    ancho: float = 1.0
    alineacion: str = "izquierda"  # "izquierda" | "derecha" | "centro"
    negrita: bool = False


# Relleno interno de las celdas y grises usados para separar las filas.
_PAD = 4.0
_PAD_Y = 3.0
_GRIS_ENCABEZADO = 0.90
_GRIS_CEBRA = 0.97
_GRIS_LINEA = 0.75
_GRIS_TEXTO_SUAVE = 0.42


def _repartir(columnas: Sequence["Columna"], ancho_util: float) -> List[float]:
    """Convierte las proporciones de las columnas en anchos en puntos."""
    total = sum(c.ancho for c in columnas) or 1.0
    return [c.ancho * ancho_util / total for c in columnas]


class DocumentoPDF:
    """Documento A4: cabecera, tablas paginadas y pie con la numeración.

    Uso típico::

        pdf = DocumentoPDF("Liquidación de profesionales", "Noviembre 2026")
        pdf.tabla(columnas, filas)
        pdf.totales([("Honorarios", "$ 120.000,00")])
        bytes_pdf = pdf.generar()
    """

    def __init__(
        self,
        titulo: str,
        subtitulo: Optional[str] = None,
        nota_pie: Optional[str] = None,
    ):
        self.titulo = titulo
        self.subtitulo = subtitulo or ""
        self.nota_pie = nota_pie or ""
        self._paginas: List[List[bytes]] = []
        self._y = 0.0
        self._nueva_pagina()

    # ------------------------------------------------------------------ #
    # Geometría y páginas
    # ------------------------------------------------------------------ #
    @property
    def ancho_util(self) -> float:
        return ANCHO_A4 - 2 * MARGEN

    @property
    def limite_inferior(self) -> float:
        return ALTO_A4 - MARGEN - ALTO_PIE

    def _nueva_pagina(self) -> None:
        self._paginas.append([])
        self._y = MARGEN + ALTO_CABECERA
        self._cabecera()

    def _escribir(self, *operadores: str) -> None:
        """Agrega operadores a la página en curso."""
        for operador in operadores:
            self._paginas[-1].append(operador.encode("latin-1"))

    def _py(self, y: float) -> float:
        """Convierte una coordenada "desde arriba" al eje del PDF."""
        return ALTO_A4 - y

    def _cabecera(self) -> None:
        """Banda superior con el título del reporte y el subtítulo."""
        self._escribir(
            "0.184 0.522 0.478 rg",
            f"0 {self._py(0):.2f} {ANCHO_A4:.2f} 10.00 re f",
            "0 0 0 rg",
        )
        self.texto(MARGEN, MARGEN + 18, self.titulo, negrita=True, tamano=15.5)
        if self.subtitulo:
            self._escribir(f"{_GRIS_TEXTO_SUAVE} g")
            self.texto(MARGEN, MARGEN + 34, self.subtitulo, tamano=9)
            self._escribir("0 g")

    # ------------------------------------------------------------------ #
    # Bloques de texto
    # ------------------------------------------------------------------ #
    def texto(
        self,
        x: float,
        y: float,
        contenido: str,
        negrita: bool = False,
        tamano: float = 10.0,
        alineacion: str = "izquierda",
        ancho: float = 0.0,
    ) -> None:
        """Dibuja una línea de texto en la posición exacta ``(x, y)``."""
        self._texto_en(
            len(self._paginas) - 1, x, y, contenido, negrita, tamano, alineacion, ancho
        )

    def _texto_en(
        self,
        pagina: int,
        x: float,
        y: float,
        contenido: str,
        negrita: bool = False,
        tamano: float = 10.0,
        alineacion: str = "izquierda",
        ancho: float = 0.0,
    ) -> None:
        """Dibuja texto en una página concreta (la cabecera usa la primera)."""
        if not contenido:
            return
        if alineacion == "derecha":
            x = x + ancho - ancho_texto(contenido, negrita, tamano)
        elif alineacion == "centro":
            x = x + (ancho - ancho_texto(contenido, negrita, tamano)) / 2
        fuente = "F2" if negrita else "F1"
        operadores = [
            "BT",
            f"/{fuente} {tamano:.2f} Tf",
            f"1 0 0 1 {x:.2f} {self._py(y):.2f} Tm",
            (b"(" + _escapar(contenido) + b") Tj").decode("latin-1"),
            "ET",
        ]
        self._paginas[pagina].extend(op.encode("latin-1") for op in operadores)

    def espacio(self, puntos: float = 8.0) -> None:
        """Deja un espacio vertical en blanco."""
        self._y += puntos

    def seccion(self, titulo: str) -> None:
        """Subtítulo de sección, con una regla debajo."""
        if self._y + 26 > self.limite_inferior:
            self._nueva_pagina()
        self._y += 6
        self.texto(MARGEN, self._y + 4, titulo, negrita=True, tamano=11)
        self._y += 12
        self._escribir(
            f"{_GRIS_LINEA} G",
            "0.7 w",
            f"{MARGEN:.2f} {self._py(self._y):.2f} m "
            f"{MARGEN + self.ancho_util:.2f} {self._py(self._y):.2f} l S",
            "0 G",
        )
        self._y += 8

    def parrafo(
        self, contenido: str, tamano: float = 9.0, negrita: bool = False
    ) -> None:
        """Párrafo de ancho completo, con salto de página automático."""
        interlinea = tamano * _TAMANO_INTERLINEA
        for linea in envolver(contenido, self.ancho_util, negrita, tamano):
            if self._y + interlinea > self.limite_inferior:
                self._nueva_pagina()
            self._y += interlinea
            self.texto(MARGEN, self._y, linea, negrita=negrita, tamano=tamano)

    # ------------------------------------------------------------------ #
    # Tablas
    # ------------------------------------------------------------------ #
    def tabla(
        self,
        columnas: Sequence[Columna],
        filas: Iterable[Sequence[object]],
        tamano: float = 9.0,
        encabezado: bool = True,
        cebra: bool = True,
    ) -> None:
        """Dibuja una tabla y repite el encabezado en cada página nueva."""
        anchos = _repartir(columnas, self.ancho_util)
        if encabezado:
            self._fila(
                columnas, [c.titulo for c in columnas], anchos, tamano, True, False
            )
        for indice, fila in enumerate(filas):
            salto = self._fila(
                columnas, list(fila), anchos, tamano, False, cebra and indice % 2 == 1
            )
            if salto and encabezado:
                self._fila(
                    columnas,
                    [c.titulo for c in columnas],
                    anchos,
                    tamano,
                    True,
                    False,
                )

    def _fila(
        self,
        columnas: Sequence[Columna],
        valores: Sequence[object],
        anchos: Sequence[float],
        tamano: float,
        es_encabezado: bool,
        cebra: bool,
    ) -> bool:
        """Dibuja una fila de la tabla. Devuelve ``True`` si cambió de página."""
        celdas = [
            envolver(
                "" if valor is None else str(valor),
                max(ancho - 2 * _PAD, 6.0),
                True if es_encabezado else columnas[i].negrita,
                tamano,
            )
            for i, (valor, ancho) in enumerate(zip(valores, anchos))
        ]
        alto = (
            max(len(lineas) for lineas in celdas) * tamano * _TAMANO_INTERLINEA
            + 2 * _PAD_Y
        )
        salto = False
        if self._y + alto > self.limite_inferior:
            self._nueva_pagina()
            salto = True
        tope = self._y

        if es_encabezado or cebra:
            gris = _GRIS_ENCABEZADO if es_encabezado else _GRIS_CEBRA
            self._escribir(
                f"{gris} g",
                f"{MARGEN:.2f} {self._py(tope + alto):.2f} "
                f"{self.ancho_util:.2f} {alto:.2f} re f",
                "0 g",
            )

        x = MARGEN
        for columna, ancho, lineas in zip(columnas, anchos, celdas):
            for numero, linea in enumerate(lineas):
                self.texto(
                    x + _PAD,
                    tope + _PAD_Y + (numero + 1) * tamano * _TAMANO_INTERLINEA,
                    linea,
                    negrita=es_encabezado or columna.negrita,
                    tamano=tamano,
                    alineacion=columna.alineacion,
                    ancho=ancho - 2 * _PAD,
                )
            x += ancho

        self._y = tope + alto
        self._escribir(
            f"{_GRIS_LINEA} G",
            "0.5 w",
            f"{MARGEN:.2f} {self._py(self._y):.2f} m "
            f"{MARGEN + self.ancho_util:.2f} {self._py(self._y):.2f} l S",
            "0 G",
        )
        return salto

    def totales(self, pares: Sequence[Tuple[str, str]], tamano: float = 10.0) -> None:
        """Bloque de cierre "etiqueta ........ valor" (los totales del reporte)."""
        alto = tamano * _TAMANO_INTERLINEA * len(pares)
        if self._y + alto + 24 > self.limite_inferior:
            self._nueva_pagina()
        self._y += 6
        self._escribir(
            f"{_GRIS_LINEA} G",
            "1 w",
            f"{MARGEN:.2f} {self._py(self._y):.2f} m "
            f"{MARGEN + self.ancho_util:.2f} {self._py(self._y):.2f} l S",
            "0 G",
        )
        self._y += 4
        for etiqueta, valor in pares:
            self._y += tamano * _TAMANO_INTERLINEA
            self.texto(MARGEN + 4, self._y, etiqueta, negrita=True, tamano=tamano)
            self.texto(
                MARGEN + 4,
                self._y,
                valor,
                negrita=True,
                tamano=tamano,
                alineacion="derecha",
                ancho=self.ancho_util - 8,
            )
        self._y += 6

    # ------------------------------------------------------------------ #
    # Pie y archivo final
    # ------------------------------------------------------------------ #
    def _numerar_pie(self, indice: int) -> None:
        """Agrega la regla, la nota del pie y "Página X de Y"."""
        total = len(self._paginas)
        y = ALTO_A4 - MARGEN + 2
        self._paginas[indice].extend(
            [
                f"{_GRIS_LINEA} G".encode("ascii"),
                b"0.5 w",
                (
                    f"{MARGEN:.2f} {self._py(y):.2f} m "
                    f"{MARGEN + self.ancho_util:.2f} {self._py(y):.2f} l S"
                ).encode("ascii"),
                b"0 G",
            ]
        )
        self._texto_en(indice, MARGEN, ALTO_A4 - MARGEN + 14, self.nota_pie, tamano=8)
        self._texto_en(
            indice,
            MARGEN,
            ALTO_A4 - MARGEN + 14,
            f"Página {indice + 1} de {total}",
            tamano=8,
            alineacion="derecha",
            ancho=self.ancho_util,
        )

    def generar(self) -> bytes:
        """Cierra el documento y devuelve los bytes del PDF (listo para bajar)."""
        cantidad = len(self._paginas)
        for indice in range(cantidad):
            self._numerar_pie(indice)

        salida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        posiciones: dict[int, int] = {}

        def objeto(numero: int, contenido: bytes) -> None:
            posiciones[numero] = len(salida)
            salida.extend(f"{numero} 0 obj\n".encode("ascii"))
            salida.extend(contenido)
            salida.extend(b"\nendobj\n")

        def stream(numero: int, datos: bytes) -> None:
            objeto(
                numero,
                b"<< /Length %d >>\nstream\n" % len(datos) + datos + b"\nendstream",
            )

        total_objetos = 4 + 2 * cantidad
        hijos = " ".join(f"{5 + 2 * i} 0 R" for i in range(cantidad))
        objeto(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        objeto(
            2,
            f"<< /Type /Pages /Kids [{hijos}] /Count {cantidad} >>".encode("ascii"),
        )
        for numero, base in ((3, b"Helvetica"), (4, b"Helvetica-Bold")):
            objeto(
                numero,
                b"<< /Type /Font /Subtype /Type1 /BaseFont /"
                + base
                + b" /Encoding /WinAnsiEncoding >>",
            )
        recursos = b"<< /Font << /F1 3 0 R /F2 4 0 R >> >>"
        for indice, operadores in enumerate(self._paginas):
            pagina = 5 + 2 * indice
            contenido = 6 + 2 * indice
            objeto(
                pagina,
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources "
                % (ANCHO_A4, ALTO_A4)
                + recursos
                + b" /Contents %d 0 R >>" % contenido,
            )
            stream(contenido, b"\n".join(operadores))

        inicio_xref = len(salida)
        salida.extend(f"xref\n0 {total_objetos + 1}\n".encode("ascii"))
        salida.extend(b"0000000000 65535 f \n")
        for numero in range(1, total_objetos + 1):
            salida.extend(f"{posiciones[numero]:010d} 00000 n \n".encode("ascii"))
        salida.extend(
            (
                f"trailer\n<< /Size {total_objetos + 1} /Root 1 0 R >>\n"
                f"startxref\n{inicio_xref}\n%%EOF\n"
            ).encode("ascii")
        )
        return bytes(salida)


def nombre_archivo(*partes: object) -> str:
    """Arma un nombre de archivo seguro (sin acentos ni espacios) para la descarga."""
    limpio = "-".join(
        " ".join(str(parte).strip().split()).replace(" ", "-").lower()
        for parte in partes
        if str(parte or "").strip()
    )
    normalizado = (
        limpio.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
    )
    return "".join(
        caracter
        for caracter in normalizado
        if caracter.isascii() and (caracter.isalnum() or caracter in "-_.")
    )


def fecha_legible(instante: Optional[datetime]) -> str:
    """Fecha del reporte en formato ``dd/mm/aaaa`` (o ``-`` si falta)."""
    return instante.strftime("%d/%m/%Y") if instante else "-"

