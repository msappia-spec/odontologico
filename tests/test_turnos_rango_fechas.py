"""Pruebas del filtro por rango de fechas de ``GET /api/v1/turnos``.

La agenda móvil del odontólogo puede consultar *cualquier* día del calendario:
pide una semana completa (``fecha_desde``/``fecha_hasta``) y reparte la respuesta
entre el día visible y la tira de los siete días. Estas pruebas fijan ese
contrato:

* los turnos entran por **solapamiento** con los días pedidos (no sólo si
  empiezan ese día);
* un turno que cruza la medianoche aparece en los dos días;
* un rango invertido responde ``422``;
* el filtro se combina con ``doctor_id``;
* el mismo ``PATCH`` que usa la hoja del celular reprograma, reasigna el
  paciente y cambia el estado en una sola llamada.
"""

from datetime import datetime, time, timedelta

from app.core.datetime_utils import utcnow
from app.models.doctor import Doctor
from app.models.paciente import Paciente

API = "/api/v1"


def _crear(
    client,
    doctor_id: int,
    paciente_id: int,
    *,
    dia,
    hora: int = 9,
    minuto: int = 0,
    duracion_min: int = 30,
) -> dict:
    """Crea un turno en el día indicado (``date``) y devuelve su representación."""
    inicio = datetime.combine(dia, time(hour=hora, minute=minuto))
    fin = inicio + timedelta(minutes=duracion_min)
    r = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": paciente_id,
            "doctor_id": doctor_id,
            "fecha_hora_inicio": inicio.isoformat(),
            "fecha_hora_fin": fin.isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _dias_de(client, *, desde, hasta) -> list[int]:
    """Ids de los turnos que el celular vería al pedir ese rango de días."""
    r = client.get(
        f"{API}/turnos",
        params={"fecha_desde": desde.isoformat(), "fecha_hasta": hasta.isoformat()},
    )
    assert r.status_code == 200, r.text
    return [t["id"] for t in r.json()]


def _paciente_extra(db_session, nombre: str = "Otro Paciente") -> Paciente:
    paciente = Paciente(nombre=nombre, telefono="+5491100000000")
    db_session.add(paciente)
    db_session.commit()
    db_session.refresh(paciente)
    return paciente


# --------------------------------------------------------------------------- #
# 1) El rango delimita la agenda que ve el celular
# --------------------------------------------------------------------------- #


def test_el_rango_devuelve_solo_los_dias_pedidos(client, doctor, paciente):
    hoy = utcnow().date()
    manana = _crear(client, doctor.id, paciente.id, dia=hoy + timedelta(days=1))
    _crear(client, doctor.id, paciente.id, dia=hoy + timedelta(days=4), hora=10)

    assert _dias_de(
        client, desde=hoy + timedelta(days=1), hasta=hoy + timedelta(days=2)
    ) == [manana["id"]]


def test_la_semana_pedida_por_la_tira_incluye_sus_dias(client, doctor, paciente):
    """La request semanal del celular trae el día visible y los de la tira."""
    hoy = utcnow().date()
    lunes = hoy - timedelta(days=hoy.weekday())
    creados = [
        _crear(
            client, doctor.id, paciente.id, dia=lunes + timedelta(days=i), hora=8 + i
        )
        for i in range(4)
    ]

    ids = _dias_de(client, desde=lunes, hasta=lunes + timedelta(days=6))

    assert ids == [t["id"] for t in creados]


def test_un_dia_sin_turnos_devuelve_lista_vacia(client, doctor, paciente):
    hoy = utcnow().date()
    _crear(client, doctor.id, paciente.id, dia=hoy)
    lejos = hoy + timedelta(days=10)

    assert _dias_de(client, desde=lejos, hasta=lejos) == []


def test_un_turno_que_cruza_la_medianoche_aparece_en_los_dos_dias(
    client, doctor, paciente
):
    """23:45 a 00:15 pertenece al día en que empieza y al siguiente."""
    hoy = utcnow().date()
    noche = _crear(
        client, doctor.id, paciente.id, dia=hoy, hora=23, minuto=45, duracion_min=30
    )
    manana = hoy + timedelta(days=1)

    assert _dias_de(client, desde=hoy, hasta=hoy) == [noche["id"]]
    assert _dias_de(client, desde=manana, hasta=manana) == [noche["id"]]


def test_sin_parametros_de_fecha_sigue_devolviendo_todo(client, doctor, paciente):
    hoy = utcnow().date()
    _crear(client, doctor.id, paciente.id, dia=hoy)
    _crear(client, doctor.id, paciente.id, dia=hoy + timedelta(days=30))

    r = client.get(f"{API}/turnos")

    assert r.status_code == 200, r.text
    assert len(r.json()) == 2


def test_rango_invertido_devuelve_422(client):
    """``fecha_hasta`` anterior a ``fecha_desde`` es un dato inválido (no 200)."""
    hoy = utcnow().date()

    r = client.get(
        f"{API}/turnos",
        params={
            "fecha_desde": (hoy + timedelta(days=5)).isoformat(),
            "fecha_hasta": hoy.isoformat(),
        },
    )

    assert r.status_code == 422, r.text
    assert "anterior" in r.json()["detail"].lower()


def test_el_rango_se_combina_con_el_filtro_de_doctor(
    client, doctor, paciente, db_session
):
    otro_doctor = Doctor(
        nombre="Dra. Endodoncia", especialidad="Endodoncia", activo=True
    )
    db_session.add(otro_doctor)
    db_session.commit()
    db_session.refresh(otro_doctor)
    otro_paciente = _paciente_extra(db_session)

    hoy = utcnow().date()
    mio = _crear(client, doctor.id, paciente.id, dia=hoy, hora=9)
    _crear(client, otro_doctor.id, otro_paciente.id, dia=hoy, hora=16)

    r = client.get(
        f"{API}/turnos",
        params={
            "doctor_id": doctor.id,
            "fecha_desde": hoy.isoformat(),
            "fecha_hasta": hoy.isoformat(),
        },
    )

    assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()] == [mio["id"]]


# --------------------------------------------------------------------------- #
# 2) La hoja del celular reprograma y reasigna con un solo PATCH
# --------------------------------------------------------------------------- #


def test_el_patch_de_la_hoja_reprograma_reasigna_y_cambia_el_estado(
    client, doctor, paciente, db_session
):
    hoy = utcnow().date()
    turno = _crear(client, doctor.id, paciente.id, dia=hoy)
    otro_paciente = _paciente_extra(db_session, "Paciente Reasignado")

    destino = hoy + timedelta(days=2)
    inicio = datetime.combine(destino, time(hour=10, minute=30))
    fin = inicio + timedelta(minutes=45)

    r = client.patch(
        f"{API}/turnos/{turno['id']}",
        json={
            "paciente_id": otro_paciente.id,
            "fecha_hora_inicio": inicio.isoformat(),
            "fecha_hora_fin": fin.isoformat(),
            "estado_turno": "CONFIRMADO",
        },
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["paciente_id"] == otro_paciente.id
    assert data["fecha_hora_inicio"] == inicio.isoformat()
    assert data["fecha_hora_fin"] == fin.isoformat()
    assert data["estado_turno"] == "CONFIRMADO"

    # El turno se muda de día: ya no aparece en el original y sí en el nuevo.
    assert turno["id"] not in _dias_de(client, desde=hoy, hasta=hoy)
    assert _dias_de(client, desde=destino, hasta=destino) == [turno["id"]]
