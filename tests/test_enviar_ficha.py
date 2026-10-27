"""Pruebas del envío manual de la ficha de salud (Módulo 4).

Cubren las dos piezas nuevas del envío del enlace:

1. **API**: ``GET /anamnesis/panel/{turno}/enlace`` y
   ``POST /anamnesis/panel/{turno}/enviar`` (``canal=manual`` y ``canal=api``).
   Se verifica que el enlace lo firma el servidor, que el mensaje viaja listo
   para copiar y que mandar la ficha **no** marca el recordatorio del turno
   (``estado_whatsapp``) como enviado.
2. **CLI**: ``scripts/enviar_ficha`` contra un **servidor HTTP falso**
   (``http.server`` en un hilo sobre ``127.0.0.1``), igual que
   ``test_replicar_datos_api``: las pruebas no tocan la red ni la base de
   producción.
3. **Panel**: el botón de cada fila (``Enviar ficha`` abre WhatsApp Web con el
   mensaje ya escrito para el paciente) y el modal de «Más opciones», que
   muestra el texto, el enlace como link clickeable (con aviso si la URL es
   ``localhost``), lo copia y manda con ``canal=api`` (ids de ``index.html`` y
   contrato con ``app.js``). El mensaje cierra con el enlace en su propia línea
   para que WhatsApp lo muestre en azul.
"""

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.anamnesis_token import (
    construir_enlace_anamnesis,
    validar_token_anamnesis,
)
from app.core.config import settings
from app.core.whatsapp_utils import (
    construir_enlace_click_to_chat,
    normalizar_telefono_whatsapp,
)
from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.paciente import Paciente
from app.services import whatsapp_service
from scripts import enviar_ficha as cli
from scripts.enviar_ficha import URL_POR_DEFECTO, elegir_turno, main
from tests.test_anamnesis import _crear_turno

API = "/api/v1"


def _crear_turno_cancelado(db_session, doctor, paciente, *, dias=2):
    """Turno futuro ya cancelado (el panel y el envío lo rechazan con 409)."""
    turno = _crear_turno(db_session, doctor, paciente, dias=dias)
    turno.estado_turno = EstadoTurno.CANCELADO
    db_session.commit()
    return turno


# ---------------------------------------------------------------------------
# 1) API: enlace y envío desde el panel interno
# ---------------------------------------------------------------------------


def test_el_enlace_trae_el_mensaje_y_el_whatsapp(client, db_session, doctor, paciente):
    turno = _crear_turno(db_session, doctor, paciente)

    r = client.get(f"{API}/anamnesis/panel/{turno.id}/enlace")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["turno_id"] == turno.id
    assert body["paciente_nombre"] == paciente.nombre
    assert body["doctor_nombre"] == doctor.nombre
    assert body["completado"] is False

    # El enlace lo firma el servidor: el token se valida contra el turno real.
    assert body["enlace"].startswith(f"{settings.url_publica}/anamnesis/")
    token = body["enlace"].rsplit("/", 1)[-1]
    datos = validar_token_anamnesis(token)
    assert (datos.turno_id, datos.paciente_id) == (turno.id, paciente.id)

    # El mensaje llega redactado y con el enlace adentro (se copia tal cual).
    assert paciente.nombre in body["mensaje"]
    assert doctor.nombre in body["mensaje"]
    assert body["enlace"] in body["mensaje"]

    assert body["telefono_whatsapp"] == normalizar_telefono_whatsapp(paciente.telefono)
    assert body["enlace_whatsapp"] == construir_enlace_click_to_chat(
        paciente.telefono, body["mensaje"]
    )
    assert body["enlace_whatsapp"].startswith("https://wa.me/")


