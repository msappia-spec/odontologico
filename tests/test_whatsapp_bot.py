"""Pruebas del bot de WhatsApp sobre WAHA y del webhook de entrada.

Cubren configuración, formato argentino del ``chatId``, envío real/simulado,
filtros del webhook, menú numerado y confirmación de turnos con ``1``/``si`` y
``2``/``no`` con estado persistido en ``conversaciones_whatsapp``.
"""

import logging
from datetime import datetime, timedelta

import httpx
import pytest

from app.core.config import Settings, settings
from app.core.datetime_utils import utcnow
from app.core.exceptions import WhatsAppEnvioError
from app.core.whatsapp_utils import (
    construir_chat_id_waha,
    normalizar_destinatario_waha,
    normalizar_telefono_waha,
    numero_de_jid,
)
from app.models.conversacion_whatsapp import ConversacionWhatsApp
from app.models.enums import (
    EstadoConversacionWhatsApp,
    EstadoTurno,
    EstadoWhatsApp,
)
from app.models.turno import Turno
from app.services import (
    whatsapp_bot_service,
    whatsapp_conversacion_service,
    whatsapp_service,
)

API = "/api/v1"
TELEFONO = "5491122334455"
CHAT_ID = f"{TELEFONO}@c.us"
WAMID_ENTRANTE = "waha.ENTRANTE.TEXTO.1"
#: Identificador de privacidad con el que WAHA puede entregar el remitente.
LID = "50783827554549@lid"
#: Número real del mismo paciente, tal como lo publica WAHA en ``_data.key``.
TELEFONO_REAL = "5493515945075"
JID_REAL = f"{TELEFONO_REAL}@s.whatsapp.net"


@pytest.fixture()
def envios(monkeypatch) -> list:
    """Intercepta los envíos del bot y registra ``{telefono, texto}``."""
    registrados: list = []

    def _envio_falso(telefono: str, texto: str, **_kwargs) -> str:
        registrados.append({"telefono": telefono, "texto": texto})
        return f"waha.SIMULADO.{len(registrados)}"

    monkeypatch.setattr(whatsapp_bot_service, "enviar_mensaje_texto", _envio_falso)
    return registrados


