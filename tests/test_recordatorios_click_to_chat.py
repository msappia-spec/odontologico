"""Pruebas de los recordatorios por enlace Click-to-Chat (wa.me).

Cubren tres capas:

1. **Helpers del servicio** (``app.services.whatsapp_service``): normalización de
   teléfonos, fecha "en letras" y artículo del profesional.
2. **Endpoint** ``GET /api/v1/whatsapp/recordatorios-pendientes``: ventana de
   días, exclusiones (cancelados / ya enviados) y enlace con URL Encoding.
3. **Flujo completo**: abrir el enlace marca el turno como ``ENVIADO`` con
   ``PATCH /api/v1/turnos/{id}`` y el turno desaparece de la lista.
"""

from datetime import datetime, timedelta
from urllib.parse import unquote

from app.core.datetime_utils import utcnow
from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.turno import Turno
from app.services import whatsapp_service
from app.services.whatsapp_service import (
    DIAS_SEMANA,
    MESES,
    formatear_fecha_recordatorio,
    normalizar_telefono_whatsapp,
    profesional_con_articulo,
)

API = "/api/v1"


def _crear_turno(
    client, doctor, paciente, *, dias: int = 3, hora: int = 10, minutos: int = 30
) -> dict:
    """Crea un turno a ``dias`` de hoy con el endpoint real (como el resto de tests)."""
    inicio = (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0
    )
    payload = {
        "paciente_id": paciente.id,
        "doctor_id": doctor.id,
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": (inicio + timedelta(minutes=minutos)).isoformat(),
    }
    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1) Helpers del servicio
# ---------------------------------------------------------------------------


def test_normalizar_telefono_whatsapp_aplica_codigo_de_pais():
    casos = {
        "+54 9 11 2233-4455": "5491122334455",
        "5491122334455": "5491122334455",
        "(011) 2233-4455": "5491122334455",
        "11 2233 4455": "5491122334455",
        "005491122334455": "5491122334455",
        # Ya trae el código de país sin el "9" de móvil: se respeta tal cual.
        "541122334455": "541122334455",
        # Sin dígitos no hay número posible.
        "": "",
        "   ": "",
        "s/d": "",
    }
    for entrada, esperado in casos.items():
        assert normalizar_telefono_whatsapp(entrada) == esperado, entrada


def test_normalizar_telefono_whatsapp_acepta_codigo_de_pais_explicito():
    assert normalizar_telefono_whatsapp("11 2233 4455", codigo_pais="54") == "541122334455"
    assert normalizar_telefono_whatsapp("1122334455", codigo_pais="54") == "541122334455"


def test_profesional_con_articulo():
    assert profesional_con_articulo("Dra. Gómez") == "la Dra. Gómez"
    assert profesional_con_articulo("Dr. House") == "el Dr. House"
    assert profesional_con_articulo("Doctor Ramírez") == "el Doctor Ramírez"
    # Sin tratamiento no se agrega artículo (evita "con el Juan").
    assert profesional_con_articulo("Juan") == "Juan"
    assert profesional_con_articulo("") == ""
    assert profesional_con_articulo(None) == ""


def test_formatear_fecha_recordatorio_en_letras():
    viernes_16 = datetime(2026, 10, 16, 10, 0)
    assert viernes_16.weekday() == 4  # sanity check del caso de prueba
    assert formatear_fecha_recordatorio(viernes_16) == "viernes 16 de octubre de 2026"

    # El nombre coincide con las tablas del servicio (sin depender del locale).
    otro = datetime(2026, 3, 9, 15, 30)
    assert formatear_fecha_recordatorio(otro) == (
        f"{DIAS_SEMANA[otro.weekday()]} 9 de {MESES[otro.month - 1]} de 2026"
    )


