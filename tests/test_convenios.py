"""Convenios por obra social e importación masiva de precios (Módulo 3).

Estas pruebas cierran los dos pedidos del módulo de convenios:

1. **Grilla de convenios** (``GET/PUT /obras-sociales/{id}/convenio``): la
   secretaría elige una cobertura y ve **todo** el nomenclador base de Córdoba con
   el monto que cubre la OS; el **coseguro sugerido** es
   ``valor_base - monto_cubre_os`` (nunca negativo) y se guarda de verdad en la
   tabla ``aranceles_obra_social``.
2. **Importador masivo** (``GET /obras-sociales/{id}/convenio/plantilla`` y
   ``POST /obras-sociales/{id}/importar-aranceles``): la plantilla tiene dos
   columnas (``codigo`` y ``precio_cobertura``) y el importador traduce cada
   código contra el nomenclador actualizando los aranceles, con un reporte por
   fila (creado / actualizado / sin cambios / ignorada / error).

Además se fija el cierre de la visita: al tildar las prestaciones en el modal de
cobro, el saldo de la obra social queda como **PENDIENTE** en
``movimientos_caja`` y el mostrador muestra el cartel *Cobrar Coseguro*.
"""

import base64
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import planilla_aranceles
from app.core.exceptions import ValidacionError
from app.models.arancel_obra_social import ArancelObraSocial
from app.models.movimiento_caja import MovimientoCaja
from app.web.router import STATIC_DIR

API = "/api/v1"

# Códigos del nomenclador base de Córdoba usados en las pruebas.
CONSULTA = ("10100", "Capítulo I: Consulta, examen, diagnóstico y fichado", 18000)
OBTURACION = ("20100", "Capítulo II: Obturación con amalgama cavidad simple", 32000)
LIMPIEZA = ("50100", "Capítulo V: Tartrectomía y cepillado mecánico", 38000)

DIA = datetime(2026, 11, 3, 12, 0)


# --------------------------------------------------------------------------- #
# Ayudas
# --------------------------------------------------------------------------- #
def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


