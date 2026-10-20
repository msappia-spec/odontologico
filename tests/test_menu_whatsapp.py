"""Pruebas del menú configurable del FAQ Bot de WhatsApp.

Cubren las dos mitades de la funcionalidad:

* **API del panel** (``/api/v1/whatsapp/menu``): alta, modificación, baja lógica,
  reactivación, vista previa y restauración del menú histórico, incluidos los
  errores de validación (número repetido → ``409``, ``TEXTO`` sin respuesta →
  ``422``).
* **Bot**: ``whatsapp_bot_service.responder_mensaje`` lee las opciones de la
  tabla, muestra el menú configurado y ejecuta el tipo de cada tecla (texto
  libre, precio, obras sociales, ubicación o derivar a una secretaria). Si la
  tabla está vacía sigue funcionando con las cuatro opciones históricas; si
  todas están dadas de baja, el bot deriva la conversación a atención manual.

Igual que en ``test_whatsapp_bot.py``, los envíos salientes se interceptan con
un doble de ``enviar_mensaje_texto`` para comprobar el texto exacto sin salir a
la red (el fixture ``whatsapp_sin_credenciales`` de ``conftest.py`` ya deja
``settings`` sin credenciales).
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import whatsapp_bot_service, whatsapp_menu_service

API = "/api/v1"
MENU = f"{API}/whatsapp/menu"
TELEFONO = "5491122334455"


# ---------------------------------------------------------------------------
# Fixtures y helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def envios(monkeypatch) -> list:
    """Intercepta los envíos del bot y registra ``{telefono, texto}``."""
    registrados: list = []

    def _envio_falso(telefono: str, texto: str) -> str:
        registrados.append({"telefono": telefono, "texto": texto})
        return f"wamid.SIMULADO.MENU.{len(registrados)}"

    monkeypatch.setattr(whatsapp_bot_service, "enviar_mensaje_texto", _envio_falso)
    return registrados


def _payload_opcion(**overrides) -> dict:
    """Payload mínimo para crear una opción de tipo ``TEXTO``."""
    payload = {
        "numero": "5",
        "titulo": "Turnos",
        "tipo": "TEXTO",
        "respuesta": "Escribinos tu nombre y te damos un turno.",
        "activa": True,
        "orden": 5,
    }
    payload.update(overrides)
    return payload


def _crear_opcion(client, **overrides) -> dict:
    """Crea una opción por API y devuelve la representación devuelta."""
    r = client.post(MENU, json=_payload_opcion(**overrides))
    assert r.status_code == 201, r.text
    return r.json()


def _payload_texto(
    texto: str, *, telefono: str = TELEFONO, wamid: str = "wamid.MENU.1"
) -> dict:
    """Payload de Meta para un mensaje de texto del paciente."""
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


# ---------------------------------------------------------------------------
# Menú histórico como respaldo
# ---------------------------------------------------------------------------


def test_tabla_vacia_usa_el_menu_historico_y_lo_siembra_una_sola_vez(db_session):
    assert whatsapp_menu_service.contar_opciones(db_session) == 0

    # ``obtener_menu_vigente`` no escribe: devuelve las cuatro históricas.
    vigente = whatsapp_menu_service.obtener_menu_vigente(db_session)
    assert [o.numero for o in vigente] == ["1", "2", "3", "4"]
    assert all(o.activa is True for o in vigente)
    assert whatsapp_menu_service.contar_opciones(db_session) == 0

    # La siembra de ``init_db`` es idempotente.
    assert whatsapp_menu_service.sembrar_menu_por_defecto(db_session) == 4
    assert whatsapp_menu_service.sembrar_menu_por_defecto(db_session) == 0

    opciones = whatsapp_menu_service.obtener_menu_vigente(db_session)
    assert whatsapp_bot_service.mensaje_menu(opciones) == whatsapp_bot_service.MENU_PRINCIPAL


# ---------------------------------------------------------------------------
# CRUD del panel
# ---------------------------------------------------------------------------


def test_crud_del_menu_whatsapp(client):
    assert client.get(MENU).json() == []

    creada = _crear_opcion(client)
    assert creada["numero"] == "5"
    assert creada["titulo"] == "Turnos"
    assert creada["tipo"] == "TEXTO"
    assert creada["activa"] is True
    opcion_id = creada["id"]

    # El dígito es la tecla que responde el paciente: no se puede repetir.
    repetida = client.post(MENU, json=_payload_opcion(numero=" 5 ", titulo="Otra"))
    assert repetida.status_code == 409, repetida.text
    assert "número 5" in repetida.json()["detail"]

    # TEXTO sin texto no tendría nada para enviar (valida Pydantic).
    sin_texto = client.post(
        MENU, json=_payload_opcion(numero="6", respuesta=None, titulo="Sin texto")
    )
    assert sin_texto.status_code == 422, sin_texto.text

    # Un solo dígito por opción.
    dos_digitos = client.post(MENU, json=_payload_opcion(numero="10"))
    assert dos_digitos.status_code == 422, dos_digitos.text

    # PATCH sólo cambia lo enviado.
    modificar = client.patch(f"{MENU}/{opcion_id}", json={"titulo": "Turnos y consultas"})
    assert modificar.status_code == 200, modificar.text
    assert modificar.json()["titulo"] == "Turnos y consultas"
    assert modificar.json()["respuesta"] == creada["respuesta"]

    # En PATCH, «sin cambios» es omitir el campo: null se rechaza con 422 y no
    # llega a la base (antes terminaba como un 409 de número repetido).
    for campo in ("numero", "titulo", "tipo", "activa", "orden"):
        nulo = client.patch(f"{MENU}/{opcion_id}", json={campo: None})
        assert nulo.status_code == 422, (campo, nulo.text)

    # Pasar a TEXTO una opción sin respuesta se rechaza en el servicio.
    precio = _crear_opcion(
        client, numero="8", titulo="Precio", tipo="PRECIO", respuesta=None, orden=8
    )
    sin_respuesta = client.patch(f"{MENU}/{precio['id']}", json={"tipo": "TEXTO"})
    assert sin_respuesta.status_code == 422, sin_respuesta.text
    assert "necesita el texto" in sin_respuesta.json()["detail"]

    # DELETE es baja lógica: la fila se conserva y se puede reactivar.
    baja = client.delete(f"{MENU}/{opcion_id}")
    assert baja.status_code == 200, baja.text
    assert baja.json()["activa"] is False

    activas = client.get(MENU, params={"activas": True}).json()
    assert [o["id"] for o in activas] == [precio["id"]]
    inactivas = client.get(MENU, params={"activas": False}).json()
    assert [o["id"] for o in inactivas] == [opcion_id]
    assert client.get(f"{MENU}/{opcion_id}").json()["activa"] is False

    reactivar = client.patch(f"{MENU}/{opcion_id}", json={"activa": True})
    assert reactivar.status_code == 200, reactivar.text
    assert reactivar.json()["activa"] is True

    # PUT reemplaza todos los campos.
    reemplazo = client.put(
        f"{MENU}/{opcion_id}",
        json=_payload_opcion(
            numero="9", titulo="Hablar con alguien", tipo="SECRETARIA", respuesta=None, orden=9
        ),
    )
    assert reemplazo.status_code == 200, reemplazo.text
    assert reemplazo.json()["numero"] == "9"
    assert reemplazo.json()["tipo"] == "SECRETARIA"
    assert reemplazo.json()["respuesta"] is None

    assert client.get(f"{MENU}/999999").status_code == 404
    assert client.delete(f"{MENU}/999999").status_code == 404


def test_una_carrera_con_el_numero_repetido_devuelve_conflicto(client, monkeypatch):
    """Si otra petición gana entre la validación y el ``commit``, gana el índice único.

    Se anula la comprobación previa para simular exactamente esa ventana: el
    ``IntegrityError`` del índice tiene que salir como ``409``, no como ``500``.
    """
    _crear_opcion(client, numero="5", titulo="Turnos")
    existente = _crear_opcion(client, numero="6", titulo="Otra opción", orden=6)
    monkeypatch.setattr(
        whatsapp_menu_service, "_validar_numero_unico", lambda *args, **kwargs: None
    )

    carrera_alta = client.post(
        MENU, json=_payload_opcion(numero="5", titulo="Repetida")
    )
    assert carrera_alta.status_code == 409, carrera_alta.text
    assert "número 5" in carrera_alta.json()["detail"]

    carrera_edicion = client.patch(
        f"{MENU}/{existente['id']}", json={"numero": "5"}
    )
    assert carrera_edicion.status_code == 409, carrera_edicion.text
    assert "número 5" in carrera_edicion.json()["detail"]

    # Sólo queda la opción original: las dos carreras se revirtieron completas.
    assert [o["numero"] for o in client.get(MENU).json()] == ["5", "6"]


# ---------------------------------------------------------------------------
# Vista previa y restauración
# ---------------------------------------------------------------------------


def test_vista_previa_devuelve_el_texto_exacto_del_bot(client):
    _crear_opcion(
        client,
        numero="2",
        titulo="Hablar con una secretaria",
        tipo="SECRETARIA",
        respuesta=None,
        orden=2,
    )
    _crear_opcion(
        client,
        numero="1",
        titulo="Promo de invierno",
        respuesta="Blanqueamiento con 20% de descuento.",
        orden=1,
    )
    # Una opción dada de baja no aparece en la vista previa.
    baja = _crear_opcion(client, numero="9", titulo="Opción vieja", orden=9)
    assert client.delete(f"{MENU}/{baja['id']}").status_code == 200

    r = client.get(f"{MENU}/vista-previa")

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["opciones"] == 2
    assert data["texto"] == (
        whatsapp_bot_service.ENCABEZADO_MENU
        + "1️⃣ Promo de invierno\n2️⃣ Hablar con una secretaria"
    )


def test_restaurar_vuelve_a_las_cuatro_opciones_historicas(client):
    _crear_opcion(client, numero="9", titulo="Opción personalizada")

    r = client.post(f"{MENU}/restaurar")

    assert r.status_code == 200, r.text
    opciones = r.json()
    assert [o["numero"] for o in opciones] == ["1", "2", "3", "4"]
    assert [o["tipo"] for o in opciones] == [
        "PRECIO",
        "OBRAS_SOCIALES",
        "UBICACION",
        "SECRETARIA",
    ]
    assert len(client.get(MENU).json()) == 4


def test_restaurar_revierte_todo_si_el_commit_falla(client, db_session, monkeypatch):
    """Ante una carrera, el menú anterior no puede quedar borrado a medias."""
    _crear_opcion(client, numero="9", titulo="Opción personalizada")
    commit_original = db_session.commit

    def _commit_con_conflicto():
        raise IntegrityError(
            "INSERT INTO opciones_menu_whatsapp ...",
            {},
            Exception("UNIQUE constraint failed: opciones_menu_whatsapp.numero"),
        )

    monkeypatch.setattr(db_session, "commit", _commit_con_conflicto)
    r = client.post(f"{MENU}/restaurar")
    # Se recupera el commit real para poder consultar el estado final.
    monkeypatch.setattr(db_session, "commit", commit_original)

    assert r.status_code == 409, r.text
    assert "Volvé a intentarlo" in r.json()["detail"]
    # El rollback dejó intacta la configuración que existía antes de restaurar.
    assert [o["numero"] for o in client.get(MENU).json()] == ["9"]


# ---------------------------------------------------------------------------
# El bot usa el menú configurado
# ---------------------------------------------------------------------------


def test_el_bot_respeta_el_orden_los_tipos_y_los_numeros_configurados(
    client, db_session, envios
):
    # Precio de la consulta (lo usa la opción PRECIO).
    consulta = client.post(
        f"{API}/prestaciones",
        json={
            "codigo_nomenclador": "10100",
            "descripcion": "Consulta odontológica",
            "valor_base": 18500,
        },
    )
    assert consulta.status_code == 201, consulta.text

    _crear_opcion(
        client,
        numero="1",
        titulo="Promo de invierno",
        respuesta="Blanqueamiento con 20% de descuento.",
        orden=1,
    )
    _crear_opcion(client, numero="2", titulo="Precio", tipo="PRECIO", respuesta=None, orden=2)
    _crear_opcion(
        client,
        numero="3",
        titulo="Coberturas",
        tipo="OBRAS_SOCIALES",
        respuesta=None,
        orden=3,
    )
    _crear_opcion(
        client, numero="4", titulo="Dónde estamos", tipo="UBICACION", respuesta=None, orden=4
    )
    _crear_opcion(
        client,
        numero="5",
        titulo="Hablar con una secretaria",
        tipo="SECRETARIA",
        respuesta=None,
        orden=5,
    )

    # "hola" → menú con los títulos y el orden cargados en el panel.
    saludo = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "hola")
    assert saludo.tipo == "MENU"
    assert saludo.atencion_manual is False
    assert saludo.respuesta == (
        whatsapp_bot_service.ENCABEZADO_MENU
        + "1️⃣ Promo de invierno\n"
        "2️⃣ Precio\n"
        "3️⃣ Coberturas\n"
        "4️⃣ Dónde estamos\n"
        "5️⃣ Hablar con una secretaria"
    )
    assert envios[-1]["texto"] == saludo.respuesta

    # "1" → el texto libre cargado por la secretaría.
    texto = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "1")
    assert texto.tipo == "TEXTO"
    assert texto.opcion == "1"
    assert texto.respuesta == "Blanqueamiento con 20% de descuento."
    assert envios[-1]["texto"] == texto.respuesta

    # "2" → precio real, referenciando los números nuevos de coberturas y secretaría.
    precio = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "2")
    assert precio.tipo == "PRECIO"
    assert "$18.500,00" in precio.respuesta
    assert "*3*" in precio.respuesta
    assert "*5*" in precio.respuesta

    # "3" → obras sociales (sin coberturas cargadas) con el número de secretaría.
    obras = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "3")
    assert obras.tipo == "OBRAS_SOCIALES"
    assert "*5*" in obras.respuesta

    # "4" → ubicación y horarios.
    ubicacion = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "4")
    assert ubicacion.tipo == "UBICACION"
    assert "Dirección:" in ubicacion.respuesta

    # "5" → derivación: no se envía nada y queda para la secretaría.
    enviados_antes = len(envios)
    manual = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "5")
    assert manual.tipo == "MANUAL"
    assert manual.atencion_manual is True
    assert manual.respuesta is None
    assert len(envios) == enviados_antes

    # Una tecla que ya no existe en el menú también deriva a atención manual.
    inexistente = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "9")
    assert inexistente.atencion_manual is True
    assert inexistente.opcion == "9"


def test_si_todas_las_opciones_estan_de_baja_el_bot_pide_atencion_manual(
    client, db_session, envios
):
    primera = _crear_opcion(client, numero="1", titulo="Precio", tipo="PRECIO", respuesta=None)
    segunda = _crear_opcion(
        client, numero="2", titulo="Ubicación", tipo="UBICACION", respuesta=None
    )
    assert client.delete(f"{MENU}/{primera['id']}").status_code == 200
    assert client.delete(f"{MENU}/{segunda['id']}").status_code == 200

    vista = client.get(f"{MENU}/vista-previa").json()
    assert vista["opciones"] == 0
    assert "No hay opciones activas" in vista["texto"]

    saludo = whatsapp_bot_service.responder_mensaje(db_session, TELEFONO, "hola")
    assert saludo.tipo == "MANUAL"
    assert saludo.atencion_manual is True
    assert envios == []


def test_el_webhook_usa_el_texto_configurado_en_el_panel(client, envios):
    _crear_opcion(
        client,
        numero="1",
        titulo="Promo de invierno",
        respuesta="Blanqueamiento con 20% de descuento.",
    )

    r = client.post(
        f"{API}/whatsapp/webhook",
        json=_payload_texto("1", wamid="wamid.MENU.TEXTO.1"),
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tipo"] == "BOT"
    assert data["atencion_manual"] is False
    assert data["respuesta"] == "Blanqueamiento con 20% de descuento."
    assert envios == [
        {"telefono": TELEFONO, "texto": "Blanqueamiento con 20% de descuento."}
    ]

