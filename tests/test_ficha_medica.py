"""Pruebas de la ficha médica del paciente (Módulo 4).

Cubren la hoja de papel que el consultorio completa después de revisar al
paciente:

1. **Lectura**: un paciente sin ficha devuelve ``id = null``, sus datos
   filiatorios y el libro vacío, sin crear la historia clínica.
2. **Guardado**: cabecera (vencimiento, grupo familiar, parentesco y
   antecedentes), estado bucal (sarro y periodontal) y libro «Trabajo
   realizado» con el saldo acumulado por renglón.
3. **Reemplazo**: cada ``PUT`` sustituye el libro completo, descarta los
   renglones en blanco y no duplica la ficha.
4. **Validación**: paciente inexistente (404), importes negativos o textos
   demasiado largos (422).
"""

from datetime import date

from sqlalchemy import func, select

from app.models.ficha_medica import (
    MAX_RENGLONES_TRABAJO,
    FichaMedica,
    FichaMedicaTrabajo,
)
from app.models.historia_clinica import HistoriaClinica
from app.models.prestacion import Prestacion

API = "/api/v1"


def _url(paciente_id: int) -> str:
    return f"{API}/pacientes/{paciente_id}/ficha-medica"


def _payload() -> dict:
    return {
        "vencimiento": "2026-08-31",
        "grupo_familiar": "Familia Moreno",
        "parentesco": "Titular",
        "antecedentes_enfermedad": "Hipertensión controlada. Alergia a penicilina.",
        "presencia_sarro": True,
        "enfermedad_periodontal": False,
        "trabajos": [
            {
                "fecha": "2026-08-01",
                "codigo": "01.01",
                "pieza": "18",
                "trabajo": "Obturación composite",
                "debe": 10000,
                "haber": 0,
            },
            {
                "fecha": "2026-08-10",
                "codigo": "02.03",
                "pieza": "26, 27",
                "trabajo": "Endodoncia",
                "debe": 5000,
                "haber": 2000,
            },
            {
                "fecha": "2026-08-20",
                "codigo": None,
                "pieza": None,
                "trabajo": "Pago a cuenta",
                "debe": None,
                "haber": 3000,
            },
        ],
    }


def test_get_sin_ficha_no_crea_historia(client, db_session, paciente):
    """La lectura de un paciente sin ficha no escribe en la base."""
    respuesta = client.get(_url(paciente.id))

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] is None
    assert datos["historia_clinica_id"] is None
    assert datos["numero_ficha"] == f"HC-{paciente.id:06d}"
    assert datos["trabajos"] == []
    assert datos["total_debe"] == 0
    assert datos["total_haber"] == 0
    assert datos["saldo"] == 0
    assert datos["paciente"]["id"] == paciente.id
    assert datos["paciente"]["nombre"] == paciente.nombre
    assert db_session.execute(
        select(func.count()).select_from(HistoriaClinica)
    ).scalar_one() == 0
    assert db_session.execute(
        select(func.count()).select_from(FichaMedica)
    ).scalar_one() == 0


def test_guarda_y_devuelve_la_ficha(client, db_session, paciente):
    """La ficha guarda cabecera, estado bucal y el saldo de cada renglón."""
    paciente.dni = "30123456"
    paciente.fecha_nacimiento = date(1990, 5, 10)
    paciente.domicilio = "San Martín 123, Córdoba"
    paciente.titular = "Moreno, Benjamín"
    db_session.commit()

    respuesta = client.put(_url(paciente.id), json=_payload())

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] is not None
    assert datos["historia_clinica_id"] is not None
    assert datos["vencimiento"] == "2026-08-31"
    assert datos["grupo_familiar"] == "Familia Moreno"
    assert datos["parentesco"] == "Titular"
    assert datos["antecedentes_enfermedad"].startswith("Hipertensión")
    assert datos["presencia_sarro"] is True
    assert datos["enfermedad_periodontal"] is False
    assert datos["actualizado_en"]
    assert datos["paciente"]["dni"] == "30123456"
    assert datos["paciente"]["domicilio"] == "San Martín 123, Córdoba"
    assert datos["paciente"]["fecha_nacimiento"] == "1990-05-10"
    assert datos["paciente"]["edad"] is not None

    assert [fila["trabajo"] for fila in datos["trabajos"]] == [
        "Obturación composite",
        "Endodoncia",
        "Pago a cuenta",
    ]
    assert [fila["saldo"] for fila in datos["trabajos"]] == [10000, 13000, 10000]
    assert datos["total_debe"] == 15000
    assert datos["total_haber"] == 5000
    assert datos["saldo"] == 10000

    leido = client.get(_url(paciente.id)).json()
    assert leido["trabajos"] == datos["trabajos"]
    assert leido["grupo_familiar"] == "Familia Moreno"


def test_crea_la_historia_clinica_si_no_existe(client, db_session, paciente):
    """Guardar la ficha abre la historia clínica (HC-0000xx)."""
    assert client.put(_url(paciente.id), json=_payload()).status_code == 200

    historia = db_session.execute(select(HistoriaClinica)).scalars().one()
    assert historia.paciente_id == paciente.id
    assert historia.numero_ficha == f"HC-{paciente.id:06d}"
    ficha = db_session.execute(select(FichaMedica)).scalars().one()
    assert ficha.historia_clinica_id == historia.id
    assert ficha.presencia_sarro is True
    assert ficha.enfermedad_periodontal is False


