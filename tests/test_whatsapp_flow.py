"""Pruebas del flujo de WhatsApp sobre WAHA: recordatorios + respuestas de texto."""

import logging
from datetime import timedelta

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.models.conversacion_whatsapp import ConversacionWhatsApp
from app.models.enums import (
    EstadoConversacionWhatsApp,
    EstadoTurno,
)
from app.models.turno import Turno

API = "/api/v1"
TELEFONO = "5491122334455"


def _crear_turno(client, doctor, paciente, *, dias: int = 3, hora: int = 10) -> dict:
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


def _enviar_texto(client, texto: str, *, telefono: str = TELEFONO) -> dict:
    r = client.post(
        f"{API}/whatsapp/webhook", json=_webhook(texto, telefono=telefono)
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_tarea_envia_recordatorio_y_actualiza_estado(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=3, hora=10)

    r = client.post(f"{API}/tareas/enviar-recordatorios")
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["dias_antelacion"] == 3
    assert data["evaluados"] == 1
    assert data["enviados"] == 1
    assert data["fallidos"] == 0

    detalle = data["detalles"][0]
    assert detalle["turno_id"] == turno["id"]
    assert detalle["resultado"] == "ENVIADO"
    assert detalle["whatsapp_mensaje_id"].startswith("waha.SIMULADO.")

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_whatsapp"] == "ENVIADO"
    assert actualizado["whatsapp_mensaje_id"] == detalle["whatsapp_mensaje_id"]


def test_tarea_rechaza_ejecucion_sin_el_secreto(client, doctor, paciente):
    _crear_turno(client, doctor, paciente, dias=3, hora=10)

    r = client._client.post(f"{API}/tareas/enviar-recordatorios")

    assert r.status_code == 401


def test_cron_autorizado_usa_el_mismo_flujo(client, doctor, paciente):
    _crear_turno(client, doctor, paciente, dias=3, hora=10)

    r = client.get(
        f"{API}/tareas/enviar-recordatorios",
        headers={"Authorization": f"Bearer {settings.cron_secret}"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["enviados"] == 1


def test_tarea_registra_log_de_envio(client, doctor, paciente, caplog):
    _crear_turno(client, doctor, paciente, dias=3, hora=11)

    with caplog.at_level(logging.INFO, logger="app.services.whatsapp"):
        client.post(f"{API}/tareas/enviar-recordatorios")

    mensajes = [rec.getMessage() for rec in caplog.records]
    assert any("Enviando WhatsApp a" in m for m in mensajes)


def test_tarea_ignora_turnos_de_otro_dia(client, doctor, paciente):
    _crear_turno(client, doctor, paciente, dias=5, hora=10)

    data = client.post(f"{API}/tareas/enviar-recordatorios").json()
    assert data["evaluados"] == 0
    assert data["enviados"] == 0


def test_tarea_ignora_turnos_no_pendientes(client, doctor, paciente, db_session):
    _crear_turno(client, doctor, paciente, dias=3, hora=13)

    turno = db_session.query(Turno).one()
    turno.estado_turno = EstadoTurno.CONFIRMADO
    db_session.commit()

    data = client.post(f"{API}/tareas/enviar-recordatorios").json()
    assert data["evaluados"] == 0


def test_la_tarea_guarda_el_turno_pendiente_de_confirmacion(
    client, db_session, doctor, paciente
):
    turno = _crear_turno(client, doctor, paciente, dias=3, hora=12)
    client.post(f"{API}/tareas/enviar-recordatorios")

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.telefono == TELEFONO
    assert conversacion.estado == EstadoConversacionWhatsApp.CONFIRMACION_TURNO
    assert conversacion.turno_id == turno["id"]


def test_webhook_confirma_turno_con_texto(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=3, hora=14)
    client.post(f"{API}/tareas/enviar-recordatorios")

    data = _enviar_texto(client, "1")

    assert data["tipo"] == "CONFIRMACION"
    assert data["turno_id"] == turno["id"]
    assert data["estado_turno"] == "CONFIRMADO"
    assert data["estado_whatsapp"] == "RESPONDIDO"


def test_webhook_cancela_turno_con_texto(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=3, hora=15)
    client.post(f"{API}/tareas/enviar-recordatorios")

    data = _enviar_texto(client, "no")

    assert data["estado_turno"] == "CANCELADO"

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == "CANCELADO"
    assert actualizado["estado_whatsapp"] == "RESPONDIDO"


def test_webhook_sin_confirmacion_pendiente_muestra_el_menu(client):
    data = _enviar_texto(client, "hola")

    assert data["tipo"] == "BOT"
    assert data["atencion_manual"] is False
    assert "Precio de la consulta" in data["respuesta"]


def test_webhook_ignora_mensajes_propios_y_de_grupo(client):
    propio = _webhook("1")
    propio["payload"]["fromMe"] = True
    r = client.post(f"{API}/whatsapp/webhook", json=propio)
    assert r.status_code == 200
    assert r.json()["tipo"] == "IGNORADO"

    grupo = _webhook("1")
    grupo["payload"]["from"] = "120363000000000000@g.us"
    r = client.post(f"{API}/whatsapp/webhook", json=grupo)
    assert r.status_code == 200
    assert r.json()["tipo"] == "IGNORADO"
