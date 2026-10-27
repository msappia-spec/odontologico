"""Pruebas del importador del PDF de aranceles del Colegio.

Cubren el circuito completo sin depender del archivo real:

* el **parser** reconstruye las prácticas (código, descripción con acentos y
  honorario) aun cuando los importes vienen en líneas separadas;
* la **vista previa** (``simular=true``) no escribe nada en el catálogo;
* la importación crea las prácticas, respeta las que no traen honorario y
  vuelve a importar el mismo PDF sin duplicar filas (``SIN_CAMBIOS``);
* un código escrito con otro formato (``11.02`` y ``1102``) se reconoce como la
  misma práctica y un código repetido dentro del PDF no pisa al primero.

Si el PDF oficial está en la raíz del proyecto, también se lee y se importa
completo para comprobar que el circuito sigue entendiendo el documento real del
Colegio.
"""

import base64
from pathlib import Path

import pymupdf
import pytest

from app.core.aranceles_pdf import leer_aranceles_pdf, parsear_texto_aranceles
from app.core.planilla_aranceles import canonizar_codigo

API = "/api/v1"

# Líneas representativas del PDF: una práctica con la descripción en la misma
# línea y los importes debajo, dos prácticas sin honorario y el código con
# punto del capítulo de implantes.
LINEAS = [
    "101 Examen - Diagnóstico - Fichado y Plan de Tratamiento.",
    "$ 16.106",
    "$ 7.555",
    "$ 23.661",
    "$ 24.390",
    "$ 48.050",
    "103 Visita a Domicilio.",
    "$ 24.158",
    "$ 7.599",
    "$ 31.757",
    "$ 36.584",
    "$ 68.342",
    "204 Reconstrucción de ángulos con tornillo en conducto",
    "209 Reconstrucción de ángulos con material estético en dientes anteriores",
    "215 Restauración Simple de piezas dentarias.",
    "$ 24.158",
    "$ 37.276",
    "$ 61.434",
    "$ 36.584",
    "$ 98.018",
    (
        "11.02 Técnica Quirúrgica Simple: colocación de 1 implante "
        "Maxilar Sup. ó Inferior."
    ),
    "$ 1.158.180",
]

DESCRIPCION_1102 = (
    "Técnica Quirúrgica Simple: colocación de 1 implante Maxilar Sup. ó Inferior."
)

RUTA_PDF_REAL = Path(__file__).resolve().parents[1] / "ARANCELES-GENERALES-23.pdf"


def _pdf(lineas=LINEAS, paginas=None) -> bytes:
    """Arma un PDF de prueba con una línea de texto por renglón."""
    documento = pymupdf.open()
    bloques = paginas if paginas is not None else [lineas]
    for bloque in bloques:
        pagina = documento.new_page()
        for indice, texto in enumerate(bloque):
            pagina.insert_text((72, 72 + indice * 16), texto, fontsize=10)
    contenido = documento.tobytes()
    documento.close()
    return contenido


def _importar(client, contenido=None, *, simular=False, nombre="aranceles.pdf"):
    """Hace ``POST /prestaciones/importar-pdf`` con el PDF en base64."""
    contenido = _pdf() if contenido is None else contenido
    payload = {
        "nombre_archivo": nombre,
        "contenido_base64": base64.b64encode(contenido).decode("ascii"),
        "simular": simular,
    }
    return client.post(f"{API}/prestaciones/importar-pdf", json=payload)


def _catalogo(client):
    """Devuelve el catálogo indexado por código de nomenclador."""
    respuesta = client.get(f"{API}/prestaciones")
    assert respuesta.status_code == 200, respuesta.text
    return {practica["codigo_nomenclador"]: practica for practica in respuesta.json()}


def _precio(linea) -> float:
    """Convierte ``$ 48.050`` en ``48050`` (para comparar con la API)."""
    return float(linea.replace("$", "").replace(".", "").strip())


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def test_parsea_codigos_descripciones_y_honorarios():
    filas = parsear_texto_aranceles("\n".join(LINEAS))

    assert [fila.codigo for fila in filas] == [
        "101",
        "103",
        "204",
        "209",
        "215",
        "11.02",
    ]
    por_codigo = {fila.codigo: fila for fila in filas}
    assert por_codigo["101"].descripcion.startswith("Examen - Diagnóstico")
    assert por_codigo["101"].valor == _precio("$ 48.050")  # el último importe
    assert por_codigo["103"].valor == _precio("$ 68.342")
    assert por_codigo["204"].valor is None
    assert por_codigo["209"].valor is None
    assert por_codigo["215"].valor == _precio("$ 98.018")
    assert por_codigo["11.02"].valor == _precio("$ 1.158.180")
    assert "Diagnóstico" in por_codigo["101"].descripcion  # acento intacto
    assert "estético" in por_codigo["209"].descripcion


