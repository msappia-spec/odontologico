"""Pruebas de la regla de "paciente ubicuo" en turnos.

Un paciente no puede estar en dos agendas a la misma hora: si ya tiene un turno
vigente (no ``CANCELADO``) que se solapa con el rango pedido en la agenda de
**otro** odontólogo, el backend responde HTTP 400 con un mensaje explícito y el
detalle del turno en conflicto (``turno_en_conflicto``).

El solapamiento contra el **mismo** odontólogo se sigue reportando como HTTP 409
(diagnóstico de agenda del profesional); ambas validaciones conviven.
"""

from datetime import timedelta

from app.core.datetime_utils import utcnow
from app.models.doctor import Doctor
from app.models.paciente import Paciente

API = "/api/v1"

MENSAJE_DUPLICADO = (
    "El paciente ya tiene un turno asignado con otro odontólogo en este horario"
)


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


def _crear_doctor(db_session, nombre: str = "Dra. Ortodoncia") -> Doctor:
    doctor = Doctor(nombre=nombre, especialidad="Ortodoncia", activo=True)
    db_session.add(doctor)
    db_session.commit()
    db_session.refresh(doctor)
    return doctor


def _crear_paciente(db_session, nombre: str = "Paciente Dos") -> Paciente:
    paciente = Paciente(nombre=nombre, telefono="+5491199998888")
    db_session.add(paciente)
    db_session.commit()
    db_session.refresh(paciente)
    return paciente


def test_mismo_paciente_otro_doctor_en_el_mismo_horario_devuelve_400(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    primero = _crear(client, doctor.id, paciente.id, hora=9)

    # Mismo paciente, misma hora, otra agenda -> rechazado con 400.
    r = client.post(
        f"{API}/turnos",
        json={"paciente_id": paciente.id, "doctor_id": otro.id, **_rango(hora=9)},
    )

    assert r.status_code == 400, r.text
    data = r.json()
    assert data["detail"] == MENSAJE_DUPLICADO
    # El cuerpo informa dónde está el paciente para que la secretaría lo vea.
    assert data["turno_en_conflicto"]["id"] == primero["id"]
    assert data["turno_en_conflicto"]["doctor_id"] == doctor.id


def test_mismo_paciente_otro_doctor_con_solape_parcial_devuelve_400(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    _crear(client, doctor.id, paciente.id, hora=9, minuto=0, duracion_min=30)

    # 09:15 - 09:45 se cruza con 09:00 - 09:30 en otra agenda.
    r = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": paciente.id,
            "doctor_id": otro.id,
            **_rango(hora=9, minuto=15),
        },
    )

    assert r.status_code == 400, r.text
    assert r.json()["detail"] == MENSAJE_DUPLICADO


def test_mismo_paciente_otro_doctor_contiguo_es_valido(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    _crear(client, doctor.id, paciente.id, hora=9, minuto=0, duracion_min=30)

    # 09:30 - 10:00 empieza justo cuando termina el anterior -> permitido.
    r = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": paciente.id,
            "doctor_id": otro.id,
            **_rango(hora=9, minuto=30),
        },
    )

    assert r.status_code == 201, r.text


def test_otro_paciente_en_la_misma_hora_no_es_conflicto(
    client, doctor, paciente, db_session
):
    otro_doctor = _crear_doctor(db_session)
    otro_paciente = _crear_paciente(db_session)
    _crear(client, doctor.id, paciente.id, hora=9)

    # La ubicuidad es por paciente: otro paciente puede estar a la misma hora.
    r = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": otro_paciente.id,
            "doctor_id": otro_doctor.id,
            **_rango(hora=9),
        },
    )

    assert r.status_code == 201, r.text