def _crear_prestacion(client, codigo: str, descripcion: str, valor_base: float) -> dict:
    """Crea una práctica del nomenclador por API."""
    r = client.post(
        f"{API}/prestaciones",
        json={
            "codigo_nomenclador": codigo,
            "descripcion": descripcion,
            "valor_base": valor_base,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _crear_obra_social(client, nombre: str = "OSDE") -> dict:
    """Crea una cobertura por API."""
    r = client.post(f"{API}/obras-sociales", json={"nombre": nombre})
    assert r.status_code == 201, r.text
    return r.json()


def _convenio(client, obra_social_id: int) -> dict:
    """GET de la grilla de convenios de una cobertura."""
    r = client.get(f"{API}/obras-sociales/{obra_social_id}/convenio")
    assert r.status_code == 200, r.text
    return r.json()


def _fila(convenio: dict, codigo: str) -> dict:
    """Devuelve la fila del convenio correspondiente a un código."""
    return next(f for f in convenio["filas"] if f["codigo_nomenclador"] == codigo)


def _guardar_convenio(client, obra_social_id: int, filas: list):
    """PUT de la grilla de convenios y devuelve la respuesta cruda."""
    return client.put(
        f"{API}/obras-sociales/{obra_social_id}/convenio", json={"filas": filas}
    )


def _importar(
    client, obra_social_id: int, contenido: bytes, nombre="planilla.csv", **extra
):
    """Sube una planilla al importador masivo (en base64, como lo hace el panel)."""
    payload = {
        "nombre_archivo": nombre,
        "contenido_base64": base64.b64encode(contenido).decode("ascii"),
    }
    payload.update(extra)
    return client.post(
        f"{API}/obras-sociales/{obra_social_id}/importar-aranceles", json=payload
    )


def _importar_texto(client, obra_social_id: int, texto: str, **extra):
    """Sube la planilla como texto plano (el otro camino que acepta el endpoint)."""
    payload = {"nombre_archivo": "planilla.csv", "contenido": texto}
    payload.update(extra)
    return client.post(
        f"{API}/obras-sociales/{obra_social_id}/importar-aranceles", json=payload
    )


def _planilla(*filas, separador=";", cabecera=True) -> bytes:
    """Arma una planilla CSV con la cabecera estándar."""
    lineas = [f"codigo{separador}precio_cobertura"] if cabecera else []
    lineas += [f"{codigo}{separador}{precio}" for codigo, precio in filas]
    return ("\r\n".join(lineas) + "\r\n").encode("utf-8")


def _aranceles(db_session) -> dict:
    """``{codigo_nomenclador: arancel}`` con lo que hay guardado en la base."""
    db_session.expire_all()
    filas = db_session.execute(select(ArancelObraSocial)).scalars().all()
    return {a.prestacion.codigo_nomenclador: a for a in filas}


def _xlsx_con_cadenas_compartidas(filas) -> bytes:
    """Arma un ``.xlsx`` mínimo que usa ``sharedStrings`` (otro camino del lector).

    Excel guarda las cadenas una sola vez y las referencia por índice, así que el
    lector tiene que soportar ese formato además del ``inlineStr`` que emite la
    plantilla del proyecto.
    """
    cadenas = [str(c) for fila in filas for c in fila if isinstance(c, str)]
    indice = {texto: i for i, texto in enumerate(cadenas)}
    filas_xml = []
    for numero, fila in enumerate(filas, start=1):
        celdas = []
        for columna, valor in zip(("A", "B"), fila):
            if isinstance(valor, str):
                celdas.append(
                    f'<c r="{columna}{numero}" t="s"><v>{indice[valor]}</v></c>'
                )
            else:
                celdas.append(f'<c r="{columna}{numero}"><v>{valor}</v></c>')
        filas_xml.append(f'<row r="{numero}">' + "".join(celdas) + "</row>")
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    hoja = (
        f'<?xml version="1.0"?><worksheet xmlns="{ns}"><sheetData>'
        + "".join(filas_xml)
        + "</sheetData></worksheet>"
    )
    compartidos = (
        f'<?xml version="1.0"?><sst xmlns="{ns}" count="{len(cadenas)}" '
        f'uniqueCount="{len(cadenas)}">'
        + "".join(f"<si><t>{texto}</t></si>" for texto in cadenas)
        + "</sst>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as paquete:
        paquete.writestr("xl/sharedStrings.xml", compartidos)
        paquete.writestr("xl/worksheets/sheet1.xml", hoja)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# 1. Formato de la planilla (pruebas de unidad)
# --------------------------------------------------------------------------- #
def test_los_importes_de_la_planilla_se_interpretan_en_los_dos_formatos():
    """La planilla no guarda el *locale*: hay que aceptar es-AR y en-US."""
    assert planilla_aranceles.parsear_monto("1234.56") == 1234.56
    assert planilla_aranceles.parsear_monto("1.234,56") == 1234.56
    assert planilla_aranceles.parsear_monto("1,234.56") == 1234.56
    assert planilla_aranceles.parsear_monto("12,500") == 12500.0
    assert planilla_aranceles.parsear_monto("12.500") == 12500.0
    assert planilla_aranceles.parsear_monto("120,50") == 120.5
    assert planilla_aranceles.parsear_monto("$ 18.000") == 18000.0
    assert planilla_aranceles.parsear_monto("") is None
    assert planilla_aranceles.parsear_monto(None) is None


def test_un_precio_ilegible_o_negativo_no_se_puede_leer():
    for texto in ("abc", "-500", "18 pesos", "1,2,3,4,5,"):
        with pytest.raises(ValueError):
            planilla_aranceles.parsear_monto(texto)


def test_los_codigos_se_comparan_sin_puntos_guiones_ni_mayusculas():
    assert planilla_aranceles.canonizar_codigo("01.01") == "0101"
    assert planilla_aranceles.canonizar_codigo(" 01-01 ") == "0101"
    assert planilla_aranceles.canonizar_codigo("10100") == "10100"
    assert planilla_aranceles.canonizar_codigo(None) == ""


def test_el_nombre_del_archivo_de_la_plantilla_se_normaliza():
    assert planilla_aranceles.nombre_archivo_seguro("Swiss Médical") == "swiss-medical"
    assert planilla_aranceles.nombre_archivo_seguro("O.S.D.E.") == "o-s-d-e"
    assert planilla_aranceles.nombre_archivo_seguro("") == "aranceles"


def test_la_plantilla_xlsx_es_un_paquete_ooxml_completo():
    contenido = planilla_aranceles.generar_xlsx([("10100", 18000)])
    with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
        partes = set(paquete.namelist())
    assert {
        "[Content_Types].xml",
        "_rels/.rels",
        "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
        "xl/worksheets/sheet1.xml",
    } <= partes


def test_la_plantilla_generada_se_vuelve_a_leer_sin_cambios():
    """Ida y vuelta: lo que emite el proyecto (CSV y XLSX) lo vuelve a leer él mismo."""
    filas = [("10100", 18000.0), ("01.01", None)]
    for contenido, nombre in (
        (planilla_aranceles.generar_csv(filas), "p.csv"),
        (planilla_aranceles.generar_xlsx(filas), "p.xlsx"),
    ):
        leidas = planilla_aranceles.leer_planilla(contenido, nombre)
        assert [(f.codigo, f.monto, f.error) for f in leidas] == [
            ("10100", 18000.0, None),
            ("01.01", None, None),
        ]


def test_una_planilla_sin_filas_de_datos_no_se_puede_leer():
    with pytest.raises(ValidacionError) as excinfo:
        planilla_aranceles.leer_planilla(b"codigo;precio_cobertura\r\n", "p.csv")
    assert "filas de datos" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 2. Pantalla "Configuración de convenios por obra social"
# --------------------------------------------------------------------------- #
def test_la_grilla_del_convenio_trae_el_nomenclador_con_el_coseguro_sugerido(client):
    _crear_prestacion(client, *CONSULTA)
    _crear_prestacion(client, *OBTURACION)
    obra = _crear_obra_social(client)

    convenio = _convenio(client, obra["id"])

    assert convenio["obra_social_nombre"] == "OSDE"
    assert convenio["total_prestaciones"] == 2
    assert convenio["total_cargadas"] == 0
    fila = _fila(convenio, "10100")
    assert fila["descripcion"].startswith("Capítulo I")
    assert fila["valor_base"] == 18000
    assert fila["monto_cubre_os"] == 0
    # Sin acuerdo cargado, el coseguro sugerido es el arancel base completo.
    assert fila["coseguro_sugerido"] == 18000
    assert fila["cargado"] is False
    assert fila["arancel_id"] is None
    assert convenio["total_valor_base"] == 50000
    assert convenio["total_coseguro"] == 50000


def test_la_grilla_del_convenio_marca_las_practicas_que_ya_tienen_arancel(client):
    prestacion = _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)
    r = client.post(
        f"{API}/obras-sociales/{obra['id']}/aranceles",
        json={
            "prestacion_id": prestacion["id"],
            "monto_cubre_os": 12000,
            "monto_coseguro_paciente": 4000,
        },
    )
    assert r.status_code == 201, r.text

    fila = _fila(_convenio(client, obra["id"]), "10100")

    assert fila["cargado"] is True
    assert fila["arancel_id"] == r.json()["id"]
    assert fila["monto_cubre_os"] == 12000
    assert fila["monto_coseguro_paciente"] == 4000
    assert fila["coseguro_sugerido"] == 6000  # 18000 - 12000
    assert fila["monto_total"] == 16000


def test_guardar_el_convenio_persiste_el_monto_de_la_os_y_el_coseguro(
    client, db_session
):
    prestacion = _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    r = _guardar_convenio(
        client,
        obra["id"],
        [
            {
                "prestacion_id": prestacion["id"],
                "monto_cubre_os": 12000,
                "monto_coseguro_paciente": 6000,
            }
        ],
    )

    assert r.status_code == 200, r.text
    convenio = r.json()
    assert (convenio["creados"], convenio["actualizados"], convenio["sin_cambios"]) == (
        1,
        0,
        0,
    )
    fila = _fila(convenio, "10100")
    assert fila["monto_cubre_os"] == 12000
    assert fila["monto_coseguro_paciente"] == 6000
    # Persistencia real en la tabla del módulo.
    guardado = _aranceles(db_session)["10100"]
    assert (guardado.monto_cubre_os, guardado.monto_coseguro_paciente) == (12000, 6000)


def test_el_guardado_sugiere_el_coseguro_cuando_no_se_envia(client, db_session):
    """La regla del panel (arancel base - OS) rige también el guardado en lote."""
    prestacion = _crear_prestacion(client, *OBTURACION)
    obra = _crear_obra_social(client)

    r = _guardar_convenio(
        client,
        obra["id"],
        [{"prestacion_id": prestacion["id"], "monto_cubre_os": 20000}],
    )

    assert r.status_code == 200, r.text
    assert _fila(r.json(), "20100")["monto_coseguro_paciente"] == 12000
    assert _aranceles(db_session)["20100"].monto_coseguro_paciente == 12000


def test_el_coseguro_sugerido_nunca_es_negativo(client):
    """Si la cobertura paga más que el arancel base, el mostrador no cobra nada."""
    prestacion = _crear_prestacion(client, *LIMPIEZA)
    obra = _crear_obra_social(client)

    r = _guardar_convenio(
        client,
        obra["id"],
        [{"prestacion_id": prestacion["id"], "monto_cubre_os": 40000}],
    )

    assert r.status_code == 200, r.text
    fila = _fila(r.json(), "50100")
    assert fila["coseguro_sugerido"] == 0
    assert fila["monto_coseguro_paciente"] == 0


def test_guardar_el_convenio_actualiza_lo_que_ya_estaba(client, db_session):
    prestacion = _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)
    primero = _guardar_convenio(
        client,
        obra["id"],
        [{"prestacion_id": prestacion["id"], "monto_cubre_os": 10000}],
    )
    assert primero.status_code == 200, primero.text

    segundo = _guardar_convenio(
        client,
        obra["id"],
        [{"prestacion_id": prestacion["id"], "monto_cubre_os": 15000}],
    )

    assert segundo.json()["actualizados"] == 1
    assert _aranceles(db_session)["10100"].monto_cubre_os == 15000
    # Volver a guardar el mismo precio no reescribe nada.
    repetido = _guardar_convenio(
        client,
        obra["id"],
        [{"prestacion_id": prestacion["id"], "monto_cubre_os": 15000}],
    )
    assert repetido.json()["sin_cambios"] == 1


def test_guardar_el_convenio_es_atomico_si_falta_una_practica(client, db_session):
    prestacion = _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    r = _guardar_convenio(
        client,
        obra["id"],
        [
            {"prestacion_id": prestacion["id"], "monto_cubre_os": 12000},
            {"prestacion_id": 999999, "monto_cubre_os": 5000},
        ],
    )

    assert r.status_code == 404, r.text
    assert _aranceles(db_session) == {}  # no se guardó la primera fila


def test_guardar_el_convenio_valida_los_montos_y_la_grilla(client):
    prestacion = _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    negativo = _guardar_convenio(
        client, obra["id"], [{"prestacion_id": prestacion["id"], "monto_cubre_os": -1}]
    )
    vacia = _guardar_convenio(client, obra["id"], [])
    repetida = _guardar_convenio(
        client,
        obra["id"],
        [
            {"prestacion_id": prestacion["id"], "monto_cubre_os": 1000},
            {"prestacion_id": prestacion["id"], "monto_cubre_os": 2000},
        ],
    )

    assert negativo.status_code == 422
    assert vacia.status_code == 422
    assert repetida.status_code == 422


def test_guardar_el_convenio_de_una_practica_dada_de_baja_no_se_puede(client):
    prestacion = _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)
    assert client.delete(f"{API}/prestaciones/{prestacion['id']}").status_code == 200

    r = _guardar_convenio(
        client, obra["id"], [{"prestacion_id": prestacion["id"], "monto_cubre_os": 1000}]
    )

    assert r.status_code == 404, r.text
    assert "dada de baja" in r.json()["detail"]


