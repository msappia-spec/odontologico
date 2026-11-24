"""Pruebas del envío automático de recordatorios desde el panel.

Cubren el circuito completo que pidió la secretaría:

1. ``POST /whatsapp/recordatorios-pendientes/{turno_id}/enviar``: el servidor
   manda el recordatorio con la API de WAHA (real o simulado), deja el turno en
   ``ENVIADO`` y el teléfono esperando la respuesta.
2. El paciente contesta ``1`` / ``si`` → el turno queda **CONFIRMADO**; ``2`` /
   ``no`` → **CANCELADO**. El estado se ve en ``GET /turnos/{id}`` (lo que
   muestra la app).
3. ``POST /whatsapp/recordatorios-pendientes/{turno_id}/enviado``: cierra el
   envío manual (Click-to-Chat) y registra la misma confirmación pendiente, así
   la respuesta del paciente también actualiza el turno.
"""

from datetime import timedelta

import httpx

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.models.conversacion_whatsapp import ConversacionWhatsApp
from app.models.enums import (
    EstadoConversacionWhatsApp,
    EstadoTurno,
    EstadoWhatsApp,
)
from app.models.turno import Turno
from app.services import whatsapp_service

API = "/api/v1"
TELEFONO = "5491122334455"
CHAT_ID = f"{TELEFONO}@c.us"


def _crear_turno(client, doctor, paciente, *, dias: int = 2, hora: int = 9) -> dict:
    """Crea un turno a ``dias`` de hoy con el endpoint real."""
    inicio = (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0
    )
    payload = {
        "paciente_id": paciente.id,
        "doctor_id": doctor.id,
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": (inicio + timedelta(minutes=30)).isoformat(),
    }
    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _webhook(texto: str, *, telefono: str = TELEFONO) -> dict:
    return {
        "event": "message",
        "session": "default",
        "payload": {
            "id": f"waha.ENTRANTE.{texto}",
            "from": f"{telefono}@c.us",
            "fromMe": False,
            "body": texto,
            "type": "chat",
        },
    }


def _responder(client, texto: str) -> dict:
    r = client.post(f"{API}/whatsapp/webhook", json=_webhook(texto))
    assert r.status_code == 200, r.text
    return r.json()


class _RespuestaHttpFalsa:
    """Doble mínimo de ``httpx.Response`` para el envío real a WAHA."""

    def __init__(self, data: dict):
        self._data = data
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data

# ---------------------------------------------------------------------------
# 1) Envío automático (API de WAHA) desde el panel
# ---------------------------------------------------------------------------