@pytest.fixture()
def consulta(client) -> dict:
    """Carga la consulta del nomenclador (código ``10100``) con su tarifa."""
    payload = {
        "codigo_nomenclador": "10100",
        "descripcion": "Consulta odontológica",
        "valor_base": 18500,
    }
    r = client.post(f"{API}/prestaciones", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def obras_sociales(client) -> dict:
    """Carga dos coberturas activas y una dada de baja (borrado lógico)."""
    osde = _crear_obra_social(client, nombre="OSDE")
    ioma = _crear_obra_social(client, nombre="IOMA")
    vieja = _crear_obra_social(client, nombre="Cobertura Dada de Baja")
    baja = client.delete(f"{API}/obras-sociales/{vieja['id']}")
    assert baja.status_code == 200, baja.text
    return {"activas": [ioma, osde], "baja": vieja}


def _crear_obra_social(client, **overrides) -> dict:
    payload = {"nombre": "OSDE", "codigo": "OSDE-01"}
    payload.update(overrides)
    r = client.post(f"{API}/obras-sociales", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _payload_texto(
    texto: str,
    *,
    telefono: str = TELEFONO,
    wamid: str = WAMID_ENTRANTE,
    event: str = "message",
    from_me: bool = False,
    from_: str | None = None,
) -> dict:
    """Arma un evento de WAHA con un mensaje de texto."""
    return {
        "event": event,
        "session": "default",
        "payload": {
            "id": wamid,
            "from": from_ if from_ is not None else f"{telefono}@c.us",
            "fromMe": from_me,
            "body": texto,
            "type": "chat",
        },
    }


def _enviar_texto(client, texto: str, **kwargs) -> dict:
    r = client.post(f"{API}/whatsapp/webhook", json=_payload_texto(texto, **kwargs))
    assert r.status_code == 200, r.text
    return r.json()


def _payload_lid(texto: str, *, alt: str | None = JID_REAL, **kwargs) -> dict:
    """Evento de WAHA con el remitente como ``@lid`` (privacidad del número).

    ``_data.key`` reproduce lo que publica WAHA: ``remoteJidAlt`` con el JID
    real del paciente (``alt=None`` arma el caso en que no viene).
    """
    evento = _payload_texto(texto, from_=LID, **kwargs)
    if alt is not None:
        evento["payload"]["_data"] = {"key": {"remoteJidAlt": alt}}
    return evento


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


def _recordatorio_pendiente(client, doctor, paciente) -> tuple[dict, str]:
    turno = _crear_turno(client, doctor, paciente)
    tarea = client.post(f"{API}/tareas/enviar-recordatorios")
    assert tarea.status_code == 200, tarea.text
    detalle = tarea.json()["detalles"][0]
    return turno, detalle["whatsapp_mensaje_id"]


class _RespuestaHttpFalsa:
    """Doble mínimo de ``httpx.Response`` para el envío real."""

    def __init__(self, data: dict):
        self._data = data
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


# ---------------------------------------------------------------------------
# Configuración WAHA y formato del número
# ---------------------------------------------------------------------------


def test_las_variables_de_waha_se_leen_del_entorno(monkeypatch):
    monkeypatch.setenv("WAHA_URL", "http://turnos_waha:3000/")
    monkeypatch.setenv("WAHA_API_KEY", "turnos2026")
    monkeypatch.setenv("WAHA_SESSION", "consultorio")

    config = Settings(_env_file=None)

    assert config.waha_url == "http://turnos_waha:3000/"
    assert config.waha_url_base == "http://turnos_waha:3000"
    assert config.waha_api_key == "turnos2026"
    assert config.waha_session == "consultorio"
    assert config.whatsapp_envio_real is True


@pytest.mark.parametrize(
    ("telefono", "esperado"),
    [
        ("+54 9 351 643-2863", "5493516432863"),
        ("0351 15 643-2863", "5493516432863"),
        ("351 643-2863", "5493516432863"),
        ("+54 351 15 643-2863", "5493516432863"),
        ("011 15 2233-4455", "5491122334455"),
        ("5491122334455", "5491122334455"),
        ("", ""),
        (None, ""),
    ],
)
def test_normaliza_el_telefono_argentino_para_waha(telefono, esperado):
    assert normalizar_telefono_waha(telefono) == esperado


def test_el_chat_id_termina_en_c_us():
    assert construir_chat_id_waha("+54 9 351 643-2863") == "5493516432863@c.us"
    assert construir_chat_id_waha("") == ""


def test_un_chat_id_ya_armado_no_se_vuelve_a_normalizar():
    assert construir_chat_id_waha(CHAT_ID) == CHAT_ID
    # El @lid es un identificador opaco: si se normaliza deja de existir.
    assert construir_chat_id_waha(LID) == LID
    # Un JID de teléfono sí se convierte al dominio que espera /api/sendText.
    assert construir_chat_id_waha(JID_REAL) == f"{TELEFONO_REAL}@c.us"
    assert construir_chat_id_waha(f"{TELEFONO_REAL}:12@s.whatsapp.net") == (
        f"{TELEFONO_REAL}@c.us"
    )


@pytest.mark.parametrize(
    ("jid", "esperado"),
    [
        (JID_REAL, TELEFONO_REAL),
        (f"{TELEFONO_REAL}@c.us", TELEFONO_REAL),
        ("+54 9 351 594-5075", TELEFONO_REAL),
        (LID, ""),
        ("120363000000000000@g.us", ""),
        ("status@broadcast", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_numero_de_jid_solo_devuelve_telefonos(jid, esperado):
    assert numero_de_jid(jid) == esperado


def test_normalizar_destinatario_conserva_el_lid():
    assert normalizar_destinatario_waha(LID) == LID
    assert normalizar_destinatario_waha("+54 9 11 2233-4455") == TELEFONO
    assert normalizar_destinatario_waha(None) == ""
    # Los dos servicios que usan la clave de contacto respetan el @lid.
    assert whatsapp_bot_service.normalizar_destinatario(LID) == LID
    assert whatsapp_conversacion_service.normalizar_telefono_contacto(LID) == LID


def test_telefono_de_chat_no_inventa_un_numero_para_un_lid():
    assert whatsapp_service.telefono_de_chat(LID) == LID
    assert whatsapp_service.telefono_de_chat(CHAT_ID) == TELEFONO
    assert whatsapp_service.telefono_de_chat("120363000000000000@g.us") == ""


def test_el_payload_de_texto_tiene_la_forma_de_waha():
    payload = whatsapp_service.construir_payload_texto(TELEFONO, "Hola")

    assert payload == {
        "chatId": CHAT_ID,
        "text": "Hola",
        "session": "default",
    }


def test_el_recordatorio_es_texto_con_instrucciones_1_y_2(db_session, doctor, paciente):
    turno = Turno(
        paciente=paciente,
        doctor=doctor,
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=datetime(2026, 11, 5, 10, 30),
        fecha_hora_fin=datetime(2026, 11, 5, 11, 0),
    )
    db_session.add(turno)
    db_session.commit()
    db_session.refresh(turno)

    payload = whatsapp_service.construir_payload_recordatorio(turno)

    assert payload["chatId"] == CHAT_ID
    assert payload["session"] == "default"
    assert "template" not in payload
    assert "Respondé *1* (o *SI*) para confirmar" in payload["text"]
    assert "Respondé *2* (o *NO*) para cancelarlo" in payload["text"]


def test_sin_credenciales_el_envio_se_simula_y_queda_en_el_log(caplog):
    with caplog.at_level(logging.INFO, logger="app.services.whatsapp"):
        mensaje_id = whatsapp_service.enviar_mensaje_texto(TELEFONO, "Hola")

    assert mensaje_id.startswith("waha.SIMULADO.")
    assert any(
        "Enviando WhatsApp (texto) a" in registro.getMessage()
        for registro in caplog.records
    )


def test_con_credenciales_el_envio_es_un_post_a_send_text(monkeypatch):
    monkeypatch.setattr(settings, "waha_url", "http://turnos_waha:3000/")
    monkeypatch.setattr(settings, "waha_api_key", "turnos2026")
    monkeypatch.setattr(settings, "waha_session", "consultorio")

    llamadas: list = []
    pausas: list = []

    def _post_falso(url: str, **kwargs):
        llamadas.append({"url": url, **kwargs})
        return _RespuestaHttpFalsa({"id": "true_5491122334455@c.us_ABC"})

    monkeypatch.setattr(whatsapp_service.httpx, "post", _post_falso)
    monkeypatch.setattr(whatsapp_service.time, "sleep", pausas.append)

    mensaje_id = whatsapp_service.enviar_mensaje_texto(TELEFONO, "Hola")

    assert mensaje_id == "true_5491122334455@c.us_ABC"
    assert pausas == [whatsapp_service.PAUSA_ENVIO_WAHA]
    assert len(llamadas) == 1
    assert llamadas[0]["url"] == "http://turnos_waha:3000/api/sendText"
    assert llamadas[0]["headers"]["X-Api-Key"] == "turnos2026"
    assert llamadas[0]["json"] == {
        "chatId": CHAT_ID,
        "text": "Hola",
        "session": "consultorio",
    }


def test_el_error_de_waha_conserva_el_detalle(monkeypatch):
    monkeypatch.setattr(settings, "waha_url", "http://turnos_waha:3000")
    monkeypatch.setattr(settings, "waha_api_key", "turnos2026")

    request = httpx.Request("POST", "http://turnos_waha:3000/api/sendText")
    respuesta = httpx.Response(
        400, request=request, json={"message": "Sesión no iniciada"}
    )
    monkeypatch.setattr(whatsapp_service.httpx, "post", lambda *_a, **_k: respuesta)
    monkeypatch.setattr(whatsapp_service.time, "sleep", lambda _s: None)

    with pytest.raises(WhatsAppEnvioError, match=r"HTTP 400.*Sesión no iniciada"):
        whatsapp_service._enviar_http_whatsapp(
            {"chatId": CHAT_ID, "text": "Hola", "session": "default"}
        )


def test_un_telefono_sin_digitos_no_se_intenta_enviar():
    with pytest.raises(WhatsAppEnvioError, match="no tiene dígitos"):
        whatsapp_service.enviar_mensaje_texto("", "Hola")


# ---------------------------------------------------------------------------
# Webhook WAHA: filtros y extracción del mensaje
# ---------------------------------------------------------------------------


def test_el_get_de_verificacion_de_meta_ya_no_existe(client):
    r = client.get(f"{API}/whatsapp/webhook")
    assert r.status_code == 405


def test_el_webhook_no_exige_firma_de_meta(client, envios):
    r = client.post(f"{API}/whatsapp/webhook", json=_payload_texto("hola"))

    assert r.status_code == 200, r.text
    assert r.json()["tipo"] == "BOT"
    assert envios


@pytest.mark.parametrize(
    "payload",
    [
        _payload_texto("hola", event="session.status"),
        _payload_texto("hola", event="message.ack"),
        _payload_texto("hola", from_me=True),
        _payload_texto("hola", from_="120363000000000000@g.us"),
        _payload_texto("hola", from_="status@broadcast"),
        _payload_texto("   "),
    ],
)
def test_el_webhook_ignora_eventos_que_no_son_de_pacientes(client, envios, payload):
    r = client.post(f"{API}/whatsapp/webhook", json=payload)

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tipo"] == "IGNORADO"
    assert data["atencion_manual"] is False
    assert envios == []


def test_un_evento_sin_payload_no_falla(client):
    r = client.post(
        f"{API}/whatsapp/webhook", json={"event": "session.status", "session": "default"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["tipo"] == "IGNORADO"


def test_el_webhook_extrae_el_telefono_del_chat_id(client, envios):
    data = _enviar_texto(client, "hola", telefono="+54 9 351 643-2863")

    assert data["telefono"] == "5493516432863"
    assert data["mensaje_id_entrante"] == WAMID_ENTRANTE
    assert envios[0]["telefono"] == "5493516432863"


# ---------------------------------------------------------------------------
# Remitentes con identificador de privacidad (@lid)
# ---------------------------------------------------------------------------


def test_el_webhook_resuelve_el_numero_real_de_un_lid(client, envios):
    r = client.post(f"{API}/whatsapp/webhook", json=_payload_lid("hola"))

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tipo"] == "BOT"
    assert data["telefono"] == TELEFONO_REAL
    assert data["respuesta"] == whatsapp_bot_service.MENU_PRINCIPAL
    # La respuesta sale al número real, no al identificador @lid.
    assert envios == [
        {"telefono": TELEFONO_REAL, "texto": whatsapp_bot_service.MENU_PRINCIPAL}
    ]
    payload = whatsapp_service.construir_payload_texto(data["telefono"], "Hola")
    assert payload["chatId"] == f"{TELEFONO_REAL}@c.us"


def test_el_numero_real_tambien_aparece_en_sender_pn_o_participant_alt(client, envios):
    for campo in ("senderPn", "participantAlt"):
        evento = _payload_texto("hola", from_=LID)
        evento["payload"]["_data"] = {"key": {campo: JID_REAL}}

        r = client.post(f"{API}/whatsapp/webhook", json=evento)

        assert r.status_code == 200, r.text
        assert r.json()["telefono"] == TELEFONO_REAL
    assert [envio["telefono"] for envio in envios] == [TELEFONO_REAL, TELEFONO_REAL]


@pytest.mark.parametrize("alt", [None, "99999999999999@lid"])
def test_sin_numero_real_el_webhook_contesta_al_lid_original(client, envios, alt):
    r = client.post(f"{API}/whatsapp/webhook", json=_payload_lid("hola", alt=alt))

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tipo"] == "BOT"
    assert data["telefono"] == LID
    assert envios == [{"telefono": LID, "texto": whatsapp_bot_service.MENU_PRINCIPAL}]
    # El chatId viaja intacto: sin 549 adelante y sin cambiarle el dominio.
    payload = whatsapp_service.construir_payload_texto(data["telefono"], "Hola")
    assert payload["chatId"] == LID


def test_un_lid_con_numero_real_confirma_el_turno_del_paciente(
    client, envios, doctor, paciente
):
    turno, _ = _recordatorio_pendiente(client, doctor, paciente)

    r = client.post(
        f"{API}/whatsapp/webhook",
        json=_payload_lid("1", alt=f"{TELEFONO}@s.whatsapp.net"),
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tipo"] == "CONFIRMACION"
    assert data["turno_id"] == turno["id"]
    assert data["estado_turno"] == "CONFIRMADO"
    assert envios[-1]["texto"] == data["respuesta"]
    assert envios[-1]["telefono"] == TELEFONO


def test_la_conversacion_del_lid_se_guarda_con_el_numero_real(
    client, db_session, envios
):
    client.post(f"{API}/whatsapp/webhook", json=_payload_lid("hola"))

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.telefono == TELEFONO_REAL
    assert conversacion.estado == EstadoConversacionWhatsApp.MENU


def test_sin_numero_real_la_conversacion_conserva_el_lid(client, db_session, envios):
    client.post(f"{API}/whatsapp/webhook", json=_payload_lid("hola", alt=None))

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.telefono == LID


# ---------------------------------------------------------------------------
# Menú numerado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "saludo",
    [
        "hola",
        "Hola!",
        "buenas",
        "Buenas tardes",
        "info",
        "TURNOS",
        "Menú",
        "hola, quería información",
    ],
)
def test_un_saludo_responde_el_menu_de_opciones(client, envios, saludo):
    data = _enviar_texto(client, saludo)

    assert data["tipo"] == "BOT"
    assert data["opcion"] is None
    assert data["atencion_manual"] is False
    assert data["telefono"] == TELEFONO
    assert data["mensaje_id_entrante"] == WAMID_ENTRANTE
    assert data["respuesta"] == whatsapp_bot_service.MENU_PRINCIPAL

    for opcion in (
        "Precio de la consulta",
        "Obras sociales con convenio",
        "Ubicación y horarios",
        "Hablar con una secretaria",
    ):
        assert opcion in data["respuesta"]

    assert envios == [
        {"telefono": TELEFONO, "texto": whatsapp_bot_service.MENU_PRINCIPAL}
    ]
    assert data["mensaje_id_saliente"].startswith("waha.SIMULADO.")


@pytest.mark.parametrize("respuesta", ["1", "1.", "(1)", "opción 1", "1️⃣"])
def test_opcion_1_envia_la_tarifa_de_la_consulta(client, envios, consulta, respuesta):
    data = _enviar_texto(client, respuesta)

    assert data["tipo"] == "BOT"
    assert data["opcion"] == "1"
    assert data["atencion_manual"] is False
    assert consulta["descripcion"] in data["respuesta"]
    assert "$18.500,00" in data["respuesta"]
    assert "Córdoba" in data["respuesta"]

    assert envios == [{"telefono": TELEFONO, "texto": data["respuesta"]}]


def test_opcion_2_lista_las_obras_sociales_activas(
    client, envios, obras_sociales
):
    data = _enviar_texto(client, "2")

    assert data["opcion"] == "2"
    assert "IOMA" in data["respuesta"] and "OSDE" in data["respuesta"]
    assert data["respuesta"].index("IOMA") < data["respuesta"].index("OSDE")
    assert obras_sociales["baja"]["nombre"] not in data["respuesta"]
    assert "Son 2 coberturas" in data["respuesta"]
    assert len(envios) == 1


def test_opcion_3_envia_la_direccion_y_los_horarios(client, envios):
    data = _enviar_texto(client, "3️⃣")

    assert data["opcion"] == "3"
    assert settings.whatsapp_consultorio_direccion in data["respuesta"]
    assert settings.whatsapp_consultorio_horarios in data["respuesta"]
    assert len(envios) == 1


@pytest.mark.parametrize("respuesta", ["4", "4.", "opción 4"])
def test_opcion_4_no_responde_y_registra_atencion_manual(
    client, envios, caplog, respuesta
):
    with caplog.at_level(logging.WARNING, logger="app.services.whatsapp_bot"):
        data = _enviar_texto(client, respuesta)

    assert data["tipo"] == "BOT"
    assert data["opcion"] == "4"
    assert data["atencion_manual"] is True
    assert data["respuesta"] is None
    assert data["mensaje_id_saliente"] is None
    assert envios == []

    registros = [registro.getMessage() for registro in caplog.records]
    assert any(reg.startswith("Atención Manual Requerida") for reg in registros)
    assert any(TELEFONO in reg and "opcion=4" in reg for reg in registros)


@pytest.mark.parametrize(
    "mensaje",
    [
        "¿Atienden por la tarde?",
        "Necesito un implante urgente",
        "¿Cuánto sale un blanqueamiento?",
        "🙂",
        "23",
        "no entiendo",
        "gracias",
    ],
)
def test_texto_libre_sin_confirmacion_muestra_el_menu(client, envios, mensaje):
    data = _enviar_texto(client, mensaje)

    assert data["tipo"] == "BOT"
    assert data["atencion_manual"] is False
    assert data["respuesta"] == whatsapp_bot_service.MENU_PRINCIPAL
    assert len(envios) == 1


def test_una_tecla_inexistente_tambien_vuelve_al_menu(client, envios):
    data = _enviar_texto(client, "7")

    assert data["tipo"] == "BOT"
    assert data["atencion_manual"] is False
    assert data["respuesta"] == whatsapp_bot_service.MENU_PRINCIPAL
    assert len(envios) == 1


def test_el_bot_guarda_el_estado_menu_del_contacto(client, db_session, envios):
    _enviar_texto(client, "hola")

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.telefono == TELEFONO
    assert conversacion.estado == EstadoConversacionWhatsApp.MENU
    assert conversacion.turno_id is None


# ---------------------------------------------------------------------------
# Confirmación / cancelación por texto
# ---------------------------------------------------------------------------


def test_el_recordatorio_deja_la_confirmacion_pendiente_en_la_base(
    client, db_session, doctor, paciente
):
    turno, mensaje_id = _recordatorio_pendiente(client, doctor, paciente)

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.telefono == TELEFONO
    assert conversacion.estado == EstadoConversacionWhatsApp.CONFIRMACION_TURNO
    assert conversacion.turno_id == turno["id"]
    assert conversacion.whatsapp_mensaje_id == mensaje_id
    assert mensaje_id.startswith("waha.SIMULADO.")


@pytest.mark.parametrize("respuesta", ["1", "si", "SÍ", "confirmo", "confirmar"])
def test_responder_1_o_si_confirma_el_turno(client, envios, doctor, paciente, respuesta):
    turno, _ = _recordatorio_pendiente(client, doctor, paciente)

    data = _enviar_texto(client, respuesta)

    assert data["tipo"] == "CONFIRMACION"
    assert data["turno_id"] == turno["id"]
    assert data["boton"] == "confirmar_turno"
    assert data["estado_turno"] == "CONFIRMADO"
    assert data["estado_whatsapp"] == "RESPONDIDO"
    assert "Turno confirmado" in data["respuesta"]
    assert data["mensaje_id_saliente"].startswith("waha.SIMULADO.")
    assert envios[-1]["texto"] == data["respuesta"]


@pytest.mark.parametrize("respuesta", ["2", "no", "NO", "cancelo", "cancelar"])
def test_responder_2_o_no_cancela_el_turno(client, envios, doctor, paciente, respuesta):
    turno, _ = _recordatorio_pendiente(client, doctor, paciente)

    data = _enviar_texto(client, respuesta)

    assert data["tipo"] == "CONFIRMACION"
    assert data["turno_id"] == turno["id"]
    assert data["boton"] == "cancelar_turno"
    assert data["estado_turno"] == "CANCELADO"
    assert data["estado_whatsapp"] == "RESPONDIDO"
    assert "Turno cancelado" in data["respuesta"]

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == "CANCELADO"
    assert actualizado["estado_whatsapp"] == "RESPONDIDO"


def test_una_respuesta_invalida_repite_la_pregunta_y_mantiene_el_estado(
    client, db_session, envios, doctor, paciente
):
    turno, _ = _recordatorio_pendiente(client, doctor, paciente)

    data = _enviar_texto(client, "hola")

    assert data["tipo"] == "BOT"
    assert data["turno_id"] is None
    assert data["atencion_manual"] is False
    assert "Respondé *1* (o *SI*) para confirmar" in data["respuesta"]
    assert "Respondé *2* (o *NO*) para cancelar" in data["respuesta"]

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.estado == EstadoConversacionWhatsApp.CONFIRMACION_TURNO
    assert conversacion.turno_id == turno["id"]

    actualizado = client.get(f"{API}/turnos/{turno['id']}").json()
    assert actualizado["estado_turno"] == "PENDIENTE"


def test_despues_de_confirmar_vuelve_al_menu(client, envios, doctor, paciente):
    _recordatorio_pendiente(client, doctor, paciente)

    confirmacion = _enviar_texto(client, "1")
    assert confirmacion["estado_turno"] == "CONFIRMADO"

    otro = _enviar_texto(client, "gracias")
    assert otro["tipo"] == "BOT"
    assert otro["respuesta"] == whatsapp_bot_service.MENU_PRINCIPAL


def test_sin_confirmacion_pendiente_un_texto_muestra_el_menu(client, envios):
    data = _enviar_texto(client, "¿me pasás un turno?")

    assert data["tipo"] == "BOT"
    assert data["respuesta"] == whatsapp_bot_service.MENU_PRINCIPAL


def test_el_webhook_confirma_la_recepcion_si_falla_el_envio_saliente(
    client, monkeypatch
):
    def _fallar_envio(*_args, **_kwargs):
        raise WhatsAppEnvioError("WAHA no disponible")

    monkeypatch.setattr(whatsapp_bot_service, "responder_mensaje", _fallar_envio)

    r = client.post(f"{API}/whatsapp/webhook", json=_payload_texto("hola"))

    assert r.status_code == 200, r.text
    assert r.json()["tipo"] == "BOT"
    assert r.json()["atencion_manual"] is True
    assert r.json()["mensaje_id_saliente"] is None


# ---------------------------------------------------------------------------
# Utilidades del bot y estado persistido
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Holá, ¿Precios?", "hola, ¿precios?"),
        ("  BUENAS   TARDES  ", "buenas tardes"),
        ("Opción 2", "opcion 2"),
        ("1️⃣", "1"),
        ("4️⃣", "4"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalizar_texto(entrada, esperado):
    assert whatsapp_bot_service.normalizar_texto(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("1", "1"),
        ("1.", "1"),
        ("(1)", "1"),
        ("n°4", "4"),
        ("opcion 1", "1"),
        ("opción: 3", "3"),
        ("3️⃣", "3"),
        ("7", "7"),
        ("23", None),
        ("hola", None),
        ("", None),
    ],
)
def test_detectar_opcion(entrada, esperado):
    texto = whatsapp_bot_service.normalizar_texto(entrada)
    assert whatsapp_bot_service.detectar_opcion(texto) == esperado


@pytest.mark.parametrize(
    "saludo",
    [
        "hola",
        "HOLA!",
        "buenas",
        "buenas tardes",
        "info",
        "turnos",
        "Menú",
        "Quería info de precios",
    ],
)
def test_es_saludo_inicial(saludo):
    normalizado = whatsapp_bot_service.normalizar_texto(saludo)
    assert whatsapp_bot_service.es_saludo_inicial(normalizado) is True


@pytest.mark.parametrize("texto", ["informe de laboratorio", "gracias!", "ok", ""])
def test_no_es_saludo_inicial(texto):
    normalizado = whatsapp_bot_service.normalizar_texto(texto)
    assert whatsapp_bot_service.es_saludo_inicial(normalizado) is False


def test_formatear_importe_usa_los_separadores_argentinos():
    assert whatsapp_bot_service.formatear_importe(18000) == "$18.000,00"
    assert whatsapp_bot_service.formatear_importe(1234.5) == "$1.234,50"
    assert whatsapp_bot_service.formatear_importe(0) == "$0,00"


def test_normalizar_destinatario_deja_solo_los_digitos():
    assert (
        whatsapp_bot_service.normalizar_destinatario("+54 9 11 2233-4455")
        == TELEFONO
    )
    assert whatsapp_bot_service.normalizar_destinatario(None) == ""


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1", "CONFIRMAR"),
        ("si", "CONFIRMAR"),
        ("confirmo", "CONFIRMAR"),
        ("2", "CANCELAR"),
        ("no", "CANCELAR"),
        ("cancelar", "CANCELAR"),
        ("hola", None),
        ("", None),
    ],
)
def test_clasificar_la_respuesta_de_confirmacion(texto, esperado):
    normalizado = whatsapp_bot_service.normalizar_texto(texto)
    assert whatsapp_bot_service._clasificar_confirmacion(normalizado) == esperado


def test_el_estado_pendiente_se_limpia_si_el_turno_ya_no_esta_pendiente(
    db_session, doctor, paciente
):
    inicio = utcnow() + timedelta(days=2)
    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=inicio,
        fecha_hora_fin=inicio + timedelta(minutes=30),
        estado_turno=EstadoTurno.CONFIRMADO,
    )
    db_session.add(turno)
    db_session.commit()
    db_session.refresh(turno)
    whatsapp_conversacion_service.marcar_confirmacion_pendiente(
        db_session, TELEFONO, turno.id, "waha.X"
    )

    assert (
        whatsapp_conversacion_service.obtener_confirmacion_pendiente(
            db_session, TELEFONO
        )
        is None
    )
    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.estado == EstadoConversacionWhatsApp.MENU
    assert conversacion.turno_id is None


def test_el_estado_se_guarda_por_telefono_y_sobrevive_a_otra_consulta(
    db_session, doctor, paciente
):
    inicio = utcnow() + timedelta(days=2)
    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=inicio,
        fecha_hora_fin=inicio + timedelta(minutes=30),
        estado_turno=EstadoTurno.PENDIENTE,
        estado_whatsapp=EstadoWhatsApp.ENVIADO,
    )
    db_session.add(turno)
    db_session.commit()
    db_session.refresh(turno)
    whatsapp_conversacion_service.marcar_confirmacion_pendiente(
        db_session, "011 15 2233-4455", turno.id, "waha.Y"
    )

    conversacion = db_session.query(ConversacionWhatsApp).one()
    assert conversacion.telefono == TELEFONO

    pendiente = whatsapp_conversacion_service.obtener_confirmacion_pendiente(
        db_session, TELEFONO
    )
    assert pendiente is not None
    _, turno_guardado = pendiente
    assert turno_guardado.id == turno.id