def test_reemplaza_el_libro_y_descarta_renglones_en_blanco(
    client, db_session, paciente
):
    """Un segundo ``PUT`` sustituye el libro y no guarda filas vacías."""
    creado = client.put(_url(paciente.id), json=_payload()).json()

    respuesta = client.put(
        _url(paciente.id),
        json={
            "vencimiento": None,
            "grupo_familiar": "Otro grupo",
            "presencia_sarro": None,
            "enfermedad_periodontal": None,
            "trabajos": [
                {
                    "fecha": None,
                    "codigo": None,
                    "pieza": None,
                    "trabajo": None,
                    "debe": None,
                    "haber": None,
                },
                {
                    "fecha": "2026-09-01",
                    "codigo": "04.01",
                    "pieza": "11",
                    "trabajo": "Control",
                    "debe": 2500,
                    "haber": 500,
                },
            ],
        },
    )

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] == creado["id"]
    assert datos["vencimiento"] is None
    assert datos["grupo_familiar"] == "Otro grupo"
    assert datos["presencia_sarro"] is None
    assert datos["enfermedad_periodontal"] is None
    assert len(datos["trabajos"]) == 1
    assert datos["trabajos"][0]["trabajo"] == "Control"
    assert datos["trabajos"][0]["saldo"] == 2000
    assert db_session.execute(
        select(func.count()).select_from(FichaMedica)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(FichaMedicaTrabajo)
    ).scalar_one() == 1


def test_rechaza_paciente_inexistente(client):
    """Un paciente que no existe responde 404 (lectura y guardado)."""
    assert client.get(_url(9999)).status_code == 404
    assert client.put(_url(9999), json={}).status_code == 404


def test_rechaza_importes_negativos(client, paciente):
    """Debe y Haber son importes en pesos: nunca negativos."""
    respuesta = client.put(
        _url(paciente.id),
        json={"trabajos": [{"trabajo": "Prueba", "debe": -1}]},
    )

    assert respuesta.status_code == 422


def test_rechaza_haber_negativo(client, paciente):
    """El Haber también es un importe positivo (el signo lo pone el saldo)."""
    respuesta = client.put(
        _url(paciente.id),
        json={"trabajos": [{"trabajo": "Pago", "haber": -0.01}]},
    )

    assert respuesta.status_code == 422


def test_rechaza_codigo_o_pieza_demasiado_largos(client, paciente):
    """El código y la pieza comparten el tope de 30 caracteres de la tabla."""
    assert (
        client.put(
            _url(paciente.id),
            json={"trabajos": [{"codigo": "x" * 31}]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            _url(paciente.id),
            json={"trabajos": [{"pieza": "x" * 31}]},
        ).status_code
        == 422
    )


def test_rechaza_mas_de_cien_renglones(client, paciente):
    """El libro tiene un tope para no aceptar un payload desmedido."""
    respuesta = client.put(
        _url(paciente.id),
        json={
            "trabajos": [
                {"trabajo": f"Práctica {numero}"}
                for numero in range(MAX_RENGLONES_TRABAJO + 1)
            ]
        },
    )

    assert respuesta.status_code == 422


def test_guarda_el_codigo_del_catalogo_de_prestaciones(
    client, db_session, paciente
):
    """Un código del catálogo se conserva tal cual en el libro."""
    db_session.add(
        Prestacion(
            codigo_nomenclador="01.01",
            descripcion="Obturación composite",
            valor_base=10000,
            activa=True,
        )
    )
    db_session.commit()

    respuesta = client.put(_url(paciente.id), json=_payload())

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["trabajos"][0]["codigo"] == "01.01"
    assert datos["trabajos"][0]["trabajo"] == "Obturación composite"
    guardado = db_session.execute(
        select(FichaMedicaTrabajo).order_by(FichaMedicaTrabajo.orden)
    ).scalars().first()
    assert guardado is not None
    assert guardado.codigo == "01.01"


def test_el_libro_toma_el_codigo_del_catalogo_en_el_panel(client):
    """Regresión de la hoja web: el código se elige del nomenclador."""
    js = client.get("/static/odontograma.js").text
    for fragmento in (
        'API_PRESTACIONES = "/api/v1/prestaciones?activas=true"',
        "function cargarPrestaciones()",
        'id="fm-prestaciones"',
        'setAttribute("list", "fm-prestaciones")',
        "function validarFicha()",
        "data-codigo-original",
        "no está en el catálogo de prestaciones",
        'setAttribute("aria-invalid", "true")',
    ):
        assert fragmento in js

    css = client.get("/static/odontograma.css").text
    assert ".odo-codigo-desconocido" in css


def test_rechaza_textos_demasiado_largos(client, paciente):
    """El detalle del trabajo y los textos de la cabecera tienen tope."""
    respuesta = client.put(
        _url(paciente.id),
        json={"trabajos": [{"trabajo": "x" * 501}]},
    )

    assert respuesta.status_code == 422

    respuesta = client.put(
        _url(paciente.id),
        json={"grupo_familiar": "x" * 121},
    )

    assert respuesta.status_code == 422


def test_acepta_fecha_vacia_en_el_libro(client, paciente):
    """El ``""`` del formulario web equivale a una fecha sin informar."""
    respuesta = client.put(
        _url(paciente.id),
        json={
            "vencimiento": "",
            "trabajos": [{"fecha": "", "trabajo": "Consulta", "debe": 0}],
        },
    )

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["vencimiento"] is None
    assert datos["trabajos"][0]["fecha"] is None
