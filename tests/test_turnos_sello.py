"""Pruebas del sello de cambios de la agenda (``GET /api/v1/turnos/sello``).

El panel de la secretaría y la agenda del odontólogo consultan este endpoint
cada pocos segundos. Cuando el sello cambia —por ejemplo porque el paciente
respondió «1/SI» o «2/NO» al recordatorio y el webhook dejó el turno
``CONFIRMADO`` o ``CANCELADO``— las pantallas abiertas releen los turnos sin que
nadie recargue la página (F5).
"""

from datetime import timedelta

from app.core.datetime_utils import utcnow
from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.turno import Turno

API = "/api/v1"
TELEFONO = "5491122334455"


def _rango(*, dias: int = 5, hora: int = 9, minuto: int = 0, duracion_min: int = 30) -> dict:
    """Devuelve un rango horario válido expresado en ISO-8601."""
    inicio = (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=minuto, second=0, microsecond=0
    )
    fin = inicio + timedelta(minutes=duracion_min)
    return {
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": fin.isoformat(),
    }


def _crear(client, doctor_id: int, paciente_id: int, **kwargs) -> dict:
    """Crea un turno por API y devuelve su representación."""
    payload = {"paciente_id": paciente_id, "doctor_id": doctor_id, **_rango(**kwargs)}
    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _sello(client) -> str:
    """Consulta el sello vigente de la agenda."""
    r = client.get(f"{API}/turnos/sello")
    assert r.status_code == 200, r.text
    return r.json()["sello"]


def _webhook(texto: str) -> dict:
    """Mensaje entrante de WAHA, igual al que manda el paciente por WhatsApp."""
    return {
        "event": "message",
        "session": "default",
        "payload": {
            "id": f"waha.ENTRANTE.{texto}",
            "from": f"{TELEFONO}@c.us",
            "fromMe": False,
            "body": texto,
            "type": "chat",
        },
    }


def _responder(client, texto: str) -> dict:
    r = client.post(f"{API}/whatsapp/webhook", json=_webhook(texto))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1) Forma de la respuesta y estabilidad del sello
# ---------------------------------------------------------------------------


def test_el_sello_informa_la_cantidad_sin_devolver_los_turnos(client):
    r = client.get(f"{API}/turnos/sello")

    assert r.status_code == 200, r.text
    datos = r.json()
    assert set(datos) == {"sello", "cantidad"}
    assert datos["cantidad"] == 0
    assert len(datos["sello"]) == 40  # sha1 hexadecimal


def test_el_sello_no_lo_captura_el_detalle_por_id(client):
    """``/turnos/sello`` no puede resolverse como ``/turnos/{turno_id}`` (422)."""
    r = client.get(f"{API}/turnos/sello")
    assert r.status_code == 200, r.text

    # La ruta de detalle sigue funcionando con un id numérico inexistente.
    assert client.get(f"{API}/turnos/999999").status_code == 404


def test_el_sello_no_cambia_si_nadie_toca_la_agenda(client, doctor, paciente):
    _crear(client, doctor.id, paciente.id)

    assert _sello(client) == _sello(client)


# ---------------------------------------------------------------------------
# 2) Cambios hechos desde la API
# ---------------------------------------------------------------------------


def test_el_sello_cambia_al_crear_un_turno(client, doctor, paciente):
    antes = _sello(client)

    turno = _crear(client, doctor.id, paciente.id)

    assert _sello(client) != antes
    assert client.get(f"{API}/turnos/sello").json()["cantidad"] == 1
    assert client.get(f"{API}/turnos/{turno['id']}").status_code == 200


def test_el_sello_cambia_al_confirmar_y_al_cancelar(client, doctor, paciente):
    turno = _crear(client, doctor.id, paciente.id)

    al_crear = _sello(client)
    assert client.patch(
        f"{API}/turnos/{turno['id']}", json={"estado_turno": "CONFIRMADO"}
    ).status_code == 200
    al_confirmar = _sello(client)
    assert al_confirmar != al_crear

    assert client.patch(
        f"{API}/turnos/{turno['id']}", json={"estado_turno": "CANCELADO"}
    ).status_code == 200
    assert _sello(client) != al_confirmar


def test_el_sello_cambia_al_reprogramar(client, doctor, paciente):
    turno = _crear(client, doctor.id, paciente.id, hora=9)
    antes = _sello(client)

    r = client.patch(f"{API}/turnos/{turno['id']}", json=_rango(hora=11))

    assert r.status_code == 200, r.text
    assert _sello(client) != antes


# ---------------------------------------------------------------------------
# 3) El caso que motiva el endpoint: la respuesta del paciente por WhatsApp
# ---------------------------------------------------------------------------


def test_el_sello_cambia_cuando_el_paciente_confirma_por_whatsapp(
    client, doctor, paciente
):
    turno = _crear(client, doctor.id, paciente.id, dias=2, hora=14)
    client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")
    antes = _sello(client)

    data = _responder(client, "si")

    assert data["estado_turno"] == EstadoTurno.CONFIRMADO.value
    assert _sello(client) != antes
    # Lo que la pantalla abierta ve al releer los turnos.
    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == EstadoTurno.CONFIRMADO.value
    assert actualizado["estado_whatsapp"] == EstadoWhatsApp.RESPONDIDO.value


def test_el_sello_cambia_cuando_el_paciente_cancela_por_whatsapp(
    client, doctor, paciente
):
    turno = _crear(client, doctor.id, paciente.id, dias=2, hora=15)
    client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")
    antes = _sello(client)

    data = _responder(client, "2")

    assert data["estado_turno"] == EstadoTurno.CANCELADO.value
    assert _sello(client) != antes
    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == EstadoTurno.CANCELADO.value


def test_el_sello_detecta_un_cambio_guardado_fuera_de_la_api(
    client, db_session, doctor, paciente
):
    """El webhook escribe con el ORM: el sello tiene que verlo igual."""
    turno = _crear(client, doctor.id, paciente.id, dias=2, hora=16)
    antes = _sello(client)

    fila = db_session.get(Turno, turno["id"])
    fila.estado_turno = EstadoTurno.CONFIRMADO
    fila.estado_whatsapp = EstadoWhatsApp.RESPONDIDO
    db_session.commit()

    assert _sello(client) != antes
