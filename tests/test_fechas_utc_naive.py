"""Pruebas de la convención de fechas UTC *naive* (:mod:`app.db.types`).

Bug de producción (13/10/2026): en PostgreSQL las columnas ``TIMESTAMP WITH TIME
ZONE`` vuelven *aware* y compararlas con las fechas naive que arma el código
lanzaba ``TypeError: can't compare offset-naive and offset-aware datetimes`` en
``GET /api/v1/doctores/{id}/horarios-disponibles`` — la vista móvil del
odontólogo (``/medico``) mostraba un error al pintar la agenda.

Estas pruebas fijan el tipo ``UTCDateTime`` (normaliza al leer y al escribir) y el
cálculo de horarios libres cuando los turnos llegan con zona horaria, tal como los
entrega PostgreSQL.
"""

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql, sqlite

from app.core.datetime_utils import dia_semana_de
from app.db.types import UTCDateTime
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.turno import Turno
from app.services.turno_service import _turnos_vigentes_del_dia

UTC = timezone.utc
CON_ZONA = datetime(2026, 10, 13, 15, 30, tzinfo=UTC)
SIN_ZONA = datetime(2026, 10, 13, 15, 30)
FECHA = date(2026, 10, 13)


class _SesionFalsa:
    """Sesión mínima que devuelve los turnos indicados (sin base de datos)."""

    def __init__(self, turnos) -> None:
        self._turnos = turnos

    def execute(self, _stmt):
        return self

    def scalars(self):
        return self

    def all(self):
        return self._turnos


def test_el_tipo_entrega_utc_naive_al_leer():
    """PostgreSQL devuelve ``timestamptz`` (aware): el ORM debe dar naive."""
    tipo = UTCDateTime()

    assert tipo.process_result_value(CON_ZONA, postgresql.dialect()) == SIN_ZONA
    assert tipo.process_result_value(SIN_ZONA, postgresql.dialect()) == SIN_ZONA
    assert tipo.process_result_value(None, postgresql.dialect()) is None


def test_el_tipo_guarda_siempre_en_utc():
    """Una hora con zona local se guarda como UTC naive."""
    buenos_aires = timezone(timedelta(hours=-3))
    local = datetime(2026, 10, 13, 12, 30, tzinfo=buenos_aires)
    tipo = UTCDateTime()

    assert tipo.process_bind_param(local, postgresql.dialect()) == SIN_ZONA
    assert tipo.process_bind_param(SIN_ZONA, postgresql.dialect()) == SIN_ZONA
    assert tipo.process_bind_param(None, postgresql.dialect()) is None


def test_el_esquema_de_sqlite_no_cambia():
    """El tipo sigue compilando a ``DATETIME`` en SQLite (sin migraciones)."""
    assert UTCDateTime().compile(dialect=sqlite.dialect()) == "DATETIME"


def test_las_fechas_del_turno_usan_el_tipo_utc_naive():
    """Regresión: no volver a ``DateTime(timezone=True)`` pelado."""
    assert isinstance(Turno.fecha_hora_inicio.type, UTCDateTime)
    assert isinstance(Turno.fecha_hora_fin.type, UTCDateTime)


def test_los_intervalos_del_dia_se_normalizan_antes_de_comparar():
    """Fechas *aware* (como las de PostgreSQL) no rompen el cálculo de huecos."""
    turno = SimpleNamespace(
        fecha_hora_inicio=CON_ZONA, fecha_hora_fin=CON_ZONA + timedelta(minutes=30)
    )

    intervalos = _turnos_vigentes_del_dia(_SesionFalsa([turno]), 1, FECHA)

    assert intervalos == [(SIN_ZONA, SIN_ZONA + timedelta(minutes=30))]
    assert all(
        inicio.tzinfo is None and fin.tzinfo is None for inicio, fin in intervalos
    )


def test_el_endpoint_de_horarios_libres_responde_200(
    client, db_session, doctor, paciente
):
    """End-to-end: el endpoint que devolvía 500 descuenta el turno ocupado."""
    db_session.add(
        DisponibilidadDoctor(
            doctor_id=doctor.id,
            dia_semana=dia_semana_de(FECHA),
            hora_inicio=time(9, 0),
            hora_fin=time(11, 0),
        )
    )
    db_session.add(
        Turno(
            paciente_id=paciente.id,
            doctor_id=doctor.id,
            fecha_hora_inicio=datetime.combine(FECHA, time(9, 30)),
            fecha_hora_fin=datetime.combine(FECHA, time(10, 0)),
        )
    )
    db_session.commit()

    respuesta = client.get(
        f"/api/v1/doctores/{doctor.id}/horarios-disponibles",
        params={"fecha": FECHA.isoformat()},
    )

    assert respuesta.status_code == 200, respuesta.text
    bloques = [
        (bloque["hora_inicio"], bloque["hora_fin"])
        for bloque in respuesta.json()["bloques"]
    ]
    # 09:00–11:00 en bloques de 30 min, sin el que está ocupado (09:30–10:00).
    assert bloques == [
        ("09:00", "09:30"),
        ("10:00", "10:30"),
        ("10:30", "11:00"),
    ]
