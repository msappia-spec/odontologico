"""PDF de la Historia Clínica General sobre la plantilla de papel (Módulo 4).

El consultorio archiva la ficha médica en la **misma hoja** que usa en papel:
``app/resources/historia_clinica_general.pdf`` (las dos páginas oficiales, con
el encabezado, el cuestionario, el examen bucal, el odontograma y el
consentimiento). Este servicio abre una copia en memoria de esa plantilla y le
escribe encima los datos del paciente y lo que declaró, así lo impreso se ve
exactamente igual que una ficha completada a mano.

Decisiones de diseño:

* **La plantilla no se modifica**: se abre con PyMuPDF y se le agregan los
  textos y las cruces. La plantilla tampoco se toca en disco.
* **Sin coordenadas fijas**: cada dato se ubica buscando su etiqueta en la capa
  de texto del formulario (``get_text("dict")`` y ``search_for``) y las casillas
  SI/NO se reconocen por la fuente del glifo (**Wingdings 2**). Si algún día se
  reemplaza el PDF por una versión corregida, las etiquetas se vuelven a
  encontrar solas; la prueba ``tests/test_ficha_pdf.py`` avisa si alguna
  desapareció.
* **Sólo se escribe lo que tiene dato**: un campo vacío no tapa la línea del
  formulario y no inventa un «NO» en una ficha que todavía no se completó.
* **Lo que no entra en la hoja no se pierde**: el detalle del examen bucal, el
  odontograma y el texto que no cabe en las cuatro líneas del diagnóstico, del
  plan o de las observaciones van a una **página de anexo** (el formulario ya
  tiene el casillero «Continúa en Anexo Nº»).

El odontólogo del encabezado se resuelve, en este orden: el ``doctor_id`` que
pida la API, el profesional que firmó el odontograma y el de la última
evolución. La matrícula no se imprime porque el sistema no la guarda (queda en
blanco para que la escriba el profesional).

Los valores se escriben en negro con Helvetica (las fuentes estándar del PDF),
que es la misma familia tipográfica del formulario. Los acentos del castellano
se imprimen bien porque PyMuPDF codifica el texto en WinAnsi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.core.exceptions import RecursoNoEncontradoError
from app.core.pdf import nombre_archivo
from app.models.antecedentes_salud import AntecedentesSalud
from app.models.doctor import Doctor
from app.models.evolucion_medica import EvolucionMedica
from app.models.historia_clinica import HistoriaClinica
from app.models.odontograma import Odontograma
from app.models.paciente import Paciente

logger = logging.getLogger("app.services.ficha_pdf")

#: Plantilla oficial de dos páginas que se rellena.
RUTA_PLANTILLA = (
    Path(__file__).resolve().parents[1] / "resources" / "historia_clinica_general.pdf"
)

# Tamaño A4 en puntos (el de la plantilla) y margen del anexo.
ANCHO_A4 = 595.28
ALTO_A4 = 841.89
MARGEN_ANEXO = 40.0

# Tipografía de los valores y de los textos del anexo.
TAMANO = 8.5
TAMANO_ANEXO = 8.5
COLOR_TEXTO = (0.0, 0.0, 0.0)
COLOR_MARCA = (0.0, 0.0, 0.0)
GROSOR_MARCA = 0.9

#: Sangría de la X dentro de la casilla (el glifo de Wingdings 2 es más alto
#: que el cuadro visible, por eso se recorta arriba y abajo).
_INSET_CAJA_X = 1.1
_INSET_CAJA_ARRIBA = 2.4
_INSET_CAJA_ABAJO = 1.3

#: Límites horizontales de las dos columnas del cuestionario.
_LIMITE_IZQUIERDA = 299.0
_LIMITE_DERECHA = 580.0

#: Separación mínima para escribir un texto largo sobre la misma línea de la
#: etiqueta (si queda menos, se usa la línea punteada de abajo).
_ESPACIO_MINIMO_TEXTO = 60.0
#: Separación mínima para un «SI»/«NO» o una X (dos letras).
_ESPACIO_MINIMO_MARCA = 9.0

# --------------------------------------------------------------------------- #
# Texto y medidas
# --------------------------------------------------------------------------- #
def _ancho(texto: str, tamano: float = TAMANO, negrita: bool = False) -> float:
    """Ancho del texto en puntos con las métricas de la fuente del PDF."""
    return pymupdf.get_text_length(
        texto or "", fontname="hebo" if negrita else "helv", fontsize=tamano
    )


def _recortar(texto: str, ancho: float, tamano: float = TAMANO) -> str:
    """Recorta el texto con puntos suspensivos si no entra en ``ancho``."""
    if not texto or ancho <= 4:
        return ""
    if _ancho(texto, tamano) <= ancho:
        return texto
    recorte = texto
    while recorte and _ancho(f"{recorte}...", tamano) > ancho:
        recorte = recorte[:-1]
    return f"{recorte.rstrip()}..." if recorte else ""


def _envolver(texto: str, ancho: float, tamano: float = TAMANO) -> List[str]:
    """Parte el texto en líneas que entren en ``ancho`` puntos."""
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
        salida.append(actual)
    return salida


def _baseline(linea: dict) -> float:
    """Altura de la línea de base de una línea de la plantilla."""
    return float(linea["spans"][0]["origin"][1])


def _es_punteada(texto: str) -> bool:
    """``True`` si la línea es un renglón para completar (puntos o comas)."""
    limpio = (texto or "").strip()
    if len(limpio) < 8:
        return False
    puntos = sum(1 for caracter in limpio if caracter in ".…,·")
    return puntos / len(limpio) >= 0.7


def _escribir(
    pagina: pymupdf.Page,
    x: float,
    y: float,
    valor: Any,
    tamano: float = TAMANO,
    limite: Optional[float] = None,
    negrita: bool = False,
    borrar_punteado: bool = False,
) -> None:
    """Escribe un valor ajustado al espacio disponible (sin saltos de línea).

    ``borrar_punteado`` pinta de blanco el renglón debajo del valor: así los
    puntos del formulario no se ven entre las letras del dato escrito.
    """
    texto = " ".join(str(valor if valor is not None else "").split())
    if not texto:
        return
    if limite is not None:
        texto = _recortar(texto, limite - x, tamano)
    if not texto:
        return
    if borrar_punteado:
        ancho = _ancho(texto, tamano, negrita)
        pagina.draw_rect(
            pymupdf.Rect(x - 3.5, y - tamano + 1.0, x + ancho + 1.0, y + 2.8),
            color=None,
            fill=(1.0, 1.0, 1.0),
            width=0,
        )
    pagina.insert_text(
        (x, y),
        texto,
        fontname="hebo" if negrita else "helv",
        fontsize=tamano,
        color=COLOR_TEXTO,
    )


def _escribir_lineas(
    pagina: pymupdf.Page,
    x: float,
    y: float,
    ancho: float,
    valor: Any,
    tamano: float = TAMANO,
    siguientes: Sequence[float] = (),
) -> bool:
    """Escribe un texto envuelto en varias líneas. Devuelve si hubo que cortar."""
    texto = " ".join(str(valor if valor is not None else "").split())
    if not texto:
        return False
    lineas = _envolver(texto, ancho, tamano)
    disponibles = 1 + len(siguientes)
    recortado = len(lineas) > disponibles
    if recortado:
        sobrante = " ".join(lineas[disponibles - 1 :])
        lineas = lineas[:disponibles]
        lineas[-1] = _recortar(sobrante, ancho, tamano)
    _escribir(pagina, x, y, lineas[0], tamano, x + ancho, borrar_punteado=True)
    for base, contenido in zip(siguientes, lineas[1:]):
        _escribir(
            pagina, x, base, contenido, tamano, x + ancho, borrar_punteado=True
        )
    return recortado


def _marcar(pagina: pymupdf.Page, caja: dict) -> None:
    """Dibuja una X dentro de una casilla SI/NO de la plantilla."""
    x0, y0, x1, y1 = caja["bbox"]
    izquierda = x0 + _INSET_CAJA_X
    derecha = x1 - _INSET_CAJA_X
    arriba = y0 + _INSET_CAJA_ARRIBA
    abajo = y1 - _INSET_CAJA_ABAJO
    pagina.draw_line(
        (izquierda, arriba), (derecha, abajo), color=COLOR_MARCA, width=GROSOR_MARCA
    )
    pagina.draw_line(
        (derecha, arriba), (izquierda, abajo), color=COLOR_MARCA, width=GROSOR_MARCA
    )





# --------------------------------------------------------------------------- #
# Capa de texto de una hoja
# --------------------------------------------------------------------------- #
class _Hoja:
    """Índice de la capa de texto de una página de la plantilla.

    Ubicar las etiquetas por su texto (y no por coordenadas) es lo que permite
    reemplazar la plantilla sin reescribir el servicio: alcanza con que las
    etiquetas sigan existiendo.
    """

    def __init__(self, pagina: pymupdf.Page):
        self.pagina = pagina
        self.lineas: List[dict] = []
        for bloque in pagina.get_text("dict")["blocks"]:
            if bloque.get("type") != 0:
                continue
            for linea in bloque["lines"]:
                spans = [s for s in linea["spans"] if s["text"].strip()]
                if not spans:
                    continue
                self.lineas.append(
                    {
                        "bbox": pymupdf.Rect(linea["bbox"]),
                        "spans": spans,
                        "texto": "".join(s["text"] for s in spans),
                    }
                )

    # -- Búsqueda -------------------------------------------------------- #
    def linea(self, texto: str, desde: Optional[float] = None) -> Optional[dict]:
        """Primera línea que contiene ``texto`` (a partir de ``desde``)."""
        buscado = (texto or "").lower()
        for linea in sorted(self.lineas, key=lambda l: l["bbox"].y0):
            if desde is not None and linea["bbox"].y0 < desde:
                continue
            if buscado in linea["texto"].lower():
                return linea
        return None

    def rect(self, linea: Optional[dict], texto: str) -> Optional[pymupdf.Rect]:
        """Rectángulo del texto dentro de la línea (o la línea completa)."""
        if linea is None:
            return None
        encontrados = self.pagina.search_for(texto, clip=linea["bbox"])
        if encontrados:
            return encontrados[0]
        return pymupdf.Rect(linea["bbox"])

    # -- Casillas -------------------------------------------------------- #
    def _spans_de_casillas(self, linea: dict) -> List[dict]:
        """Glifos de casilla (Wingdings 2) que comparten la fila de ``linea``."""
        centro = (linea["bbox"].y0 + linea["bbox"].y1) / 2
        salida: List[dict] = []
        for otra in self.lineas:
            otro_centro = (otra["bbox"].y0 + otra["bbox"].y1) / 2
            if abs(otro_centro - centro) > 5:
                continue
            salida.extend(
                span for span in otra["spans"] if "Wingdings" in span["font"]
            )
        return sorted(salida, key=lambda s: s["bbox"][0])

    def cajas(self, linea: dict, izquierda: bool) -> List[dict]:
        """Casillas de la misma columna que la pregunta.

        La plantilla tiene dos columnas: las casillas de la izquierda empiezan
        antes de ``x = 305`` y las de la derecha después. Así una pregunta de la
        izquierda no toma las casillas de la pregunta que está enfrente.
        """
        salida = []
        for caja in self._spans_de_casillas(linea):
            x0 = caja["bbox"][0]
            if izquierda and x0 >= 305:
                continue
            if not izquierda and x0 < 305:
                continue
            salida.append(caja)
        return salida

    def cajas_fila(self, linea: dict) -> List[dict]:
        """Todas las casillas de la fila (para las preguntas de ancho completo)."""
        return self._spans_de_casillas(linea)

    def caja_despues(self, linea: dict, texto: str) -> Optional[dict]:
        """Primera casilla a la derecha de ``texto`` en la fila."""
        if linea is None:
            return None
        rect = self.rect(linea, texto)
        if rect is None:
            return None
        for caja in self._spans_de_casillas(linea):
            if caja["bbox"][0] >= rect.x1 - 0.5:
                return caja
        return None

    # -- Renglones para completar ---------------------------------------- #
    def lineas_punteadas(self, desde_y: float, x0: float, cantidad: int) -> List[dict]:
        """Renglones punteados que siguen a una etiqueta, en la misma columna.

        Se corta en la primera línea con texto (otra pregunta o un título): el
        bloque de renglones de una pregunta termina ahí, así un texto largo no
        se derrama en el bloque de la pregunta siguiente.
        """
        salida: List[dict] = []
        for linea in sorted(self.lineas, key=lambda l: l["bbox"].y0):
            if linea["bbox"].y0 <= desde_y + 0.5:
                continue
            if abs(linea["bbox"].x0 - x0) > 8:
                continue
            if not _es_punteada(linea["texto"]):
                if salida:
                    break
                continue
            salida.append(linea)
            if len(salida) >= cantidad:
                break
        return salida



# --------------------------------------------------------------------------- #
# Mapa de la plantilla
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DatoEncabezado:
    """Dato filiatorio del encabezado y las etiquetas que lo delimitan."""

    clave: str
    etiqueta: str
    siguiente: Optional[str] = None


@dataclass(frozen=True)
class Texto:
    """Texto libre (o una marca) que se escribe sobre un renglón del formulario.

    * ``clave``: campo de la ficha o del paciente.
    * ``etiqueta``: texto de la plantilla donde empieza el renglón.
    * ``lineas``: renglones disponibles (el primero puede ser el de la etiqueta).
    * ``despues_de``: pregunta anterior que desambigua etiquetas repetidas.
    * ``en_linea``: ``False`` para empezar en el renglón punteado de abajo.
    * ``modo``: ``texto`` | ``si_no`` | ``solo_si`` | ``numero`` | ``fecha``.
    """

    clave: str
    etiqueta: str
    lineas: int = 1
    despues_de: Optional[str] = None
    en_linea: bool = True
    modo: str = "texto"


#: Datos del encabezado de la página 1, en el orden en que aparecen.
ENCABEZADO: Tuple[DatoEncabezado, ...] = (
    DatoEncabezado("obra_social", "O. Social:", "F.Nac"),
    DatoEncabezado("fecha_nacimiento", "F.Nac", "Tel."),
    DatoEncabezado("telefono", "Tel."),
    DatoEncabezado("edad", "Edad", "Estado Civil"),
    DatoEncabezado("estado_civil", "Estado Civil", "Nacionalidad"),
    DatoEncabezado("nacionalidad", "Nacionalidad", "Nº de Doc"),
    DatoEncabezado("dni", "Nº de Doc", "Cel"),
    DatoEncabezado("domicilio", "localidad):"),
    DatoEncabezado("profesion", "Profesión/Actividad", "Titular:"),
    DatoEncabezado("titular", "Titular:", "Lugar de trabajo"),
    DatoEncabezado("lugar_trabajo", "Lugar de trabajo", "Jerarquía"),
    DatoEncabezado("jerarquia", "Jerarquía"),
)

#: Preguntas SI/NO (dos casillas) y el campo booleano que las responde.
MARCAS_SI_NO: Tuple[Tuple[str, str], ...] = (
    # Antecedentes familiares
    ("padre_vive", "Padre con vida?"),
    ("madre_vive", "Madre con vida?"),
    ("hermanos", "Hermanos?"),
    # Antecedentes personales (columna izquierda)
    ("enfermedad_actual", "Sufre de alguna enfermedad?"),
    ("tratamiento_medico", "Hace algún tratamiento médico?"),
    ("deporte", "Realiza algún deporte?"),
    ("deporte_malestar", "Nota algún malestar al realizarlo"),
    ("cicatriza_bien", "cicatriza bien?"),
    ("colageno", "Tiene problema de colágeno"),
    ("fiebre_reumatica", "Antecedentes de fiebre reumática?"),
    ("diabetes", "Es diabético?"),
    ("problemas_cardiacos", "Tiene algún problema cardíaco?"),
    ("anticoagulantes", "Toma seguido aspirina"),
    ("presion_alta", "presión alta?"),
    ("chagas", "Chagas?"),
    ("problemas_renales", "Tiene problemas renales?"),
    ("ulcera_gastrica", "Ulcera Gástrica?"),
    ("hepatitis", "Tuvo hepatitis?"),
    ("problemas_hepaticos", "Tiene algún problema hepático?"),
    ("convulsiones", "Tuvo convulsiones?"),
    ("epilepsia", "Es epiléptico?"),
    ("sifilis_gonorrea", "Ha tenido Sífilis o Gonorrea?"),
    # Antecedentes personales (columna derecha)
    ("enfermedad_infectocontagiosa", "Otra enfermedad infecto-contagiosa?"),
    ("transfusiones", "Tuvo transfusiones?"),
    ("operaciones", "Fue operado alguna vez?"),
    ("problemas_respiratorios", "Tiene algún problema respiratorio?"),
    ("fuma", "Fuma?"),
    ("embarazo", "Está embarazada?"),
    ("otra_enfermedad", "médico que quiera dejar constancia?"),
    # Historia clínica odontológica
    ("consulta_previa", "otro profesional?"),
    ("medicacion_previa", "Tomó algún medicamento?"),
    ("medicacion_previa_resultados", "Obtuvo resultados"),
    ("traumatismo_dental", "Sufrió algún golpe en los dientes?"),
    ("fractura_dental", "Se le fracturó algún diente?"),
    # Examen bucal (página 2)
    ("lesiones_manchas", "Manchas?"),
    ("lesiones_abultamiento", "Abultamiento de los tejidos?"),
    ("lesiones_ulceraciones", "Ulceraciones?"),
    ("lesiones_ampollas", "Ampollas?"),
    ("sangrado_encias", "Le sangran las encías?"),
    ("pus_boca", "Sale pus de algún lugar de su boca?"),
    ("movilidad_dental", "Tiene movilidad en sus dientes?"),
    ("cara_hinchada", "Ha tenido la cara hinchada?"),
)

#: Casilla única: se marca cuando el campo es verdadero (no hay casilla «NO»).
MARCAS_SIMPLES: Tuple[Tuple[str, str], ...] = (
    ("dolor", "Ha tenido dolor?"),
)

#: Preguntas de ancho completo cuyas casillas se marcan por orden.
OPCIONES_POR_ORDEN: Tuple[Tuple[str, str, Tuple[str, ...], Optional[str]], ...] = (
    ("hepatitis_tipo", "de qué tipo?", ("A", "B", "C"), "Tuvo hepatitis?"),
    (
        "higiene_bucal",
        "Muy bueno",
        ("MUY_BUENO", "BUENO", "DEFICIENTE", "MALO"),
        None,
    ),
)

#: Preguntas cuyas casillas se identifican por la etiqueta de cada opción.
OPCIONES_POR_ETIQUETA: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = (
    (
        "dolor_intensidad",
        (("SUAVE", "Suave"), ("MODERADO", "Moderado"), ("INTENSO", "Intenso")),
    ),
    (
        "dolor_duracion",
        (
            ("TEMPORARIO", "Temporario"),
            ("INTERMITENTE", "Intermitente"),
            ("CONTINUO", "Continuo"),
        ),
    ),
    (
        "dolor_desencadenante",
        (("ESPONTANEO", "Espontáneo"), ("PROVOCADO", "Provocado")),
    ),
    ("dolor_estimulo", (("FRIO", "Al frío"), ("CALOR", "Al calor"))),
    (
        "dolor_localizacion",
        (("LOCALIZADO", "Localizado"), ("IRRADIADO", "Irradiado")),
    ),
)

#: Bloques de cuatro renglones al pie de la página 2.
BLOQUES: Tuple[Tuple[str, str], ...] = (
    ("diagnostico_presuntivo", "Diagnóstico presuntivo"),
    ("plan_tratamiento", "Plan de tratamiento"),
    ("observaciones", "Observaciones:"),
)


#: Textos, marcas y fechas que se escriben sobre los renglones del formulario.
TEXTOS: Tuple[Texto, ...] = (
    # -- Antecedentes familiares --------------------------------------- #
    Texto("padre_patologias", "padeció", lineas=2, despues_de="Padre con vida?"),
    Texto("madre_patologias", "padeció", lineas=2, despues_de="Madre con vida?"),
    # -- Antecedentes personales (columna izquierda) -------------------- #
    Texto("enfermedad_actual_detalle", "de que?"),
    Texto(
        "tratamiento_medico_detalle",
        "cuál?",
        despues_de="Hace algún tratamiento médico?",
    ),
    Texto("medicacion_actual", "consume habitualmente?", lineas=3),
    Texto(
        "medicamentos_ultimos_5_anios",
        "medicamentos ha consumido",
        lineas=2,
        en_linea=False,
    ),
    Texto("alergia_anestesia", "a la anestesia", modo="solo_si"),
    Texto("alergia_penicilina", "a la penicilina", modo="solo_si"),
    Texto("sangra_mucho", "sangra mucho?", modo="si_no"),
    Texto("fiebre_reumatica_proteccion", "Se protege con alguna medicación?"),
    Texto("diabetes_controlada", "controlado?", modo="si_no"),
    Texto("diabetes_tratamiento", "con qué?"),
    Texto(
        "problemas_cardiacos_detalle",
        "cuál?",
        despues_de="Tiene algún problema cardíaco?",
    ),
    Texto("anticoagulantes_frecuencia", "con qué frecuencia?"),
    Texto("chagas_tratamiento", "está en tratamiento?"),
    Texto(
        "problemas_hepaticos_detalle",
        "cuál?",
        despues_de="Tiene algún problema hepático?",
    ),
    Texto("epilepsia_medicacion", "medicación que toma"),
    Texto(
        "enfermedad_infectocontagiosa_detalle",
        "cuál?",
        despues_de="Otra enfermedad infecto-contagiosa?",
    ),
    Texto("operaciones_detalle", "de qué?", despues_de="Fue operado alguna vez?"),
    Texto("operaciones_cuando", "Cuando?", despues_de="Fue operado alguna vez?"),
    Texto(
        "problemas_respiratorios_detalle",
        "cuál?",
        despues_de="Tiene algún problema respiratorio?",
    ),
    Texto("embarazo_meses", "de cuántos", modo="numero"),
    Texto(
        "otra_enfermedad_detalle",
        "cuál?",
        lineas=2,
        despues_de="constancia?",
    ),
    Texto(
        "tratamientos_alternativos",
        "tratamiento homeopático",
        en_linea=False,
    ),
    Texto("medico_clinico", "Médico clínico:"),
    Texto("centro_derivacion", "derivación:", lineas=2),
    # -- Historia clínica odontológica ---------------------------------- #
    Texto("medicacion_previa_detalle", "Nombre de los medicamentos", lineas=2),
    Texto("medicacion_previa_desde", "Desde cuando?"),
    Texto("dolor_calmante", "Puede calmarlo con algo?", lineas=2),
    Texto(
        "traumatismo_cuando",
        "cuándo?",
        despues_de="Sufrió algún golpe en los dientes?",
    ),
    Texto("traumatismo_como", "cómo se produjo?", en_linea=False),
    Texto(
        "fractura_cual",
        "cuál?",
        despues_de="Se le fracturó algún diente?",
    ),
    Texto(
        "fractura_tratamiento",
        "recibió algún tratamiento?",
        lineas=2,
        en_linea=False,
    ),
    # -- Examen bucal (página 2) ---------------------------------------- #
    Texto("labios_anormal", "labios?", modo="solo_si"),
    Texto("lengua_anormal", "lengua?", modo="solo_si"),
    Texto("paladar_anormal", "paladar?", modo="solo_si"),
    Texto("piso_boca_anormal", "piso de boca?", modo="solo_si"),
    Texto("carrillos_anormal", "carrillos?", modo="solo_si"),
    Texto("rebordes_anormal", "rebordes?", modo="solo_si"),
    Texto("trigono_anormal", "trígono", modo="solo_si"),
    Texto("retromolar_anormal", "retromolar?", modo="solo_si"),
    Texto("lesiones_otros", "Otros:", lineas=2),
    Texto(
        "sangrado_encias_cuando",
        "cuándo?",
        despues_de="Le sangran las encías?",
    ),
    Texto("pus_boca_donde", "de dónde?"),
    Texto("dientes_altos", "altos los dientes?", modo="si_no"),
    Texto("cara_hinchada_hielo", "hielo?", modo="solo_si"),
    Texto("cara_hinchada_calor", "calor?", modo="solo_si"),
    Texto("cara_hinchada_otros", "otros?", despues_de="Ha tenido la cara hinchada?"),
    Texto("momentos_azucar", "Momentos de azúcar diario", modo="numero"),
    Texto("indice_placa", "Indice de placa"),
    Texto("fecha_plan", "fecha:", despues_de="Plan de tratamiento", modo="fecha"),
)


# --------------------------------------------------------------------------- #
# Valores de la ficha y del paciente
# --------------------------------------------------------------------------- #
def _texto(valor: Any) -> Optional[str]:
    """Texto limpio, o ``None`` si el campo está vacío."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _fecha(valor: Any) -> Optional[str]:
    """Fecha en formato ``dd/mm/aaaa`` (acepta ``date`` o ISO ``AAAA-MM-DD``)."""
    if isinstance(valor, (date, datetime)):
        return valor.strftime("%d/%m/%Y")
    texto = _texto(valor)
    if not texto:
        return None
    try:
        return date.fromisoformat(texto[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return texto


def _edad(nacimiento: Any) -> Optional[int]:
    """Edad en años cumplidos (``None`` si no hay fecha de nacimiento)."""
    if not isinstance(nacimiento, date):
        return None
    if isinstance(nacimiento, datetime):
        nacimiento = nacimiento.date()
    hoy = utcnow().date()
    cumplio = (hoy.month, hoy.day) >= (nacimiento.month, nacimiento.day)
    return hoy.year - nacimiento.year - (0 if cumplio else 1)


def _formatear(valor: Any, modo: str) -> Optional[str]:
    """Convierte el valor de un campo al texto que se imprime."""
    if modo == "si_no":
        return None if valor is None else ("SI" if valor else "NO")
    if modo == "solo_si":
        return "SI" if valor else None
    if modo == "numero":
        return None if valor is None or valor == "" else str(valor)
    if modo == "fecha":
        return _fecha(valor)
    return _texto(valor)


def _dato_paciente(paciente: Paciente, clave: str) -> Optional[str]:
    """Valor de un dato del encabezado (los filiatorios viven en el paciente)."""
    if clave == "edad":
        return _formatear(_edad(getattr(paciente, "fecha_nacimiento", None)), "numero")
    if clave == "fecha_nacimiento":
        return _fecha(getattr(paciente, "fecha_nacimiento", None))
    if clave == "obra_social":
        cobertura = getattr(paciente, "obra_social", None)
        if isinstance(cobertura, str):
            return _texto(cobertura)
        return _texto(getattr(cobertura, "nombre", None))
    if clave == "celular":
        # El sistema guarda un solo teléfono: se imprime en «Tel.».
        return None
    return _texto(getattr(paciente, clave, None))


def _limite(linea: dict) -> float:
    """Borde derecho de la columna donde está la etiqueta."""
    return _LIMITE_IZQUIERDA if linea["bbox"].x0 < 300 else _LIMITE_DERECHA


# --------------------------------------------------------------------------- #
# Página 1: encabezado
# --------------------------------------------------------------------------- #
def _escribir_encabezado(
    hoja: _Hoja,
    paciente: Paciente,
    fecha: Optional[date],
    doctor: Optional[Doctor] = None,
) -> None:
    """Completa el lugar, la fecha, el odontólogo y los filiatorios."""
    pagina = hoja.pagina

    linea = hoja.linea("Lugar:")
    if linea is not None:
        rect_lugar = hoja.rect(linea, "Lugar:")
        rect_fecha = hoja.rect(linea, "Fecha")
        limite = rect_fecha.x0 - 4 if rect_fecha is not None else None
        _escribir(
            pagina,
            rect_lugar.x1 + 4,
            _baseline(linea),
            settings.whatsapp_consultorio_direccion,
            TAMANO,
            limite,
        )
        if rect_fecha is not None:
            _escribir(
                pagina,
                rect_fecha.x1 + 4,
                _baseline(linea),
                _fecha(fecha),
                TAMANO,
                552,
            )

    linea = hoja.linea("ODONTOLOGO:")
    rect = hoja.rect(linea, "ODONTOLOGO:")
    if rect is not None:
        _escribir(
            pagina,
            rect.x1 + 4,
            _baseline(linea),
            getattr(doctor, "nombre", None),
            TAMANO,
            455,
        )

    linea = hoja.linea("PACIENTE")
    if linea is not None:
        rect_nombre = hoja.rect(linea, "PACIENTE")
        rect_afil = hoja.rect(linea, "Nº AFIL")
        limite = rect_afil.x0 - 6 if rect_afil is not None else 320
        _escribir(
            pagina,
            rect_nombre.x1 + 6,
            _baseline(linea),
            _texto(getattr(paciente, "nombre", None)),
            9.5,
            limite,
        )
        if rect_afil is not None:
            _escribir(
                pagina,
                rect_afil.x1 + 8,
                _baseline(linea),
                _texto(getattr(paciente, "numero_afiliado", None)),
                TAMANO,
                543,
            )

    for dato in ENCABEZADO:
        linea = hoja.linea(dato.etiqueta)
        if linea is None:
            logger.warning("La plantilla no tiene la etiqueta %r.", dato.etiqueta)
            continue
        rect = hoja.rect(linea, dato.etiqueta)
        if rect is None:
            continue
        limite = 558.0
        if dato.siguiente:
            rect_siguiente = hoja.rect(linea, dato.siguiente)
            if rect_siguiente is not None:
                limite = rect_siguiente.x0 - 8
        _escribir(
            pagina,
            rect.x1 + 3,
            _baseline(linea),
            _dato_paciente(paciente, dato.clave),
            TAMANO,
            limite,
        )


# --------------------------------------------------------------------------- #
# Página 1 y 2: cuestionario
# --------------------------------------------------------------------------- #
def _marcar_si_no(hoja: _Hoja, ficha: AntecedentesSalud) -> None:
    """Marca SI o NO en cada pregunta de dos casillas."""
    pagina = hoja.pagina
    for clave, pregunta in MARCAS_SI_NO:
        valor = getattr(ficha, clave, None)
        if valor is None:
            continue
        linea = hoja.linea(pregunta)
        if linea is None:
            logger.debug("La plantilla no tiene la pregunta %r.", pregunta)
            continue
        cajas = hoja.cajas(linea, linea["bbox"].x0 < 300)
        if len(cajas) < 2:
            logger.debug("La pregunta %r no tiene dos casillas.", pregunta)
            continue
        _marcar(pagina, cajas[0] if valor else cajas[1])


def _marcar_alergias(hoja: _Hoja, ficha: AntecedentesSalud) -> None:
    """«¿Es alérgico a alguna droga?» se deduce de las alergias declaradas."""
    alergia = bool(
        getattr(ficha, "alergia_anestesia", False)
        or getattr(ficha, "alergia_penicilina", False)
        or _texto(getattr(ficha, "alergias_medicamentos", None))
        or _texto(getattr(ficha, "alergia_otros", None))
    )
    linea = hoja.linea("Es alérgico a alguna droga?")
    if linea is None:
        return
    cajas = hoja.cajas(linea, True)
    if len(cajas) >= 2:
        _marcar(hoja.pagina, cajas[0] if alergia else cajas[1])


def _marcar_simples(hoja: _Hoja, ficha: AntecedentesSalud) -> None:
    """Casillas sueltas que sólo existen cuando la respuesta es afirmativa."""
    for clave, etiqueta in MARCAS_SIMPLES:
        if not getattr(ficha, clave, False):
            continue
        linea = hoja.linea(etiqueta)
        caja = hoja.caja_despues(linea, etiqueta) if linea else None
        if caja is not None:
            _marcar(hoja.pagina, caja)


def _marcar_opciones(hoja: _Hoja, ficha: AntecedentesSalud) -> None:
    """Marca la casilla de las respuestas cerradas (hepatitis, dolor, higiene)."""
    pagina = hoja.pagina
    for clave, ancla, valores, despues_de in OPCIONES_POR_ORDEN:
        valor = getattr(ficha, clave, None)
        if not valor or valor not in valores:
            continue
        desde = None
        if despues_de:
            linea_previa = hoja.linea(despues_de)
            desde = linea_previa["bbox"].y0 if linea_previa is not None else None
        linea = hoja.linea(ancla, desde=desde)
        if linea is None:
            continue
        cajas = hoja.cajas(linea, linea["bbox"].x0 < 300)
        indice = valores.index(valor)
        if indice < len(cajas):
            _marcar(pagina, cajas[indice])
    for clave, opciones in OPCIONES_POR_ETIQUETA:
        valor = getattr(ficha, clave, None)
        if not valor:
            continue
        for opcion, etiqueta in opciones:
            if opcion != valor:
                continue
            linea = hoja.linea(etiqueta)
            caja = hoja.caja_despues(linea, etiqueta) if linea else None
            if caja is not None:
                _marcar(pagina, caja)


def _marcar_estado_bucal(hoja: _Hoja, ficha: AntecedentesSalud) -> None:
    """Sarro y enfermedad periodontal comparten una fila de cuatro casillas."""
    linea = hoja.linea("Presencia de sarro")
    if linea is None:
        return
    cajas = hoja.cajas_fila(linea)
    if len(cajas) < 4:
        return
    pagina = hoja.pagina
    _marcar(pagina, cajas[0] if getattr(ficha, "sarro", False) else cajas[1])
    _marcar(
        pagina,
        cajas[2] if getattr(ficha, "enfermedad_periodontal", False) else cajas[3],
    )


def _escribir_texto(hoja: _Hoja, entrada: Texto, valor: str) -> None:
    """Escribe un valor sobre la etiqueta o sobre el renglón que le sigue."""
    if not valor:
        return
    pagina = hoja.pagina
    desde = None
    if entrada.despues_de:
        ancla = hoja.linea(entrada.despues_de)
        if ancla is None:
            # La pregunta pertenece a la otra página del formulario.
            return
        desde = ancla["bbox"].y0
    linea = hoja.linea(entrada.etiqueta, desde=desde)
    if linea is None:
        logger.debug("La plantilla no tiene la etiqueta %r.", entrada.etiqueta)
        return

    rect = hoja.rect(linea, entrada.etiqueta)
    tope = _limite(linea)
    y = _baseline(linea)
    renglones = hoja.lineas_punteadas(linea["bbox"].y0 + 2, linea["bbox"].x0, 4)
    bases = [_baseline(renglon) for renglon in renglones]

    if entrada.en_linea and rect is not None:
        espacio = tope - (rect.x1 + 5)
        minimo = (
            _ESPACIO_MINIMO_TEXTO
            if entrada.modo == "texto"
            else _ESPACIO_MINIMO_MARCA
        )
        if entrada.lineas > 1:
            # Un texto de varios renglones necesita la línea completa de abajo.
            minimo = max(minimo, 100.0)
        if espacio >= minimo:
            x = rect.x1 + 5
            _escribir_lineas(
                pagina,
                x,
                y,
                tope - x,
                valor,
                TAMANO,
                bases[: max(entrada.lineas - 1, 0)],
            )
            return

    if not bases:
        if rect is not None:
            x = rect.x1 + 5
            _escribir_lineas(pagina, x, y, max(tope - x, 20), valor, TAMANO)
        return

    x = linea["bbox"].x0 + 1
    _escribir_lineas(
        pagina, x, bases[0], tope - x, valor, TAMANO, bases[1: entrada.lineas]
    )


def _escribir_textos(hoja: _Hoja, ficha: AntecedentesSalud) -> None:
    """Recorre la tabla de textos y escribe los que tienen dato."""
    for entrada in TEXTOS:
        valor = _formatear(getattr(ficha, entrada.clave, None), entrada.modo)
        if valor:
            _escribir_texto(hoja, entrada, valor)

    hermanos = _formatear(getattr(ficha, "hermanos_sanos", None), "si_no")
    if hermanos:
        detalle = _texto(getattr(ficha, "hermanos_detalle", None))
        _escribir_texto(
            hoja,
            Texto("hermanos_sanos", "sanos?"),
            f"{hermanos} ({detalle})" if detalle else hermanos,
        )

    alergias = " · ".join(
        texto
        for texto in (
            _texto(getattr(ficha, "alergias_medicamentos", None)),
            _texto(getattr(ficha, "alergia_otros", None)),
        )
        if texto
    )
    if alergias:
        _escribir_texto(
            hoja,
            Texto("alergias", "otros", despues_de="Es alérgico a alguna droga?"),
            alergias,
        )

    zona = _texto(getattr(ficha, "dolor_zona", None))
    if zona:
        irradiado = getattr(ficha, "dolor_localizacion", None) == "IRRADIADO"
        _escribir_texto(
            hoja,
            Texto(
                "dolor_zona",
                "hacia dónde?" if irradiado else "dónde?",
                despues_de="Ha tenido dolor?",
            ),
            zona,
        )


def _marcar_continuacion(hoja: _Hoja, titulo: str) -> None:
    """Escribe «1» en el casillero «Continúa en Anexo Nº» del bloque."""
    linea_titulo = hoja.linea(titulo)
    if linea_titulo is None:
        return
    linea = hoja.linea("Continúa en Anexo", desde=linea_titulo["bbox"].y1)
    if linea is None:
        return
    rect = hoja.rect(linea, "Nº")
    if rect is None:
        return
    _escribir(hoja.pagina, rect.x1 + 4, _baseline(linea), "1", 7.5, 566)


def _escribir_bloques(
    hoja: _Hoja, ficha: AntecedentesSalud
) -> List[Tuple[str, str]]:
    """Diagnóstico, plan y observaciones. Devuelve los textos que no entraron."""
    continuaciones: List[Tuple[str, str]] = []
    for clave, titulo in BLOQUES:
        valor = _texto(getattr(ficha, clave, None))
        if not valor:
            continue
        linea_titulo = hoja.linea(titulo)
        if linea_titulo is None:
            logger.debug("La plantilla no tiene el bloque %r.", titulo)
            continue
        renglones = hoja.lineas_punteadas(
            linea_titulo["bbox"].y1, linea_titulo["bbox"].x0, 4
        )
        if not renglones:
            continue
        x = renglones[0]["bbox"].x0 + 1
        recortado = _escribir_lineas(
            hoja.pagina,
            x,
            _baseline(renglones[0]),
            448 - x,
            valor,
            7.5,
            [_baseline(renglon) for renglon in renglones[1:]],
        )
        if recortado:
            continuaciones.append((titulo, valor))
            _marcar_continuacion(hoja, titulo)
    return continuaciones


def _escribir_cuestionario(
    hoja_uno: _Hoja, hoja_dos: _Hoja, ficha: AntecedentesSalud
) -> List[Tuple[str, str]]:
    """Completa las dos páginas del cuestionario."""
    for hoja in (hoja_uno, hoja_dos):
        _marcar_si_no(hoja, ficha)
        _marcar_simples(hoja, ficha)
        _marcar_opciones(hoja, ficha)
        _escribir_textos(hoja, ficha)
    _marcar_alergias(hoja_uno, ficha)
    _marcar_estado_bucal(hoja_dos, ficha)
    return _escribir_bloques(hoja_dos, ficha)


# --------------------------------------------------------------------------- #
# Página 2: consentimiento
# --------------------------------------------------------------------------- #
def _escribir_consentimiento(
    hoja: _Hoja, paciente: Paciente, doctor: Optional[Doctor] = None
) -> None:
    """Nombre, DNI y domicilio del consentimiento (la firma queda en blanco)."""
    pagina = hoja.pagina
    linea = hoja.linea("El/la que suscribe")
    if linea is not None:
        y = _baseline(linea)
        rect_nombre = hoja.rect(linea, "El/la que suscribe")
        rect_dni = hoja.rect(linea, "DNI Nº")
        rect_domicilio = hoja.rect(linea, "con domicilio en calle")
        limite = rect_dni.x0 - 4 if rect_dni is not None else 400
        _escribir(
            pagina,
            rect_nombre.x1 + 4,
            y,
            _texto(getattr(paciente, "nombre", None)),
            7.5,
            limite,
        )
        if rect_dni is not None:
            limite = rect_domicilio.x0 - 4 if rect_domicilio is not None else 560
            _escribir(
                pagina,
                rect_dni.x1 + 4,
                y,
                _texto(getattr(paciente, "dni", None)),
                7.5,
                limite,
            )
        if rect_domicilio is not None:
            _escribir(
                pagina,
                rect_domicilio.x1 + 4,
                y,
                _texto(getattr(paciente, "domicilio", None)),
                7.5,
                560,
            )

    linea = hoja.linea("Dr/a MP")
    rect = hoja.rect(linea, "Dr/a MP")
    if rect is not None:
        _escribir(
            pagina,
            rect.x1 + 4,
            _baseline(linea),
            _texto(getattr(doctor, "nombre", None)),
            7.5,
            560,
        )


# --------------------------------------------------------------------------- #
# Anexo
# --------------------------------------------------------------------------- #
#: Cuadrantes permanentes en el orden del formulario: arriba y abajo, con la
#: línea media entre el grupo derecho y el izquierdo.
GRUPOS_FDI: Tuple[Tuple[Tuple[int, ...], Tuple[int, ...]], ...] = (
    ((18, 17, 16, 15, 14, 13, 12, 11), (21, 22, 23, 24, 25, 26, 27, 28)),
    ((48, 47, 46, 45, 44, 43, 42, 41), (31, 32, 33, 34, 35, 36, 37, 38)),
)

#: Relleno y borde de cada estado del odontograma (los colores del PDF).
COLORES_DIENTE = {
    "EXISTENTE": ((0.99, 0.90, 0.90), (0.72, 0.16, 0.16)),
    "REQUERIDO": ((0.90, 0.94, 0.99), (0.16, 0.34, 0.72)),
    "AUSENTE": ((0.93, 0.93, 0.93), (0.35, 0.35, 0.35)),
}


class _Anexo:
    """Página(s) de anexo con títulos, párrafos y el odontograma."""

    LIMITE = ALTO_A4 - 50

    def __init__(
        self,
        documento: pymupdf.Document,
        paciente: Paciente,
        numero_ficha: Optional[str] = None,
    ):
        self.documento = documento
        self.paciente = paciente
        self.numero_ficha = numero_ficha
        self.pagina: pymupdf.Page
        self.y = 0.0
        self.paginas = 0
        self._nueva_pagina()

    # -- Estructura ------------------------------------------------------ #
    def _nueva_pagina(self, continuacion: bool = False) -> None:
        self.pagina = self.documento.new_page(width=ANCHO_A4, height=ALTO_A4)
        self.paginas += 1
        self.pagina.draw_rect(
            pymupdf.Rect(0, 0, ANCHO_A4, 10),
            color=None,
            fill=(0.184, 0.522, 0.478),
            width=0,
        )
        titulo = (
            "ANEXO — continuación"
            if continuacion
            else "ANEXO · HISTORIA CLÍNICA GENERAL"
        )
        self.pagina.insert_text(
            (MARGEN_ANEXO, 46),
            titulo,
            fontname="hebo",
            fontsize=14,
            color=COLOR_TEXTO,
        )
        datos = [self.paciente.nombre]
        if getattr(self.paciente, "dni", None):
            datos.append(f"DNI {self.paciente.dni}")
        if self.numero_ficha:
            datos.append(f"Ficha {self.numero_ficha}")
        self.pagina.insert_text(
            (MARGEN_ANEXO, 62),
            " · ".join(datos),
            fontname="helv",
            fontsize=8.5,
            color=(0.42, 0.42, 0.42),
        )
        self.y = 92.0

    def _asegurar(self, alto: float) -> None:
        if self.y + alto > self.LIMITE:
            self._nueva_pagina(continuacion=True)

    def seccion(self, titulo: str) -> None:
        """Título de sección con una regla debajo."""
        self._asegurar(38)
        self.y += 30
        self.pagina.insert_text(
            (MARGEN_ANEXO, self.y),
            titulo,
            fontname="hebo",
            fontsize=10.5,
            color=COLOR_TEXTO,
        )
        self.y += 6
        self.pagina.draw_line(
            (MARGEN_ANEXO, self.y),
            (ANCHO_A4 - MARGEN_ANEXO, self.y),
            color=(0.75, 0.75, 0.75),
            width=0.7,
        )
        self.y += 12

    def parrafo(self, texto: str, tamano: float = TAMANO_ANEXO) -> None:
        """Párrafo de ancho completo con salto de página automático."""
        ancho = ANCHO_A4 - 2 * MARGEN_ANEXO
        for linea in _envolver(texto, ancho, tamano):
            alto = tamano * 1.45
            self._asegurar(alto)
            self.y += alto
            self.pagina.insert_text(
                (MARGEN_ANEXO, self.y),
                linea,
                fontname="helv",
                fontsize=tamano,
                color=COLOR_TEXTO,
            )

    def dato(self, etiqueta: str, valor: Any) -> None:
        """Renglón «Etiqueta: valor» (se omite si el valor está vacío)."""
        texto = _texto(valor)
        if texto:
            self.parrafo(f"{etiqueta}: {texto}", 8.5)



    # -- Odontograma ----------------------------------------------------- #
    def odontograma(self, odontograma: Odontograma) -> None:
        """Grilla de los 32 dientes permanentes con los colores del PDF."""
        self.seccion("Odontograma")
        estados = {diente.diente: diente.estado for diente in odontograma.dientes}
        ancho_caja, alto_caja, separacion = 26.0, 20.0, 2.0
        ancho_grupo = len(GRUPOS_FDI[0][0]) * ancho_caja + 7 * separacion
        alto_banda = alto_caja + 16
        self._asegurar(len(GRUPOS_FDI) * alto_banda + 24)
        y = self.y + 8
        for banda in GRUPOS_FDI:
            for indice_grupo, grupo in enumerate(banda):
                x = MARGEN_ANEXO + indice_grupo * (ancho_grupo + 40)
                for diente in grupo:
                    estado = estados.get(diente)
                    relleno, borde = COLORES_DIENTE.get(
                        estado, ((1.0, 1.0, 1.0), (0.62, 0.62, 0.62))
                    )
                    caja = pymupdf.Rect(x, y, x + ancho_caja, y + alto_caja)
                    self.pagina.draw_rect(caja, color=borde, fill=relleno, width=0.7)
                    if estado == "AUSENTE":
                        self.pagina.draw_line(
                            (caja.x0 + 5, caja.y0 + 4),
                            (caja.x1 - 5, caja.y1 - 4),
                            color=borde,
                            width=0.9,
                        )
                        self.pagina.draw_line(
                            (caja.x1 - 5, caja.y0 + 4),
                            (caja.x0 + 5, caja.y1 - 4),
                            color=borde,
                            width=0.9,
                        )
                    numero = str(diente)
                    self.pagina.insert_text(
                        (
                            x + (ancho_caja - _ancho(numero, 6.5)) / 2,
                            y + alto_caja + 8,
                        ),
                        numero,
                        fontname="helv",
                        fontsize=6.5,
                        color=COLOR_TEXTO,
                    )
                    x += ancho_caja + separacion
            y += alto_banda
        self.y = y + 2
        self.parrafo(
            "Rojo: prestaciones existentes · Azul: prestaciones requeridas · "
            "X: diente ausente o a extraer.",
            7.5,
        )
        self.dato("Prótesis fija", odontograma.protesis_fija)
        self.dato("Prótesis removible", odontograma.protesis_removible)
        self.dato("Coronas", odontograma.coronas)
        self.dato("Cantidad de dientes existentes", odontograma.dientes_existentes)
        self.dato("Observaciones", odontograma.observaciones)


def _alergias_de(ficha: AntecedentesSalud) -> Optional[str]:
    """Alergias declaradas en una línea (el renglón del formulario es corto)."""
    textos = [
        texto
        for texto in (
            _texto(getattr(ficha, "alergias_medicamentos", None)),
            _texto(getattr(ficha, "alergia_otros", None)),
        )
        if texto
    ]
    partes: List[str] = []
    for etiqueta, activo in (
        ("Anestesia", getattr(ficha, "alergia_anestesia", False)),
        ("Penicilina", getattr(ficha, "alergia_penicilina", False)),
    ):
        mencionada = any(etiqueta.lower() in texto.lower() for texto in textos)
        if activo and not mencionada:
            partes.append(etiqueta)
    partes.extend(textos)
    return " · ".join(partes) if partes else None


def _pagina_anexo(
    documento: pymupdf.Document,
    paciente: Paciente,
    numero_ficha: Optional[str],
    continuaciones: Sequence[Tuple[str, str]],
    detalle: Optional[str],
    alergias: Optional[str],
    odontograma: Optional[Odontograma],
) -> int:
    """Agrega el anexo cuando hay algo que no entra en el formulario."""
    anexo = _Anexo(documento, paciente, numero_ficha)
    for titulo, texto in continuaciones:
        anexo.seccion(f"{titulo} (continuación)")
        anexo.parrafo(texto)
    if detalle:
        anexo.seccion("Detalle del examen bucal")
        anexo.parrafo(detalle)
    if alergias:
        anexo.seccion("Alergias declaradas")
        anexo.parrafo(alergias)
    if odontograma is not None:
        anexo.odontograma(odontograma)
    return anexo.paginas


def _fecha_de_la_ficha(ficha: Optional[AntecedentesSalud]) -> date:
    """Fecha del formulario: la de la declaración o, si no hay, la de hoy."""
    completado = getattr(ficha, "fecha_completado", None) if ficha else None
    if isinstance(completado, datetime):
        return completado.date()
    if isinstance(completado, date):
        return completado
    return utcnow().date()



# --------------------------------------------------------------------------- #
# Documento
# --------------------------------------------------------------------------- #
def construir_pdf(
    paciente: Paciente,
    ficha: Optional[AntecedentesSalud] = None,
    odontograma: Optional[Odontograma] = None,
    doctor: Optional[Doctor] = None,
    fecha: Optional[date] = None,
    numero_ficha: Optional[str] = None,
) -> bytes:
    """Rellena la plantilla y devuelve los bytes del PDF.

    Es una función pura sobre los objetos recibidos (no consulta la base), así
    las pruebas pueden armarla con datos en memoria.
    """
    if not RUTA_PLANTILLA.is_file():
        raise FileNotFoundError(
            "No se encontró la plantilla de la Historia Clínica General: "
            f"{RUTA_PLANTILLA}"
        )

    documento = pymupdf.open(str(RUTA_PLANTILLA))
    hojas = [_Hoja(pagina) for pagina in documento]
    _escribir_encabezado(hojas[0], paciente, fecha or _fecha_de_la_ficha(ficha), doctor)

    continuaciones: List[Tuple[str, str]] = []
    if ficha is not None:
        continuaciones = _escribir_cuestionario(hojas[0], hojas[1], ficha)
    _escribir_consentimiento(hojas[1], paciente, doctor)

    detalle = _texto(getattr(ficha, "examen_bucal_detalle", None)) if ficha else None
    alergias = _alergias_de(ficha) if ficha is not None else None
    if continuaciones or detalle or alergias or odontograma is not None:
        _pagina_anexo(
            documento,
            paciente,
            numero_ficha,
            continuaciones,
            detalle,
            alergias,
            odontograma,
        )

    return documento.tobytes(garbage=3, deflate=True)


def _resolver_doctor(
    db: Session,
    historia: Optional[HistoriaClinica],
    odontograma: Optional[Odontograma],
    doctor_id: Optional[int],
) -> Optional[Doctor]:
    """Profesional del encabezado: el pedido, el del odontograma o el último."""
    if doctor_id is not None:
        doctor = db.get(Doctor, doctor_id)
        if doctor is None:
            raise RecursoNoEncontradoError(
                f"No existe un profesional con id={doctor_id}."
            )
        return doctor
    if odontograma is not None and odontograma.doctor is not None:
        return odontograma.doctor
    if historia is None:
        return None
    return db.execute(
        select(Doctor)
        .join(EvolucionMedica, EvolucionMedica.doctor_id == Doctor.id)
        .where(EvolucionMedica.historia_clinica_id == historia.id)
        .order_by(EvolucionMedica.fecha_atencion.desc(), EvolucionMedica.id.desc())
        .limit(1)
    ).scalars().first()


def generar_pdf(
    db: Session, paciente_id: int, doctor_id: Optional[int] = None
) -> Tuple[bytes, str]:
    """Arma el PDF del paciente y el nombre de archivo para la descarga."""
    paciente = db.get(Paciente, paciente_id)
    if paciente is None:
        raise RecursoNoEncontradoError(f"No existe un paciente con id={paciente_id}.")
    historia = (
        db.execute(
            select(HistoriaClinica).where(HistoriaClinica.paciente_id == paciente.id)
        )
        .scalars()
        .first()
    )
    ficha = historia.antecedentes_salud if historia is not None else None
    odontograma = historia.odontograma if historia is not None else None
    doctor = _resolver_doctor(db, historia, odontograma, doctor_id)

    contenido = construir_pdf(
        paciente,
        ficha=ficha,
        odontograma=odontograma,
        doctor=doctor,
        numero_ficha=historia.numero_ficha if historia is not None else None,
    )
    nombre = nombre_archivo("historia-clinica", paciente.nombre, paciente.dni or "")
    logger.info(
        "Historia clínica en PDF generada (paciente=%s, ficha=%s, %s bytes).",
        paciente.id,
        historia.numero_ficha if historia is not None else "-",
        len(contenido),
    )
    return contenido, f"{nombre}.pdf"


# --------------------------------------------------------------------------- #
# Verificación de la plantilla (la usa la prueba)
# --------------------------------------------------------------------------- #
def etiquetas_de_la_plantilla() -> List[str]:
    """Todas las etiquetas que el relleno busca en el formulario."""
    etiquetas: List[str] = [dato.etiqueta for dato in ENCABEZADO]
    etiquetas += [pregunta for _, pregunta in MARCAS_SI_NO]
    etiquetas += [etiqueta for _, etiqueta in MARCAS_SIMPLES]
    etiquetas += [ancla for _, ancla, _, _ in OPCIONES_POR_ORDEN]
    etiquetas += [
        etiqueta for _, opciones in OPCIONES_POR_ETIQUETA for _, etiqueta in opciones
    ]
    etiquetas += [titulo for _, titulo in BLOQUES]
    etiquetas += [entrada.etiqueta for entrada in TEXTOS]
    etiquetas += [
        "Lugar:",
        "Fecha",
        "ODONTOLOGO:",
        "PACIENTE",
        "Nº AFIL",
        "Es alérgico a alguna droga?",
        "sanos?",
        "otros",
        "Presencia de sarro",
        "El/la que suscribe",
        "DNI Nº",
        "con domicilio en calle",
        "Dr/a MP",
        "Continúa en Anexo",
        "Nº",
        "dónde?",
        "hacia dónde?",
    ]
    return etiquetas


def etiquetas_faltantes() -> List[str]:
    """Etiquetas del mapa que no aparecen en la plantilla (debería ser vacío)."""
    documento = pymupdf.open(str(RUTA_PLANTILLA))
    hojas = [_Hoja(pagina) for pagina in documento]
    faltantes = [
        etiqueta
        for etiqueta in etiquetas_de_la_plantilla()
        if not any(hoja.linea(etiqueta) is not None for hoja in hojas)
    ]
    return sorted(set(faltantes))


