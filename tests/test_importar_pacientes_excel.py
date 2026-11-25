"""Pruebas del importador de pacientes desde Access (``pacientes.xlsx``).

Verifican ``scripts/importar_pacientes_excel`` sobre la base SQLite en memoria
del ``conftest`` (nunca tocan ``odontologico.db`` ni el PostgreSQL local):

1. **Lectura**: ubica las columnas por el **nombre** del encabezado, normaliza el
   teléfono (incluidos los sufijos de Access) y arma el número de afiliado con el
   plan.
2. **Catálogos**: crea las categorías y las obras sociales que falten; el código
   ``0``/``PARTICULAR`` deja al paciente sin cobertura y una categoría
   desconocida cae en ``SIN_CATEGORIZAR``.
3. **Carga**: inserta los pacientes, reemplaza el teléfono vacío y es
   **idempotente** (una segunda pasada no duplica).
4. **Consola**: ``main`` importa, ``--dry-run`` no escribe y un archivo
   inexistente termina con código de error.
"""

import io
import zipfile

import pytest

from app.core.exceptions import ValidacionError
from app.core.planilla_aranceles import leer_filas
from app.models.categoria_paciente import CategoriaPaciente
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from scripts import importar_pacientes_excel as importador

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"


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
    if texto.isdigit():
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
        '<sheet name="Pacientes" sheetId="1" r:id="rId1"/>'
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


ENCABEZADO = [
    "codigo",
    "obra social",
    "nombre",
    "nro afiliado",
    "plan obra social",
    "domicilio",
    "Telefono",
    "categoria",
]

FILAS = [
    ["29", "0", "AGUILAR MIGUEL ANGEL", "", "", "HEROES 3388", "0", "SIN CATEGORIZAR"],
    [
        "30",
        "1",
        "AGUILAR SARA",
        "10-064083-11",
        "PLAN UNICO",
        "PAULO 2747",
        "+5493512169073LIDIA",
        "EXCELENTE",
    ],
]


def _planilla(filas=FILAS) -> bytes:
    return _xlsx([ENCABEZADO, *filas])


def _filas(filas=FILAS):
    """La planilla ya interpretada, como la recibe ``leer_pacientes``."""
    return leer_filas(_planilla(filas), "pacientes.xlsx")


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


# --------------------------------------------------------------------------- #
# 1. Lectura de la planilla y normalización
# --------------------------------------------------------------------------- #


def test_lee_nombre_telefono_plan_y_domicilio():
    pacientes = importador.leer_pacientes(_filas(), {})

    assert [p.linea for p in pacientes] == [2, 3]
    assert pacientes[0].nombre == "AGUILAR MIGUEL ANGEL"
    assert pacientes[0].domicilio == "HEROES 3388"
    assert pacientes[0].sin_telefono is True  # «0» no es un número
    assert pacientes[1].telefono == "+5493512169073"  # sufijo «LIDIA» descartado
    assert pacientes[1].numero_afiliado == "10-064083-11 (PLAN UNICO)"
    assert pacientes[1].obra_social_codigo == "1"
    assert pacientes[1].categoria == "SIN_CATEGORIZAR"  # sin catálogo cargado


def test_sin_plan_no_anexa_el_plan_al_afiliado():
    pacientes = importador.leer_pacientes(_filas(), {}, con_plan=False)

    assert pacientes[1].numero_afiliado == "10-064083-11"


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("+5493512169073LIDIA", "+5493512169073"),
        ("+5493515642590", "+5493515642590"),
        ("4710716", "4710716"),
        ("0351 471-0716", "03514710716"),
        ("0", None),
        ("00", None),
        ("NUERA DE R. BRITOS", None),
        ("", None),
    ],
)
def test_normaliza_el_telefono(crudo, esperado):
    assert importador.normalizar_telefono(crudo) == esperado


def test_las_columnas_se_ubican_por_nombre_y_en_cualquier_orden():
    encabezado = ["categoria", "Telefono", "nombre", "obra social", "Observaciones"]
    filas = [encabezado, ["MALO", "3511112222", "PEREZ ANA", "0", "ignorar"]]

    pacientes = importador.leer_pacientes(leer_filas(_xlsx(filas), "p.xlsx"), {"malo": "MALO"})

    assert pacientes[0].nombre == "PEREZ ANA"
    assert pacientes[0].telefono == "3511112222"
    assert pacientes[0].categoria == "MALO"


def test_sin_el_encabezado_de_nombre_lanza_validacion():
    with pytest.raises(ValidacionError):
        importador.leer_pacientes(leer_filas(_xlsx([["a", "b"], ["c", "d"]]), "p.xlsx"), {})


def test_una_categoria_desconocida_cae_en_sin_categorizar():
    filas = [[*FILAS[0][:7], "CATEGORIA NUEVA"]]
    pacientes = importador.leer_pacientes(_filas(filas), {"excelente": "EXCELENTE"})

    assert pacientes[0].categoria == "SIN_CATEGORIZAR"



# --------------------------------------------------------------------------- #
# 2 y 3. Catálogos y carga
# --------------------------------------------------------------------------- #


def test_carga_crea_las_categorias_y_obras_sociales_que_faltan(db_session):
    resumen = importador.Resumen()
    categorias = importador.cargar_categorias(
        db_session, [("2", "MUY BUENO"), ("6", "MUY MALO")], resumen
    )
    obras = importador.cargar_obras_sociales(
        db_session, [("1", "OSDE"), ("0", "PARTICULAR")], resumen
    )

    assert categorias["muy bueno"] == "MUY_BUENO"
    assert categorias["muy malo"] == "MUY_MALO"
    assert resumen.categorias_creadas == 2
    assert obras["1"] is not None
    assert obras["0"] is None  # PARTICULAR = sin cobertura
    assert resumen.obras_creadas == 1

    muy_malo = db_session.query(CategoriaPaciente).filter_by(codigo="MUY_MALO").one()
    assert muy_malo.alerta is True  # aviso previo al turno
    assert muy_malo.nombre == "Muy malo"
    assert db_session.query(ObraSocial).filter_by(nombre="OSDE").count() == 1