def test_construir_mensaje_y_enlace_no_rompen_la_url(db_session, doctor, paciente):
    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=datetime(2026, 10, 16, 10, 0),
        fecha_hora_fin=datetime(2026, 10, 16, 10, 30),
    )
    db_session.add(turno)
    db_session.commit()
    db_session.refresh(turno)

    mensaje = whatsapp_service.construir_mensaje_recordatorio(turno)
    assert mensaje.startswith("Hola Paciente Demo, te recordamos tu turno el día")
    assert "viernes 16 de octubre de 2026 a las 10:00" in mensaje
    assert "con el Dr. House" in mensaje
    assert mensaje.endswith("¡Muchas gracias!")

    enlace = whatsapp_service.construir_enlace_whatsapp(turno)
    assert enlace.startswith("https://wa.me/5491122334455?text=Hola%20Paciente%20Demo%2C")
    # Espacios y acentos viajan codificados (URL Encoding) para no romper la URL.
    texto_url = enlace.split("?text=", 1)[1]
    assert " " not in texto_url
    assert "á" not in enlace
    # El texto se recupera íntegro al des-codificar.
    assert unquote(texto_url) == mensaje


def test_construir_enlace_whatsapp_sin_telefono(db_session, doctor, paciente):
    paciente.telefono = ""
    db_session.commit()

    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=datetime(2026, 10, 16, 11, 0),
        fecha_hora_fin=datetime(2026, 10, 16, 11, 30),
    )
    db_session.add(turno)
    db_session.commit()

    assert whatsapp_service.normalizar_telefono_whatsapp(turno.paciente.telefono) == ""
    assert whatsapp_service.construir_enlace_whatsapp(turno) == ""


# ---------------------------------------------------------------------------
# 2) Endpoint GET /whatsapp/recordatorios-pendientes
# ---------------------------------------------------------------------------


def test_endpoint_lista_pendientes_ordenados_con_enlace(client, doctor, paciente):
    lejano = _crear_turno(client, doctor, paciente, dias=2, hora=11)
    cercano = _crear_turno(client, doctor, paciente, dias=1, hora=9)

    r = client.get(f"{API}/whatsapp/recordatorios-pendientes")
    assert r.status_code == 200, r.text
    items = r.json()

    # Orden cronológico y sólo los turnos dentro de la ventana por defecto (3 días).
    assert [i["turno_id"] for i in items] == [cercano["id"], lejano["id"]]

    primero = items[0]
    assert primero["paciente"] == "Paciente Demo"
    assert primero["paciente_id"] == paciente.id
    assert primero["doctor"] == "Dr. House"
    assert primero["estado_turno"] == EstadoTurno.PENDIENTE.value
    assert primero["estado_whatsapp"] == EstadoWhatsApp.PENDIENTE_ENVIO.value
    assert primero["dias_restantes"] == 1
    assert primero["telefono"] == "+5491122334455"
    assert primero["telefono_whatsapp"] == "5491122334455"
    assert primero["enlace_whatsapp"].startswith("https://wa.me/5491122334455?text=")
    assert unquote(primero["enlace_whatsapp"].split("?text=", 1)[1]) == primero["mensaje"]
    assert "el Dr. House" in primero["mensaje"]


def test_endpoint_respeta_la_ventana_de_dias(client, doctor, paciente):
    _crear_turno(client, doctor, paciente, dias=2, hora=10)
    _crear_turno(client, doctor, paciente, dias=6, hora=10)

    corto = client.get(f"{API}/whatsapp/recordatorios-pendientes?dias=3").json()
    largo = client.get(f"{API}/whatsapp/recordatorios-pendientes?dias=7").json()

    assert len(corto) == 1
    assert corto[0]["dias_restantes"] == 2
    assert len(largo) == 2
    assert [i["dias_restantes"] for i in largo] == [2, 6]


