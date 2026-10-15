"""Pruebas de los reportes en PDF (presentación a la obra social y liquidación).

El panel exporta CSV para Excel y, además, **PDF** para imprimir o adjuntar. El
PDF se genera con :mod:`app.core.pdf` (sin dependencias externas), así que las
pruebas verifican dos cosas:

1. **Que el archivo sea un PDF válido**: firma, tabla ``xref`` con los offsets
   reales de cada objeto, ``/Length`` de cada contenido, la cantidad de páginas
   declarada y el pie con la numeración.
2. **Que el reporte diga lo que tiene que decir**: los datos de la cobertura (o
   del profesional), los importes en formato del mostrador, los acentos del
   castellano y las filas del detalle.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.pdf import (
    Columna,
    DocumentoPDF,
    _a_winansi,
    ancho_texto,
    envolver,
    nombre_archivo,
)
from app.services import reporte_pdf_service
from app.web.router import STATIC_DIR

API = "/api/v1"

# Mismo día de trabajo que tests/test_facturacion.py (UTC naive).
DIA = datetime(2026, 11, 3, 12, 0)
ANIO, MES = 2026, 11


def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


def _crear_prestacion(client, **overrides) -> dict:
    payload = {
        "codigo_nomenclador": "20100",
        "descripcion": "Consulta y diagnóstico",
        "valor_base": 15000,
    }
    payload.update(overrides)
    r = client.post(f"{API}/prestaciones", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_obra_social(client, **overrides) -> dict:
    payload = {"nombre": "OSDE", "codigo": "OSDE-01"}
    payload.update(overrides)
    r = client.post(f"{API}/obras-sociales", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_arancel(
    client, obra_social_id: int, prestacion_id: int, **overrides
) -> dict:
    payload = {
        "prestacion_id": prestacion_id,
        "monto_cubre_os": 10000,
        "monto_coseguro_paciente": 5000,
    }
    payload.update(overrides)
    r = client.post(f"{API}/obras-sociales/{obra_social_id}/aranceles", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_paciente(client, **overrides) -> dict:
    payload = {"nombre": "Ana Suárez", "telefono": "+5491155667788"}
    payload.update(overrides)
    r = client.post(f"{API}/pacientes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_turno(client, doctor_id: int, paciente_id: int, **overrides) -> dict:
    payload = {
        "paciente_id": paciente_id,
        "doctor_id": doctor_id,
        "fecha_hora_inicio": DIA.isoformat(),
        "fecha_hora_fin": (DIA + timedelta(hours=1)).isoformat(),
    }
    payload.update(overrides)
    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _facturar_ok(client, turno_id: int, prestaciones, **extra) -> dict:
    payload = {"prestaciones": prestaciones}
    payload.update(extra)
    r = client.post(f"{API}/turnos/{turno_id}/facturar", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _escenario_obra_social(client, doctor, cantidad: int = 2) -> dict:
    """Una cobertura con ``cantidad`` visitas facturadas en noviembre."""
    cobertura = _crear_obra_social(client, nombre="OSDE")
    practica = _crear_prestacion(client)
    _crear_arancel(client, cobertura["id"], practica["id"])
    for indice in range(cantidad):
        paciente = _crear_paciente(
            client,
            nombre=f"Afiliado {indice} Suárez",
            dni=f"4314440{indice}",
            obra_social_id=cobertura["id"],
        )
        inicio = DIA + timedelta(hours=2 * indice)
        turno = _crear_turno(
            client,
            doctor.id,
            paciente["id"],
            fecha_hora_inicio=inicio.isoformat(),
            fecha_hora_fin=(inicio + timedelta(hours=1)).isoformat(),
        )
        _facturar_ok(
            client,
            turno["id"],
            [{"prestacion_id": practica["id"]}],
            fecha=f"2026-11-{5 + indice:02d}T10:00:00",
        )
    return {"cobertura": cobertura, "practica": practica}


def _verificar_pdf(datos: bytes) -> int:
    """Valida la estructura del PDF y devuelve la cantidad de páginas.

    Se recorre la tabla ``xref`` como lo haría un lector: cada offset tiene que
    caer exactamente en el encabezado ``N 0 obj`` y cada contenido tiene que
    declarar la longitud real de su stream (si no, ningún visor lo abre).
    """
    assert datos.startswith(b"%PDF-1.4"), "el archivo no arranca con la firma"
    assert datos.rstrip().endswith(b"%%EOF"), "el archivo no cierra con %%EOF"

    inicio = int(re.search(rb"startxref\n(\d+)", datos).group(1))
    assert datos[inicio : inicio + 4] == b"xref", "startxref no apunta a la xref"
    tabla, trailer = datos[inicio:].split(b"trailer", 1)
    assert b"/Root 1 0 R" in trailer, "el trailer no referencia el catálogo"

    paginas = int(re.search(rb"/Count (\d+)", datos).group(1))
    offsets = re.findall(rb"^(\d{10}) 00000 n $", tabla, re.M)
    assert len(offsets) == 2 * paginas + 4, "la xref no lista todos los objetos"
    for numero, offset in enumerate(offsets, start=1):
        encabezado = f"{numero} 0 obj".encode()
        assert (
            datos[int(offset) : int(offset) + len(encabezado)] == encabezado
        ), f"la entrada {numero} de la xref apunta al lugar equivocado"

    for coincidencia in re.finditer(rb"<< /Length (\d+) >>\nstream\n", datos):
        largo = int(coincidencia.group(1))
        fin = coincidencia.end() + largo
        assert (
            datos[fin : fin + 10] == b"\nendstream"
        ), "un stream miente sobre su largo"
    return paginas


def _texto_del_pdf(datos: bytes) -> str:
    """Texto dibujado por el PDF (el contenido no va comprimido, a propósito)."""
    return datos.decode("latin-1")


# --------------------------------------------------------------------------- #
# 1) El generador (app/core/pdf.py)
# --------------------------------------------------------------------------- #
def test_el_pdf_generado_es_estructuralmente_valido():
    pdf = DocumentoPDF("Reporte de prueba", "Subtítulo", "Nota al pie")
    pdf.tabla(
        [Columna("Paciente", 2), Columna("Importe", 1, "derecha")],
        [[f"Paciente {i}", f"$ {i}.000,00"] for i in range(1, 80)],
    )
    pdf.totales([("Total", "$ 79.000,00")])

    datos = pdf.generar()

    paginas = _verificar_pdf(datos)
    assert paginas >= 2, "con 79 filas el reporte tiene que paginar"
    texto = _texto_del_pdf(datos)
    assert f"Página 1 de {paginas}" in texto
    assert f"Página {paginas} de {paginas}" in texto
    assert "Reporte de prueba" in texto
    assert "Nota al pie" in texto
    # El encabezado de la tabla se repite en la página siguiente.
    assert texto.count("(Paciente) Tj") >= 2


def test_un_dato_ancho_o_un_encabezado_no_se_parte_de_linea():
    """Un DNI, un importe o un encabezado tienen que salir en una sola línea."""
    pdf = DocumentoPDF("T")
    pdf.tabla(
        [Columna("DNI", 1.1), Columna("Presentado", 1.35, "derecha")],
        [["43144415", "$ 999.999,99"]],
    )
    texto = _texto_del_pdf(pdf.generar())
    assert "(DNI) Tj" in texto
    assert "(Presentado) Tj" in texto
    assert "43144415" in texto
    assert "$ 999.999,99" in texto


def test_las_columnas_de_la_presentacion_no_parten_los_datos():
    """Con los anchos reales del reporte, los datos duros salen en una línea."""
    pdf = DocumentoPDF("T")
    pdf.tabla(
        reporte_pdf_service.COLUMNAS_PRESENTACION,
        [
            [
                "15/10/2026",
                "Francisco Sappia Badra",
                "43144415",
                "1x 10010 · 2x 20100",
                "$ 40.000,00",
                "$ 5.000,00",
                "Sin presentar",
                "coseguro en efectivo",
            ],
            [
                "05/11/2026",
                "Ana Suárez",
                "28123456",
                "1x 10010",
                "$ 1.234.567,89",
                "$ 999.999,99",
                "Presentada",
                "",
            ],
        ],
        tamano=8.5,  # el mismo tamaño que usan los dos reportes
    )
    texto = _texto_del_pdf(pdf.generar())
    for columna in reporte_pdf_service.COLUMNAS_PRESENTACION:
        assert f"({columna.titulo}) Tj" in texto, columna.titulo
    for dato in (
        "15/10/2026", "05/11/2026", "43144415", "28123456",
        "$ 40.000,00", "$ 5.000,00", "$ 1.234.567,89", "$ 999.999,99",
        "Sin presentar", "Presentada",
    ):
        assert dato in texto, f"{dato} quedó partido en dos líneas"


def test_las_columnas_de_la_liquidacion_no_parten_los_datos():
    pdf = DocumentoPDF("T")
    pdf.tabla(
        reporte_pdf_service.COLUMNAS_LIQUIDACION,
        [
            [
                "Fabiana Fransioci",
                "Implantes",
                "Dueño",
                "100%",
                "1",
                "$ 5.000,00",
                "$ 40.000,00",
                "$ 45.000,00",
                "$ 45.000,00",
            ]
        ],
        tamano=8.5,  # el mismo tamaño que usa el reporte
    )
    texto = _texto_del_pdf(pdf.generar())
    for columna in reporte_pdf_service.COLUMNAS_LIQUIDACION:
        assert f"({columna.titulo}) Tj" in texto, columna.titulo
    for dato in (
        "Fabiana Fransioci", "Implantes", "100%", "$ 5.000,00",
        "$ 40.000,00", "$ 45.000,00",
    ):
        assert dato in texto, f"{dato} quedó partido en dos líneas"


def test_los_totales_se_alinean_a_la_derecha():
    corto = DocumentoPDF("T")
    corto.totales([("Un total largo", "$ 10,00")])
    largo = DocumentoPDF("T")
    largo.totales([("Un total largo", "$ 10.000.000,00")])
    # Un importe más largo arranca más a la izquierda (mismo borde derecho).
    x_corto = float(
        re.search(
            rb"1 0 0 1 ([\d.]+) [\d.]+ Tm\n\(\$ 10,00\)", corto.generar()
        ).group(1)
    )
    x_largo = float(
        re.search(
            rb"1 0 0 1 ([\d.]+) [\d.]+ Tm\n\(\$ 10\.000\.000,00\)",
            largo.generar(),
        ).group(1)
    )
    assert x_largo < x_corto


def test_la_codificacion_winansi_cubre_el_castellano_y_degrada_lo_demas():
    assert _a_winansi("Suárez") == b"Su\xe1rez"
    assert _a_winansi("SESIÓN") == b"SESI\xd3N"
    assert _a_winansi("20100 · 21600") == b"20100 \xb7 21600"  # U+00B7 en WinAnsi
    assert _a_winansi("viñeta • aparte") == b"vi\xf1eta \x95 aparte"
    # Lo que WinAnsi no tiene se aproxima con ASCII, nunca queda un "?" suelto.
    assert _a_winansi("✓ listo") == b"v listo"
    assert _a_winansi("a → b") == b"a -> b"


def test_metricas_de_texto():
    assert ancho_texto("1234") == ancho_texto("5678")  # dígitos de ancho fijo
    assert ancho_texto("M") > ancho_texto("i")
    assert ancho_texto("Hola", negrita=True) > ancho_texto("Hola")
    assert ancho_texto("Hola", tamano=20) == pytest.approx(2 * ancho_texto("Hola"))
    # El texto se envuelve dentro del ancho pedido y nunca pierde palabras.
    lineas = envolver("consulta de urgencia " * 10, 120)
    assert len(lineas) > 3
    assert " ".join(lineas).split() == ("consulta de urgencia " * 10).split()
    assert all(ancho_texto(linea) <= 120 for linea in lineas)
    assert envolver("corto", 500) == ["corto"]


def test_nombre_de_archivo_seguro():
    assert nombre_archivo("presentacion", "PRES-OS1-202611") == (
        "presentacion-pres-os1-202611"
    )
    assert nombre_archivo("Liquidación", "Año 2026") == "liquidacion-ano-2026"
    assert "/" not in nombre_archivo("a/b", "c\\d")


# --------------------------------------------------------------------------- #
# 2) La presentación a la obra social
# --------------------------------------------------------------------------- #
def test_la_presentacion_se_descarga_como_pdf(client, doctor):
    datos = _escenario_obra_social(client, doctor)
    obra_social_id = datos["cobertura"]["id"]

    r = client.get(
        f"{API}/liquidaciones/obras-sociales/pdf"
        f"?obra_social_id={obra_social_id}&anio={ANIO}&mes={MES}"
    )

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == (
        f'attachment; filename="presentacion-pres-os{obra_social_id}-202611.pdf"'
    )
    _verificar_pdf(r.content)
    texto = _texto_del_pdf(r.content)
    assert "Presentación a la obra social" in texto
    assert "OSDE" in texto
    assert "Noviembre 2026" in texto
    assert "No \\(borrador\\)" in texto       # todavía no se presentó
    assert "Afiliado 0 Suárez" in texto       # el acento sobrevive en el PDF
    assert "43144400" in texto                 # el DNI entra en una sola línea
    assert "20100" in texto                    # la práctica facturada
    assert "Sin presentar" in texto            # estado traducido, no el enum
    assert "EN_PROCESO_LIQUIDACION" not in texto
    # Totales del período: 2 × (10.000 presentado / 5.000 de coseguro).
    assert "$ 20.000,00" in texto
    assert "$ 10.000,00" in texto


def test_la_presentacion_marca_las_ordenes_ya_presentadas(client, doctor):
    datos = _escenario_obra_social(client, doctor)
    obra_social_id = datos["cobertura"]["id"]
    generada = client.post(
        f"{API}/liquidaciones/obras-sociales",
        json={"obra_social_id": obra_social_id, "anio": ANIO, "mes": MES},
    )
    assert generada.status_code == 200, generada.text

    r = client.get(
        f"{API}/liquidaciones/obras-sociales/pdf"
        f"?obra_social_id={obra_social_id}&anio={ANIO}&mes={MES}"
    )

    assert r.status_code == 200, r.text
    texto = _texto_del_pdf(r.content)
    assert "Emitida" in texto
    assert "Presentada" in texto
    assert "Sin presentar" not in texto


def test_la_presentacion_de_un_periodo_sin_ordenes_explica_que_esta_vacia(
    client, doctor
):
    datos = _escenario_obra_social(client, doctor)
    r = client.get(
        f"{API}/liquidaciones/obras-sociales/pdf"
        f"?obra_social_id={datos['cobertura']['id']}&anio={ANIO}&mes=12"
    )

    assert r.status_code == 200, r.text
    _verificar_pdf(r.content)
    texto = _texto_del_pdf(r.content)
    assert "No hay órdenes de esta cobertura" in texto
    assert "Diciembre 2026" in texto


def test_la_presentacion_de_una_cobertura_inexistente_es_404(client):
    r = client.get(
        f"{API}/liquidaciones/obras-sociales/pdf"
        f"?obra_social_id=999&anio={ANIO}&mes={MES}"
    )
    assert r.status_code == 404, r.text


def test_el_periodo_de_los_reportes_pdf_se_valida(client):
    del_mes = client.get(
        f"{API}/liquidaciones/obras-sociales/pdf?obra_social_id=1&anio=2026&mes=13"
    )
    assert del_mes.status_code == 422, del_mes.text
    del_anio = client.get(f"{API}/liquidaciones/profesionales/pdf?anio=1990&mes=11")
    assert del_anio.status_code == 422, del_anio.text


# --------------------------------------------------------------------------- #
# 3) La liquidación de profesionales
# --------------------------------------------------------------------------- #
def test_la_liquidacion_se_descarga_como_pdf(client, doctor):
    _escenario_obra_social(client, doctor)

    r = client.get(f"{API}/liquidaciones/profesionales/pdf?anio={ANIO}&mes={MES}")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == (
        'attachment; filename="liquidacion-profesionales-2026-11.pdf"'
    )
    _verificar_pdf(r.content)
    texto = _texto_del_pdf(r.content)
    assert "Liquidación de profesionales" in texto
    assert "Noviembre 2026" in texto
    assert doctor.nombre in texto
    assert doctor.especialidad in texto
    assert "Honorarios" in texto
    assert "Cómo se calculó cada liquidación" in texto
    assert "$ 20.000,00" in texto  # producción del período (2 × 10.000 de OS)


def test_la_liquidacion_de_un_solo_profesional(client, doctor):
    _escenario_obra_social(client, doctor)

    r = client.get(
        f"{API}/liquidaciones/profesionales/pdf"
        f"?anio={ANIO}&mes={MES}&doctor_id={doctor.id}"
    )

    assert r.status_code == 200, r.text
    _verificar_pdf(r.content)
    assert doctor.nombre in _texto_del_pdf(r.content)


def test_la_liquidacion_de_un_mes_sin_produccion_lo_dice(client):
    r = client.get(f"{API}/liquidaciones/profesionales/pdf?anio=2026&mes=1")

    assert r.status_code == 200, r.text
    _verificar_pdf(r.content)
    texto = _texto_del_pdf(r.content)
    assert "No hay producción cargada en el período" in texto
    assert "$ 0,00" in texto


# --------------------------------------------------------------------------- #
# 4) Los endpoints JSON siguen igual (las rutas PDF no los tapan)
# --------------------------------------------------------------------------- #
def test_las_rutas_pdf_no_reemplazan_a_las_de_json(client, doctor):
    datos = _escenario_obra_social(client, doctor)
    obra_social_id = datos["cobertura"]["id"]

    presentacion = client.get(
        f"{API}/liquidaciones/obras-sociales"
        f"?obra_social_id={obra_social_id}&anio={ANIO}&mes={MES}"
    )
    liquidacion = client.get(f"{API}/liquidaciones/profesionales?anio={ANIO}&mes={MES}")

    assert presentacion.status_code == 200
    assert presentacion.headers["content-type"].startswith("application/json")
    assert presentacion.json()["total_presentado"] == 20000
    assert liquidacion.status_code == 200
    assert liquidacion.json()["total_os"] == 20000


# --------------------------------------------------------------------------- #
# 5) Formato de los importes y del período
# --------------------------------------------------------------------------- #
def test_los_importes_se_muestran_como_en_el_mostrador():
    moneda = reporte_pdf_service._moneda
    assert moneda(18000) == "$ 18.000,00"
    assert moneda(1234567.5) == "$ 1.234.567,50"
    assert moneda(0) == "$ 0,00"
    assert moneda(None) == "$ 0,00"
    assert moneda("no es un número") == "$ 0,00"


def test_el_periodo_se_escribe_en_castellano():
    periodo = reporte_pdf_service._periodo_texto
    assert periodo(2026, 11) == "Noviembre 2026"
    assert periodo(2026, 1) == "Enero 2026"
    assert periodo(2026, 12) == "Diciembre 2026"


# --------------------------------------------------------------------------- #
# 6) El panel ofrece los dos reportes en PDF
# --------------------------------------------------------------------------- #
def test_el_panel_tiene_los_botones_de_pdf():
    html = _leer_estatico("index.html")
    assert 'id="fact-lis-pdf"' in html  # presentación a la obra social
    assert 'id="fact-med-pdf"' in html  # liquidación de profesionales


def test_el_panel_descarga_los_pdf_del_servidor():
    js = _leer_estatico("app.js")
    assert "async function descargarArchivo" in js
    assert "/pdf?" in js
    assert "fact-lis-pdf" in js
    assert "fact-med-pdf" in js
