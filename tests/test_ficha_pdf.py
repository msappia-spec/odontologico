"""Pruebas del PDF de la Historia Clínica General (Módulo 4).

La ficha médica se imprime sobre la **misma plantilla de papel** del
consultorio (``app/resources/historia_clinica_general.pdf``). Las pruebas
verifican tres cosas:

1. **La plantilla sigue teniendo todas las etiquetas** que el relleno busca. Si
   se reemplaza el PDF por otra versión con un texto distinto, esta prueba falla
   antes de que el formulario salga incompleto.
2. **El PDF sale completo**: las dos páginas del formulario con el paciente, sus
   filiatorios y lo declarado, las casillas SI/NO marcadas y el odontograma y el
   detalle del examen bucal en el anexo.
3. **La API** devuelve ``application/pdf`` para imprimir, responde 404 con un
   paciente o un profesional inexistente y no exige que la ficha esté cargada.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pymupdf

from app.core.datetime_utils import utcnow
from app.models.turno import Turno
from app.services import ficha_pdf_service

API = "/api/v1"


def _crear_turno(db_session, doctor, paciente) -> Turno:
    """Turno futuro del paciente (la ficha se carga por turno)."""
    inicio = (utcnow() + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=inicio,
        fecha_hora_fin=inicio + timedelta(minutes=30),
    )
    db_session.add(turno)
    db_session.commit()
    db_session.refresh(turno)
    return turno


def _filiatorios(client, paciente) -> dict:
    """Nombre, documento y cobertura del paciente de la prueba."""
    respuesta = client.patch(
        f"{API}/pacientes/{paciente.id}",
        json={
            "nombre": "Muñoz, María José",
            "dni": "30123456",
            "obra_social": "OSDE 210",
            "numero_afiliado": "61002345",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _payload_ficha() -> dict:
    """Declaración con filiatorios, antecedentes y examen del profesional."""
    return {
        # Filiatorios (se sincronizan en el paciente)
        "fecha_nacimiento": "1985-03-12",
        "estado_civil": "Casada",
        "nacionalidad": "Argentina",
        "domicilio": "Av. Colón 1234, 5º B, Nueva Córdoba",
        "profesion": "Docente",
        "titular": "Muñoz, María José",
        "lugar_trabajo": "Escuela Normal",
        "jerarquia": "Titular",
        # Antecedentes
        "padre_vive": True,
        "padre_patologias": "Hipertensión",
        "madre_vive": False,
        "hermanos": True,
        "hermanos_sanos": True,
        "alergias_medicamentos": "Penicilina",
        "alergia_anestesia": True,
        "sangra_mucho": True,
        "hepatitis": True,
        "hepatitis_tipo": "B",
        "dolor": True,
        "dolor_intensidad": "INTENSO",
        "dolor_localizacion": "IRRADIADO",
        "dolor_zona": "Hacia el oído",
        "higiene_bucal": "BUENO",
        "sarro": True,
        # Examen bucal y diagnóstico (los completa el profesional)
        "lengua_anormal": True,
        "lesiones_manchas": True,
        "sangrado_encias": True,
        "sangrado_encias_cuando": "Al cepillarse",
        "cara_hinchada": True,
        "cara_hinchada_otros": "Enjuagues",
        "examen_bucal_detalle": "Lesión blanquecina en carrillo derecho de 5 mm.",
        "diagnostico_presuntivo": "Caries oclusal en 16. Gingivitis marginal.",
        "plan_tratamiento": "Obturación 16. Destartraje y pulido.",
        "observaciones": "Control en 6 meses.",
    }


def _texto(documento: pymupdf.Document) -> str:
    return "\n".join(pagina.get_text() for pagina in documento)


# --------------------------------------------------------------------------- #
# 1) La plantilla
# --------------------------------------------------------------------------- #
def test_la_plantilla_existe_y_conserva_todas_las_etiquetas():
    assert ficha_pdf_service.RUTA_PLANTILLA.is_file()


# --------------------------------------------------------------------------- #
# 2) Relleno del formulario
# --------------------------------------------------------------------------- #
def test_el_pdf_rellena_los_datos_de_la_ficha_y_el_anexo(
    client, db_session, doctor, paciente
):
    _filiatorios(client, paciente)
    turno = _crear_turno(db_session, doctor, paciente)

    respuesta = client.put(
        f"{API}/anamnesis/panel/{turno.id}", json=_payload_ficha()
    )
    assert respuesta.status_code == 200, respuesta.text

    respuesta = client.put(
        f"{API}/pacientes/{paciente.id}/odontograma",
        json={
            "protesis_fija": "Puente 24-26",
            "coronas": "Corona 11",
            "dientes_existentes": 28,
            "dientes": [
                {"diente": 11, "estado": "EXISTENTE"},
                {"diente": 26, "estado": "REQUERIDO"},
                {"diente": 47, "estado": "AUSENTE"},
            ],
        },
    )
    assert respuesta.status_code == 200, respuesta.text

    pdf = client.get(f"{API}/pacientes/{paciente.id}/historia-clinica/pdf")

    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline;")
    assert "historia-clinica" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF")

    documento = pymupdf.open("pdf", pdf.content)
    assert documento.page_count == 3

    formulario = _texto(documento)
    assert "HISTORIA CLINICA GENERAL" in formulario
    assert "Muñoz, María José" in formulario
    assert "OSDE 210" in formulario
    assert "61002345" in formulario
    assert "Casada" in formulario
    assert "Argentina" in formulario
    assert "Docente" in formulario
    assert "Escuela Normal" in formulario
    assert "12/03/1985" in formulario
    assert "Hipertensión" in formulario
    assert "Penicilina" in formulario
    assert "Al cepillarse" in formulario
    assert "Hacia el oído" in formulario
    assert "Caries oclusal en 16" in formulario
    assert "Control en 6 meses" in formulario

    anexo = documento[2].get_text()
    assert "ANEXO" in anexo
    assert "Lesión blanquecina" in anexo
    assert "Alergias declaradas" in anexo
    assert "Odontograma" in anexo
    assert "Puente 24-26" in anexo
    assert "Cantidad de dientes existentes: 28" in anexo
    for diente in ("11", "26", "47", "18", "38"):
        assert diente in anexo


def test_el_pdf_marca_las_casillas_si_no(client, db_session, doctor, paciente):
    _filiatorios(client, paciente)
    turno = _crear_turno(db_session, doctor, paciente)
    client.put(f"{API}/anamnesis/panel/{turno.id}", json=_payload_ficha())

    pdf = client.get(f"{API}/pacientes/{paciente.id}/historia-clinica/pdf")
    documento = pymupdf.open("pdf", pdf.content)

    # La plantilla trae 34 y 11 trazos vectoriales; cada casilla marcada suma dos.
    plantilla = pymupdf.open(str(ficha_pdf_service.RUTA_PLANTILLA))
    assert len(documento[0].get_drawings()) > len(plantilla[0].get_drawings())
    assert len(documento[1].get_drawings()) > len(plantilla[1].get_drawings())


def test_un_texto_largo_continua_en_el_anexo():
    paciente = SimpleNamespace(nombre="Pérez, Juan", dni="11222333")
    ficha = SimpleNamespace(
        diagnostico_presuntivo="Caries múltiples. " * 40,
        sarro=False,
        enfermedad_periodontal=False,
    )
    contenido = ficha_pdf_service.construir_pdf(paciente, ficha)
    documento = pymupdf.open("pdf", contenido)

    assert documento.page_count == 3
    assert "Diagnóstico presuntivo (continuación)" in documento[2].get_text()


# --------------------------------------------------------------------------- #
# 3) Paciente sin ficha y errores
# --------------------------------------------------------------------------- #
def test_el_pdf_de_un_paciente_sin_ficha_sale_en_blanco(client):
    creado = client.post(
        f"{API}/pacientes",
        json={"nombre": "Gómez, Ana", "telefono": "+5493515550000"},
    ).json()
    pdf = client.get(f"{API}/pacientes/{creado['id']}/historia-clinica/pdf")

    assert pdf.status_code == 200
    documento = pymupdf.open("pdf", pdf.content)
    assert documento.page_count == 2
    assert "Gómez, Ana" in documento[0].get_text()


def test_el_pdf_responde_404_si_el_paciente_no_existe(client):
    pdf = client.get(f"{API}/pacientes/9999/historia-clinica/pdf")
    assert pdf.status_code == 404


def test_el_pdf_responde_404_si_el_profesional_no_existe(client, paciente):
    pdf = client.get(
        f"{API}/pacientes/{paciente.id}/historia-clinica/pdf",
        params={"doctor_id": 9999},
    )
    assert pdf.status_code == 404


def test_el_pdf_usa_el_profesional_elegido(client, doctor, paciente):
    pdf = client.get(
        f"{API}/pacientes/{paciente.id}/historia-clinica/pdf",
        params={"doctor_id": doctor.id},
    )

    assert pdf.status_code == 200
    documento = pymupdf.open("pdf", pdf.content)
    assert "Dr. House" in documento[0].get_text()


def test_el_encabezado_completa_lugar_fecha_y_cobertura():
    paciente = SimpleNamespace(
        nombre="López, Ana",
        dni="27888999",
        domicilio="San Martín 45",
        fecha_nacimiento=date(1990, 7, 8),
        obra_social=SimpleNamespace(nombre="Swiss Medical"),
        numero_afiliado="9001",
    )
    contenido = ficha_pdf_service.construir_pdf(paciente, fecha=date(2026, 10, 26))
    texto = pymupdf.open("pdf", contenido)[0].get_text()

    assert "26/10/2026" in texto
    assert "Swiss Medical" in texto
    assert "9001" in texto
    assert "López, Ana" in texto

    assert ficha_pdf_service.etiquetas_faltantes() == []