def test_el_mensaje_deja_el_enlace_solo_en_su_propia_linea(
    client, db_session, doctor, paciente
):
    """WhatsApp sólo pinta el enlace en azul si la URL arranca una línea.

    El texto terminaba en «... completá tu ficha de salud en este enlace:
    https://...» y WhatsApp lo dejaba como texto gris: el paciente tocaba el
    enlace y no pasaba nada. Ahora la URL va sola, en un párrafo aparte.
    """
    turno = _crear_turno(db_session, doctor, paciente)

    body = client.get(f"{API}/anamnesis/panel/{turno.id}/enlace").json()
    lineas = body["mensaje"].splitlines()

    assert lineas[-1] == body["enlace"]
    assert lineas[-2] == ""
    assert lineas[-3].endswith("enlace:")
    # Sin puntuación pegada a la URL: no queda dentro de la frase anterior.
    assert body["enlace"] not in lineas[-3]


def test_el_recordatorio_tambien_manda_el_enlace_en_su_propia_linea(
    db_session, doctor, paciente
):
    """El bloque de la ficha que suma el recordatorio usa el mismo formato."""
    turno = _crear_turno(db_session, doctor, paciente)

    mensaje = whatsapp_service.construir_mensaje_recordatorio(turno)
    lineas = mensaje.splitlines()

    assert lineas[-1].startswith(f"{settings.url_publica}/anamnesis/")
    assert lineas[-2] == ""
    assert lineas[-3].endswith("enlace:")


def test_el_envio_por_api_pide_la_vista_previa_del_enlace():
    """La Cloud API recibe ``preview_url`` para que el enlace se vea como enlace."""
    preview = whatsapp_service.construir_payload_texto(
        "5491122334455", "Hola", preview_url=True
    )
    assert preview["text"] == {"preview_url": True, "body": "Hola"}
    # El bot sigue igual: contesta con la dirección del consultorio y sin tarjeta.
    assert whatsapp_service.construir_payload_texto("5491122334455", "Hola")["text"] == {
        "preview_url": False,
        "body": "Hola",
    }


def test_el_enlace_sirve_fuera_de_la_ventana_del_panel(
    client, db_session, doctor, paciente
):
    """La secretaría busca por paciente: el turno puede estar lejos en el tiempo."""
    turno = _crear_turno(db_session, doctor, paciente, dias=200)

    listado = client.get(f"{API}/anamnesis/panel?dias=30")
    assert listado.status_code == 200, listado.text
    assert turno.id not in [f["turno_id"] for f in listado.json()]

    r = client.get(f"{API}/anamnesis/panel/{turno.id}/enlace")
    assert r.status_code == 200, r.text
    assert r.json()["turno_id"] == turno.id


