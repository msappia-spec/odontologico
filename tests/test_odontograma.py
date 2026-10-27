"""Pruebas del odontograma (Módulo 4: ficha odontológica gráfica).

Cubren el gráfico que el profesional completa **después** de revisar al
paciente, separado de la ficha de salud:

1. **Lectura**: un paciente sin odontograma devuelve la lista vacía y no crea la
   historia clínica (la consulta es de sólo lectura).
2. **Guardado**: crea la historia si hace falta, guarda cabecera y dientes con
   los tres estados del PDF (rojo, azul y X) y devuelve los totales.
3. **Reemplazo**: cada ``PUT`` sustituye el conjunto de dientes; no se acumulan
   filas ni se duplica el odontograma.
4. **Validación**: números FDI, estados conocidos, dientes repetidos y
   referencias inexistentes (paciente u odontólogo) responden 422/404.
"""

from sqlalchemy import func, select

from app.models.historia_clinica import HistoriaClinica
from app.models.odontograma import Odontograma, OdontogramaDiente

API = "/api/v1"


def _url(paciente_id: int) -> str:
    return f"{API}/pacientes/{paciente_id}/odontograma"


def _payload() -> dict:
    return {
        "protesis_fija": "Puente 24-26",
        "protesis_removible": None,
        "coronas": "Corona 11",
        "dientes_existentes": 28,
        "observaciones": "Control semestral",
        "dientes": [
            {"diente": 11, "estado": "EXISTENTE", "notas": "Obturación"},
            {"diente": 26, "estado": "REQUERIDO"},
            {"diente": 47, "estado": "AUSENTE"},
        ],
    }


def test_get_sin_odontograma_no_crea_historia(client, db_session, paciente):
    """La lectura de un paciente sin ficha no escribe en la base."""
    respuesta = client.get(_url(paciente.id))

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] is None
    assert datos["dientes"] == []
    assert datos["total_existentes"] == 0
    assert datos["total_requeridos"] == 0
    assert datos["total_ausentes"] == 0
    assert db_session.execute(
        select(func.count()).select_from(HistoriaClinica)
    ).scalar_one() == 0


def test_guarda_y_devuelve_el_odontograma(client, paciente):
    """El gráfico guarda cabecera, dientes y totales por color."""
    respuesta = client.put(_url(paciente.id), json=_payload())

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] is not None
    assert datos["historia_clinica_id"] is not None
    assert datos["protesis_fija"] == "Puente 24-26"
    assert datos["coronas"] == "Corona 11"
    assert datos["dientes_existentes"] == 28
    assert datos["actualizado_en"]
    assert [d["diente"] for d in datos["dientes"]] == [11, 26, 47]
    assert datos["total_existentes"] == 1
    assert datos["total_requeridos"] == 1
    assert datos["total_ausentes"] == 1

    leido = client.get(_url(paciente.id)).json()
    assert leido["dientes"] == datos["dientes"]
    assert leido["protesis_fija"] == "Puente 24-26"


def test_guardar_reemplaza_los_dientes_anteriores(client, db_session, paciente):
    """Un segundo ``PUT`` sustituye el conjunto y no duplica el odontograma."""
    creado = client.put(_url(paciente.id), json=_payload()).json()

    respuesta = client.put(
        _url(paciente.id),
        json={
            "protesis_fija": None,
            "dientes": [{"diente": 21, "estado": "REQUERIDO"}],
        },
    )

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["id"] == creado["id"]
    assert [d["diente"] for d in datos["dientes"]] == [21]
    assert datos["total_requeridos"] == 1
    assert datos["total_existentes"] == 0
    assert datos["protesis_fija"] is None
    assert db_session.execute(
        select(func.count()).select_from(Odontograma)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(OdontogramaDiente)
    ).scalar_one() == 1


def test_acepta_el_estado_en_minusculas(client, paciente):
    """La API normaliza ``existente`` a ``EXISTENTE``."""
    respuesta = client.put(
        _url(paciente.id),
        json={"dientes": [{"diente": 36, "estado": "existente"}]},
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["dientes"][0]["estado"] == "EXISTENTE"


def test_crea_la_historia_clinica_si_no_existe(client, db_session, paciente):
    """Guardar el odontograma abre la ficha del paciente (HC-0000xx)."""
    assert client.put(_url(paciente.id), json=_payload()).status_code == 200

    historia = db_session.execute(select(HistoriaClinica)).scalars().one()
    assert historia.paciente_id == paciente.id
    assert historia.numero_ficha == f"HC-{paciente.id:06d}"


def test_conserva_el_odontologo_si_no_se_informa(client, doctor, paciente):
    """El profesional que revisó queda registrado hasta que otro lo reemplace."""
    primero = client.put(
        _url(paciente.id), json={**_payload(), "doctor_id": doctor.id}
    ).json()
    assert primero["doctor_id"] == doctor.id

    segundo = client.put(
        _url(paciente.id),
        json={"dientes": [{"diente": 12, "estado": "AUSENTE"}]},
    ).json()

    assert segundo["doctor_id"] == doctor.id


def test_rechaza_diente_fuera_de_la_notacion_fdi(client, paciente):
    """Un número que no es diente permanente responde 422."""
    respuesta = client.put(
        _url(paciente.id),
        json={"dientes": [{"diente": 19, "estado": "AUSENTE"}]},
    )

    assert respuesta.status_code == 422


def test_rechaza_estado_desconocido(client, paciente):
    """El estado debe ser uno de los tres colores del PDF."""
    respuesta = client.put(
        _url(paciente.id),
        json={"dientes": [{"diente": 11, "estado": "VERDE"}]},
    )

    assert respuesta.status_code == 422


def test_rechaza_dientes_repetidos(client, paciente):
    """Un diente no puede venir dos veces en el mismo guardado."""
    respuesta = client.put(
        _url(paciente.id),
        json={
            "dientes": [
                {"diente": 11, "estado": "EXISTENTE"},
                {"diente": 11, "estado": "REQUERIDO"},
            ]
        },
    )

    assert respuesta.status_code == 422


def test_rechaza_paciente_inexistente(client):
    """Un paciente que no existe responde 404 (lectura y guardado)."""
    assert client.get(_url(9999)).status_code == 404
    assert client.put(_url(9999), json={"dientes": []}).status_code == 404


def test_rechaza_odontologo_inexistente(client, paciente):
    """El doctor_id informado tiene que existir."""
    respuesta = client.put(
        _url(paciente.id), json={"doctor_id": 4321, "dientes": []}
    )

    assert respuesta.status_code == 404


def test_valida_la_cantidad_de_dientes_existentes(client, paciente):
    """La cantidad de dientes existentes no puede superar los 32."""
    respuesta = client.put(
        _url(paciente.id), json={"dientes_existentes": 40, "dientes": []}
    )

    assert respuesta.status_code == 422