def test_envio_automatico_marca_enviado_y_espera_la_respuesta(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(client, doctor, paciente, dias=2, hora=9)

    r = client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["turno_id"] == turno["id"]
    assert data["resultado"] == "ENVIADO"
    assert data["simulado"] is True
    assert data["whatsapp_mensaje_id"].startswith("waha.SIMULADO.")
    assert data["telefono"] == paciente.telefono

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_whatsapp"] == EstadoWhatsApp.ENVIADO.value
    assert actualizado["estado_turno"] == EstadoTurno.PENDIENTE.value
    assert actualizado["whatsapp_mensaje_id"] == data["whatsapp_mensaje_id"]

    # La conversación queda esperando el "1"/"si" o "2"/"no" del paciente.
    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.telefono == TELEFONO
    assert conversacion.estado == EstadoConversacionWhatsApp.CONFIRMACION_TURNO
    assert conversacion.turno_id == turno["id"]
    assert conversacion.whatsapp_mensaje_id == data["whatsapp_mensaje_id"]

    # Y el turno desaparece de la lista de pendientes.
    pendientes = client.get(f"{API}/whatsapp/recordatorios-pendientes").json()
    assert pendientes == []


def test_envio_automatico_real_usa_la_api_de_waha(
    client, doctor, paciente, monkeypatch
):
    """Con las credenciales cargadas el mensaje sale por ``/api/sendText``."""
    monkeypatch.setattr(settings, "waha_url", "http://turnos_waha:3000")
    monkeypatch.setattr(settings, "waha_api_key", "turnos2026")
    monkeypatch.setattr(settings, "waha_session", "consultorio")

    llamadas: list = []
    pausas: list = []

    def _post_falso(url: str, **kwargs):
        llamadas.append({"url": url, **kwargs})
        return _RespuestaHttpFalsa({"id": "true_5491122334455@c.us_ABC"})

    monkeypatch.setattr(whatsapp_service.httpx, "post", _post_falso)
    monkeypatch.setattr(whatsapp_service.time, "sleep", pausas.append)

    turno = _crear_turno(client, doctor, paciente, dias=2, hora=10)

    data = client.post(
        f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar"
    ).json()

    assert data["resultado"] == "ENVIADO"
    assert data["simulado"] is False
    assert data["whatsapp_mensaje_id"] == "true_5491122334455@c.us_ABC"
    assert pausas == [whatsapp_service.PAUSA_ENVIO_WAHA]

    assert len(llamadas) == 1
    assert llamadas[0]["url"] == "http://turnos_waha:3000/api/sendText"
    assert llamadas[0]["json"]["chatId"] == CHAT_ID
    assert llamadas[0]["json"]["session"] == "consultorio"
    assert "te recordamos tu turno" in llamadas[0]["json"]["text"]
    assert "Respondé *1* (o *SI*)" in llamadas[0]["json"]["text"]
    assert "Respondé *2* (o *NO*)" in llamadas[0]["json"]["text"]

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_whatsapp"] == EstadoWhatsApp.ENVIADO.value


def test_envio_automatico_fallido_marca_el_turno_en_fallido(
    client, db_session, doctor, paciente, monkeypatch
):
    monkeypatch.setattr(settings, "waha_url", "http://turnos_waha:3000")
    monkeypatch.setattr(settings, "waha_api_key", "turnos2026")

    request = httpx.Request("POST", "http://turnos_waha:3000/api/sendText")
    respuesta = httpx.Response(
        400, request=request, json={"message": "Sesión no iniciada"}
    )
    monkeypatch.setattr(whatsapp_service.httpx, "post", lambda *_a, **_k: respuesta)
    monkeypatch.setattr(whatsapp_service.time, "sleep", lambda _s: None)

    turno = _crear_turno(client, doctor, paciente, dias=2, hora=11)

    data = client.post(
        f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar"
    ).json()

    assert data["resultado"] == "FALLIDO"
    assert "Sesión no iniciada" in data["error"]
    assert data["simulado"] is False

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_whatsapp"] == EstadoWhatsApp.FALLIDO.value
    # No quedó ninguna confirmación esperando una respuesta que no existe.
    assert db_session.query(ConversacionWhatsApp).count() == 0



def test_envio_automatico_sin_telefono_no_envia(client, db_session, doctor, paciente):
    paciente.telefono = ""
    db_session.commit()

    turno = _crear_turno(client, doctor, paciente, dias=2, hora=12)

    r = client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")

    assert r.status_code == 422, r.text
    assert "teléfono" in r.json()["detail"].lower()

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    # El turno sigue pendiente: la secretaría lo puede avisar por otro medio.
    assert actualizado["estado_whatsapp"] == EstadoWhatsApp.PENDIENTE_ENVIO.value


def test_envio_automatico_de_un_turno_cancelado_devuelve_409(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(client, doctor, paciente, dias=2, hora=13)
    turno_db = db_session.get(Turno, turno["id"])
    turno_db.estado_turno = EstadoTurno.CANCELADO
    db_session.commit()

    r = client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")

    assert r.status_code == 409, r.text


def test_envio_automatico_de_un_turno_inexistente_devuelve_404(client):
    r = client.post(f"{API}/whatsapp/recordatorios-pendientes/9999/enviar")

    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 2) La respuesta del paciente actualiza el turno en la app
# ---------------------------------------------------------------------------


def test_la_respuesta_si_confirma_el_turno(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=2, hora=14)
    client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")

    data = _responder(client, "si")

    assert data["tipo"] == "CONFIRMACION"
    assert data["turno_id"] == turno["id"]
    assert data["estado_turno"] == EstadoTurno.CONFIRMADO.value
    assert data["estado_whatsapp"] == EstadoWhatsApp.RESPONDIDO.value

    # Es lo que muestra la app (agenda y panel principal).
    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == EstadoTurno.CONFIRMADO.value
    assert actualizado["estado_whatsapp"] == EstadoWhatsApp.RESPONDIDO.value


def test_la_respuesta_no_cancela_el_turno(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=2, hora=15)
    client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")

    data = _responder(client, "2")

    assert data["tipo"] == "CONFIRMACION"
    assert data["boton"] == "cancelar_turno"
    assert data["estado_turno"] == EstadoTurno.CANCELADO.value

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == EstadoTurno.CANCELADO.value
    assert actualizado["estado_whatsapp"] == EstadoWhatsApp.RESPONDIDO.value

    # El turno cancelado no vuelve a la lista de pendientes.
    pendientes = client.get(f"{API}/whatsapp/recordatorios-pendientes").json()
    assert pendientes == []



# ---------------------------------------------------------------------------
# 3) Envío manual (Click-to-Chat): también deja la confirmación pendiente
# ---------------------------------------------------------------------------


def test_marcar_enviado_a_mano_registra_la_confirmacion(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(client, doctor, paciente, dias=2, hora=16)

    r = client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviado")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["estado_whatsapp"] == EstadoWhatsApp.ENVIADO.value
    assert data["estado_turno"] == EstadoTurno.PENDIENTE.value
    assert data["confirmacion_pendiente"] is True

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.estado == EstadoConversacionWhatsApp.CONFIRMACION_TURNO
    assert conversacion.turno_id == turno["id"]

    # Y el "1" del paciente confirma el turno que se mandó a mano.
    data = _responder(client, "1")
    assert data["estado_turno"] == EstadoTurno.CONFIRMADO.value

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == EstadoTurno.CONFIRMADO.value


def test_marcar_enviado_a_mano_no_reabre_una_respuesta_ya_recibida(
    client, doctor, paciente
):
    turno = _crear_turno(client, doctor, paciente, dias=2, hora=17)
    client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviar")
    assert _responder(client, "si")["estado_turno"] == EstadoTurno.CONFIRMADO.value

    data = client.post(
        f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviado"
    ).json()

    assert data["estado_turno"] == EstadoTurno.CONFIRMADO.value
    assert data["estado_whatsapp"] == EstadoWhatsApp.RESPONDIDO.value
    assert data["confirmacion_pendiente"] is False


def test_marcar_enviado_a_mano_de_un_turno_cancelado_devuelve_409(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(client, doctor, paciente, dias=2, hora=18)
    turno_db = db_session.get(Turno, turno["id"])
    turno_db.estado_turno = EstadoTurno.CANCELADO
    db_session.commit()

    r = client.post(f"{API}/whatsapp/recordatorios-pendientes/{turno['id']}/enviado")

    assert r.status_code == 409, r.text