def test_el_convenio_de_una_obra_social_inexistente_es_404(client):
    _crear_prestacion(client, *CONSULTA)

    assert client.get(f"{API}/obras-sociales/9999/convenio").status_code == 404
    assert (
        _guardar_convenio(
            client, 9999, [{"prestacion_id": 1, "monto_cubre_os": 1}]
        ).status_code
        == 404
    )
    assert client.get(f"{API}/obras-sociales/9999/convenio/plantilla").status_code == 404
    assert _importar(client, 9999, _planilla(("10100", "1000"))).status_code == 404


# --------------------------------------------------------------------------- #
# 3. Plantilla de ejemplo (descarga)
# --------------------------------------------------------------------------- #
def test_la_plantilla_csv_trae_dos_columnas_con_los_codigos_del_nomenclador(client):
    consulta = _crear_prestacion(client, *CONSULTA)
    _crear_prestacion(client, *OBTURACION)
    obra = _crear_obra_social(client)
    assert (
        _guardar_convenio(
            client,
            obra["id"],
            [{"prestacion_id": consulta["id"], "monto_cubre_os": 12000}],
        ).status_code
        == 200
    )

    r = client.get(f"{API}/obras-sociales/{obra['id']}/convenio/plantilla")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "plantilla-aranceles-osde.csv" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")  # BOM: Excel es-AR lo abre bien
    lineas = r.content.decode("utf-8-sig").strip().splitlines()
    assert lineas[0] == "codigo;precio_cobertura"
    assert len(lineas) == 3
    assert "10100;12000,00" in lineas  # el acuerdo que ya estaba, precargado
    assert "20100;" in lineas  # sin acuerdo: la celda del precio va vacía


