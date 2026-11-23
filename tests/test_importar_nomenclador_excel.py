"""Pruebas del importador del nomenclador en Excel (``prestaciones``, Módulo 3).

Verifican ``scripts/importar_nomenclador_excel`` sobre la base SQLite en memoria
del ``conftest`` (nunca tocan ``odontologico.db``):

1. **Lectura**: ubica las columnas por el **nombre** del encabezado (aunque
   vengan en otro orden o con columnas de más) y arma ``Capítulo: práctica``.
2. **Aranceles**: interpreta los importes de la grilla y trata los textos sin
   precio (``No publicado``, ``A convenir``…) como «sin arancel» (valor 0).
3. **Carga**: crea las prácticas nuevas, actualiza las existentes, no pisa un
   precio cargado a mano con un texto sin arancel y es **idempotente**.
4. **Baja lógica** con ``--desactivar-faltantes`` y reactivación de los códigos
   que vuelven a figurar.
5. **Errores de la planilla**: cabecera ausente, fila sin código y código
   repetido se informan sin cortar la lectura.
6. **Consola**: ``main`` con ``--dry-run`` no escribe y un archivo inexistente
   termina con código de error.
"""

import io
import zipfile

import pytest

from app.core.exceptions import ValidacionError
from app.models.prestacion import Prestacion
from scripts import importar_nomenclador_excel as importador

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

ENCABEZADO = ["Capítulo", "Código", "Descripción", "Arancel ($)"]


def _letra(indice: int) -> str:
    """Letra de la columna (0 → ``A``, 26 → ``AA``)."""
    letras = ""
    numero = indice + 1
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(ord("A") + resto) + letras
    return letras


def _celda(columna: str, fila: int, valor: object) -> str:
    """Una celda del OOXML: número si es sólo dígitos, texto en los demás casos."""
    texto = "" if valor is None else str(valor)
    if not texto:
        return ""
    referencia = f"{columna}{fila}"
    if texto.isdigit():  # Excel guarda los importes como número
        return f'<c r="{referencia}"><v>{texto}</v></c>'
    escapado = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<c r="{referencia}" t="inlineStr">'
        f'<is><t xml:space="preserve">{escapado}</t></is></c>'
    )