def test_parsea_varias_paginas_y_numeros_de_pagina():
    filas = parsear_texto_aranceles(
        "101 Consulta y diagnóstico.\n$ 10.000\n\f2\n103 Visita a domicilio.\n$ 20.000"
    )

    assert [(fila.codigo, fila.pagina, fila.valor) for fila in filas] == [
        ("101", 1, 10000.0),
        ("103", 2, 20000.0),
    ]


# --------------------------------------------------------------------------- #
# Importación
# --------------------------------------------------------------------------- #
def test_vista_previa_no_escribe_en_el_catalogo(client):
    respuesta = _importar(client, simular=True)

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["simulado"] is True
    assert datos["filas_leidas"] == 6
    assert datos["creados"] == 6
    assert datos["actualizados"] == 0
    assert datos["sin_cambios"] == 0
    assert datos["sin_precio"] == 2  # 204 y 209 no traen honorario
    assert datos["errores"] == 0
    assert {fila["estado"] for fila in datos["detalle"]} == {"NUEVO"}
    assert all(fila["prestacion_id"] is None for fila in datos["detalle"])

    assert _catalogo(client) == {}


def test_importar_crea_todo_el_catalogo(client):
    respuesta = _importar(client)

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["simulado"] is False
    assert datos["creados"] == 6
    assert datos["sin_precio"] == 2

    catalogo = _catalogo(client)
    assert set(catalogo) == {"101", "103", "204", "209", "215", "11.02"}
    assert catalogo["101"]["valor_base"] == 48050
    assert catalogo["103"]["valor_base"] == 68342
    assert catalogo["204"]["valor_base"] == 0  # sin honorario en el PDF
    assert catalogo["209"]["valor_base"] == 0
    assert catalogo["11.02"]["valor_base"] == 1158180
    assert catalogo["101"]["descripcion"].startswith("Examen - Diagnóstico")
    assert catalogo["11.02"]["descripcion"] == DESCRIPCION_1102
    assert all(practica["activa"] for practica in catalogo.values())

    # Aunque el PDF repita una práctica, el catálogo no puede tener dos filas
    # con el mismo código canónico.
    codigos = [canonizar_codigo(codigo) for codigo in catalogo]
    assert len(codigos) == len(set(codigos))


def test_reimportar_el_mismo_pdf_no_duplica(client):
    assert _importar(client).status_code == 200
    respuesta = _importar(client)

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["creados"] == 0
    assert datos["actualizados"] == 0
    assert datos["sin_cambios"] == 6
    assert datos["errores"] == 0
    assert len(_catalogo(client)) == 6


def test_codigo_con_punto_coincide_con_el_mismo_codigo_sin_punto(client):
    creada = client.post(
        f"{API}/prestaciones",
        json={
            "codigo_nomenclador": "1102",
            "descripcion": DESCRIPCION_1102,
            "valor_base": 1158180,
        },
    )
    assert creada.status_code == 201, creada.text

    respuesta = _importar(client)

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["creados"] == 5  # las otras cinco del PDF
    assert datos["sin_cambios"] == 1  # 11.02 ya existía como 1102
    assert len(_catalogo(client)) == 6


def test_codigo_repetido_dentro_del_pdf_conserva_el_primero(client):
    contenido = _pdf(
        [
            "11.02 Primera versión del implante.",
            "$ 10.000",
            "1102 Segunda versión del implante.",
            "$ 20.000",
        ]
    )
    respuesta = _importar(client, contenido)

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["filas_leidas"] == 2
    assert datos["creados"] == 1
    assert datos["errores"] == 1
    assert [fila["estado"] for fila in datos["detalle"]] == ["NUEVO", "ERROR"]
    assert "ya apareció" in datos["detalle"][1]["mensaje"]

    catalogo = _catalogo(client)
    assert list(catalogo) == ["11.02"]
    assert catalogo["11.02"]["descripcion"] == "Primera versión del implante."
    assert catalogo["11.02"]["valor_base"] == 10000