def test_endpoint_excluye_cancelados_y_ya_enviados(client, doctor, paciente, db_session):
    cancelado = _crear_turno(client, doctor, paciente, dias=1, hora=8)
    enviado = _crear_turno(client, doctor, paciente, dias=2, hora=12)
    pendiente = _crear_turno(client, doctor, paciente, dias=2, hora=13)

    db_session.get(Turno, cancelado["id"]).estado_turno = EstadoTurno.CANCELADO
    db_session.get(Turno, enviado["id"]).estado_whatsapp = EstadoWhatsApp.ENVIADO
    db_session.commit()

    items = client.get(f"{API}/whatsapp/recordatorios-pendientes").json()
    assert [i["turno_id"] for i in items] == [pendiente["id"]]


def test_endpoint_sin_telefono_no_arma_enlace(client, doctor, paciente, db_session):
    paciente.telefono = ""
    db_session.commit()

    _crear_turno(client, doctor, paciente, dias=1, hora=16)
    items = client.get(f"{API}/whatsapp/recordatorios-pendientes").json()

    assert len(items) == 1
    assert items[0]["telefono_whatsapp"] == ""
    assert items[0]["enlace_whatsapp"] == ""


def test_endpoint_valida_el_rango_de_dias(client):
    base = f"{API}/whatsapp/recordatorios-pendientes"
    assert client.get(f"{base}?dias=0").status_code == 422
    assert client.get(f"{base}?dias=31").status_code == 422
    assert client.get(f"{base}?dias=1").status_code == 200


def test_endpoint_sin_turnos_devuelve_lista_vacia(client):
    r = client.get(f"{API}/whatsapp/recordatorios-pendientes")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# 3) Flujo completo: abrir el enlace marca el turno como ENVIADO
# ---------------------------------------------------------------------------


def test_flujo_marca_enviado_y_el_turno_sale_de_la_lista(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=1, hora=14)

    pendientes = client.get(f"{API}/whatsapp/recordatorios-pendientes").json()
    assert [i["turno_id"] for i in pendientes] == [turno["id"]]

    # Es lo que hace el panel al pulsar el botón verde de WhatsApp.
    r = client.patch(f"{API}/turnos/{turno['id']}", json={"estado_whatsapp": "ENVIADO"})
    assert r.status_code == 200, r.text
    assert r.json()["estado_whatsapp"] == "ENVIADO"
    # El estado clínico del turno no se toca.
    assert r.json()["estado_turno"] == EstadoTurno.PENDIENTE.value

    assert client.get(f"{API}/whatsapp/recordatorios-pendientes").json() == []


def test_servicio_usa_la_referencia_de_tiempo_inyectada(db_session, doctor, paciente):
    """``ahora`` permite fijar la ventana sin depender del reloj del sistema."""
    base = datetime(2026, 4, 6, 8, 0)  # lunes 06/04/2026 08:00

    def _turno(dias: int, hora: int) -> Turno:
        turno = Turno(
            paciente_id=paciente.id,
            doctor_id=doctor.id,
            fecha_hora_inicio=base + timedelta(days=dias, hours=hora),
            fecha_hora_fin=base + timedelta(days=dias, hours=hora, minutes=30),
        )
        db_session.add(turno)
        return turno

    un_dia = _turno(1, 0)
    dos_dias = _turno(2, 0)
    diez_dias = _turno(10, 0)
    db_session.commit()

    items = whatsapp_service.listar_recordatorios_click_to_chat(
        db_session, dias=3, ahora=base
    )
    assert [i.turno_id for i in items] == [un_dia.id, dos_dias.id]
    assert [i.dias_restantes for i in items] == [1, 2]
    assert items[0].enlace_whatsapp.startswith("https://wa.me/5491122334455?text=")

    solo_uno = whatsapp_service.listar_recordatorios_click_to_chat(
        db_session, dias=1, ahora=base
    )
    assert [i.turno_id for i in solo_uno] == [un_dia.id]

    desde_tres = whatsapp_service.listar_recordatorios_click_to_chat(
        db_session, dias=15, ahora=base
    )
    assert [i.turno_id for i in desde_tres] == [un_dia.id, dos_dias.id, diez_dias.id]
