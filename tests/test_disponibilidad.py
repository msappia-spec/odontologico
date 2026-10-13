"""Pruebas de la disponibilidad de los odontólogos.

Cubren las tres capas que el panel administra:

* el editor de la **agenda semanal** (``/doctores/{id}/disponibilidad``);
* las **excepciones por fecha concreta**
  (``/doctores/{id}/disponibilidad-especifica``): tramos ``HABILITADO`` /
  ``BLOQUEADO`` que abren o cierran horas de un día puntual;
* la generación de **bloques horarios ofrecidos** para una fecha
  (``/doctores/{id}/horarios-disponibles``), que es la base del panel de
  *Horarios disponibles* del calendario y que aplica la precedencia entre las dos
  capas anteriores (si el día tiene excepciones, la semana se ignora).
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from app.core.datetime_utils import utcnow
from app.main import app
from app.web.router import STATIC_DIR

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
MARTES = _proxima(2)
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


def _excepcion(
    fecha: date,
    desde: str = "08:00",
    hasta: str = "12:00",
    tipo: str = "HABILITADO",
) -> dict:
    """Tramo de una fecha concreta: ``HABILITADO`` (abre) o ``BLOQUEADO`` (cierra)."""
    return {
        "fecha": fecha.isoformat(),
        "hora_inicio": desde,
        "hora_fin": hasta,
        "tipo": tipo,
    }


def _crear_excepcion(client, doctor_id: int, fecha: date, **kwargs):
    return client.post(
        f"{API}/doctores/{doctor_id}/disponibilidad-especifica",
        json=_excepcion(fecha, **kwargs),
    )


def _listar_excepciones(client, doctor_id: int, **params):
    r = client.get(
        f"{API}/doctores/{doctor_id}/disponibilidad-especifica", params=params
    )
    assert r.status_code == 200, r.text
    return r.json()


def _reemplazar_excepciones(client, doctor_id: int, fecha: date, rangos: list):
    return client.put(
        f"{API}/doctores/{doctor_id}/disponibilidad-especifica/{fecha.isoformat()}",
        json={"rangos": rangos},
    )


def _horas(pares) -> list:
    """Convierte bloques/rangos de la API en pares ``(hora_inicio, hora_fin)``."""
    return [(p["hora_inicio"], p["hora_fin"]) for p in pares]



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


# --------------------------------------------------------------------------- #
# Excepciones de agenda por fecha concreta
# --------------------------------------------------------------------------- #


def test_la_lista_de_excepciones_arranca_vacia(client, doctor):
    assert _listar_excepciones(client, doctor.id) == []
    # Y sin excepciones el día se resuelve con la agenda semanal.
    assert _bloques(client, doctor.id, LUNES)["origen"] == "SEMANAL"


def test_agregar_una_excepcion_devuelve_201_y_datos_normalizados(client, doctor):
    r = _crear_excepcion(client, doctor.id, MARTES, desde="15:00", hasta="17:00")

    assert r.status_code == 201, r.text
    datos = r.json()
    assert datos["doctor_id"] == doctor.id
    assert datos["fecha"] == MARTES.isoformat()
    assert datos["hora_inicio"] == "15:00"
    assert datos["hora_fin"] == "17:00"
    assert datos["tipo"] == "HABILITADO"      # el tipo por defecto abre atención
    assert isinstance(datos["id"], int)

    guardadas = _listar_excepciones(client, doctor.id)
    assert [e["id"] for e in guardadas] == [datos["id"]]


def test_el_tipo_por_defecto_es_habilitado(client, doctor):
    r = client.post(
        f"{API}/doctores/{doctor.id}/disponibilidad-especifica",
        json={
            "fecha": MARTES.isoformat(),
            "hora_inicio": "10:00",
            "hora_fin": "12:00",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["tipo"] == "HABILITADO"


def test_la_excepcion_define_los_bloques_del_dia(client, doctor):
    assert _crear(client, doctor.id, dia_semana=2, desde="08:00", hasta="12:00").status_code == 201
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="15:00", hasta="17:00"
    ).status_code == 201

    r = _bloques(client, doctor.id, MARTES)

    assert r["origen"] == "ESPECIFICO"
    assert r["dia_semana"] == 2
    assert r["dia"] == "martes"
    assert _horas(r["rangos"]) == [("15:00", "17:00")]
    assert len(r["excepciones"]) == 1
    assert r["excepciones"][0]["tipo"] == "HABILITADO"
    assert r["excepciones"][0]["fecha"] == MARTES.isoformat()
    assert [b["hora_inicio"] for b in r["bloques"]] == ["15:00", "15:30", "16:00", "16:30"]


def test_habilitar_un_dia_que_la_semana_no_cubre(client, doctor):
    # La agenda semanal sólo atiende los lunes.
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201

    assert _bloques(client, doctor.id, DOMINGO)["origen"] == "SEMANAL"
    assert _bloques(client, doctor.id, DOMINGO)["bloques"] == []

    assert _crear_excepcion(
        client, doctor.id, DOMINGO, desde="09:00", hasta="13:00"
    ).status_code == 201

    r = _bloques(client, doctor.id, DOMINGO)
    assert r["origen"] == "ESPECIFICO"
    assert r["dia"] == "domingo"
    assert r["dia_semana"] == 0
    assert _horas(r["rangos"]) == [("09:00", "13:00")]
    assert len(r["bloques"]) == 8                  # 09:00 a 13:00 cada 30 min
    assert _horas(r["bloques"])[0] == ("09:00", "09:30")
    assert _horas(r["bloques"])[-1] == ("12:30", "13:00")


def test_la_agenda_semanal_se_ignora_si_el_dia_tiene_excepciones(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _crear(client, doctor.id, dia_semana=1, desde="16:00", hasta="20:00").status_code == 201

    # Una sola excepción de la tarde reemplaza por completo la agenda del lunes.
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="16:30", hasta="18:30"
    ).status_code == 201

    r = _bloques(client, doctor.id, LUNES)
    assert r["origen"] == "ESPECIFICO"
    assert _horas(r["rangos"]) == [("16:30", "18:30")]
    assert _horas(r["bloques"]) == [
        ("16:30", "17:00"),
        ("17:00", "17:30"),
        ("17:30", "18:00"),
        ("18:00", "18:30"),
    ]
    # La mañana de la agenda semanal ya no se ofrece.
    assert ("08:00", "08:30") not in _horas(r["bloques"])


# --------------------------------------------------------------------------- #
# Excepciones: bloqueos que recortan y precedencia sobre la agenda semanal
# --------------------------------------------------------------------------- #


def test_bloquear_horas_recorta_el_dia(client, doctor):
    # El día excepcional se describe entero con excepciones: se habilita de
    # 08:00 a 12:00 y se bloquea su última parte (por ejemplo, una reunión).
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="08:00", hasta="12:00"
    ).status_code == 201
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="10:00", hasta="12:00", tipo="BLOQUEADO"
    ).status_code == 201

    r = _bloques(client, doctor.id, LUNES)
    assert r["origen"] == "ESPECIFICO"
    assert _horas(r["rangos"]) == [("08:00", "10:00")]
    assert len(r["bloques"]) == 4
    assert _horas(r["bloques"])[-1] == ("09:30", "10:00")
    assert ("10:00", "10:30") not in _horas(r["bloques"])


def test_un_dia_con_solo_bloqueos_no_atiende(client, doctor):
    # La precedencia es total: con excepciones cargadas la agenda semanal se
    # ignora, así que un día sin ningún tramo HABILITADO queda cerrado.
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="10:00", hasta="12:00", tipo="BLOQUEADO"
    ).status_code == 201

    r = _bloques(client, doctor.id, LUNES)
    assert r["origen"] == "ESPECIFICO"
    assert r["rangos"] == []
    assert r["bloques"] == []


def test_los_bloqueos_se_solapan_entre_si_y_se_unen(client, doctor):
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="08:00", hasta="13:00"
    ).status_code == 201
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="09:00", hasta="11:00", tipo="BLOQUEADO"
    ).status_code == 201
    r = _crear_excepcion(
        client, doctor.id, LUNES, desde="10:00", hasta="12:00", tipo="BLOQUEADO"
    )
    assert r.status_code == 201, r.text          # solaparse no molesta al bloqueo

    dia = _bloques(client, doctor.id, LUNES)
    assert _horas(dia["rangos"]) == [("08:00", "09:00"), ("12:00", "13:00")]
    assert len(dia["bloques"]) == 4              # dos bloques por cada tramo
    assert len(dia["excepciones"]) == 3


def test_bloquear_todo_el_dia_lo_deja_sin_bloques(client, doctor):
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="08:00", hasta="12:00"
    ).status_code == 201
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="00:00", hasta="23:59", tipo="BLOQUEADO"
    ).status_code == 201

    r = _bloques(client, doctor.id, MARTES)
    assert r["origen"] == "ESPECIFICO"
    assert r["rangos"] == []
    assert r["bloques"] == []


def test_los_habilitados_contiguos_mantienen_su_propio_compas(client, doctor):
    # Dos tramos pegados no se fusionan: cada uno arranca su propia cuenta de
    # bloques (si se unieran, con 45 min el segundo arrancaría en 09:30).
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="08:00", hasta="10:00"
    ).status_code == 201
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="10:00", hasta="12:00"
    ).status_code == 201

    r = _bloques(client, doctor.id, MARTES, duracion=45)
    assert _horas(r["rangos"]) == [("08:00", "10:00"), ("10:00", "12:00")]
    assert _horas(r["bloques"]) == [
        ("08:00", "08:45"),
        ("08:45", "09:30"),
        ("10:00", "10:45"),
        ("10:45", "11:30"),
    ]


def test_dos_habilitados_solapados_devuelven_409(client, doctor):
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="08:00", hasta="12:00"
    ).status_code == 201

    r = _crear_excepcion(client, doctor.id, MARTES, desde="11:00", hasta="13:00")
    assert r.status_code == 409, r.text
    assert "solapa" in r.json()["detail"].lower()
    assert len(_listar_excepciones(client, doctor.id)) == 1


def test_el_solape_de_habilitados_no_mira_otros_dias(client, doctor):
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="08:00", hasta="12:00"
    ).status_code == 201
    # Mismo horario pero en otro día: no hay solape.
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="08:00", hasta="12:00"
    ).status_code == 201
    assert len(_listar_excepciones(client, doctor.id)) == 2


def test_las_excepciones_de_otro_dia_no_afectan_al_dia_consultado(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="14:00", hasta="18:00"
    ).status_code == 201

    lunes = _bloques(client, doctor.id, LUNES)
    assert lunes["origen"] == "SEMANAL"
    assert lunes["excepciones"] == []
    assert _horas(lunes["rangos"]) == [("08:00", "12:00")]
    assert len(lunes["bloques"]) == 8


# --------------------------------------------------------------------------- #
# Excepciones: reemplazo, borrado y validaciones del contrato
# --------------------------------------------------------------------------- #


def test_reemplazar_el_dia_deja_exactamente_lo_enviado(client, doctor):
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="08:00", hasta="12:00"
    ).status_code == 201
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="12:00", hasta="16:00", tipo="BLOQUEADO"
    ).status_code == 201

    payload = [
        {"hora_inicio": "09:00", "hora_fin": "13:00", "tipo": "HABILITADO"},
        {"hora_inicio": "13:00", "hora_fin": "15:00", "tipo": "BLOQUEADO"},
        {"hora_inicio": "15:00", "hora_fin": "19:00", "tipo": "HABILITADO"},
    ]
    r = _reemplazar_excepciones(client, doctor.id, LUNES, payload)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 3
    assert len(_listar_excepciones(client, doctor.id)) == 3

    dia = _bloques(client, doctor.id, LUNES)
    assert _horas(dia["rangos"]) == [("09:00", "13:00"), ("15:00", "19:00")]
    assert len(dia["excepciones"]) == 3
    # El rango de la mañana que había antes ya no está.
    assert ("08:00", "08:30") not in _horas(dia["bloques"])

    # Con agenda semanal cargada, una lista vacía devuelve el día a la semana.
    assert _crear(client, doctor.id, dia_semana=1, desde="07:00", hasta="08:00").status_code == 201
    r = _reemplazar_excepciones(client, doctor.id, LUNES, [])
    assert r.status_code == 200, r.text
    assert r.json() == []
    assert _listar_excepciones(client, doctor.id) == []

    vuelta = _bloques(client, doctor.id, LUNES)
    assert vuelta["origen"] == "SEMANAL"
    assert _horas(vuelta["rangos"]) == [("07:00", "08:00")]


def test_reemplazar_con_habilitados_solapados_devuelve_422(client, doctor):
    payload = [
        {"hora_inicio": "09:00", "hora_fin": "13:00", "tipo": "HABILITADO"},
        {"hora_inicio": "12:00", "hora_fin": "15:00", "tipo": "HABILITADO"},
    ]
    r = _reemplazar_excepciones(client, doctor.id, LUNES, payload)
    assert r.status_code == 422, r.text
    assert _listar_excepciones(client, doctor.id) == []


def test_reemplazar_solapes_de_distinto_tipo_es_valido(client, doctor):
    payload = [
        {"hora_inicio": "09:00", "hora_fin": "13:00", "tipo": "HABILITADO"},
        {"hora_inicio": "12:00", "hora_fin": "15:00", "tipo": "BLOQUEADO"},
    ]
    r = _reemplazar_excepciones(client, doctor.id, LUNES, payload)
    assert r.status_code == 200, r.text
    assert _horas(_bloques(client, doctor.id, LUNES)["rangos"]) == [("09:00", "12:00")]


def test_un_tramo_invertido_devuelve_422(client, doctor):
    assert _crear_excepcion(client, doctor.id, LUNES, desde="12:00", hasta="08:00").status_code == 422
    # Un tramo de duración cero tampoco es válido.
    assert _crear_excepcion(client, doctor.id, LUNES, desde="08:00", hasta="08:00").status_code == 422
    r = _reemplazar_excepciones(
        client,
        doctor.id,
        LUNES,
        [{"hora_inicio": "18:00", "hora_fin": "09:00", "tipo": "BLOQUEADO"}],
    )
    assert r.status_code == 422, r.text
    assert _listar_excepciones(client, doctor.id) == []


def test_tipo_o_fecha_invalidos_devuelven_422(client, doctor):
    assert _crear_excepcion(client, doctor.id, LUNES, tipo="CERRADO").status_code == 422

    r = client.post(
        f"{API}/doctores/{doctor.id}/disponibilidad-especifica",
        json={"fecha": "no-es-fecha", "hora_inicio": "08:00", "hora_fin": "12:00"},
    )
    assert r.status_code == 422, r.text

    assert _reemplazar_excepciones(
        client, doctor.id, LUNES, [{"hora_inicio": "08:00", "hora_fin": "12:00", "tipo": "X"}]
    ).status_code == 422

    # El endpoint de reemplazo no expone lectura por fecha.
    assert client.get(
        f"{API}/doctores/{doctor.id}/disponibilidad-especifica/{LUNES.isoformat()}"
    ).status_code == 405


def test_listar_excepciones_se_acota_por_fecha(client, doctor):
    # ``LUNES``/``MARTES`` son "hoy o después", así que el orden entre ambos
    # depende del día en que se corra la suite.
    primero, segundo = sorted([LUNES, MARTES])

    assert _crear_excepcion(client, doctor.id, LUNES).status_code == 201
    assert _crear_excepcion(
        client, doctor.id, MARTES, desde="14:00", hasta="18:00"
    ).status_code == 201

    solo_primero = _listar_excepciones(
        client, doctor.id, desde=primero.isoformat(), hasta=primero.isoformat()
    )
    assert [e["fecha"] for e in solo_primero] == [primero.isoformat()]

    hasta_primero = _listar_excepciones(client, doctor.id, hasta=primero.isoformat())
    assert [e["fecha"] for e in hasta_primero] == [primero.isoformat()]

    desde_segundo = _listar_excepciones(client, doctor.id, desde=segundo.isoformat())
    assert [e["fecha"] for e in desde_segundo] == [segundo.isoformat()]

    # El rango completo (inclusive) devuelve las dos fechas cargadas.
    ambas = _listar_excepciones(
        client, doctor.id, desde=primero.isoformat(), hasta=segundo.isoformat()
    )
    assert [e["fecha"] for e in ambas] == [primero.isoformat(), segundo.isoformat()]

    # Sin filtros se listan todas las excepciones del profesional.
    assert len(_listar_excepciones(client, doctor.id)) == 2

    assert client.get(
        f"{API}/doctores/{doctor.id}/disponibilidad-especifica",
        params={"desde": "no-es-fecha"},
    ).status_code == 422


# --------------------------------------------------------------------------- #
# Excepciones: borrado e integración con los turnos del día
# --------------------------------------------------------------------------- #


def test_eliminar_una_excepcion_devuelve_204(client, doctor):
    assert _crear(client, doctor.id, dia_semana=1, desde="08:00", hasta="12:00").status_code == 201
    habilitado = _crear_excepcion(
        client, doctor.id, LUNES, desde="15:00", hasta="17:00"
    ).json()
    bloqueado = _crear_excepcion(
        client, doctor.id, LUNES, desde="16:00", hasta="18:00", tipo="BLOQUEADO"
    ).json()

    base = f"{API}/doctores/{doctor.id}/disponibilidad-especifica"
    assert client.delete(f"{base}/{habilitado['id']}").status_code == 204

    # Queda sólo el bloqueo: el día sigue personalizado y sin nada abierto.
    dia = _bloques(client, doctor.id, LUNES)
    assert dia["origen"] == "ESPECIFICO"
    assert [e["id"] for e in dia["excepciones"]] == [bloqueado["id"]]
    assert dia["rangos"] == []
    assert dia["bloques"] == []

    # Al borrar la última excepción el día vuelve a la agenda semanal.
    assert client.delete(f"{base}/{bloqueado['id']}").status_code == 204
    assert _listar_excepciones(client, doctor.id) == []
    vuelta = _bloques(client, doctor.id, LUNES)
    assert vuelta["origen"] == "SEMANAL"
    assert len(vuelta["bloques"]) == 8


def test_eliminar_una_excepcion_inexistente_o_ajena_devuelve_404(
    client, doctor, db_session
):
    from app.models.disponibilidad_especifica import DisponibilidadEspecifica
    from app.models.doctor import Doctor

    excepcion = _crear_excepcion(client, doctor.id, MARTES).json()
    base = f"{API}/doctores/{doctor.id}/disponibilidad-especifica"

    assert client.delete(f"{base}/9999").status_code == 404

    otro = Doctor(nombre="Dr. Otro", especialidad="Clínica", activo=True)
    db_session.add(otro)
    db_session.commit()
    db_session.refresh(otro)

    ajeno = client.delete(
        f"{API}/doctores/{otro.id}/disponibilidad-especifica/{excepcion['id']}"
    )
    assert ajeno.status_code == 404, ajeno.text

    # La excepción sigue perteneciendo a su dueño.
    assert len(_listar_excepciones(client, doctor.id)) == 1
    assert db_session.get(DisponibilidadEspecifica, excepcion["id"]) is not None


def test_las_excepciones_de_un_doctor_inexistente_devuelven_404(client):
    base = f"{API}/doctores/9999/disponibilidad-especifica"

    assert client.get(base).status_code == 404
    assert client.post(
        base,
        json={
            "fecha": LUNES.isoformat(),
            "hora_inicio": "08:00",
            "hora_fin": "12:00",
        },
    ).status_code == 404
    assert client.put(f"{base}/{LUNES.isoformat()}", json={"rangos": []}).status_code == 404
    assert client.delete(f"{base}/1").status_code == 404


def test_un_turno_ocupado_descuenta_su_bloque_en_un_dia_personalizado(
    client, doctor, paciente
):
    assert _crear_excepcion(
        client, doctor.id, LUNES, desde="08:00", hasta="12:00"
    ).status_code == 201
    assert _turno(client, doctor.id, paciente.id, LUNES, "09:00", "09:30").status_code == 201

    r = _bloques(client, doctor.id, LUNES)
    assert r["origen"] == "ESPECIFICO"
    assert len(r["bloques"]) == 7
    assert ("09:00", "09:30") not in _horas(r["bloques"])
    assert ("08:30", "09:00") in _horas(r["bloques"])
    assert ("09:30", "10:00") in _horas(r["bloques"])


# --------------------------------------------------------------------------- #
# Contrato con el panel: la cruz «X» del chip elimina el tramo correcto
# --------------------------------------------------------------------------- #


def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


def test_reintentar_la_baja_de_un_tramo_ya_eliminado_devuelve_404(client, doctor):
    """Reproduce el doble clic en la cruz del chip.

    El primer ``DELETE`` borra el tramo; el segundo —con el id real que el chip
    todavía mostraba— responde **404** sin tocar los demás tramos del día. El
    panel informa ese caso como aviso y se sincroniza con este listado.
    """
    bloqueo = _crear_excepcion(
        client, doctor.id, LUNES, desde="00:00", hasta="23:59", tipo="BLOQUEADO"
    ).json()
    habilitado = _crear_excepcion(
        client, doctor.id, LUNES, desde="15:00", hasta="17:00"
    ).json()

    base = f"{API}/doctores/{doctor.id}/disponibilidad-especifica"
    assert client.delete(f"{base}/{bloqueo['id']}").status_code == 204

    repetido = client.delete(f"{base}/{bloqueo['id']}")
    assert repetido.status_code == 404, repetido.text
    assert f"id={bloqueo['id']}" in repetido.json()["detail"]

    # El listado confirma la baja y el otro tramo del día quedó intacto.
    assert [e["id"] for e in _listar_excepciones(client, doctor.id)] == [habilitado["id"]]


def test_el_chip_del_tramo_lleva_el_id_real_que_devuelve_la_api():
    """La cruz elimina por el id de la excepción, no por su posición en la lista."""
    app_js = _leer_estatico("app.js")

    # El chip inyecta el id del tramo (y su dueño) en los atributos del botón.
    assert 'data-borrar-excepcion="${e.id}"' in app_js
    assert 'data-excepcion-doctor="${e.doctor_id}"' in app_js

    # El handler los lee del DOM y se los pasa a la baja junto con el botón.
    assert 'closest("[data-borrar-excepcion]")' in app_js
    assert re.search(
        r"borrarExcepcion\(\s*btn\.dataset\.excepcionDoctor,\s*"
        r"btn\.dataset\.borrarExcepcion,\s*btn\.dataset\.excepcionFecha,\s*btn\s*\)",
        app_js,
    ), "el handler debe pasar el id del dataset y el botón pulsado"


def test_la_baja_apunta_al_endpoint_de_la_excepcion():
    """El ``DELETE`` va a ``/doctores/{doctor}/disponibilidad-especifica/{id}``."""
    app_js = _leer_estatico("app.js")

    assert re.search(r'method:\s*"DELETE"', app_js), "faltó el DELETE del tramo"
    assert "`/doctores/${idDoctor}/disponibilidad-especifica/${idExcepcion}`" in app_js
    # Los ids se normalizan a número antes de armar la URL.
    assert re.search(r"const idExcepcion = Number\(excepcionId\);", app_js)

    # El endpoint existe con esos dos parámetros en la ruta.
    ruta = f"/api/v1/doctores/{{doctor_id}}/disponibilidad-especifica/{{excepcion_id}}"
    assert "delete" in app.openapi()["paths"][ruta]


def test_tras_eliminar_un_tramo_la_lista_se_recarga_del_servidor():
    """Sin la recarga forzada el chip borrado seguiría en pantalla y un segundo
    clic respondería 404 (que la UI muestra como aviso, no como error)."""
    app_js = _leer_estatico("app.js")

    # `forzar` consulta al servidor aunque la fecha ya esté en el cache precargado.
    assert "async function cargarExcepcionesDeFecha(iso, forzar)" in app_js
    assert "await cargarExcepcionesDeFecha(fecha, true);" in app_js

    # El tramo sale de la lista antes de que responda la API (baja optimista)...
    assert "quitarExcepcionDelCache(" in app_js
    # ...y un 404 se trata como «ya estaba eliminado», no como fallo de la baja.
    assert "err.status === 404" in app_js
    assert "El tramo ya estaba eliminado" in app_js


def test_el_boton_de_la_cruz_se_deshabilita_mientras_se_elimina():
    """Un doble clic no debe repetir el DELETE (el segundo respondería 404)."""
    app_js = _leer_estatico("app.js")
    css = _leer_estatico("styles.css")

    assert "if (btn) btn.disabled = true;" in app_js
    assert ".excep-chip-x:disabled" in css