def test_acepta_el_pdf_como_data_url(client):
    contenido = _pdf()
    payload = "data:application/pdf;base64," + base64.b64encode(contenido).decode(
        "ascii"
    )
    respuesta = client.post(
        f"{API}/prestaciones/importar-pdf",
        json={"contenido_base64": payload, "simular": True},
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["creados"] == 6


@pytest.mark.parametrize("contenido", ["%%%no-es-base64%%%", "á"])
def test_rechaza_un_base64_invalido(client, contenido):
    respuesta = client.post(
        f"{API}/prestaciones/importar-pdf", json={"contenido_base64": contenido}
    )

    assert respuesta.status_code == 422, respuesta.text
    assert "mal codificado" in respuesta.text
    assert _catalogo(client) == {}


def test_rechaza_un_archivo_que_no_es_pdf(client):
    respuesta = _importar(client, b"esto no es un PDF")

    assert respuesta.status_code == 422, respuesta.text
    assert "no es un PDF" in respuesta.text


def test_rechaza_un_pdf_sin_practicas(client):
    respuesta = _importar(client, _pdf(["Hola mundo", "sin códigos ni importes"]))

    assert respuesta.status_code == 422, respuesta.text
    assert "No se encontraron prácticas" in respuesta.text


# --------------------------------------------------------------------------- #
# Documento real del Colegio
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not RUTA_PDF_REAL.is_file(),
    reason="El PDF oficial no está en la raíz del proyecto.",
)
def test_lee_el_pdf_oficial_del_colegio():
    filas = leer_aranceles_pdf(RUTA_PDF_REAL.read_bytes())

    assert len(filas) >= 200
    assert any(fila.descripcion.startswith("Examen - Diagnóstico") for fila in filas)
    assert any(fila.valor is None for fila in filas)
    assert all(fila.descripcion and len(fila.descripcion) <= 200 for fila in filas)

    claves = [canonizar_codigo(fila.codigo) for fila in filas]
    assert len(claves) == len(set(claves))


@pytest.mark.skipif(
    not RUTA_PDF_REAL.is_file(),
    reason="El PDF oficial no está en la raíz del proyecto.",
)
def test_importa_el_pdf_oficial_completo(client):
    contenido = RUTA_PDF_REAL.read_bytes()
    filas = leer_aranceles_pdf(contenido)

    respuesta = _importar(client, contenido, nombre=RUTA_PDF_REAL.name)

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["filas_leidas"] == len(filas)
    assert datos["creados"] == len(filas)
    assert datos["errores"] == 0
    assert datos["sin_precio"] == sum(fila.valor is None for fila in filas)

    catalogo = _catalogo(client)
    assert len(catalogo) == len(filas)
    assert set(catalogo) == {fila.codigo.upper() for fila in filas}
    claves = [canonizar_codigo(codigo) for codigo in catalogo]
    assert len(claves) == len(set(claves))


def test_rechaza_un_pdf_sin_honorarios(client):
    respuesta = _importar(client, _pdf(["101 Consulta sin precio"]))

    assert respuesta.status_code == 422, respuesta.text
    assert "no trae honorarios" in respuesta.text


def test_rechaza_un_pdf_vacio(client):
    respuesta = _importar(client, b"")

    # El esquema rechaza el base64 vacío antes de llegar al parser.
    assert respuesta.status_code == 422, respuesta.text
    assert respuesta.json()["detail"][0]["loc"][-1] == "contenido_base64"
    assert _catalogo(client) == {}


def test_reimportar_actualiza_descripcion_y_honorario(client):
    assert _importar(client).status_code == 200

    lineas = [
        (
            "103 Visita a Domicilio (revisada)."
            if linea == "103 Visita a Domicilio."
            else linea
        )
        for linea in LINEAS
    ]
    lineas = ["$ 70.000" if linea == "$ 68.342" else linea for linea in lineas]
    respuesta = _importar(client, _pdf(lineas))

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["creados"] == 0
    assert datos["actualizados"] == 1
    assert datos["sin_cambios"] == 5

    practica = _catalogo(client)["103"]
    assert practica["descripcion"] == "Visita a Domicilio (revisada)."
    assert practica["valor_base"] == 70000


def test_una_practica_sin_honorario_conserva_el_valor_cargado(client):
    assert _importar(client).status_code == 200
    practica = _catalogo(client)["209"]
    patch = client.patch(
        f"{API}/prestaciones/{practica['id']}", json={"valor_base": 12345}
    )
    assert patch.status_code == 200, patch.text

    respuesta = _importar(client)

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["sin_cambios"] == 6
    assert _catalogo(client)["209"]["valor_base"] == 12345
