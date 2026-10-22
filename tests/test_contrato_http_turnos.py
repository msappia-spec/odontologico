"""Contrato HTTP entre el dashboard y la API: métodos de turnos y CORS.

Contexto del incidente: al pulsar "Guardar cambios" (editar) o "Cancelar" el
navegador recibía ``405 Method Not Allowed`` y la acción no llegaba a la base.
El endpoint que actualiza turnos es ``PATCH /api/v1/turnos/{id}``; estas pruebas
fijan ese contrato desde los tres lados:

* **API**: ``PATCH`` cancela, persiste ``CANCELADO`` y libera el horario;
  ``PUT``/``DELETE`` responden 405 y lo declaran en la cabecera ``Allow``.
* **Frontend**: los recursos estáticos usan exactamente los métodos que
  ``app.openapi()`` declara, así una desalineación rompe la suite de pytest en
  lugar de fallar recién en el navegador.
* **CORS**: el *preflight* autoriza GET, POST, PUT, PATCH, DELETE y OPTIONS.
"""

import re
from datetime import timedelta
from pathlib import Path

from app.core.datetime_utils import utcnow
from app.main import app
from app.models.paciente import Paciente
from app.web.router import STATIC_DIR

API = "/api/v1"
RUTA_TURNO = "/api/v1/turnos/{turno_id}"
METODOS_CORS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
ORIGEN_EXTERNO = "http://localhost:5173"


def _rango(
    *,
    dias: int = 5,
    hora: int = 9,
    minuto: int = 0,
    duracion_min: int = 30,
) -> dict:
    """Devuelve un rango horario válido expresado en ISO-8601."""
    inicio = (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=minuto, second=0, microsecond=0
    )
    fin = inicio + timedelta(minutes=duracion_min)
    return {
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": fin.isoformat(),
    }


def _crear(client, doctor_id: int, paciente_id: int, **kwargs) -> dict:
    """Crea un turno por API y devuelve su representación."""
    payload = {"paciente_id": paciente_id, "doctor_id": doctor_id, **_rango(**kwargs)}
    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_paciente(db_session, nombre: str = "Paciente Dos") -> Paciente:
    paciente = Paciente(nombre=nombre, telefono="+5491199998888")
    db_session.add(paciente)
    db_session.commit()
    db_session.refresh(paciente)
    return paciente


def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) La cancelación funciona con PATCH y libera el calendario
# ---------------------------------------------------------------------------


def test_patch_cancelar_turno_persiste_cancelado_y_libera_el_horario(
    client, doctor, paciente, db_session
):
    """PATCH con ``estado_turno=CANCELADO`` deja el hueco libre para otro paciente."""
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.patch(f"{API}/turnos/{turno['id']}", json={"estado_turno": "CANCELADO"})

    assert r.status_code == 200, r.text
    assert r.json()["estado_turno"] == "CANCELADO"

    # El estado quedó persistido.
    detalle = client.get(f"{API}/turnos/{turno['id']}").json()
    assert detalle["estado_turno"] == "CANCELADO"

    # El horario se libera: otro paciente puede ocuparlo con el mismo doctor.
    otro = _crear_paciente(db_session)
    r2 = client.post(
        f"{API}/turnos",
        json={"paciente_id": otro.id, "doctor_id": doctor.id, **_rango(hora=9)},
    )
    assert r2.status_code == 201, r2.text


def test_patch_reprogramar_turno_sigue_funcionando(client, doctor, paciente):
    """El mismo PATCH reprograma (caso "Guardar cambios" del modal de edición)."""
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.patch(f"{API}/turnos/{turno['id']}", json=_rango(hora=12))

    assert r.status_code == 200, r.text
    assert r.json()["fecha_hora_inicio"] == _rango(hora=12)["fecha_hora_inicio"]


# ---------------------------------------------------------------------------
# 2) PUT y DELETE no existen en la ruta: 405 con cabecera Allow
# ---------------------------------------------------------------------------


def test_put_en_la_ruta_de_turnos_devuelve_405(client, doctor, paciente):
    """La API no expone PUT: reprogramar es siempre PATCH (y da 405 con PUT)."""
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.put(f"{API}/turnos/{turno['id']}", json=_rango(hora=10))

    assert r.status_code == 405, r.text
    # Starlette informa en ``Allow`` la primera ruta que coincide por ruta pero no
    # por método; lo relevante es que el método enviado no esté habilitado.
    assert "PUT" not in r.headers.get("allow", "")
    assert r.json()["detail"] == "Method Not Allowed"


