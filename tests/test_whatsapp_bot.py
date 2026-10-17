"""Pruebas del Chatbot de Respuestas Automáticas (FAQ Bot) de WhatsApp.

Simulan los mensajes entrantes de un paciente (``type: text``) contra el webhook
``POST /api/v1/whatsapp/webhook`` y validan el árbol de respuestas completo:

* saludo o palabra clave ("hola", "buenas", "info", "turnos") → menú de opciones;
* ``1`` → tarifa de la consulta tomada de ``Prestacion`` (código ``10100``);
* ``2`` → obras sociales activas listadas **en un solo mensaje**, ordenadas;
* ``3`` → dirección y horarios del consultorio;
* ``4`` o cualquier texto que no sea del menú → **no** responde y registra
  ``'Atención Manual Requerida'`` para la secretaría.

Los envíos salientes se interceptan con un *doble* de
``whatsapp_bot_service.enviar_mensaje_texto``: sin credenciales configuradas el
servicio ya simula el ``POST`` a Meta, pero así también se comprueba **a quién**
y **qué texto** se le manda, sin depender del ``.env`` de la máquina. El fixture
``autouse`` ``whatsapp_sin_credenciales`` de ``conftest.py`` deja además
``settings`` sin credenciales, para que nada de lo que haya exportado el entorno
se traduzca en un ``POST`` real.
"""

import logging
from datetime import timedelta

import pytest

from app.core.config import URL_WHATSAPP_POR_DEFECTO, Settings, settings
from app.core.datetime_utils import utcnow
from app.services import whatsapp_bot_service, whatsapp_service

API = "/api/v1"
TELEFONO = "5491122334455"
WAMID_ENTRANTE = "wamid.ENTRANTE.TEXTO.1"


