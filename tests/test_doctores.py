"""Pruebas de la gestión de doctores: edición (``PUT``/``PATCH``) y baja lógica.

El panel permite editar al profesional desde un modal y darlo de baja sin perder
sus turnos. Estas pruebas fijan ese contrato desde los dos lados:

* **API**: ``PUT /api/v1/doctores/{id}`` reemplaza el recurso completo;
  ``PATCH`` toca sólo los campos enviados (es el que usa el toggle *Activo* del
  modal, ``{"activo": true/false}``); ``DELETE`` es **baja lógica** (conserva la
  fila y los turnos, deja de ofrecer al odontólogo en las agendas y se revierte
  con ``PATCH``).
* **Frontend**: la vista *Doctores* trae el modal de edición y los botones de
  baja/reactivación que llaman a esos endpoints (``app.js``).
"""

import re
from datetime import timedelta
from pathlib import Path

from app.core.datetime_utils import utcnow
from app.main import app
from app.web.router import STATIC_DIR

API = "/api/v1"
RUTA_COLECCION = "/api/v1/doctores"
RUTA_ITEM = "/api/v1/doctores/{doctor_id}"


def _payload(**overrides) -> dict:
    """Datos de alta/edición de un doctor, con posibilidad de sobreescribir."""
    datos = {"nombre": "Dra. Ana Pérez", "especialidad": "Ortodoncia", "activo": True}
    datos.update(overrides)
    return datos


def _crear(client, **overrides) -> dict:
    """Crea un doctor por API y devuelve su representación."""
    r = client.post(f"{API}/doctores", json=_payload(**overrides))
    assert r.status_code == 201, r.text
    return r.json()


def _rango(dias: int = 5, hora: int = 9, duracion_min: int = 30) -> dict:
    """Rango horario válido expresado en ISO-8601 (igual que el resto de la suite)."""
    inicio = (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0
    )
    return {
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": (inicio + timedelta(minutes=duracion_min)).isoformat(),
    }


def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Edición: PUT (reemplazo) y PATCH (parcial)
# ---------------------------------------------------------------------------