def test_el_enlace_permite_un_destino_alternativo(client, db_session, doctor, paciente):
    turno = _crear_turno(db_session, doctor, paciente)

    r = client.get(
        f"{API}/anamnesis/panel/{turno.id}/enlace", params={"telefono": "351 555-1234"}
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["telefono_whatsapp"] == "5493515551234"
    assert body["paciente_telefono"] == paciente.telefono
    assert body["enlace_whatsapp"].startswith("https://wa.me/5493515551234?text=")


def test_el_enlace_rechaza_turnos_inexistentes_y_cancelados(
    client, db_session, doctor, paciente
):
    assert client.get(f"{API}/anamnesis/panel/9999/enlace").status_code == 404

    cancelado = _crear_turno_cancelado(db_session, doctor, paciente)
    r = client.get(f"{API}/anamnesis/panel/{cancelado.id}/enlace")
    assert r.status_code == 409, r.text
    assert "cancelado" in r.json()["detail"].lower()


def test_el_envio_manual_no_envia_ni_marca_el_recordatorio(
    client, db_session, doctor, paciente
):
    """``canal=manual`` sólo devuelve el enlace: la secretaría lo copia."""
    turno = _crear_turno(db_session, doctor, paciente)

    r = client.post(f"{API}/anamnesis/panel/{turno.id}/enviar", json={"canal": "manual"})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["canal"] == "manual"
    assert body["enviado"] is False
    assert body["simulado"] is False
    assert body["whatsapp_mensaje_id"] is None
    assert body["enlace"].startswith(f"{settings.url_publica}/anamnesis/")
    assert body["mensaje"]

    # El recordatorio del turno sigue pendiente y la ficha también.
    db_session.refresh(turno)
    assert turno.estado_whatsapp == EstadoWhatsApp.PENDIENTE_ENVIO
    assert turno.whatsapp_mensaje_id is None
    panel = client.get(f"{API}/anamnesis/panel?dias=30").json()
    fila = next(f for f in panel if f["turno_id"] == turno.id)
    assert fila["completado"] is False


def test_el_envio_sin_canal_es_manual(client, db_session, doctor, paciente):
    """El cuerpo es opcional: el panel puede pedir el enlace con un POST vacío."""
    turno = _crear_turno(db_session, doctor, paciente)

    r = client.post(f"{API}/anamnesis/panel/{turno.id}/enviar")
    assert r.status_code == 200, r.text
    assert r.json()["canal"] == "manual"
    assert r.json()["enviado"] is False


def test_el_envio_por_api_queda_simulado_sin_credenciales(
    client, db_session, doctor, paciente
):
    """La fixture ``whatsapp_sin_credenciales`` deja la Cloud API sin tokens."""
    turno = _crear_turno(db_session, doctor, paciente)

    r = client.post(f"{API}/anamnesis/panel/{turno.id}/enviar", json={"canal": "api"})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["canal"] == "api"
    assert body["enviado"] is True
    assert body["simulado"] is True
    assert body["whatsapp_mensaje_id"]

    # Tampoco acá se toca el recordatorio del turno: la ficha no salió de verdad
    # como recordatorio y se completa recién cuando el paciente responde.
    db_session.refresh(turno)
    assert turno.estado_whatsapp == EstadoWhatsApp.PENDIENTE_ENVIO


def test_el_envio_por_api_exige_un_telefono_valido(client, db_session, doctor):
    sin_celular = Paciente(nombre="Sin Celular", telefono="s/d")
    db_session.add(sin_celular)
    db_session.commit()
    db_session.refresh(sin_celular)
    turno = _crear_turno(db_session, doctor, sin_celular)

    enlace = client.get(f"{API}/anamnesis/panel/{turno.id}/enlace")
    assert enlace.status_code == 200, enlace.text
    assert enlace.json()["telefono_whatsapp"] == ""
    assert enlace.json()["enlace_whatsapp"] == ""

    r = client.post(f"{API}/anamnesis/panel/{turno.id}/enviar", json={"canal": "api"})
    assert r.status_code == 409, r.text
    assert "teléfono" in r.json()["detail"].lower()

    # El envío manual sigue disponible para copiar el enlace a mano.
    manual = client.post(f"{API}/anamnesis/panel/{turno.id}/enviar")
    assert manual.status_code == 200, manual.text
    assert manual.json()["enlace"]


def test_el_envio_rechaza_canales_desconocidos(client, db_session, doctor, paciente):
    turno = _crear_turno(db_session, doctor, paciente)

    r = client.post(f"{API}/anamnesis/panel/{turno.id}/enviar", json={"canal": "sms"})
    assert r.status_code == 422, r.text


def test_el_envio_valida_el_turno_y_usa_el_destino_alternativo(
    client, db_session, doctor, paciente
):
    assert (
        client.post(f"{API}/anamnesis/panel/9999/enviar", json={"canal": "manual"}).status_code
        == 404
    )

    cancelado = _crear_turno_cancelado(db_session, doctor, paciente)
    assert (
        client.post(f"{API}/anamnesis/panel/{cancelado.id}/enviar").status_code == 409
    )

    turno = _crear_turno(db_session, doctor, paciente)
    r = client.post(
        f"{API}/anamnesis/panel/{turno.id}/enviar",
        json={"canal": "manual", "telefono": "351 555-1234"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["telefono_whatsapp"] == "5493515551234"


def test_el_panel_trae_el_mensaje_listo_para_cada_ficha(
    client, db_session, doctor, paciente
):
    """El botón «Enviar ficha» usa estos campos: tienen que venir en el listado."""
    turno = _crear_turno(db_session, doctor, paciente)

    panel = client.get(f"{API}/anamnesis/panel?dias=30").json()
    fila = next(f for f in panel if f["turno_id"] == turno.id)

    assert fila["enlace"] in fila["mensaje"]
    assert fila["telefono_whatsapp"] == normalizar_telefono_whatsapp(paciente.telefono)
    assert fila["enlace_whatsapp"] == construir_enlace_click_to_chat(
        paciente.telefono, fila["mensaje"]
    )


# ---------------------------------------------------------------------------
# 2) CLI de secretaría (servidor HTTP falso, sin red ni base de datos)
# ---------------------------------------------------------------------------

PACIENTES_FALSOS = [
    {"id": 1, "nombre": "Ana Pérez", "dni": "30111222", "telefono": "+5491155550000"},
    {"id": 2, "nombre": "Ana Gómez", "dni": "28999888", "telefono": "+5491144440000"},
    {"id": 3, "nombre": "Carla Ruiz", "dni": "30111223", "telefono": "+5491133330000"},
]


def _turno_falso(turno_id, paciente_id, inicio, estado):
    """Turno del servidor falso con la forma que devuelve ``GET /turnos``."""
    desde = datetime.fromisoformat(inicio)
    return {
        "id": turno_id,
        "paciente_id": paciente_id,
        "doctor_id": 1,
        "fecha_hora_inicio": inicio,
        "fecha_hora_fin": (desde + timedelta(minutes=30)).isoformat(),
        "estado_turno": estado,
    }


#: Turnos del servidor falso: uno futuro, uno futuro cancelado, uno pasado y dos
#: de pacientes que ya no tienen turnos por delante.
TURNOS_FALSOS = [
    _turno_falso(41, 1, "2999-01-01T12:00:00", "PENDIENTE"),
    _turno_falso(42, 1, "2999-06-01T12:00:00", "CANCELADO"),
    _turno_falso(40, 1, "2000-01-01T12:00:00", "PENDIENTE"),
    _turno_falso(50, 2, "2000-02-01T12:00:00", "CANCELADO"),
    _turno_falso(60, 3, "2000-03-01T12:00:00", "PENDIENTE"),
]


class ApiFalsa(BaseHTTPRequestHandler):
    """Doble en memoria de la API que consume el script (mismos contratos)."""

    pacientes: list = []
    turnos: list = []
    consultas: list = []
    envios: list = []
    simulado: bool = False

    def log_message(self, *args) -> None:  # pragma: no cover - silencia el log
        """Evita que cada petición ensucie la salida de pytest."""

    # --- utilidades ---------------------------------------------------------
    def _responder(self, codigo: int, cuerpo=None) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        if cuerpo is not None:
            self.wfile.write(json.dumps(cuerpo).encode("utf-8"))

    def _payload(self) -> dict:
        largo = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(largo).decode("utf-8")) if largo else {}

    def _pacientes(self, consulta: dict) -> list:
        dni = (consulta.get("dni") or [""])[0]
        buscar = (consulta.get("buscar") or [""])[0].lower()
        telefono = (consulta.get("telefono") or [""])[0]
        if dni:
            return [p for p in self.pacientes if str(p["dni"]) == dni]
        if buscar:
            return [p for p in self.pacientes if buscar in p["nombre"].lower()]
        if telefono:
            return [p for p in self.pacientes if p["telefono"] == telefono]
        return list(self.pacientes)


    def _ficha(self, turno_id: int, destino=None):
        """Enlace firmado + mensaje, igual que ``GET /panel/{turno}/enlace``."""
        turno = next((t for t in self.turnos if t["id"] == turno_id), None)
        if turno is None:
            self._responder(404, {"detail": "No existe el turno"})
            return None
        if turno["estado_turno"] == EstadoTurno.CANCELADO.value:
            self._responder(409, {"detail": "El turno está cancelado"})
            return None
        paciente = next(p for p in self.pacientes if p["id"] == turno["paciente_id"])
        telefono = destino or paciente["telefono"]
        enlace = construir_enlace_anamnesis(turno["id"], paciente["id"])
        mensaje = f"Hola {paciente['nombre']}, completá tu ficha de salud en {enlace}"
        return {
            "turno_id": turno["id"],
            "paciente_id": paciente["id"],
            "paciente_nombre": paciente["nombre"],
            "paciente_dni": paciente["dni"],
            "paciente_telefono": paciente["telefono"],
            "telefono_whatsapp": normalizar_telefono_whatsapp(telefono),
            "doctor_nombre": "Dra. Gómez",
            "fecha_hora_inicio": turno["fecha_hora_inicio"],
            "fecha_hora_fin": turno["fecha_hora_fin"],
            "estado_turno": turno["estado_turno"],
            "completado": False,
            "fecha_completado": None,
            "enlace": enlace,
            "mensaje": mensaje,
            "enlace_whatsapp": construir_enlace_click_to_chat(telefono, mensaje),
        }

    # --- verbos -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - nombre exigido por http.server
        partes_url = urlparse(self.path)
        ruta = partes_url.path
        consulta = parse_qs(partes_url.query)
        partes = ruta.strip("/").split("/")
        if ruta == "/api/v1/pacientes":
            return self._responder(200, self._pacientes(consulta))
        if ruta == "/api/v1/turnos":
            paciente_id = int((consulta.get("paciente_id") or ["0"])[0])
            return self._responder(
                200, [t for t in self.turnos if t["paciente_id"] == paciente_id]
            )
        if (
            len(partes) == 6
            and partes[:4] == ["api", "v1", "anamnesis", "panel"]
            and partes[5] == "enlace"
        ):
            destino = (consulta.get("telefono") or [None])[0]
            type(self).consultas.append({"turno": int(partes[4]), "telefono": destino})
            ficha = self._ficha(int(partes[4]), destino)
            if ficha is not None:
                self._responder(200, ficha)
            return
        self._responder(404, {"detail": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802 - nombre exigido por http.server
        partes = urlparse(self.path).path.strip("/").split("/")
        payload = self._payload()
        if (
            len(partes) == 6
            and partes[:4] == ["api", "v1", "anamnesis", "panel"]
            and partes[5] == "enviar"
        ):
            turno_id = int(partes[4])
            type(self).envios.append({"turno": turno_id, "payload": payload})
            ficha = self._ficha(turno_id, payload.get("telefono"))
            if ficha is None:
                return
            canal = payload.get("canal", "manual")
            ficha.update(
                {
                    "canal": canal,
                    "enviado": canal == "api",
                    "simulado": bool(type(self).simulado and canal == "api"),
                    "whatsapp_mensaje_id": "wamid.123" if canal == "api" else None,
                }
            )
            return self._responder(200, ficha)
        self._responder(404, {"detail": "Not Found"})


@pytest.fixture()
def api_falsa():
    """Servidor falso en un puerto libre; devuelve su URL base."""
    ApiFalsa.pacientes = [dict(p) for p in PACIENTES_FALSOS]
    ApiFalsa.turnos = [dict(t) for t in TURNOS_FALSOS]
    ApiFalsa.consultas = []
    ApiFalsa.envios = []
    ApiFalsa.simulado = False

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), ApiFalsa)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"http://127.0.0.1:{servidor.server_port}"
    finally:
        servidor.shutdown()
        servidor.server_close()
        hilo.join(timeout=5)


# --- salida del enlace (modo manual) ----------------------------------------


def test_el_cli_muestra_el_enlace_sin_enviar_nada(api_falsa, capsys):
    assert main(["--url", api_falsa, "--telefono", "549 11 5555-0000"]) == 0

    salida = capsys.readouterr().out
    assert "Ana Pérez" in salida
    assert "Ficha    : PENDIENTE" in salida
    assert "Enlace para el paciente:" in salida
    assert "Mensaje sugerido" in salida
    assert "https://wa.me/" in salida

    # Se buscó por los dígitos del teléfono y se usó el turno vigente más próximo
    # (el cancelado de junio queda afuera), sin mandar nada.
    assert ApiFalsa.consultas == [{"turno": 41, "telefono": None}]
    assert ApiFalsa.envios == []
    # El enlace impreso es el que firma el servidor: la CLI no tiene el secreto.
    enlace = next(linea.strip() for linea in salida.splitlines() if "/anamnesis/" in linea)
    assert validar_token_anamnesis(enlace.rsplit("/", 1)[-1]).turno_id == 41


def test_el_cli_envia_por_la_api_y_muestra_el_mensaje_id(api_falsa, capsys):
    assert main(["--url", api_falsa, "--dni", "30111222", "--api"]) == 0

    salida = capsys.readouterr().out
    assert ApiFalsa.envios == [{"turno": 41, "payload": {"canal": "api"}}]
    assert "Enviado por la WhatsApp Cloud API" in salida
    assert "wamid.123" in salida


def test_el_cli_avisa_cuando_el_envio_queda_simulado(api_falsa, capsys):
    ApiFalsa.simulado = True

    assert main(["--url", api_falsa, "--turno", "41", "--api"]) == 0

    assert "SIMULADO" in capsys.readouterr().out


def test_el_cli_manda_al_destino_alternativo(api_falsa):
    comando = [
        "--url",
        api_falsa,
        "--turno",
        "41",
        "--api",
        "--destino",
        "351 555-1234",
    ]
    assert main(comando) == 0

    assert ApiFalsa.envios == [
        {"turno": 41, "payload": {"canal": "api", "telefono": "351 555-1234"}}
    ]


def test_el_cli_json_devuelve_la_respuesta_cruda(api_falsa, capsys):
    assert main(["--url", api_falsa, "--turno", "41", "--json"]) == 0

    datos = json.loads(capsys.readouterr().out)
    assert datos["turno_id"] == 41
    assert datos["paciente_nombre"] == "Ana Pérez"
    assert "/anamnesis/" in datos["enlace"]


def test_el_cli_listar_muestra_los_turnos_del_paciente(api_falsa, capsys):
    assert main(["--url", api_falsa, "--dni", "30111222", "--listar"]) == 0

    salida = capsys.readouterr().out
    assert "Ana Pérez" in salida
    assert "PENDIENTE" in salida and "CANCELADO" in salida
    assert "--turno <id>" in salida
    # Listar no genera el enlace ni envía nada.
    assert ApiFalsa.consultas == []
    assert ApiFalsa.envios == []


# --- errores explicados -----------------------------------------------------


def test_el_cli_exige_un_criterio_de_busqueda(api_falsa, capsys):
    assert main(["--url", api_falsa]) == 1

    assert "Indicá a quién buscar" in capsys.readouterr().err


def test_el_cli_avisa_si_no_encuentra_al_paciente(api_falsa, capsys):
    assert main(["--url", api_falsa, "--dni", "99999999"]) == 1

    assert "No se encontró ningún paciente" in capsys.readouterr().err


def test_el_cli_pide_elegir_cuando_hay_varios_pacientes(api_falsa, capsys):
    assert main(["--url", api_falsa, "--nombre", "Ana"]) == 1

    error = capsys.readouterr().err
    assert "Hay 2 pacientes" in error
    assert "--dni" in error


def test_el_cli_sugiere_un_turno_cuando_no_hay_futuros(api_falsa, capsys):
    assert main(["--url", api_falsa, "--dni", "30111223"]) == 1

    error = capsys.readouterr().err
    assert "no tiene turnos de hoy en adelante" in error
    assert "#60" in error


def test_el_cli_avisa_cuando_todos_los_turnos_estan_cancelados(api_falsa, capsys):
    assert main(["--url", api_falsa, "--dni", "28999888"]) == 1

    assert "no tiene turnos vigentes" in capsys.readouterr().err


def test_una_api_inalcanzable_devuelve_codigo_1(capsys):
    assert main(["--url", "http://127.0.0.1:1", "--turno", "41"]) == 1

    assert "Error:" in capsys.readouterr().err


# --- elección del turno y valores por defecto -------------------------------


def test_elegir_turno_salta_los_cancelados_y_toma_el_mas_proximo():
    ahora = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
    turnos = [
        _turno_falso(7, 1, "2026-10-27T10:00:00", "CANCELADO"),
        _turno_falso(9, 1, "2026-11-02T10:00:00", "PENDIENTE"),
        _turno_falso(8, 1, "2026-10-28T10:00:00", "PENDIENTE"),
        _turno_falso(6, 1, "2026-10-20T10:00:00", "PENDIENTE"),
    ]

    assert elegir_turno(turnos, ahora=ahora)["id"] == 8


def test_la_url_por_defecto_es_la_app_desplegada():
    assert URL_POR_DEFECTO == "https://odontologico-omega.vercel.app"
    assert cli.URL_POR_DEFECTO == URL_POR_DEFECTO


def test_la_hora_que_muestra_la_cli_es_la_del_panel():
    """El panel dibuja la hora guardada tal cual (``iso.slice(11, 16)``).

    Convertirla como si fuera UTC mostraría una hora distinta de la del panel y
    de la que se le manda al paciente en el mensaje.
    """
    assert cli._hhmm("2026-10-13T15:30:00") == "13/10/2026 15:30"


def test_la_hora_con_zona_se_lleva_a_la_hora_local():
    """Si la API alguna vez manda una zona, no se muestra el valor «crudo»."""
    esperado = datetime(2026, 10, 13, 15, 30, tzinfo=timezone.utc).astimezone()
    con_offset = datetime.fromisoformat("2026-10-13T15:30:00-03:00")

    assert cli._hhmm("2026-10-13T15:30:00Z") == esperado.strftime("%d/%m/%Y %H:%M")
    assert cli._hhmm("2026-10-13T15:30:00-03:00") == con_offset.astimezone().strftime(
        "%d/%m/%Y %H:%M"
    )



# ---------------------------------------------------------------------------
# 3) Panel: el botón «Enviar ficha» y su modal (HTML + app.js)
# ---------------------------------------------------------------------------


def _cuerpo_de_funcion(js: str, nombre: str, siguiente: str) -> str:
    """Fuente de ``nombre`` en app.js, hasta la declaración de ``siguiente``.

    Sirve para revisar el contrato de una función concreta sin parsear todo el
    archivo: los chequeos de texto sueltos no ven un ``f.`` que no existe.
    """
    inicio = js.index(f"function {nombre}(")
    fin = js.index(f"function {siguiente}(", inicio)
    return js[inicio:fin]


def test_la_tabla_de_fichas_ofrece_el_boton_enviar_ficha(client):
    """Cada fila trae «Enviar ficha» (abre WhatsApp) y «Más opciones» (modal)."""
    js = client.get("/static/app.js").text

    assert 'data-ficha-enviar="' in js
    assert 'closest("[data-ficha-enviar]")' in js
    assert "abrirWhatsAppFicha(" in js
    # «Más opciones» mantiene a mano el enlace, el copiado y el envío por API.
    assert 'data-ficha-opciones="' in js
    assert 'closest("[data-ficha-opciones]")' in js
    assert "abrirModalEnviarFicha(" in js


def test_el_modal_de_envio_existe_en_el_html(client):
    """Los ids que usa app.js para armar el modal están en index.html."""
    html = client.get("/").text

    for identificador in (
        "modal-enviar-ficha",
        "modal-enviar-ficha-close",
        "modal-enviar-ficha-cancel",
        "enviar-ficha-titulo",
        "enviar-ficha-subtitulo",
        "enviar-ficha-destino",
        "enviar-ficha-mensaje",
        "enviar-ficha-enlace",
        "enviar-ficha-wa",
        "enviar-ficha-sin-wa",
        "enviar-ficha-error",
        "enviar-ficha-copiar",
        "enviar-ficha-api",
    ):
        assert f'id="{identificador}"' in html


def test_el_modal_copia_el_mensaje_y_manda_por_la_api(client):
    """«Copiar mensaje» copia el texto completo y «Enviar por API» usa canal=api."""
    js = client.get("/static/app.js").text

    assert "copiarMensajeFicha" in js
    # Se copia el mensaje entero (ya trae el enlace), no sólo la URL.
    assert "ficha.mensaje || ficha.enlace" in js
    assert "/anamnesis/panel/${ficha.turno_id}/enviar" in js
    assert 'canal: "api"' in js
    # Sin credenciales de WhatsApp el servidor responde ``simulado``: hay que avisar.
    assert "Envío simulado" in js


def test_el_boton_enviar_ficha_abre_whatsapp_web(client):
    """El clic del listado abre WhatsApp Web con el número del paciente.

    ``abrirWhatsAppFicha`` navega al Click-to-Chat que arma el backend
    (``enlace_whatsapp``) en una pestaña nueva; si el paciente no tiene un
    teléfono válido se abre el modal para copiar el mensaje.
    """
    js = client.get("/static/app.js").text
    cuerpo = _cuerpo_de_funcion(js, "abrirWhatsAppFicha", "abrirModalFicha")

    assert "ficha.enlace_whatsapp" in cuerpo
    assert 'enlace.target = "_blank"' in cuerpo
    # No se abre por script: con "noopener" window.open devuelve null y el
    # navegador puede bloquear la pestaña del chat.
    assert "window.open(" not in cuerpo
    # Sin teléfono válido, el camino de respaldo es el modal con el texto.
    assert "abrirModalEnviarFicha(ficha.turno_id)" in cuerpo


def test_el_enlace_del_modal_es_un_link_clickeable(client):
    """El enlace del modal es un ``<a>`` azul, no un campo de texto readonly.

    Con ``<input readonly>`` el enlace no se veía como enlace (ni azul ni
    clickeable) y la secretaría no podía probarlo antes de mandarlo.
    """
    html = client.get("/").text
    inicio = html.index('id="enviar-ficha-enlace"')
    etiqueta = html[html.rindex("<a ", 0, inicio) : html.index(">", inicio)]

    assert 'id="enviar-ficha-enlace"' in etiqueta
    assert 'target="_blank"' in etiqueta
    assert "underline" in etiqueta
    assert "readonly" not in etiqueta

    js = client.get("/static/app.js").text
    cuerpo = _cuerpo_de_funcion(js, "abrirModalEnviarFicha", "cerrarModalEnviarFicha")

    assert "enlaceFicha.href = ficha.enlace" in cuerpo
    assert "enlaceFicha.textContent" in cuerpo
    # Ya no se escribe el enlace dentro de un campo de texto.
    assert 'getElementById("enviar-ficha-enlace").value' not in cuerpo


def test_el_modal_avisa_cuando_el_enlace_no_es_publico(client):
    """Con ``localhost`` WhatsApp no reconoce el enlace y el toque no hace nada.

    La URL de desarrollo (``http://localhost:8000/anamnesis/<token>``) no tiene
    dominio: WhatsApp la deja como texto gris y, además, en el celular del
    paciente apuntaría a su propio teléfono. El modal lo advierte antes de enviar.
    """
    html = client.get("/").text
    assert 'id="enviar-ficha-local"' in html

    js = client.get("/static/app.js").text
    cuerpo = _cuerpo_de_funcion(js, "abrirModalEnviarFicha", "cerrarModalEnviarFicha")

    assert 'getElementById("enviar-ficha-local")' in cuerpo
    assert "localhost" in cuerpo

    """El modal de envío se armaba con ``f.`` y el clic moría en silencio.

    ``abrirModalEnviarFicha`` sólo tiene ``ficha`` en su ámbito, así que
    cualquier ``f.`` fuera del callback de ``state.fichasSalud.find((f) => ...)``
    es un ``ReferenceError`` que deja el botón sin efecto.
    """
    js = client.get("/static/app.js").text
    cuerpo = _cuerpo_de_funcion(js, "abrirModalEnviarFicha", "cerrarModalEnviarFicha")
    sin_callback = "\n".join(
        linea for linea in cuerpo.splitlines() if "(f) =>" not in linea
    )

    assert "ficha.fecha_hora_inicio" in cuerpo
    assert not re.search(r"(?<![\w.$])f\.", sin_callback)