def _xlsx(filas) -> bytes:
    """Arma un ``.xlsx`` mínimo (una hoja, sin ``sharedStrings``) con esas filas."""
    cuerpo = []
    for numero, fila in enumerate(filas, start=1):
        celdas = "".join(
            _celda(_letra(indice), numero, valor) for indice, valor in enumerate(fila)
        )
        cuerpo.append(f'<row r="{numero}">{celdas}</row>')

    cabecera = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    hoja = (
        cabecera
        + f'<worksheet xmlns="{_NS}"><sheetData>'
        + "".join(cuerpo)
        + "</sheetData></worksheet>"
    )
    content_types = (
        cabecera
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
        cabecera
        + f'<Relationships xmlns="{_NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{_NS_REL}/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        cabecera
        + f'<workbook xmlns="{_NS}" xmlns:r="{_NS_REL}"><sheets>'
        '<sheet name="Nomenclador" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    )
    workbook_rels = (
        cabecera
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
        paquete.writestr("xl/worksheets/sheet1.xml", hoja)
    return buffer.getvalue()


def _planilla(filas) -> bytes:
    return _xlsx([ENCABEZADO, *filas])


FILAS = [
    ["CONSULTAS", "101", "Examen – Diagnóstico – Fichado y Plan de Tratamiento", "48050"],
    ["OPERATORIA", "204", "Reconstrucción de ángulos con tornillo en conducto", "No indicado"],
    ["PRÓTESIS", "40101", "Incrustación cavidad simple", "$ 317.480"],
]


class _SesionCompartida:
    """Context manager mínimo: entrega la sesión de prueba sin cerrarla."""

    def __init__(self, sesion):
        self._sesion = sesion

    def __enter__(self):
        return self._sesion

    def __exit__(self, *exc_info):
        return False


@pytest.fixture()
def importador_aislado(monkeypatch, db_session):
    """Apunta ``main`` a la sesión de prueba y neutraliza ``init_db``."""
    monkeypatch.setattr(
        importador, "SessionLocal", lambda: _SesionCompartida(db_session)
    )
    monkeypatch.setattr(importador, "init_db", lambda: None)
    return db_session


def _catalogo(db_session) -> dict:
    return {fila.codigo_nomenclador: fila for fila in db_session.query(Prestacion)}


# --------------------------------------------------------------------------- #
# 1 y 2. Lectura de la planilla
# --------------------------------------------------------------------------- #


def test_lee_codigo_descripcion_y_arancel():
    practicas = importador.leer_practicas(_planilla(FILAS), "nomenclador.xlsx")

    assert [p.codigo for p in practicas] == ["101", "204", "40101"]
    assert practicas[0].descripcion == (
        "CONSULTAS: Examen – Diagnóstico – Fichado y Plan de Tratamiento"
    )
    assert practicas[0].valor == 48050.0
    assert practicas[0].linea == 2  # la línea 1 es la cabecera
    assert all(p.error is None for p in practicas)


def test_arancel_con_simbolo_y_separador_de_miles():
    practicas = importador.leer_practicas(_planilla(FILAS), "nomenclador.xlsx")

    assert practicas[2].valor == 317480.0


def test_textos_sin_arancel_no_son_error():
    practicas = importador.leer_practicas(_planilla(FILAS), "nomenclador.xlsx")

    assert practicas[1].arancel == "No indicado"
    assert practicas[1].valor is None
    assert practicas[1].error is None


def test_columnas_se_ubican_por_nombre_y_en_cualquier_orden():
    encabezado = ["Observaciones", "Arancel", "Descripción", "Capítulo", "Código"]
    filas = [encabezado, ["", "12345", "Práctica suelta", "VARIAS", "999"]]

    practicas = importador.leer_practicas(_xlsx(filas), "planilla.xlsx")

    assert practicas[0].codigo == "999"
    assert practicas[0].descripcion == "VARIAS: Práctica suelta"
    assert practicas[0].valor == 12345.0


def test_sin_cabecera_lanza_validacion():
    with pytest.raises(ValidacionError):
        importador.leer_practicas(_xlsx([FILAS[0]]), "planilla.xlsx")


def test_planilla_vacia_lanza_validacion():
    with pytest.raises(ValidacionError):
        importador.leer_practicas(b"", "planilla.xlsx")


def test_fila_sin_codigo_se_informa_sin_cortar_la_lectura():
    practicas = importador.leer_practicas(
        _planilla([*FILAS, ["CONSULTAS", "", "Sin código", "100"]]), "planilla.xlsx"
    )

    assert [p.codigo for p in practicas[:3]] == ["101", "204", "40101"]
    assert practicas[-1].error == "falta el código"


def test_codigo_repetido_conserva_la_primera_aparicion():
    practicas = importador.leer_practicas(
        _planilla([FILAS[0], ["CONSULTAS", " 101 ", "Otra descripción", "1"]]),
        "planilla.xlsx",
    )

    assert practicas[0].error is None
    assert "ya apareció en la línea 2" in practicas[1].error


def test_arancel_con_texto_agregado_es_error():
    practicas = importador.leer_practicas(
        _planilla([["CONSULTAS", "999", "Práctica rara", "12,50 según sesión"]]),
        "planilla.xlsx",
    )

    assert practicas[0].valor is None
    assert practicas[0].error == "no se entiende el arancel «12,50 según sesión»"


def test_descripcion_larga_se_recorta_a_doscientos():
    practicas = importador.leer_practicas(
        _planilla([["VARIAS", "999", "x" * 300, "100"]]), "planilla.xlsx"
    )

    assert len(practicas[0].descripcion) == 200
    assert practicas[0].descripcion.endswith("…")


def test_csv_tambien_se_acepta():
    contenido = (
        "Capítulo;Código;Descripción;Arancel ($)\r\n"
        "CONSULTAS;101;Consulta de urgencia;83.603\r\n"
    ).encode("utf-8-sig")

    practicas = importador.leer_practicas(contenido, "planilla.csv")

    assert practicas[0].codigo == "101"
    assert practicas[0].valor == 83603.0


# --------------------------------------------------------------------------- #
# 3. Carga del catálogo: altas, actualizaciones e idempotencia
# --------------------------------------------------------------------------- #


def test_importa_crea_las_practicas_nuevas(db_session):
    contadores = importador.cargar_practicas(
        db_session, importador.leer_practicas(_planilla(FILAS)), detalle=False
    )

    assert contadores["nuevas"] == 3
    assert contadores["actualizadas"] == 0
    assert contadores["sin_precio"] == 1
    assert contadores["errores"] == 0
    assert contadores["total"] == 3

    catalogo = _catalogo(db_session)
    assert catalogo["101"].descripcion == (
        "CONSULTAS: Examen – Diagnóstico – Fichado y Plan de Tratamiento"
    )
    assert catalogo["101"].valor_base == 48050.0
    assert catalogo["204"].valor_base == 0.0  # «No indicado»: sin arancel
    assert all(fila.activa for fila in catalogo.values())


def test_segunda_corrida_no_duplica_ni_cambia(db_session):
    practicas = importador.leer_practicas(_planilla(FILAS))
    importador.cargar_practicas(db_session, practicas, detalle=False)

    contadores = importador.cargar_practicas(db_session, practicas, detalle=False)

    assert contadores["nuevas"] == 0
    assert contadores["actualizadas"] == 0
    assert contadores["sin_cambios"] == 3
    assert contadores["total"] == 3
    assert db_session.query(Prestacion).count() == 3


def test_actualiza_descripcion_y_precio_de_las_existentes(db_session):
    db_session.add(
        Prestacion(
            codigo_nomenclador="101",
            descripcion="Cargada a mano",
            valor_base=1.0,
            activa=True,
        )
    )
    db_session.commit()

    contadores = importador.cargar_practicas(
        db_session, importador.leer_practicas(_planilla(FILAS)), detalle=False
    )

    assert contadores["nuevas"] == 2
    assert contadores["actualizadas"] == 1
    fila = _catalogo(db_session)["101"]
    assert fila.valor_base == 48050.0
    assert fila.descripcion.startswith("CONSULTAS: ")


def test_arancel_no_publicado_no_pisa_el_precio_cargado(db_session):
    db_session.add(
        Prestacion(
            codigo_nomenclador="204",
            descripcion="Operatoria vieja",
            valor_base=7000.0,
            activa=True,
        )
    )
    db_session.commit()

    contadores = importador.cargar_practicas(
        db_session, importador.leer_practicas(_planilla(FILAS)), detalle=False
    )

    assert contadores["actualizadas"] == 1  # cambia la descripción, no el precio
    fila = _catalogo(db_session)["204"]
    assert fila.valor_base == 7000.0
    assert fila.descripcion.startswith("OPERATORIA: ")


def test_solo_nuevas_respeta_los_precios_ajustados_a_mano(db_session):
    db_session.add(
        Prestacion(
            codigo_nomenclador="101",
            descripcion="Ajustada en el panel",
            valor_base=99999.0,
            activa=True,
        )
    )
    db_session.commit()

    contadores = importador.cargar_practicas(
        db_session,
        importador.leer_practicas(_planilla(FILAS)),
        solo_nuevas=True,
        detalle=False,
    )

    assert contadores["nuevas"] == 2
    assert contadores["actualizadas"] == 0
    fila = _catalogo(db_session)["101"]
    assert fila.valor_base == 99999.0
    assert fila.descripcion == "Ajustada en el panel"


def test_dry_run_no_escribe_en_la_base(db_session):
    contadores = importador.cargar_practicas(
        db_session,
        importador.leer_practicas(_planilla(FILAS)),
        dry_run=True,
        detalle=False,
    )

    assert contadores["nuevas"] == 3
    assert contadores["total"] == 0
    assert db_session.query(Prestacion).count() == 0


# --------------------------------------------------------------------------- #
# 4. Bajas lógicas y reactivaciones
# --------------------------------------------------------------------------- #


def test_desactivar_faltantes_da_de_baja_logica(db_session):
    db_session.add(
        Prestacion(
            codigo_nomenclador="50100",
            descripcion="Limpieza vieja",
            valor_base=38000.0,
            activa=True,
        )
    )
    db_session.commit()

    contadores = importador.cargar_practicas(
        db_session,
        importador.leer_practicas(_planilla(FILAS)),
        desactivar_faltantes=True,
        detalle=False,
    )

    assert contadores["desactivadas"] == 1
    fila = _catalogo(db_session)["50100"]
    assert fila.activa is False
    assert fila.valor_base == 38000.0  # se conserva: hay facturas que la usan


def test_sin_la_opcion_las_faltantes_siguen_activas(db_session):
    db_session.add(
        Prestacion(
            codigo_nomenclador="50100",
            descripcion="Limpieza vieja",
            valor_base=38000.0,
            activa=True,
        )
    )
    db_session.commit()

    contadores = importador.cargar_practicas(
        db_session, importador.leer_practicas(_planilla(FILAS)), detalle=False
    )

    assert contadores["desactivadas"] == 0
    assert _catalogo(db_session)["50100"].activa is True


def test_codigo_que_vuelve_a_figurar_se_reactiva(db_session):
    db_session.add(
        Prestacion(
            codigo_nomenclador="101",
            descripcion="Dada de baja",
            valor_base=48050.0,
            activa=False,
        )
    )
    db_session.commit()

    contadores = importador.cargar_practicas(
        db_session, importador.leer_practicas(_planilla(FILAS)), detalle=False
    )

    assert contadores["reactivadas"] == 1
    assert _catalogo(db_session)["101"].activa is True


# --------------------------------------------------------------------------- #
# 6. Interfaz de consola (``main``)
# --------------------------------------------------------------------------- #


def test_main_importa_la_planilla(tmp_path, importador_aislado, capsys):
    archivo = tmp_path / "nomenclador_odontologico.xlsx"
    archivo.write_bytes(_planilla(FILAS))

    assert importador.main([str(archivo), "--sin-detalle"]) == 0

    catalogo = _catalogo(importador_aislado)
    assert sorted(catalogo) == ["101", "204", "40101"]
    assert catalogo["101"].valor_base == 48050.0
    salida = capsys.readouterr().out
    assert "Prestaciones nuevas" in salida
    assert "Total en el catálogo" in salida


def test_main_dry_run_no_escribe(tmp_path, importador_aislado):
    archivo = tmp_path / "nomenclador_odontologico.xlsx"
    archivo.write_bytes(_planilla(FILAS))

    assert importador.main([str(archivo), "--dry-run", "--sin-detalle"]) == 0

    assert importador_aislado.query(Prestacion).count() == 0


def test_main_con_archivo_inexistente_devuelve_error(tmp_path, importador_aislado, capsys):
    assert importador.main([str(tmp_path / "no-existe.xlsx")]) == 1
    assert "No se pudo leer la planilla" in capsys.readouterr().out


def test_main_con_cabecera_invalida_devuelve_error(tmp_path, importador_aislado, capsys):
    archivo = tmp_path / "otra_planilla.xlsx"
    archivo.write_bytes(_xlsx([["a", "b"], ["c", "d"]]))

    assert importador.main([str(archivo)]) == 1
    assert "encabezados" in capsys.readouterr().out


def test_main_devuelve_error_con_filas_invalidas(tmp_path, importador_aislado):
    archivo = tmp_path / "nomenclador_odontologico.xlsx"
    archivo.write_bytes(_planilla([*FILAS, ["CONSULTAS", "", "Sin código", "1"]]))

    assert importador.main([str(archivo), "--sin-detalle"]) == 1
    assert importador_aislado.query(Prestacion).count() == 3  # carga el resto


def test_main_devuelve_error_si_falla_la_base(tmp_path, monkeypatch, capsys):
    from sqlalchemy.exc import OperationalError

    archivo = tmp_path / "nomenclador_odontologico.xlsx"
    archivo.write_bytes(_planilla(FILAS))

    class _Explota:
        def __enter__(self):
            raise OperationalError("select 1", {}, Exception("sin conexión"))

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(importador, "SessionLocal", _Explota)
    monkeypatch.setattr(importador, "init_db", lambda: None)

    assert importador.main([str(archivo), "--sin-detalle"]) == 1
    assert "No se pudo conectar" in capsys.readouterr().out