def test_la_plantilla_xlsx_se_descarga_como_archivo_de_excel(client):
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    r = client.get(f"{API}/obras-sociales/{obra['id']}/convenio/plantilla?formato=xlsx")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert r.headers["content-disposition"].endswith('plantilla-aranceles-osde.xlsx"')
    assert r.content[:2] == b"PK"  # un .xlsx es un ZIP
    assert [f.codigo for f in planilla_aranceles.leer_planilla(r.content, "p.xlsx")] == [
        "10100"
    ]


def test_un_formato_de_plantilla_desconocido_es_422(client):
    obra = _crear_obra_social(client)

    r = client.get(f"{API}/obras-sociales/{obra['id']}/convenio/plantilla?formato=pdf")

    assert r.status_code == 422
    assert "csv" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# 4. Importador masivo de precios
# --------------------------------------------------------------------------- #
def test_importar_una_planilla_crea_y_actualiza_aranceles(client, db_session):
    consulta = _crear_prestacion(client, *CONSULTA)
    _crear_prestacion(client, *OBTURACION)
    obra = _crear_obra_social(client)
    assert (
        _guardar_convenio(
            client,
            obra["id"],
            [{"prestacion_id": consulta["id"], "monto_cubre_os": 10000}],
        ).status_code
        == 200
    )

    r = _importar(client, obra["id"], _planilla(("10100", "12000"), ("20100", "30000")))

    assert r.status_code == 200, r.text
    reporte = r.json()
    assert reporte["simulado"] is False
    assert reporte["obra_social_nombre"] == "OSDE"
    assert reporte["filas_leidas"] == 2
    assert (reporte["creados"], reporte["actualizados"], reporte["errores"]) == (1, 1, 0)
    guardados = _aranceles(db_session)
    assert guardados["10100"].monto_cubre_os == 12000
    assert guardados["10100"].monto_coseguro_paciente == 6000  # 18000 - 12000
    assert guardados["20100"].monto_cubre_os == 30000
    assert guardados["20100"].monto_coseguro_paciente == 2000  # 32000 - 30000


