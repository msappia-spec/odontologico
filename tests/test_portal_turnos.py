"""Pruebas del portal público de autogestión de turnos (``/reservar``).

Cubren las garantías que el portal promete al paciente y al consultorio:

* no exige sesión de empleado y no expone datos de otros pacientes;
* calcula los horarios libres con la agenda real del Módulo 1;
* reserva para un DNI nuevo o existente, con tope de dos turnos futuros;
* bloquea la doble reserva del mismo horario y libera el hueco al cancelar;
* aplica rate limiting por IP y devuelve 429 con ``Retry-After``.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy.dialects import postgresql

from app.core.datetime_utils import dia_semana_de, utcnow
from app.core.config import settings
from app.core.rate_limit import limitador_portal
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.doctor import Doctor
from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.models.turno import Turno
from app.services import whatsapp_service
from app.services import portal_turnos_service
from app.services.portal_turnos_service import codigo_cancelacion

API = "/api/v1/portal-turnos"
HORA = time(10, 0)


def _fecha_futura(dia_semana: int = 1, minimo_dias: int = 2) -> date:
    """Fecha futura que cae en ``dia_semana`` (0 = domingo), a ≥ 2 días."""
    hoy = utcnow().date()
    adelanto = (dia_semana - dia_semana_de(hoy)) % 7
    if adelanto < minimo_dias:
        adelanto += 7
    return hoy + timedelta(days=adelanto)


FECHA = _fecha_futura(1)


def _agenda(db_session, doctor: Doctor, fecha: date = FECHA) -> None:
    """Agenda semanal 09:00–12:00 para el día de ``fecha``."""
    db_session.add(
        DisponibilidadDoctor(
            doctor_id=doctor.id,
            dia_semana=dia_semana_de(fecha),
            hora_inicio=time(9, 0),
            hora_fin=time(12, 0),
        )
    )
    db_session.commit()


def _obra_social(db_session, nombre: str = "OSDEMO", activa: bool = True) -> ObraSocial:
    obra = ObraSocial(nombre=nombre, activa=activa)
    db_session.add(obra)
    db_session.commit()
    db_session.refresh(obra)
    return obra


def _paciente(
    db_session,
    dni: str = "30123456",
    nombre: str = "María González",
    telefono: str = "+5493514589",
    obra_social_id=None,
) -> Paciente:
    paciente = Paciente(
        nombre=nombre,
        dni=dni,
        telefono=telefono,
        obra_social_id=obra_social_id,
    )
    db_session.add(paciente)
    db_session.commit()
    db_session.refresh(paciente)
    return paciente


def _turno(
    db_session,
    doctor: Doctor,
    paciente: Paciente,
    inicio: datetime,
    *,
    estado: EstadoTurno = EstadoTurno.PENDIENTE,
) -> Turno:
    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=inicio,
        fecha_hora_fin=inicio + timedelta(minutes=30),
        estado_turno=estado,
    )
    db_session.add(turno)
    db_session.commit()
    db_session.refresh(turno)
    return turno


def _payload(
    doctor_id: int,
    *,
    dni: str = "40111222",
    hora: time = HORA,
    fecha: date = FECHA,
    obra_social_id=None,
    nombre: str = "Ana",
    apellido: str = "Pérez",
    telefono: str = "3515551234",
    motivo: str = "Control",
) -> dict:
    return {
        "dni": dni,
        "nombre": nombre,
        "apellido": apellido,
        "telefono_whatsapp": telefono,
        "obra_social_id": obra_social_id,
        "doctor_id": doctor_id,
        "fecha_hora": datetime.combine(fecha, hora).isoformat(),
        "motivo_consulta": motivo,
    }


# --------------------------------------------------------------------------- #
# Catálogos y privacidad
# --------------------------------------------------------------------------- #
def test_inicial_no_exige_sesion_y_solo_devuelve_activos(client_sin_sesion, db_session):
    doctor = Doctor(nombre="Dra. Activa", especialidad="Ortodoncia", activo=True)
    inactivo = Doctor(nombre="Dr. Inactivo", especialidad="Cirugía", activo=False)
    activa = ObraSocial(nombre="OS Activa", activa=True)
    inactiva = ObraSocial(nombre="OS Inactiva", activa=False)
    db_session.add_all([doctor, inactivo, activa, inactiva])
    db_session.commit()
    _paciente(db_session, nombre="Paciente Secreto")

    r = client_sin_sesion.get(f"{API}/inicial")

    assert r.status_code == 200, r.text
    datos = r.json()
    assert [d["nombre"] for d in datos["doctores"]] == ["Dra. Activa"]
    assert [o["nombre"] for o in datos["obras_sociales"]] == ["OS Activa"]
    assert set(datos["doctores"][0]) == {"id", "nombre", "especialidad"}
    # Ni el nombre del paciente ni los datos internos del profesional viajan.
    assert "Paciente Secreto" not in r.text
    assert "tipo_pago" not in r.text


def test_disponibilidad_devuelve_solo_horas_libres(client_sin_sesion, doctor, db_session):
    _agenda(db_session, doctor)
    paciente = _paciente(db_session)
    _turno(db_session, doctor, paciente, datetime.combine(FECHA, time(9, 0)))

    r = client_sin_sesion.get(
        f"{API}/disponibilidad", params={"doctor_id": doctor.id, "fecha": FECHA.isoformat()}
    )

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["doctor_id"] == doctor.id
    assert datos["fecha"] == FECHA.isoformat()
    assert datos["horarios"][0] == "09:30"
    assert "09:00" not in datos["horarios"]
    assert datos["horarios"][-1] == "11:30"
    assert set(datos) == {
        "doctor_id", "fecha", "dia", "duracion_minutos", "horarios"
    }
    # Privacidad estricta: ni nombre, ni teléfono, ni motivo del turno ocupado.
    assert "María" not in r.text
    assert "4589" not in r.text
    assert "Control" not in r.text


def test_disponibilidad_rechaza_fechas_pasadas(client_sin_sesion, doctor, db_session):
    ayer = utcnow().date() - timedelta(days=2)
    r = client_sin_sesion.get(
        f"{API}/disponibilidad", params={"doctor_id": doctor.id, "fecha": ayer.isoformat()}
    )
    assert r.status_code == 422
    assert "pasada" in r.json()["detail"].lower()


def test_disponibilidad_oculta_las_horas_que_ya_pasaron(
    client_sin_sesion, doctor, db_session, monkeypatch
):
    """El portal interpreta "ahora" en la hora de pared del consultorio."""
    from app.services import portal_turnos_service as portal

    hoy = portal.ahora_consultorio().date()
    _agenda(db_session, doctor, hoy)
    monkeypatch.setattr(
        portal,
        "ahora_consultorio",
        lambda: datetime.combine(hoy, time(10, 15)),
    )

    r = client_sin_sesion.get(
        f"{API}/disponibilidad", params={"doctor_id": doctor.id, "fecha": hoy.isoformat()}
    )

    assert r.status_code == 200, r.text
    assert r.json()["horarios"][0] == "10:30"
    assert "10:00" not in r.json()["horarios"]


def test_buscar_paciente_enmascara_apellido_y_telefono(client_sin_sesion, db_session):
    obra = _obra_social(db_session)
    _paciente(db_session, obra_social_id=obra.id)

    r = client_sin_sesion.get(f"{API}/buscar-paciente", params={"dni": "30.123.456"})

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["nuevo"] is False
    assert datos["nombre"] == "María"
    assert datos["obra_social"] == "OSDEMO"
    assert datos["obra_social_id"] == obra.id
    assert datos["telefono_enmascarado"] == "***4589"
    assert "González" not in r.text
    assert "3514589" not in r.text


def test_buscar_paciente_inexistente_indica_nuevo(client_sin_sesion):
    r = client_sin_sesion.get(f"{API}/buscar-paciente", params={"dni": "99999999"})
    assert r.status_code == 200
    assert r.json() == {
        "nuevo": True,
        "nombre": None,
        "obra_social_id": None,
        "obra_social": None,
        "telefono_enmascarado": None,
    }


def test_buscar_paciente_con_dni_invalido_devuelve_422(client_sin_sesion):
    r = client_sin_sesion.get(f"{API}/buscar-paciente", params={"dni": "abc"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Reserva
# --------------------------------------------------------------------------- #
def test_reservar_paciente_nuevo_crea_ficha_y_turno(client_sin_sesion, doctor, db_session):
    _agenda(db_session, doctor)
    obra = _obra_social(db_session)

    r = client_sin_sesion.post(
        f"{API}/reservar",
        json=_payload(doctor.id, obra_social_id=obra.id),
    )

    assert r.status_code == 201, r.text
    datos = r.json()
    assert datos["doctor"] == doctor.nombre
    assert datos["hora"] == "10:00"
    assert datos["paciente"] == "Ana Pérez"
    assert datos["obra_social"] == "OSDEMO"
    assert datos["motivo_consulta"] == "Control"
    assert len(datos["codigo_cancelacion"]) == 8
    assert datos["whatsapp_enviado"] is False

    paciente = db_session.query(Paciente).filter(Paciente.dni == "40111222").one()
    assert paciente.nombre == "Ana Pérez"
    assert paciente.telefono == "5493515551234"
    assert paciente.obra_social_id == obra.id
    turno = db_session.query(Turno).one()
    assert turno.paciente_id == paciente.id
    assert turno.estado_turno == EstadoTurno.PENDIENTE
    assert turno.motivo_consulta == "Control"

    # El bloque reservado deja de ofrecerse en el portal.
    libres = client_sin_sesion.get(
        f"{API}/disponibilidad", params={"doctor_id": doctor.id, "fecha": FECHA.isoformat()}
    ).json()["horarios"]
    assert "10:00" not in libres


def test_reservar_paciente_existente_actualiza_telefono_y_obra_social(
    client_sin_sesion, doctor, db_session
):
    _agenda(db_session, doctor)
    anterior = _obra_social(db_session, "OS Anterior")
    nueva = _obra_social(db_session, "OS Nueva")
    paciente = _paciente(db_session, obra_social_id=anterior.id)

    r = client_sin_sesion.post(
        f"{API}/reservar",
        json=_payload(
            doctor.id,
            dni=paciente.dni,
            nombre="María",
            apellido="",
            telefono="351 555 9876",
            obra_social_id=nueva.id,
            motivo="",
        ),
    )

    assert r.status_code == 201, r.text
    db_session.refresh(paciente)
    assert db_session.query(Paciente).count() == 1
    assert paciente.nombre == "María González"  # no se pisa el nombre registrado
    assert paciente.telefono == "5493515559876"
    assert paciente.obra_social_id == nueva.id
    assert r.json()["motivo_consulta"] is None


def test_paciente_conserva_su_obra_social_dada_de_baja(
    client_sin_sesion, doctor, db_session
):
    """Una baja del catálogo no bloquea la reserva de quien ya la tenía."""
    _agenda(db_session, doctor)
    obra = _obra_social(db_session, "OS Baja", activa=False)
    paciente = _paciente(db_session, obra_social_id=obra.id)

    r = client_sin_sesion.post(
        f"{API}/reservar",
        json=_payload(
            doctor.id,
            dni=paciente.dni,
            nombre="María",
            apellido="",
            obra_social_id=obra.id,
        ),
    )

    assert r.status_code == 201, r.text
    assert r.json()["obra_social"] == "OS Baja"


def test_no_se_puede_reservar_dos_veces_el_mismo_horario(client_sin_sesion, doctor, db_session):
    _agenda(db_session, doctor)

    primera = client_sin_sesion.post(f"{API}/reservar", json=_payload(doctor.id, dni="40111222"))
    segunda = client_sin_sesion.post(f"{API}/reservar", json=_payload(doctor.id, dni="40222333"))

    assert primera.status_code == 201, primera.text
    assert segunda.status_code == 409, segunda.text
    assert db_session.query(Turno).count() == 1
    assert db_session.query(Paciente).count() == 1


def test_limite_de_dos_turnos_futuros_por_dni(client_sin_sesion, doctor, db_session):
    _agenda(db_session, doctor)
    paciente = _paciente(db_session)
    base = datetime.combine(FECHA, time(9, 0))
    _turno(db_session, doctor, paciente, base)
    _turno(db_session, doctor, paciente, base + timedelta(minutes=30))

    r = client_sin_sesion.post(
        f"{API}/reservar",
        json=_payload(
            doctor.id,
            dni=paciente.dni,
            nombre="María",
            apellido="",
            hora=time(10, 0),
        ),
    )

    assert r.status_code == 409, r.text
    assert r.json()["limite_turnos"] == 2
    assert "turnos" in r.json()["detail"].lower()
    assert db_session.query(Turno).count() == 2


def test_reservar_con_doctor_inactivo_devuelve_404(client_sin_sesion, db_session):
    inactivo = Doctor(nombre="Dr. Baja", especialidad="Clínica", activo=False)
    db_session.add(inactivo)
    db_session.commit()
    db_session.refresh(inactivo)

    r = client_sin_sesion.post(f"{API}/reservar", json=_payload(inactivo.id))
    assert r.status_code == 404


def test_reservar_requiere_apellido_si_el_dni_es_nuevo(client_sin_sesion, doctor, db_session):
    _agenda(db_session, doctor)
    r = client_sin_sesion.post(
        f"{API}/reservar", json=_payload(doctor.id, apellido="")
    )
    assert r.status_code == 422
    assert "apellido" in r.json()["detail"].lower()


def test_el_bloqueo_del_paciente_compila_en_postgresql():
    """El ``FOR UPDATE`` no puede cubrir el lado nulable del JOIN de la cobertura.

    ``Paciente.obra_social`` se carga con ``LEFT OUTER JOIN``; PostgreSQL rechaza
    un ``FOR UPDATE`` genérico sobre esa consulta. El servicio debe limitar el
    bloqueo a ``pacientes`` (regresión detectada con una base PostgreSQL real).
    """
    stmt = portal_turnos_service._consulta_paciente_por_dni(
        "30123456", bloquear=True
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "LEFT OUTER JOIN obras_sociales" in sql
    assert sql.rstrip().endswith("FOR UPDATE OF pacientes")


def test_reservar_envia_la_confirmacion_si_waha_esta_configurado(
    client_sin_sesion, doctor, db_session, monkeypatch
):
    """Con WAHA configurado sale el texto de confirmación con el código."""
    monkeypatch.setattr(settings, "waha_url", "http://waha.prueba")
    monkeypatch.setattr(settings, "waha_api_key", "clave-de-prueba")
    envios = []

    def enviar_falso(telefono, texto, preview_url=False):
        envios.append((telefono, texto))
        return "wamid.PRUEBA"

    monkeypatch.setattr(whatsapp_service, "enviar_mensaje_texto", enviar_falso)
    _agenda(db_session, doctor)

    r = client_sin_sesion.post(f"{API}/reservar", json=_payload(doctor.id))

    assert r.status_code == 201, r.text
    assert r.json()["whatsapp_enviado"] is True
    assert len(envios) == 1
    telefono, texto = envios[0]
    assert telefono == "5493515551234"
    assert "Código de cancelación" in texto
    assert "/reservar" in texto
    turno = db_session.query(Turno).one()
    assert turno.estado_whatsapp == EstadoWhatsApp.ENVIADO
    assert turno.whatsapp_mensaje_id == "wamid.PRUEBA"


def test_un_fallo_de_whatsapp_no_revierte_la_reserva(
    client_sin_sesion, doctor, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "waha_url", "http://waha.prueba")
    monkeypatch.setattr(settings, "waha_api_key", "clave-de-prueba")

    def enviar_roto(*args, **kwargs):
        raise RuntimeError("WAHA caído")

    monkeypatch.setattr(whatsapp_service, "enviar_mensaje_texto", enviar_roto)
    _agenda(db_session, doctor)

    r = client_sin_sesion.post(f"{API}/reservar", json=_payload(doctor.id))

    assert r.status_code == 201, r.text
    assert r.json()["whatsapp_enviado"] is False
    assert db_session.query(Paciente).count() == 1
    assert db_session.query(Turno).count() == 1
    assert db_session.query(Turno).one().estado_whatsapp == EstadoWhatsApp.FALLIDO



# --------------------------------------------------------------------------- #
# Cancelación
# --------------------------------------------------------------------------- #
def test_cancelar_libera_el_horario_y_es_idempotente(client_sin_sesion, doctor, db_session):
    _agenda(db_session, doctor)
    creada = client_sin_sesion.post(
        f"{API}/reservar", json=_payload(doctor.id, dni="40111222")
    )
    assert creada.status_code == 201, creada.text
    codigo = creada.json()["codigo_cancelacion"]

    antes = client_sin_sesion.get(
        f"{API}/disponibilidad", params={"doctor_id": doctor.id, "fecha": FECHA.isoformat()}
    ).json()["horarios"]
    assert "10:00" not in antes

    r = client_sin_sesion.post(
        f"{API}/cancelar", json={"dni": "40111222", "codigo": codigo}
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado_turno"] == "CANCELADO"
    assert r.json()["ya_cancelado"] is False

    despues = client_sin_sesion.get(
        f"{API}/disponibilidad", params={"doctor_id": doctor.id, "fecha": FECHA.isoformat()}
    ).json()["horarios"]
    assert "10:00" in despues

    # Repetir con el mismo código no falla: informa que ya estaba cancelado.
    otra = client_sin_sesion.post(
        f"{API}/cancelar", json={"dni": "40111222", "codigo": codigo}
    )
    assert otra.status_code == 200
    assert otra.json()["ya_cancelado"] is True


def test_cancelar_tambien_acepta_el_numero_de_turno(client_sin_sesion, doctor, db_session):
    _agenda(db_session, doctor)
    creada = client_sin_sesion.post(
        f"{API}/reservar", json=_payload(doctor.id, dni="40111222")
    ).json()

    r = client_sin_sesion.post(
        f"{API}/cancelar", json={"dni": "40111222", "codigo": str(creada["turno_id"])}
    )
    assert r.status_code == 200, r.text
    assert r.json()["turno_id"] == creada["turno_id"]


def test_cancelar_dentro_de_las_12_horas_devuelve_409(client_sin_sesion, db_session):
    paciente = _paciente(db_session)
    doctor = Doctor(nombre="Dr. Próximo", especialidad="Clínica", activo=True)
    db_session.add(doctor)
    db_session.commit()
    db_session.refresh(doctor)
    turno = _turno(db_session, doctor, paciente, utcnow() + timedelta(hours=3))
    codigo = codigo_cancelacion(turno.id, paciente.dni)

    r = client_sin_sesion.post(
        f"{API}/cancelar", json={"dni": paciente.dni, "codigo": codigo}
    )

    assert r.status_code == 409, r.text
    assert "12 horas" in r.json()["detail"]
    db_session.refresh(turno)
    assert turno.estado_turno == EstadoTurno.PENDIENTE


def test_cancelar_con_dni_o_codigo_incorrecto_devuelve_404(client_sin_sesion, db_session):
    paciente = _paciente(db_session)
    doctor = Doctor(nombre="Dr. Turnos", especialidad="Clínica", activo=True)
    db_session.add(doctor)
    db_session.commit()
    db_session.refresh(doctor)
    turno = _turno(
        db_session, doctor, paciente, datetime.combine(FECHA, HORA)
    )
    codigo = codigo_cancelacion(turno.id, paciente.dni)

    otro_dni = client_sin_sesion.post(
        f"{API}/cancelar", json={"dni": "99999999", "codigo": codigo}
    )
    mal_codigo = client_sin_sesion.post(
        f"{API}/cancelar", json={"dni": paciente.dni, "codigo": "00000000"}
    )

    assert otro_dni.status_code == 404
    assert mal_codigo.status_code == 404
    # El mensaje es genérico: no confirma que el turno exista.
    assert "no encontramos" in mal_codigo.json()["detail"].lower()
    db_session.refresh(turno)
    assert turno.estado_turno == EstadoTurno.PENDIENTE


# --------------------------------------------------------------------------- #
# Rate limiting, página y contrato público
# --------------------------------------------------------------------------- #
def test_rate_limit_por_ip_devuelve_429_con_retry_after(client_sin_sesion, monkeypatch):
    monkeypatch.setattr(limitador_portal, "maximo", 2)
    limitador_portal.reiniciar()

    primera = client_sin_sesion.get(f"{API}/inicial")
    segunda = client_sin_sesion.get(f"{API}/inicial")
    tercera = client_sin_sesion.get(f"{API}/inicial")

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert tercera.status_code == 429
    assert int(tercera.headers["retry-after"]) >= 1
    assert "demasiadas" in tercera.json()["detail"].lower()


def test_pagina_reservar_y_sus_recursos_son_publicos(client_sin_sesion):
    pagina = client_sin_sesion.get("/reservar")
    css = client_sin_sesion.get("/static/reservar.css")
    js = client_sin_sesion.get("/static/reservar.js")

    assert pagina.status_code == 200
    assert "text/html" in pagina.headers["content-type"]
    assert "Reservá tu" in pagina.text
    assert "/static/reservar.js" in pagina.text
    assert 'id="rp-paso-1"' in pagina.text
    assert 'id="rp-paso-4"' in pagina.text
    assert 'id="panel-cancelar"' in pagina.text

    assert css.status_code == 200
    assert "--rp-brand-500" in css.text
    assert js.status_code == 200
    assert "/portal-turnos" in js.text
    assert "buscar-paciente" in js.text


def test_el_panel_tiene_el_boton_para_copiar_el_link(client_sin_sesion):
    html = client_sin_sesion.get("/").text
    js = client_sin_sesion.get("/static/app.js").text

    assert 'id="copiar-link-turnos"' in html
    assert "Copiar link de turnos para pacientes" in html
    assert "copiarLinkTurnos" in js
    assert "/reservar" in js


def test_el_portal_declara_las_cinco_rutas_publicas():
    from app.main import app

    rutas = app.openapi()["paths"]
    base = "/api/v1/portal-turnos"
    assert set(rutas[base + "/inicial"]) == {"get"}
    assert set(rutas[base + "/disponibilidad"]) == {"get"}
    assert set(rutas[base + "/buscar-paciente"]) == {"get"}
    assert set(rutas[base + "/reservar"]) == {"post"}
    assert set(rutas[base + "/cancelar"]) == {"post"}