def test_carga_guarda_los_pacientes_y_reemplaza_el_telefono_vacio(db_session):
    resumen = importador.Resumen()
    categorias = importador.cargar_categorias(db_session, [], resumen)
    obras = importador.cargar_obras_sociales(
        db_session, [("1", "OSDE"), ("0", "PARTICULAR")], resumen
    )
    pacientes = importador.leer_pacientes(_filas(), categorias)

    importador.cargar_pacientes(
        db_session, pacientes, obras, resumen, telefono_sin_dato="000000"
    )

    assert resumen.creados == 2
    assert resumen.sin_telefono == 1
    assert resumen.sin_obra_social == 1
    assert db_session.query(Paciente).count() == 2

    miguel = db_session.query(Paciente).filter_by(nombre="AGUILAR MIGUEL ANGEL").one()
    assert miguel.telefono == "000000"
    assert miguel.obra_social_id is None
    assert miguel.categoria == "SIN_CATEGORIZAR"

    sara = db_session.query(Paciente).filter_by(nombre="AGUILAR SARA").one()
    assert sara.telefono == "+5493512169073"
    assert sara.obra_social is not None and sara.obra_social.nombre == "OSDE"
    assert sara.numero_afiliado == "10-064083-11 (PLAN UNICO)"
    assert sara.categoria == "EXCELENTE"
    assert sara.domicilio == "PAULO 2747"


def test_reimportar_no_duplica_los_pacientes(db_session):
    resumen = importador.Resumen()
    categorias = importador.cargar_categorias(db_session, [], resumen)
    obras = importador.cargar_obras_sociales(db_session, [], resumen)
    pacientes = importador.leer_pacientes(_filas(), categorias)
    importador.cargar_pacientes(
        db_session, pacientes, obras, resumen, telefono_sin_dato="000000"
    )

    segundo = importador.Resumen()
    importador.cargar_pacientes(
        db_session, pacientes, obras, segundo, telefono_sin_dato="000000"
    )

    assert segundo.creados == 0
    assert segundo.duplicados == 2
    assert db_session.query(Paciente).count() == 2


def test_un_codigo_de_obra_desconocido_se_informa(db_session):
    resumen = importador.Resumen()
    categorias = importador.cargar_categorias(db_session, [], resumen)
    obras = importador.cargar_obras_sociales(db_session, [("1", "OSDE")], resumen)
    filas = [[FILAS[0][0], "999", *FILAS[0][2:]]]
    pacientes = importador.leer_pacientes(_filas(filas), categorias)

    importador.cargar_pacientes(
        db_session, pacientes, obras, resumen, telefono_sin_dato="000000"
    )

    assert resumen.obras_desconocidas == {"999": 1}
    assert resumen.sin_obra_social == 1



# --------------------------------------------------------------------------- #
# 4. Interfaz de consola (``main``)
# --------------------------------------------------------------------------- #


def test_main_importa_la_planilla(tmp_path, importador_aislado, capsys):
    archivo = tmp_path / "pacientes.xlsx"
    archivo.write_bytes(_planilla())

    assert importador.main([str(archivo)]) == 0

    assert importador_aislado.query(Paciente).count() == 2
    salida = capsys.readouterr().out
    assert "Pacientes creados" in salida
    assert "Pacientes leídos" in salida


def test_main_lee_los_catalogos_de_la_misma_carpeta(
    tmp_path, importador_aislado, capsys
):
    archivo = tmp_path / "pacientes.xlsx"
    archivo.write_bytes(_planilla())
    (tmp_path / "mutuales.xlsx").write_bytes(
        _xlsx([["codMutual", "nomMutual"], ["1", "OSDE"], ["0", "PARTICULAR"]])
    )
    (tmp_path / "categorias.xlsx").write_bytes(
        _xlsx([["codCategoria", "nomCategoria"], ["6", "MUY MALO"]])
    )

    assert importador.main([str(archivo)]) == 0

    assert importador_aislado.query(ObraSocial).count() == 1
    assert (
        importador_aislado.query(CategoriaPaciente)
        .filter_by(codigo="MUY_MALO")
        .count()
        == 1
    )
    assert "Categorías nuevas" in capsys.readouterr().out


def test_main_dry_run_no_escribe(tmp_path, importador_aislado, capsys):
    archivo = tmp_path / "pacientes.xlsx"
    archivo.write_bytes(_planilla())

    assert importador.main([str(archivo), "--dry-run"]) == 0

    assert importador_aislado.query(Paciente).count() == 0
    assert importador_aislado.query(ObraSocial).count() == 0
    salida = capsys.readouterr().out
    assert "No se escribió nada" in salida


def test_main_con_limite_procesa_solo_las_primeras_filas(
    tmp_path, importador_aislado, capsys
):
    archivo = tmp_path / "pacientes.xlsx"
    archivo.write_bytes(_planilla())

    assert importador.main([str(archivo), "--limite", "1"]) == 0

    assert importador_aislado.query(Paciente).count() == 1
    assert "Se omitieron 1 filas" in capsys.readouterr().out


def test_main_con_archivo_inexistente_devuelve_error(
    tmp_path, importador_aislado, capsys
):
    assert importador.main([str(tmp_path / "no-existe.xlsx")]) == 1

    assert "No se pudo leer la planilla" in capsys.readouterr().out