def test_el_reporte_de_la_importacion_detalla_cada_fila(client):
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    reporte = _importar(client, obra["id"], _planilla(("10100", "12000"))).json()

    assert len(reporte["detalle"]) == 1
    fila = reporte["detalle"][0]
    assert fila["linea"] == 2
    assert fila["codigo"] == "10100"
    assert fila["estado"] == "NUEVO"
    assert fila["monto_cubre_os"] == 12000
    assert fila["monto_coseguro_paciente"] == 6000
    assert fila["descripcion"].startswith("Capítulo I")
    assert fila["prestacion_id"] is not None


def test_la_importacion_simulada_no_escribe_nada(client, db_session):
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    reporte = _importar(
        client, obra["id"], _planilla(("10100", "12000")), simular=True
    ).json()

    assert reporte["simulado"] is True
    assert reporte["creados"] == 1  # la vista previa anticipa el resultado...
    assert _aranceles(db_session) == {}  # ...pero no se guarda nada


def test_un_codigo_desconocido_se_reporta_sin_cortar_la_importacion(client, db_session):
    _crear_prestacion(client, *CONSULTA)
    _crear_prestacion(client, *OBTURACION)
    obra = _crear_obra_social(client)

    reporte = _importar(
        client,
        obra["id"],
        _planilla(("10100", "12000"), ("99999", "5000"), ("20100", "30000")),
    ).json()

    assert reporte["filas_leidas"] == 3
    assert reporte["errores"] == 1
    assert (reporte["creados"], reporte["actualizados"]) == (2, 0)
    con_error = [f for f in reporte["detalle"] if f["estado"] == "ERROR"]
    assert con_error[0]["linea"] == 3
    assert con_error[0]["codigo"] == "99999"
    assert "nomenclador" in con_error[0]["mensaje"]
    # El resto de la planilla sí entró.
    assert set(_aranceles(db_session)) == {"10100", "20100"}