# ---------------------------------------------------------------------------
# Fixtures y helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def envios(monkeypatch) -> list:
    """Intercepta los envíos del bot y registra ``{telefono, texto}`` de cada uno."""
    registrados: list = []

    def _envio_falso(telefono: str, texto: str) -> str:
        registrados.append({"telefono": telefono, "texto": texto})
        return f"wamid.SIMULADO.{len(registrados)}"

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
    """Crea una cobertura por API y devuelve su representación."""
    payload = {"nombre": "OSDE", "codigo": "OSDE-01"}
    payload.update(overrides)
    r = client.post(f"{API}/obras-sociales", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _payload_texto(
    texto: str, *, telefono: str = TELEFONO, wamid: str = WAMID_ENTRANTE
) -> dict:
    """Arma el payload de Meta para un mensaje de **texto** del paciente."""
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
                            "metadata": {
                                "display_phone_number": "5493510000000",
                                "phone_number_id": "PHONE_ID",
                            },
                            "messages": [
                                {
                                    "from": telefono,
                                    "id": wamid,
                                    "timestamp": "1712345678",
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _enviar_texto(client, texto: str, **kwargs) -> dict:
    """Manda un mensaje de texto por el webhook y devuelve la respuesta del bot."""
    r = client.post(f"{API}/whatsapp/webhook", json=_payload_texto(texto, **kwargs))
    assert r.status_code == 200, r.text
    return r.json()


class _RespuestaHttpFalsa:
    """Doble de ``httpx.Response`` para probar el envío real sin salir a la red."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


# ---------------------------------------------------------------------------
# Envío saliente hacia Meta
# ---------------------------------------------------------------------------


def test_el_payload_del_mensaje_de_texto_tiene_la_forma_de_la_cloud_api():
    payload = whatsapp_service.construir_payload_texto(TELEFONO, "Hola")

    assert payload == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": TELEFONO,
        "type": "text",
        "text": {"preview_url": False, "body": "Hola"},
    }


def test_sin_credenciales_el_envio_se_simula_y_queda_en_el_log(monkeypatch, caplog):
    monkeypatch.setattr(settings, "whatsapp_access_token", None)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", None)

    with caplog.at_level(logging.INFO, logger="app.services.whatsapp"):
        wamid = whatsapp_service.enviar_mensaje_texto(TELEFONO, "Hola")

    assert wamid.startswith("wamid.SIMULADO.")
    assert any(
        "Enviando WhatsApp (texto) a" in registro.getMessage()
        for registro in caplog.records
    )


def test_con_credenciales_el_envio_es_un_post_real_a_la_graph_api(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_access_token", "token-de-prueba")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(settings, "whatsapp_api_url", None)  # cae en la URL por defecto

    llamadas: list = []

    def _post_falso(url: str, **kwargs):
        llamadas.append({"url": url, **kwargs})
        return _RespuestaHttpFalsa({"messages": [{"id": "wamid.REAL.1"}]})

    monkeypatch.setattr(whatsapp_service.httpx, "post", _post_falso)

    wamid = whatsapp_service.enviar_mensaje_texto(TELEFONO, "Hola")

    assert wamid == "wamid.REAL.1"
    assert len(llamadas) == 1
    assert llamadas[0]["url"] == "https://graph.facebook.com/v21.0/1234567890/messages"
    assert llamadas[0]["headers"]["Authorization"] == "Bearer token-de-prueba"
    assert llamadas[0]["json"]["to"] == TELEFONO
    assert llamadas[0]["json"]["text"]["body"] == "Hola"


# ---------------------------------------------------------------------------
# Saludo inicial → menú de opciones
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

    # El menú ofrece las cuatro opciones del árbol de decisión.
    for opcion in (
        "Precio de la consulta",
        "Obras sociales con convenio",
        "Ubicación y horarios",
        "Hablar con una secretaria",
    ):
        assert opcion in data["respuesta"]

    # Se envió un solo mensaje, al número que escribió.
    assert envios == [
        {"telefono": TELEFONO, "texto": whatsapp_bot_service.MENU_PRINCIPAL}
    ]
    assert data["mensaje_id_saliente"].startswith("wamid.SIMULADO.")


# ---------------------------------------------------------------------------
# Opción 1 → precio de la consulta (Prestacion '10100')
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("respuesta", ["1", "1.", "(1)", "opción 1", "1️⃣"])
def test_opcion_1_envia_la_tarifa_de_la_consulta(client, envios, consulta, respuesta):
    data = _enviar_texto(client, respuesta)

    assert data["tipo"] == "BOT"
    assert data["opcion"] == "1"
    assert data["atencion_manual"] is False
    assert consulta["descripcion"] in data["respuesta"]
    assert "$18.500,00" in data["respuesta"]
    assert "Córdoba" in data["respuesta"]

    assert len(envios) == 1
    assert envios[0]["telefono"] == TELEFONO
    assert envios[0]["texto"] == data["respuesta"]


def test_opcion_1_toma_el_valor_actual_del_catalogo(client, envios, consulta):
    """Si la secretaría actualiza el arancel, el bot responde el nuevo valor."""
    update = client.put(
        f"{API}/prestaciones/{consulta['id']}",
        json={
            "codigo_nomenclador": "10100",
            "descripcion": "Consulta odontológica",
            "valor_base": 22500,
        },
    )
    assert update.status_code == 200, update.text

    data = _enviar_texto(client, "1")

    assert "$22.500,00" in data["respuesta"]
    assert "$18.500,00" not in data["respuesta"]


def test_opcion_1_sin_la_prestacion_cargada_invita_a_hablar_con_una_secretaria(
    client, envios, caplog
):
    with caplog.at_level(logging.WARNING, logger="app.services.whatsapp_bot"):
        data = _enviar_texto(client, "1")

    # Sin el precio en el catálogo igual se contesta: no es una consulta perdida.
    assert data["tipo"] == "BOT"
    assert data["opcion"] == "1"
    assert data["atencion_manual"] is False
    assert "10100" in data["respuesta"] or "no pudimos consultar" in data["respuesta"]
    assert len(envios) == 1
    assert any(
        "no hay una prestación activa" in registro.getMessage()
        for registro in caplog.records
    )


# ---------------------------------------------------------------------------
# Opción 2 → obras sociales activas (ObraSocial.activa = True)
# ---------------------------------------------------------------------------


def test_opcion_2_lista_las_obras_sociales_activas_en_un_solo_mensaje(
    client, envios, obras_sociales
):
    data = _enviar_texto(client, "2")

    assert data["tipo"] == "BOT"
    assert data["opcion"] == "2"
    assert data["atencion_manual"] is False

    respuesta = data["respuesta"]
    # Sólo las vigentes, ordenadas alfabéticamente y dentro del mismo mensaje.
    assert "IOMA" in respuesta and "OSDE" in respuesta
    assert respuesta.index("IOMA") < respuesta.index("OSDE")
    assert obras_sociales["baja"]["nombre"] not in respuesta
    assert "Son 2 coberturas" in respuesta

    # Un único envío: el listado no se parte en varios mensajes.
    assert envios == [{"telefono": TELEFONO, "texto": respuesta}]


def test_opcion_2_sin_coberturas_cargadas_responde_una_sola_vez(client, envios):
    data = _enviar_texto(client, "opción 2")

    assert data["opcion"] == "2"
    assert data["atencion_manual"] is False
    assert "no tenemos obras sociales con convenio" in data["respuesta"]
    assert len(envios) == 1


# ---------------------------------------------------------------------------
# Opción 3 → ubicación y horarios
# ---------------------------------------------------------------------------


def test_opcion_3_envia_la_direccion_y_los_horarios_del_consultorio(client, envios):
    data = _enviar_texto(client, "3️⃣")

    assert data["tipo"] == "BOT"
    assert data["opcion"] == "3"
    assert data["atencion_manual"] is False
    assert settings.whatsapp_consultorio_direccion in data["respuesta"]
    assert settings.whatsapp_consultorio_horarios in data["respuesta"]

    assert envios == [{"telefono": TELEFONO, "texto": data["respuesta"]}]


# ---------------------------------------------------------------------------
# Opción 4 y texto libre → 'Atención Manual Requerida'
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("respuesta", ["4", "4.", "opción 4"])
def test_opcion_4_no_responde_automaticamente_y_registra_atencion_manual(
    client, envios, caplog, respuesta
):
    with caplog.at_level(logging.WARNING, logger="app.services.whatsapp_bot"):
        data = _enviar_texto(client, respuesta)

    assert data["tipo"] == "BOT"
    assert data["opcion"] == "4"
    assert data["atencion_manual"] is True
    assert data["respuesta"] is None
    assert data["mensaje_id_saliente"] is None

    # El bot se corre a un costado: no se le manda nada al paciente.
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
    ],
)
def test_texto_libre_no_reconocido_queda_para_atencion_manual(
    client, envios, caplog, mensaje
):
    with caplog.at_level(logging.WARNING, logger="app.services.whatsapp_bot"):
        data = _enviar_texto(client, mensaje)

    assert data["tipo"] == "BOT"
    assert data["atencion_manual"] is True
    assert data["respuesta"] is None
    assert data["mensaje_id_saliente"] is None
    assert envios == []
    assert any(
        "Atención Manual Requerida" in registro.getMessage()
        for registro in caplog.records
    )


def test_pedir_un_turno_ofrece_el_menu_para_derivar_con_la_opcion_4(client, envios):
    """El pedido de un "turno" es palabra clave del menú: el bot no agenda solo.

    Si el paciente busca una fecha concreta, la opción 4 deriva la conversación a
    una secretaria (que es el camino de atención manual).
    """
    data = _enviar_texto(client, "Necesito un turno")

    assert data["tipo"] == "BOT"
    assert data["atencion_manual"] is False
    assert data["respuesta"] == whatsapp_bot_service.MENU_PRINCIPAL
    assert len(envios) == 1


def test_una_opcion_fuera_del_menu_queda_registrada_con_su_numero(
    client, envios, caplog
):
    with caplog.at_level(logging.WARNING, logger="app.services.whatsapp_bot"):
        data = _enviar_texto(client, "7")

    assert data["opcion"] == "7"
    assert data["atencion_manual"] is True
    assert envios == []
    assert any(
        "la opción 7 no existe en el menú" in registro.getMessage()
        for registro in caplog.records
    )


def test_un_mensaje_de_texto_sin_remitente_no_se_puede_contestar(client, envios):
    r = client.post(f"{API}/whatsapp/webhook", json=_payload_texto("hola", telefono=""))

    assert r.status_code == 400
    assert envios == []


# ---------------------------------------------------------------------------
# Convivencia con el flujo de botones del recordatorio
# ---------------------------------------------------------------------------


def _payload_boton(mensaje_contextual_id: str, boton_id: str) -> dict:
    """Arma el payload de Meta para una respuesta de botón (Confirmar/Cancelar)."""
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
                                    "from": TELEFONO,
                                    "id": "wamid.ENTRANTE.BOTON.1",
                                    "timestamp": "1712345678",
                                    "type": "interactive",
                                    "context": {
                                        "from": "5493510000000",
                                        "id": mensaje_contextual_id,
                                    },
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": boton_id,
                                            "title": "Confirmar",
                                        },
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def test_el_webhook_sigue_confirmando_turnos_con_el_boton(client, envios, doctor, paciente):
    """El ruteo por tipo de mensaje no rompe el flujo de recordatorios."""
    inicio = (utcnow() + timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    alta = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": paciente.id,
            "doctor_id": doctor.id,
            "fecha_hora_inicio": inicio.isoformat(),
            "fecha_hora_fin": (inicio + timedelta(minutes=30)).isoformat(),
        },
    )
    assert alta.status_code == 201, alta.text
    turno = alta.json()

    tarea = client.post(f"{API}/tareas/enviar-recordatorios").json()
    wamid = tarea["detalles"][0]["whatsapp_mensaje_id"]

    r = client.post(
        f"{API}/whatsapp/webhook",
        json=_payload_boton(wamid, "confirmar_turno"),
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tipo"] == "BOTON"
    assert data["turno_id"] == turno["id"]
    assert data["estado_turno"] == "CONFIRMADO"
    assert data["estado_whatsapp"] == "RESPONDIDO"
    assert data["respuesta"] is None
    assert envios == []  # el FAQ Bot no intervino


# ---------------------------------------------------------------------------
# Utilidades del bot: normalización del texto, opciones e importes
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


# ---------------------------------------------------------------------------
# Configuración del envío: los dos nombres del token y las credenciales mínimas
# ---------------------------------------------------------------------------


def test_el_token_se_puede_definir_como_whatsapp_token(monkeypatch):
    """``WHATSAPP_TOKEN`` (el nombre corto de la doc de Meta) es sinónimo válido."""
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("WHATSAPP_TOKEN", "token-corto")
    assert Settings(_env_file=None).whatsapp_access_token == "token-corto"


def test_un_whatsapp_token_vacio_no_tapa_al_access_token(monkeypatch):
    """``WHATSAPP_TOKEN=""`` en el ``.env`` no invalida las credenciales reales."""
    monkeypatch.setenv("WHATSAPP_TOKEN", "")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-real")
    assert Settings(_env_file=None).whatsapp_access_token == "token-real"


def test_la_url_de_la_api_tiene_valor_por_defecto(monkeypatch):
    """Sin ``WHATSAPP_API_URL`` se usa la Graph API de Meta."""
    monkeypatch.delenv("WHATSAPP_API_URL", raising=False)
    assert Settings(_env_file=None).whatsapp_api_url == URL_WHATSAPP_POR_DEFECTO


@pytest.mark.parametrize(
    ("phone_number_id", "token", "esperado"),
    [
        ("1234567890", "token-de-prueba", True),
        (None, "token-de-prueba", False),
        ("1234567890", None, False),
    ],
)
def test_el_envio_real_solo_necesita_el_token_y_el_numero(
    monkeypatch, phone_number_id, token, esperado
):
    """La URL no condiciona el envío: alcanza con el token y el id del número."""
    for nombre in (
        "WHATSAPP_API_URL",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_TOKEN",
    ):
        monkeypatch.delenv(nombre, raising=False)
    if phone_number_id:
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", phone_number_id)
    if token:
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", token)

    assert Settings(_env_file=None).whatsapp_envio_real is esperado