def test_put_reemplaza_todos_los_datos_del_doctor(client):
    creado = _crear(client)

    r = client.put(
        f"{API}/doctores/{creado['id']}",
        json=_payload(nombre="Dr. Julián Sosa", especialidad="Endodoncia"),
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["nombre"] == "Dr. Julián Sosa"
    assert data["especialidad"] == "Endodoncia"

    # El cambio quedó persistido.
    assert client.get(f"{API}/doctores/{creado['id']}").json()["nombre"] == "Dr. Julián Sosa"


def test_put_sin_activo_deja_al_profesional_activo(client):
    """``PUT`` es un reemplazo total: omitir ``activo`` no da de baja a nadie."""
    dado_de_baja = _crear(client, nombre="Dra. Reincorporada", activo=False)
    assert dado_de_baja["activo"] is False

    r = client.put(
        f"{API}/doctores/{dado_de_baja['id']}",
        json={"nombre": "Dra. Reincorporada", "especialidad": "Ortodoncia"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["activo"] is True


def test_patch_actualiza_solo_los_campos_enviados(client):
    creado = _crear(client)

    r = client.patch(f"{API}/doctores/{creado['id']}", json={"especialidad": "Periodoncia"})

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["especialidad"] == "Periodoncia"
    assert data["nombre"] == creado["nombre"]      # el resto no cambia
    assert data["activo"] is True


def test_patch_activo_false_da_de_baja_y_true_reactiva(client):
    creado = _crear(client)

    baja = client.patch(f"{API}/doctores/{creado['id']}", json={"activo": False})
    assert baja.status_code == 200, baja.text
    assert baja.json()["activo"] is False

    alta = client.patch(f"{API}/doctores/{creado['id']}", json={"activo": True})
    assert alta.status_code == 200, alta.text
    assert alta.json()["activo"] is True
    assert alta.json()["nombre"] == creado["nombre"]   # el PATCH no tocó los datos


# ---------------------------------------------------------------------------
# 2) Baja lógica: DELETE conserva la fila y los turnos
# ---------------------------------------------------------------------------


def test_delete_es_baja_logica_y_conserva_los_turnos(client, doctor, paciente):
    turno = client.post(
        f"{API}/turnos",
        json={"paciente_id": paciente.id, "doctor_id": doctor.id, **_rango()},
    )
    assert turno.status_code == 201, turno.text
    turno_id = turno.json()["id"]

    r = client.delete(f"{API}/doctores/{doctor.id}")

    assert r.status_code == 200, r.text
    assert r.json()["activo"] is False

    # El profesional sigue existiendo (no se borró la fila)...
    assert client.get(f"{API}/doctores/{doctor.id}").status_code == 200
    # ...y su turno también, intacto y apuntando al mismo odontólogo.
    detalle = client.get(f"{API}/turnos/{turno_id}")
    assert detalle.status_code == 200
    assert detalle.json()["doctor_id"] == doctor.id


def test_delete_es_idempotente(client, doctor):
    primera = client.delete(f"{API}/doctores/{doctor.id}")
    segunda = client.delete(f"{API}/doctores/{doctor.id}")

    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 200, segunda.text
    assert segunda.json()["activo"] is False


def test_el_listado_filtra_por_estado(client, doctor):
    activo = _crear(client, nombre="Dr. Activo")
    dado_de_baja = _crear(client, nombre="Dr. Inactivo")
    assert client.delete(f"{API}/doctores/{dado_de_baja['id']}").status_code == 200

    todos = {d["id"] for d in client.get(f"{API}/doctores").json()}
    assert {doctor.id, activo["id"], dado_de_baja["id"]} <= todos

    activos = [
        d["id"] for d in client.get(f"{API}/doctores", params={"activos": True}).json()
    ]
    assert activo["id"] in activos
    assert dado_de_baja["id"] not in activos

    inactivos = [
        d["id"] for d in client.get(f"{API}/doctores", params={"activos": False}).json()
    ]
    assert inactivos == [dado_de_baja["id"]]


def test_un_doctor_dado_de_baja_no_recibe_turnos_nuevos(client, doctor, paciente):
    assert client.delete(f"{API}/doctores/{doctor.id}").status_code == 200

    r = client.post(
        f"{API}/turnos",
        json={"paciente_id": paciente.id, "doctor_id": doctor.id, **_rango()},
    )

    assert r.status_code == 404, r.text
    assert "baja" in r.json()["detail"].lower()

    # Al reactivarlo vuelve a poder recibir turnos.
    reactivado = client.patch(f"{API}/doctores/{doctor.id}", json={"activo": True})
    assert reactivado.status_code == 200, reactivado.text
    nuevo = client.post(
        f"{API}/turnos",
        json={"paciente_id": paciente.id, "doctor_id": doctor.id, **_rango(hora=11)},
    )
    assert nuevo.status_code == 201, nuevo.text


# ---------------------------------------------------------------------------
# 3) Errores y contrato HTTP
# ---------------------------------------------------------------------------


def test_gestion_sobre_un_doctor_inexistente_devuelve_404(client):
    inexistente = f"{API}/doctores/987654"

    assert client.get(inexistente).status_code == 404
    assert client.put(inexistente, json=_payload()).status_code == 404
    assert client.patch(inexistente, json={"activo": False}).status_code == 404
    assert client.delete(inexistente).status_code == 404


def test_la_api_declara_los_metodos_de_gestion():
    """Contrato del backend: el recurso expone CRUD completo con baja lógica."""
    rutas = app.openapi()["paths"]

    assert set(rutas[RUTA_COLECCION]) == {"get", "post"}
    assert set(rutas[RUTA_ITEM]) == {"get", "put", "patch", "delete"}


def test_la_vista_de_doctores_ofrece_editar_baja_y_reactivacion():
    """El panel trae el modal de edición y los botones que llaman a la API."""
    html = _leer_estatico("index.html")
    app_js = _leer_estatico("app.js")

    assert 'id="view-doctores"' in html
    assert 'id="lista-doctores"' in html
    assert 'id="form-doctor-edicion"' in html
    assert 'id="modal-doctor"' in html

    # Botones de las filas (los maneja la delegación de eventos de app.js).
    assert "data-editar-doctor=" in app_js
    assert "data-baja-doctor=" in app_js
    assert "data-reactivar-doctor=" in app_js
    assert '"PUT"' in app_js
    assert re.search(r"api\(`/doctores/\$\{[^`]+\}`, \{\s*method: \"DELETE\"", app_js)