def test_las_filas_sin_precio_se_ignoran(client, db_session):
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    reporte = _importar(client, obra["id"], _planilla(("10100", ""))).json()

    assert reporte["ignoradas"] == 1
    assert reporte["errores"] == 0
    assert reporte["detalle"][0]["estado"] == "IGNORADA"
    assert _aranceles(db_session) == {}


def test_un_precio_ilegible_o_negativo_se_reporta_como_error(client):
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    reporte = _importar(
        client, obra["id"], _planilla(("10100", "abc"), ("20100", "-100"))
    ).json()

    assert reporte["errores"] == 2
    assert reporte["creados"] == 0
    mensajes = " ".join(f["mensaje"] or "" for f in reporte["detalle"])
    assert "no se reconoce el importe" in mensajes
    assert "negativo" in mensajes


def test_la_importacion_acepta_la_planilla_en_texto_plano(client, db_session):
    """El endpoint también acepta el contenido como texto (no sólo base64)."""
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    reporte = _importar_texto(
        client, obra["id"], "codigo;precio_cobertura\r\n10.100;12.500\r\n"
    ).json()

    assert reporte["creados"] == 1
    # El código con puntos encuentra la práctica y "12.500" se lee como miles.
    assert _aranceles(db_session)["10100"].monto_cubre_os == 12500


def test_la_importacion_acepta_un_xlsx_con_cadenas_compartidas(client, db_session):
    """Excel guarda las cadenas por índice (``sharedStrings``): hay que leerlas."""
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)
    libro = _xlsx_con_cadenas_compartidas(
        [("codigo", "precio_cobertura"), ("10100", 12000)]
    )

    reporte = _importar(client, obra["id"], libro, nombre="convenio.xlsx").json()

    assert reporte["creados"] == 1
    assert _aranceles(db_session)["10100"].monto_cubre_os == 12000