def test_mismo_doctor_mismo_horario_sigue_devolviendo_409(
    client, doctor, paciente
):
    _crear(client, doctor.id, paciente.id, hora=9)

    # El conflicto con el propio odontólogo mantiene el diagnóstico 409.
    r = client.post(
        f"{API}/turnos",
        json={"paciente_id": paciente.id, "doctor_id": doctor.id, **_rango(hora=9)},
    )

    assert r.status_code == 409, r.text
    assert "solapa" in r.json()["detail"].lower()


def test_agenda_del_doctor_ocupada_por_otro_paciente_devuelve_409(
    client, doctor, paciente, db_session
):
    otro_paciente = _crear_paciente(db_session)
    _crear(client, doctor.id, paciente.id, hora=9)

    r = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": otro_paciente.id,
            "doctor_id": doctor.id,
            **_rango(hora=9),
        },
    )

    assert r.status_code == 409, r.text


def test_turno_cancelado_libera_al_paciente_en_otra_agenda(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    cancelado = client.patch(
        f"{API}/turnos/{turno['id']}", json={"estado_turno": "CANCELADO"}
    )
    assert cancelado.status_code == 200, cancelado.text

    # El turno cancelado ya no cuenta: el paciente puede agendar en otra agenda.
    r = client.post(
        f"{API}/turnos",
        json={"paciente_id": paciente.id, "doctor_id": otro.id, **_rango(hora=9)},
    )

    assert r.status_code == 201, r.text


def test_reprogramar_sobre_la_agenda_de_otro_doctor_devuelve_400(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    _crear(client, doctor.id, paciente.id, hora=9)
    segundo = _crear(client, otro.id, paciente.id, hora=11)

    # El doctor destino está libre: el rechazo viene por la agenda del paciente.
    r = client.patch(f"{API}/turnos/{segundo['id']}", json=_rango(hora=9, minuto=15))

    assert r.status_code == 400, r.text
    assert r.json()["detail"] == MENSAJE_DUPLICADO


def test_reactivar_turno_con_paciente_citado_en_otra_agenda_devuelve_400(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    turno = _crear(client, doctor.id, paciente.id, hora=9)
    cancelar = client.patch(
        f"{API}/turnos/{turno['id']}", json={"estado_turno": "CANCELADO"}
    )
    assert cancelar.status_code == 200, cancelar.text

    # Mientras estaba cancelado, el paciente agendó en la otra agenda.
    _crear(client, otro.id, paciente.id, hora=9)

    r = client.patch(f"{API}/turnos/{turno['id']}", json={"estado_turno": "PENDIENTE"})

    assert r.status_code == 400, r.text
    assert r.json()["detail"] == MENSAJE_DUPLICADO


def test_cancelar_un_turno_solapado_siempre_se_permite(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    _crear(client, doctor.id, paciente.id, hora=9)
    segundo = _crear(client, otro.id, paciente.id, hora=11)

    # Aunque el nuevo rango se solape, cancelar no valida agenda ni ubicuidad.
    r = client.patch(
        f"{API}/turnos/{segundo['id']}",
        json={"estado_turno": "CANCELADO", **_rango(hora=9, minuto=15)},
    )

    assert r.status_code == 200, r.text
    assert r.json()["estado_turno"] == "CANCELADO"


def test_mover_turno_a_otro_horario_del_mismo_paciente(
    client, doctor, paciente, db_session
):
    otro = _crear_doctor(db_session)
    _crear(client, doctor.id, paciente.id, hora=9)
    segundo = _crear(client, otro.id, paciente.id, hora=11)

    # 09:30 es contiguo al turno de las 09:00 -> permitido (no se solapa).
    contiguo = client.patch(
        f"{API}/turnos/{segundo['id']}", json=_rango(hora=9, minuto=30)
    )
    assert contiguo.status_code == 200, contiguo.text

    # 09:15 sí se cruza con 09:00 - 09:30 de la otra agenda -> 400.
    r = client.patch(f"{API}/turnos/{segundo['id']}", json=_rango(hora=9, minuto=15))
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == MENSAJE_DUPLICADO
