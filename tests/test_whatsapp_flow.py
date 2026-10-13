"""Pruebas del flujo de WhatsApp: tarea programada de recordatorios + webhook."""

import logging
from datetime import timedelta

from app.core.datetime_utils import utcnow
from app.models.enums import EstadoTurno
from app.models.turno import Turno

API = "/api/v1"


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


def _webhook(mensaje_contextual_id: str, boton_id: str, title: str = "Botón") -> dict:
    """Construye un payload de webhook simulado (respuesta de botón)."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "5491122334455",
                                    "id": "wamid.ENTRANTE.123",
                                    "timestamp": "1712345678",
                                    "type": "interactive",
                                    "context": {
                                        "from": "5491100000000",
                                        "id": mensaje_contextual_id,
                                    },
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": boton_id, "title": title},
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


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
    assert detalle["whatsapp_mensaje_id"].startswith("wamid.SIMULADO.")

    # El turno quedó con estado_whatsapp ENVIADO y con el wamid persistido.
    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_whatsapp"] == "ENVIADO"
    assert actualizado["whatsapp_mensaje_id"] == detalle["whatsapp_mensaje_id"]


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


def test_webhook_confirma_turno(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=3, hora=14)
    resp = client.post(f"{API}/tareas/enviar-recordatorios").json()
    wamid = resp["detalles"][0]["whatsapp_mensaje_id"]

    r = client.post(
        f"{API}/whatsapp/webhook",
        json=_webhook(wamid, "confirmar_turno", "Confirmar"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["turno_id"] == turno["id"]
    assert data["estado_turno"] == "CONFIRMADO"
    assert data["estado_whatsapp"] == "RESPONDIDO"


def test_webhook_cancela_turno(client, doctor, paciente):
    turno = _crear_turno(client, doctor, paciente, dias=3, hora=15)
    resp = client.post(f"{API}/tareas/enviar-recordatorios").json()
    wamid = resp["detalles"][0]["whatsapp_mensaje_id"]

    r = client.post(
        f"{API}/whatsapp/webhook",
        json=_webhook(wamid, "cancelar_turno", "Cancelar"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado_turno"] == "CANCELADO"

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == "CANCELADO"
    assert actualizado["estado_whatsapp"] == "RESPONDIDO"


def test_webhook_turno_no_encontrado_devuelve_404(client):
    r = client.post(
        f"{API}/whatsapp/webhook",
        json=_webhook("wamid.INEXISTENTE.999", "confirmar_turno"),
    )
    assert r.status_code == 404


def test_webhook_sin_boton_devuelve_400(client):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "5491122334455", "id": "wamid.X", "type": "text"}
                            ]
                        }
                    }
                ],
            }
        ],
    }
    r = client.post(f"{API}/whatsapp/webhook", json=payload)
    assert r.status_code == 400


def test_webhook_boton_desconocido_devuelve_400(client, doctor, paciente):
    _crear_turno(client, doctor, paciente, dias=3, hora=16)
    resp = client.post(f"{API}/tareas/enviar-recordatorios").json()
    wamid = resp["detalles"][0]["whatsapp_mensaje_id"]

    r = client.post(
        f"{API}/whatsapp/webhook",
        json=_webhook(wamid, "boton_raro", "No sé"),
    )
    assert r.status_code == 400