def test_el_importador_rechaza_un_archivo_que_no_puede_leer(client):
    _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)

    vacio = _importar(client, obra["id"], b"")
    roto = _importar(client, obra["id"], b"PK\x03\x04nada", nombre="roto.xlsx")
    viejo = _importar(client, obra["id"], b"a;b\r\n", nombre="aranceles.xls")
    solo_cabecera = _importar(client, obra["id"], _planilla())
    sin_archivo = client.post(
        f"{API}/obras-sociales/{obra['id']}/importar-aranceles",
        json={"nombre_archivo": "planilla.csv"},
    )

    assert vacio.status_code == 422
    assert roto.status_code == 422
    assert "xlsx" in roto.json()["detail"]
    assert viejo.status_code == 422
    assert ".xls" in viejo.json()["detail"]
    assert solo_cabecera.status_code == 422
    assert sin_archivo.status_code == 422


def test_la_plantilla_descargada_se_puede_completar_y_volver_a_importar(
    client, db_session
):
    """Circuito completo: descargar la plantilla, completarla y subirla."""
    consulta = _crear_prestacion(client, *CONSULTA)
    _crear_prestacion(client, *OBTURACION)
    obra = _crear_obra_social(client)
    assert (
        _guardar_convenio(
            client,
            obra["id"],
            [{"prestacion_id": consulta["id"], "monto_cubre_os": 12000}],
        ).status_code
        == 200
    )

    plantilla = client.get(f"{API}/obras-sociales/{obra['id']}/convenio/plantilla")
    texto = plantilla.content.decode("utf-8-sig").replace("20100;", "20100;30000")

    reporte = _importar_texto(client, obra["id"], texto).json()

    assert reporte["errores"] == 0
    assert reporte["sin_cambios"] == 1  # la consulta ya estaba en 12000
    assert reporte["creados"] == 1  # la obturación entró con esta planilla
    assert _aranceles(db_session)["20100"].monto_coseguro_paciente == 2000


