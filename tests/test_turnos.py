"""Pruebas de creación y validación de turnos."""

from datetime import timedelta

from app.core.datetime_utils import utcnow

API = "/api/v1"


def _payload(doctor_id: int, paciente_id: int, *, dias: int = 5, hora: int = 9,
             minuto: int = 0, duracion_min: int = 30) -> dict:
    inicio = (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=minuto, second=0, microsecond=0
    )
    fin = inicio + timedelta(minutes=duracion_min)
    return {
        "paciente_id": paciente_id,
        "doctor_id": doctor_id,
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": fin.isoformat(),
    }


def test_crear_turno_ok(client, doctor, paciente):
    r = client.post(f"{API}/turnos", json=_payload(doctor.id, paciente.id))
    assert r.status_code == 201, r.text

    data = r.json()
    assert data["estado_turno"] == "PENDIENTE"
    assert data["estado_whatsapp"] == "PENDIENTE_ENVIO"
    assert data["whatsapp_mensaje_id"] is None


def test_turno_solapado_exacto_devuelve_409(client, doctor, paciente):
    payload = _payload(doctor.id, paciente.id)
    assert client.post(f"{API}/turnos", json=payload).status_code == 201

    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 409
    assert "solapa" in r.json()["detail"].lower()


def test_turno_solapado_parcial_devuelve_409(client, doctor, paciente):
    primero = _payload(doctor.id, paciente.id, hora=9, minuto=0, duracion_min=30)
    assert client.post(f"{API}/turnos", json=primero).status_code == 201

    # 09:15 - 09:45 se solapa con 09:00 - 09:30
    solapado = _payload(doctor.id, paciente.id, hora=9, minuto=15, duracion_min=30)
    r = client.post(f"{API}/turnos", json=solapado)
    assert r.status_code == 409


def test_turno_contiguo_no_solapa(client, doctor, paciente):
    primero = _payload(doctor.id, paciente.id, hora=9, minuto=0, duracion_min=30)
    assert client.post(f"{API}/turnos", json=primero).status_code == 201

    # 09:30 - 10:00 empieza justo cuando termina el anterior -> permitido
    contiguo = _payload(doctor.id, paciente.id, hora=9, minuto=30, duracion_min=30)
    assert client.post(f"{API}/turnos", json=contiguo).status_code == 201


def test_turno_fin_menor_o_igual_inicio_devuelve_422(client, doctor, paciente):
    inicio = utcnow() + timedelta(days=5)
    payload = {
        "paciente_id": paciente.id,
        "doctor_id": doctor.id,
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": inicio.isoformat(),
    }
    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 422


def test_doctor_inexistente_devuelve_404(client, paciente):
    r = client.post(f"{API}/turnos", json=_payload(9999, paciente.id))
    assert r.status_code == 404


def test_paciente_inexistente_devuelve_404(client, doctor):
    r = client.post(f"{API}/turnos", json=_payload(doctor.id, 9999))
    assert r.status_code == 404


def test_turno_cancelado_no_bloquea_solapamiento_parcial(client, doctor, paciente, db_session):
    from app.models.enums import EstadoTurno
    from app.models.turno import Turno

    primero = _payload(doctor.id, paciente.id, hora=9, minuto=0, duracion_min=30)
    assert client.post(f"{API}/turnos", json=primero).status_code == 201

    # Cancelamos el primer turno de forma directa.
    turno = db_session.query(Turno).one()
    turno.estado_turno = EstadoTurno.CANCELADO
    db_session.commit()

    # Un turno que se solapa por rango (inicio distinto) debe permitirse, ya que
    # la validación de negocio ignora los turnos CANCELADOS. El inicio EXACTO
    # también queda libre porque ``uq_turno_doctor_fecha_inicio`` es un índice
    # único *parcial* que excluye los CANCELADOS (véase
    # tests/test_turnos_actualizacion.py::test_cancelar_turno_libera_el_horario).
    solapado = _payload(doctor.id, paciente.id, hora=9, minuto=15, duracion_min=30)
    assert client.post(f"{API}/turnos", json=solapado).status_code == 201



def test_obtener_turno_inexistente_devuelve_404(client):
    r = client.get(f"{API}/turnos/123456")
    assert r.status_code == 404
