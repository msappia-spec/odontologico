"""Pruebas de la disponibilidad semanal de los odontólogos.

Cubren el editor de la agenda semanal (``/doctores/{id}/disponibilidad``) y la
generación de bloques horarios ofrecidos para una fecha
(``/doctores/{id}/horarios-disponibles``), que es la base del panel de
*Horarios disponibles* del calendario.
"""

from datetime import date, datetime, timedelta

from app.core.datetime_utils import utcnow

API = "/api/v1"


def _proxima(dia_semana: int) -> date:
    """Fecha de hoy o posterior que cae en ``dia_semana`` (0 = domingo … 6 = sábado).

    Se usa la convención del modelo ``DisponibilidadDoctor``, idéntica a
    ``Date.getDay()`` de JavaScript.
    """
    hoy = utcnow().date()
    adelanto = (dia_semana - (hoy.weekday() + 1) % 7) % 7
    return hoy + timedelta(days=adelanto)


LUNES = _proxima(1)
DOMINGO = _proxima(0)


def _rango(dia_semana: int = 1, desde: str = "08:00", hasta: str = "12:00") -> dict:
    return {"dia_semana": dia_semana, "hora_inicio": desde, "hora_fin": hasta}


def _crear(client, doctor_id: int, **kwargs):
    return client.post(f"{API}/doctores/{doctor_id}/disponibilidad", json=_rango(**kwargs))


def _listar(client, doctor_id: int):
    r = client.get(f"{API}/doctores/{doctor_id}/disponibilidad")
    assert r.status_code == 200, r.text
    return r.json()