# --------------------------------------------------------------------------- #
# 5. Cierre de la visita: el saldo de la OS queda PENDIENTE
# --------------------------------------------------------------------------- #
def _crear_paciente(client, **overrides) -> dict:
    """Crea un paciente por API (con cobertura si se pasa ``obra_social_id``)."""
    payload = {"nombre": "Ana Suárez", "telefono": "+5491155667788"}
    payload.update(overrides)
    r = client.post(f"{API}/pacientes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_turno(client, doctor_id: int, paciente_id: int) -> dict:
    """Agenda un turno por API y devuelve su representación."""
    r = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": paciente_id,
            "doctor_id": doctor_id,
            "fecha_hora_inicio": DIA.isoformat(),
            "fecha_hora_fin": (DIA + timedelta(hours=1)).isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_el_precio_importado_es_el_que_cobra_el_mostrador(client, db_session, doctor):
    """Integración: el convenio importado define el coseguro y el saldo de la OS."""
    prestacion = _crear_prestacion(client, *CONSULTA)
    obra = _crear_obra_social(client)
    # La tabla de precios llega por planilla, como si viniera de la cobertura.
    assert (
        _importar(client, obra["id"], _planilla(("10100", "12000"))).status_code == 200
    )
    paciente = _crear_paciente(
        client, obra_social_id=obra["id"], numero_afiliado="6100"
    )
    turno = _crear_turno(client, doctor.id, paciente["id"])

    r = client.post(
        f"{API}/turnos/{turno['id']}/facturar",
        json={"prestaciones": [{"prestacion_id": prestacion["id"]}]},
    )

    assert r.status_code == 201, r.text
    movimiento = r.json()
    assert movimiento["monto_efectivo"] == 6000  # el coseguro sugerido por la grilla
    assert movimiento["monto_pendiente_os"] == 12000
    assert movimiento["estado_pago"] == "PENDIENTE"  # queda para la liquidación
    db_session.expire_all()
    guardado = (
        db_session.execute(
            select(MovimientoCaja).where(MovimientoCaja.turno_id == turno["id"])
        )
        .scalars()
        .one()
    )
    assert guardado.estado_pago.value == "PENDIENTE"
    assert guardado.monto_pendiente_os == 12000
    # Y la caja del día lo muestra abierto para presentarlo en el período.
    caja = client.get(f"{API}/movimientos-caja?estado_pago=PENDIENTE").json()
    assert [m["turno_id"] for m in caja] == [turno["id"]]


# --------------------------------------------------------------------------- #
# 6. Panel: la pantalla de convenios y el importador
# --------------------------------------------------------------------------- #
def test_el_panel_tiene_la_pestana_de_convenios_con_su_grilla(client):
    html = client.get("/").text

    assert 'data-fact-tab="convenios"' in html
    assert 'id="fact-panel-convenios"' in html
    assert "Configuración de convenios por obra social" in html
    for identificador in (
        "conv-os",
        "conv-buscar",
        "conv-lista",
        "conv-guardar",
        "conv-pendientes",
        "conv-contador",
        "conv-total-base",
        "conv-total-os",
        "conv-total-coseguro",
        "conv-pie-base",
        "conv-pie-os",
        "conv-pie-coseguro",
    ):
        assert f'id="{identificador}"' in html
    # Las tres columnas que pide la pantalla.
    assert "Arancel base" in html
    assert "Monto que cubre la OS" in html
    assert "Coseguro sugerido" in html


def test_el_importador_tiene_la_plantilla_y_el_recuadro_de_arrastre(client):
    html = client.get("/").text

    assert "Descargar Plantilla Excel de Ejemplo" in html
    assert 'id="conv-plantilla"' in html
    assert 'id="conv-formato"' in html
    assert 'id="conv-drop"' in html
    assert 'id="conv-archivo"' in html
    assert 'accept=".csv,.txt,.xlsx"' in html
    assert 'id="conv-reporte"' in html
    assert 'id="conv-confirmar"' in html
    assert "precio_cobertura" in html


def test_el_javascript_calcula_el_coseguro_y_llama_a_los_endpoints(client):
    js = client.get("/static/app.js").text

    assert "function coseguroDeConvenio(" in js
    assert "Math.round(diferencia * 100) / 100" in js
    assert "function cargarConvenio(" in js
    assert "function guardarConvenio(" in js
    assert "function fijarMontoConvenio(" in js
    assert "function descargarPlantillaConvenio(" in js
    assert "function importarPlanillaConvenio(" in js
    assert "function elegirArchivoConvenio(" in js
    assert "function limpiarImportacionConvenio(" in js
    assert "${OBRAS_SOCIALES}/${obraSocialId}/convenio" in js
    assert "${OBRAS_SOCIALES}/${obraSocialId}/importar-aranceles" in js
    assert "plantilla?formato=" in js
    assert 'data-conv-monto="${fila.prestacion_id}"' in js
    assert 'data-conv-coseguro="${fila.prestacion_id}"' in js
    assert "simular: simular" in js
    # El recuadro de arrastrar y soltar está cableado de verdad.
    assert '"dragenter", "dragover"' in js
    assert 'convZona.addEventListener("drop"' in js
    assert 'convZona.addEventListener("click"' in js


def test_la_hoja_de_estilos_tiene_los_estilos_de_convenios(client):
    css = client.get("/static/styles.css").text

    for clase in (
        ".conv-drop",
        ".conv-drop.activo",
        ".conv-monto",
        ".conv-coseguro",
        "tr.conv-cambiado",
        ".conv-reporte-lista",
    ):
        assert clase in css


def test_el_modal_de_cobro_avisa_que_el_saldo_queda_pendiente(client):
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert "Cobrar Coseguro en mostrador" in html
    assert 'id="fact-cobro-pendiente"' in html
    assert "Queda PENDIENTE para la liquidación de la cobertura" in html
    assert "Queda PENDIENTE para la liquidación de ${cobertura}" in js