def test_delete_en_la_ruta_de_turnos_devuelve_405(client, doctor, paciente):
    """Para quitar un turno se cancela con PATCH; no hay borrado por DELETE."""
    turno = _crear(client, doctor.id, paciente.id, hora=9)

    r = client.delete(f"{API}/turnos/{turno['id']}")

    assert r.status_code == 405, r.text
    assert "DELETE" not in r.headers.get("allow", "")

    # El turno sigue existiendo e intacto (el DELETE no borró nada).
    detalle = client.get(f"{API}/turnos/{turno['id']}")
    assert detalle.status_code == 200
    assert detalle.json()["estado_turno"] == "PENDIENTE"


def test_la_api_declara_get_y_patch_en_la_ruta_de_turnos():
    """Contrato del backend: sólo se lee (GET) y se actualiza (PATCH)."""
    assert set(app.openapi()["paths"][RUTA_TURNO]) == {"get", "patch"}


# ---------------------------------------------------------------------------
# 3) El frontend usa los métodos que el backend declara
# ---------------------------------------------------------------------------


def test_el_dashboard_actualiza_turnos_con_patch():
    """calendar.js (cancelar/reactivar) y app.js (editar) usan PATCH, no PUT."""
    calendar_js = _leer_estatico("calendar.js")
    app_js = _leer_estatico("app.js")

    assert re.search(r'method:\s*"PATCH"', calendar_js), "faltó el PATCH al cancelar"
    # En app.js el método se elige con un ternario: PATCH al editar, POST al crear.
    assert '"PATCH"' in app_js and '"POST"' in app_js

    # Ningún recurso del dashboard usa PUT/DELETE contra /turnos: esa ruta sólo
    # expone GET y PATCH. Los PUT/DELETE del panel apuntan a `/doctores/`: la
    # agenda semanal (/doctores/{id}/disponibilidad) y la gestión del profesional
    # (PUT de edición y DELETE de baja lógica en /doctores/{id}), todos declarados
    # por la API (app/api/v1/endpoints/disponibilidad.py y doctores.py). El panel
    # de anamnesis agrega el PUT interno que carga la ficha de un turno.
    assert not re.search(r'method:\s*"(PUT|DELETE)"', calendar_js)
    assert not re.search(r'method:\s*"(PUT|DELETE)"', _leer_estatico("index.html"))

    usos_put_delete = re.findall(
        r"api\(\s*`([^`]+)`\s*,\s*\{\s*method:\s*\"(?:PUT|DELETE)\"", app_js
    )
    assert usos_put_delete, "app.js debería gestionar la agenda semanal con PUT/DELETE"
    permitidos = ("/doctores/", "/anamnesis/panel/")
    assert all(ruta.startswith(permitidos) for ruta in usos_put_delete), usos_put_delete
    assert all("turnos" not in ruta for ruta in usos_put_delete), usos_put_delete


def test_los_botones_de_editar_y_cancelar_llegan_al_patch_de_turnos():
    """Los botones del dashboard apuntan al endpoint de actualización de turnos."""
    app_js = _leer_estatico("app.js")
    calendar_js = _leer_estatico("calendar.js")

    # app.js renderiza los botones y calendar.js los maneja por delegación.
    assert "data-editar-turno=" in app_js
    assert "data-cancelar-turno=" in app_js
    assert '"[data-editar-turno]"' in calendar_js
    assert '"[data-cancelar-turno]"' in calendar_js

    # Editar y cancelar comparten el endpoint de actualización.
    assert "`/turnos/${id}`" in calendar_js
    assert "`/turnos/${state.turnoEditId}`" in app_js
    # "Guardar cambios" del modal envía el formulario que hace el PATCH.
    assert '"Guardar cambios"' in calendar_js
    assert 'id="form-turno"' in _leer_estatico("index.html")


# ---------------------------------------------------------------------------
# 4) CORS: preflight y cabeceras en respuestas reales
# ---------------------------------------------------------------------------


def test_preflight_de_cors_autoriza_put_patch_y_delete(client):
    r = client.options(
        f"{API}/turnos/1",
        headers={
            "Origin": ORIGEN_EXTERNO,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert r.status_code == 200, r.text
    permitidos = {
        m.strip() for m in r.headers["access-control-allow-methods"].split(",")
    }
    assert METODOS_CORS <= permitidos, permitidos
    assert r.headers["access-control-allow-origin"] in {"*", ORIGEN_EXTERNO}
    assert "content-type" in r.headers["access-control-allow-headers"].lower()


def test_respuesta_real_incluye_cabecera_cors(client):
    r = client.get(f"{API}/turnos", headers={"Origin": ORIGEN_EXTERNO})

    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in {"*", ORIGEN_EXTERNO}


def test_options_sin_cabeceras_cors_no_es_preflight(client):
    """Sin ``Origin``/``Access-Control-Request-Method`` no hay preflight: 405."""
    r = client.options(f"{API}/turnos/1")

    assert r.status_code == 405
    # El middleware CORS sólo responde al preflight real (con cabeceras CORS).
    assert "access-control-allow-methods" not in r.headers