def _bloques(client, doctor_id: int, fecha: date, duracion: int = 30):
    r = client.get(
        f"{API}/doctores/{doctor_id}/horarios-disponibles",
        params={"fecha": fecha.isoformat(), "duracion_minutos": duracion},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _turno(client, doctor_id: int, paciente_id: int, fecha: date, desde: str, hasta: str):
    return client.post(
        f"{API}/turnos",
        json={
            "paciente_id": paciente_id,
            "doctor_id": doctor_id,
            "fecha_hora_inicio": f"{fecha.isoformat()}T{desde}:00",
            "fecha_hora_fin": f"{fecha.isoformat()}T{hasta}:00",
        },
    )


# --------------------------------------------------------------------------- #
# Agenda semanal (editor por odontólogo)
# --------------------------------------------------------------------------- #


def test_la_semana_arranca_vacia(client, doctor):
    assert _listar(client, doctor.id) == []


def test_agregar_rango_devuelve_201_y_horas_normalizadas(client, doctor):
    r = _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00")
    assert r.status_code == 201, r.text

    data = r.json()
    assert data["doctor_id"] == doctor.id
    assert data["dia_semana"] == 1
    assert data["hora_inicio"] == "08:00"
    assert data["hora_fin"] == "12:00"
    assert isinstance(data["id"], int)


def test_un_odontologo_puede_tener_varios_rangos_el_mismo_dia(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _crear(client, doctor.id, dia_semana=1, desde="16:00", hasta="20:00").status_code == 201

    rangos = _listar(client, doctor.id)
    assert [(r["hora_inicio"], r["hora_fin"]) for r in rangos] == [
        ("08:00", "12:00"),
        ("16:00", "20:00"),
    ]


def test_los_rangos_contiguos_se_aceptan(client, doctor):
    assert _crear(client, doctor.id, dia_semana=3, desde="08:00", hasta="12:00").status_code == 201
    assert _crear(client, doctor.id, dia_semana=3, desde="12:00", hasta="16:00").status_code == 201
    assert len(_listar(client, doctor.id)) == 2


def test_rango_solapado_devuelve_409(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201

    r = _crear(client, doctor.id, dia_semana=1, desde="11:30", hasta="13:00")
    assert r.status_code == 409, r.text
    assert "solapa" in r.json()["detail"].lower()


def test_el_solape_no_aplica_a_otro_dia(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _crear(client, doctor.id, dia_semana=2, desde="08:00", hasta="12:00").status_code == 201
    assert len(_listar(client, doctor.id)) == 2


def test_rango_invertido_devuelve_422(client, doctor):
    r = _crear(client, doctor.id, dia_semana=1, desde="12:00", hasta="08:00")
    assert r.status_code == 422, r.text


def test_dia_semana_invalido_devuelve_422(client, doctor):
    assert _crear(client, doctor.id, dia_semana=7).status_code == 422
    assert _crear(client, doctor.id, dia_semana=-1).status_code == 422


def test_disponibilidad_de_un_doctor_inexistente_devuelve_404(client):
    assert client.get(f"{API}/doctores/9999/disponibilidad").status_code == 404
    assert _crear(client, 9999).status_code == 404


def test_reemplazar_agenda_deja_exactamente_lo_enviado(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201

    payload = {
        "rangos": [
            _rango(dia_semana=1, desde="09:00", hasta="13:00"),
            _rango(dia_semana=1, desde="15:00", hasta="19:00"),
            _rango(dia_semana=6, desde="09:00", hasta="13:00"),
        ]
    }
    r = client.put(f"{API}/doctores/{doctor.id}/disponibilidad", json=payload)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 3
    assert len(_listar(client, doctor.id)) == 3

    # Una lista vacía deja al profesional sin atención configurada.
    r = client.put(f"{API}/doctores/{doctor.id}/disponibilidad", json={"rangos": []})
    assert r.status_code == 200, r.text
    assert r.json() == []
    assert _listar(client, doctor.id) == []


def test_reemplazar_con_rangos_solapados_devuelve_422(client, doctor):
    payload = {
        "rangos": [
            _rango(dia_semana=1, desde="09:00", hasta="13:00"),
            _rango(dia_semana=1, desde="12:00", hasta="15:00"),
        ]
    }
    r = client.put(f"{API}/doctores/{doctor.id}/disponibilidad", json=payload)
    assert r.status_code == 422, r.text


def test_eliminar_rango_lo_quita_de_la_semana(client, doctor):
    primer_rango = _crear(
        client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00"
    ).json()
    assert _crear(client, doctor.id, dia_semana=1, desde="16:00", hasta="20:00").status_code == 201

    r = client.delete(f"{API}/doctores/{doctor.id}/disponibilidad/{primer_rango['id']}")
    assert r.status_code == 204, r.text

    restantes = _listar(client, doctor.id)
    assert len(restantes) == 1
    assert restantes[0]["hora_inicio"] == "16:00"


def test_eliminar_un_rango_inexistente_o_ajeno_devuelve_404(client, doctor, db_session):
    from app.models.disponibilidad import DisponibilidadDoctor
    from app.models.doctor import Doctor

    rango = _crear(client, doctor.id, dia_semana=1).json()

    assert client.delete(f"{API}/doctores/{doctor.id}/disponibilidad/9999").status_code == 404

    otro = Doctor(nombre="Dr. Otro", especialidad="Clínica", activo=True)
    db_session.add(otro)
    db_session.commit()
    db_session.refresh(otro)

    ajeno = client.delete(f"{API}/doctores/{otro.id}/disponibilidad/{rango['id']}")
    assert ajeno.status_code == 404, ajeno.text

    # El rango sigue perteneciendo a su dueño.
    assert len(_listar(client, doctor.id)) == 1
    assert db_session.get(DisponibilidadDoctor, rango["id"]) is not None


# --------------------------------------------------------------------------- #
# Bloques horarios ofrecidos (base del panel "Horarios disponibles")
# --------------------------------------------------------------------------- #


def test_bloques_respetan_los_rangos_configurados(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _crear(client, doctor.id, dia_semana=1, desde="16:00", hasta="20:00").status_code == 201

    r = _bloques(client, doctor.id, LUNES)
    assert r["doctor_id"] == doctor.id
    assert r["fecha"] == LUNES.isoformat()
    assert r["dia_semana"] == 1
    assert r["dia"] == "lunes"
    assert r["duracion_minutos"] == 30
    assert len(r["rangos"]) == 2

    bloques = [(b["hora_inicio"], b["hora_fin"]) for b in r["bloques"]]
    assert len(bloques) == 16          # 8 bloques por cada tramo de 4 horas
    assert bloques[0] == ("08:00", "08:30")
    assert bloques[7] == ("11:30", "12:00")
    assert bloques[8] == ("16:00", "16:30")
    assert bloques[-1] == ("19:30", "20:00")
    # El hueco de 12:00 a 16:00 no se ofrece.
    assert ("12:00", "12:30") not in bloques


def test_un_dia_sin_rangos_no_ofrece_bloques(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1).status_code == 201

    r = _bloques(client, doctor.id, DOMINGO)
    assert r["dia"] == "domingo"
    assert r["dia_semana"] == 0
    assert r["bloques"] == []
    assert r["rangos"] == []


def test_la_duracion_del_bloque_es_configurable(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="09:00", hasta="13:00").status_code == 201

    r = _bloques(client, doctor.id, LUNES, duracion=45)
    bloques = [(b["hora_inicio"], b["hora_fin"]) for b in r["bloques"]]
    assert bloques == [
        ("09:00", "09:45"),
        ("09:45", "10:30"),
        ("10:30", "11:15"),
        ("11:15", "12:00"),
        ("12:00", "12:45"),
    ]


def test_los_turnos_ocupados_no_se_ofrecen(client, doctor, paciente):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _turno(client, doctor.id, paciente.id, LUNES, "09:00", "09:30").status_code == 201

    bloques = [
        (b["hora_inicio"], b["hora_fin"])
        for b in _bloques(client, doctor.id, LUNES)["bloques"]
    ]
    assert len(bloques) == 7
    assert ("09:00", "09:30") not in bloques
    assert ("08:30", "09:00") in bloques
    assert ("09:30", "10:00") in bloques


def test_un_turno_largo_bloquea_todos_los_bloques_que_cruza(client, doctor, paciente):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _turno(client, doctor.id, paciente.id, LUNES, "08:15", "09:45").status_code == 201

    bloques = [
        (b["hora_inicio"], b["hora_fin"])
        for b in _bloques(client, doctor.id, LUNES)["bloques"]
    ]
    # Se descartan los cuatro bloques que el turno cruza (08:00 a 10:00).
    assert len(bloques) == 4
    assert bloques[0] == ("10:00", "10:30")


def test_cancelar_un_turno_libera_su_bloque(client, doctor, paciente):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    turno = _turno(client, doctor.id, paciente.id, LUNES, "09:00", "09:30")
    assert turno.status_code == 201, turno.text
    assert len(_bloques(client, doctor.id, LUNES)["bloques"]) == 7

    r = client.patch(
        f"{API}/turnos/{turno.json()['id']}", json={"estado_turno": "CANCELADO"}
    )
    assert r.status_code == 200, r.text
    assert len(_bloques(client, doctor.id, LUNES)["bloques"]) == 8


def test_los_turnos_de_otro_odontologo_no_afectan_los_bloques(
    client, doctor, paciente, db_session
):
    from app.models.doctor import Doctor
    from app.models.enums import EstadoTurno
    from app.models.turno import Turno

    otro = Doctor(nombre="Dr. Vecino", especialidad="Ortodoncia", activo=True)
    db_session.add(otro)
    db_session.commit()
    db_session.refresh(otro)

    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="10:00").status_code == 201
    db_session.add(
        Turno(
            paciente_id=paciente.id,
            doctor_id=otro.id,
            fecha_hora_inicio=datetime(LUNES.year, LUNES.month, LUNES.day, 8, 30),
            fecha_hora_fin=datetime(LUNES.year, LUNES.month, LUNES.day, 9, 0),
            estado_turno=EstadoTurno.CONFIRMADO,
        )
    )
    db_session.commit()

    # La agenda del otro profesional no descuenta bloques de este.
    assert len(_bloques(client, doctor.id, LUNES)["bloques"]) == 4


def test_horarios_de_un_doctor_inexistente_devuelve_404(client):
    r = client.get(
        f"{API}/doctores/9999/horarios-disponibles",
        params={"fecha": LUNES.isoformat()},
    )
    assert r.status_code == 404


def test_fecha_o_duracion_invalida_devuelve_422(client, doctor):
    base = f"{API}/doctores/{doctor.id}/horarios-disponibles"

    assert client.get(base, params={"fecha": "no-es-fecha"}).status_code == 422
    assert (
        client.get(
            base,
            params={"fecha": LUNES.isoformat(), "duracion_minutos": 300},
        ).status_code
        == 422
    )
    assert (
        client.get(
            base,
            params={"fecha": LUNES.isoformat(), "duracion_minutos": 0},
        ).status_code
        == 422
    )
    # La fecha es obligatoria.
    assert client.get(base).status_code == 422


def test_dia_semana_usa_la_convencion_del_calendario():
    from app.services.turno_service import dia_semana_de

    assert dia_semana_de(date(2026, 1, 5)) == 1    # lunes
    assert dia_semana_de(date(2026, 1, 10)) == 6   # sábado
    assert dia_semana_de(date(2026, 1, 11)) == 0   # domingo
    assert dia_semana_de(LUNES) == 1
    assert dia_semana_de(DOMINGO) == 0
