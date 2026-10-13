"""Pruebas de la actualización parcial de turnos (``PATCH /api/v1/turnos/{id}``)."""

from datetime import timedelta

from app.core.datetime_utils import utcnow

API = "/api/v1"


def _rango(
    *,
    dias: int = 5,
    hora: int = 9,
    minuto: int = 0,
    duracion_min: int = 30,
) -> dict:
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


def test_reprogramar_turno_actualiza_rango_y_conserva_estado(client, doctor, paciente):
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.patch(f"{API}/turnos/{turno['id']}", json=_rango(hora=11))

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] == turno["id"]
    assert data["fecha_hora_inicio"] == _rango(hora=11)["fecha_hora_inicio"]
    assert data["fecha_hora_fin"] == _rango(hora=11)["fecha_hora_fin"]
    assert data["estado_turno"] == "PENDIENTE"


def test_cancelar_turno_libera_el_horario(client, doctor, paciente):
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.patch(f"{API}/turnos/{turno['id']}", json={"estado_turno": "CANCELADO"})
    assert r.status_code == 200, r.text
    assert r.json()["estado_turno"] == "CANCELADO"

    # El mismo horario puede volver a agendarse porque el turno cancelado no bloquea.
    repetido = {"paciente_id": paciente.id, "doctor_id": doctor.id, **_rango(hora=9)}
    assert client.post(f"{API}/turnos", json=repetido).status_code == 201


def test_confirmar_turno_actualiza_estado(client, doctor, paciente):
    turno = _crear(client, doctor.id, paciente.id, hora=16)

    r = client.patch(f"{API}/turnos/{turno['id']}", json={"estado_turno": "CONFIRMADO"})

    assert r.status_code == 200, r.text
    assert r.json()["estado_turno"] == "CONFIRMADO"


def test_reprogramar_sobre_turno_solapado_devuelve_409(client, doctor, paciente):
    _crear(client, doctor.id, paciente.id, hora=9)
    segundo = _crear(client, doctor.id, paciente.id, hora=11)

    # 09:15 - 09:45 se solapa parcialmente con el turno de las 09:00.
    r = client.patch(f"{API}/turnos/{segundo['id']}", json=_rango(hora=9, minuto=15))

    assert r.status_code == 409
    assert "solapa" in r.json()["detail"].lower()


def test_reasignar_doctor_ok(client, doctor, paciente, db_session):
    from app.models.doctor import Doctor

    otro = Doctor(nombre="Dra. Ortodoncia", especialidad="Ortodoncia", activo=True)
    db_session.add(otro)
    db_session.commit()
    db_session.refresh(otro)

    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.patch(f"{API}/turnos/{turno['id']}", json={"doctor_id": otro.id})

    assert r.status_code == 200, r.text
    assert r.json()["doctor_id"] == otro.id


def test_reactivar_turno_cancelado_con_solape_devuelve_409(client, doctor, paciente):
    turno = _crear(client, doctor.id, paciente.id, hora=9)
    cancelado = client.patch(
        f"{API}/turnos/{turno['id']}", json={"estado_turno": "CANCELADO"}
    )
    assert cancelado.status_code == 200

    # Otro turno ocupa parcialmente el rango del turno cancelado.
    _crear(client, doctor.id, paciente.id, hora=9, minuto=15)

    r = client.patch(f"{API}/turnos/{turno['id']}", json={"estado_turno": "PENDIENTE"})

    assert r.status_code == 409


def test_actualizar_solo_inicio_invertido_devuelve_422(client, doctor, paciente):
    """El servicio revalida el rango cuando el payload envía una sola fecha."""
    turno = _crear(client, doctor.id, paciente.id, dias=5, hora=9, duracion_min=30)

    # El fin vigente (09:30) quedaría ANTES del nuevo inicio (10:00).
    nuevo_inicio = (utcnow() + timedelta(days=5)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )

    r = client.patch(
        f"{API}/turnos/{turno['id']}",
        json={"fecha_hora_inicio": nuevo_inicio.isoformat()},
    )

    assert r.status_code == 422
    assert "posterior" in r.json()["detail"]


def test_actualizar_turno_inexistente_devuelve_404(client):
    r = client.patch(f"{API}/turnos/987654", json={"estado_turno": "CONFIRMADO"})
    assert r.status_code == 404


def test_actualizar_con_doctor_inexistente_devuelve_404(client, doctor, paciente):
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.patch(f"{API}/turnos/{turno['id']}", json={"doctor_id": 9999})

    assert r.status_code == 404
