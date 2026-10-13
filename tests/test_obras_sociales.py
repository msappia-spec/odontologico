"""Pruebas del catálogo de obras sociales y su vínculo con los pacientes.

Cubren el CRUD del catálogo (``/api/v1/obras-sociales``) —unicidad del nombre
(``409``) y **baja lógica** (``DELETE`` marca ``activa = false``)— y las dos
formas en que el paciente lo consume:

* ``obra_social_id``: la clave foránea al catálogo (la que usa el desplegable).
* ``obra_social``: el texto libre heredado, que el servicio resuelve contra el
  catálogo (vincula la cobertura existente o la crea) para no romper a los
  clientes anteriores.
"""

import re
from pathlib import Path

from app.main import app
from app.models.paciente import Paciente
from app.web.router import STATIC_DIR

API = "/api/v1"
RUTA_COLECCION = "/api/v1/obras-sociales"
RUTA_ITEM = "/api/v1/obras-sociales/{obra_social_id}"


def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


def _crear_obra_social(client, **overrides) -> dict:
    """Crea una cobertura por API y devuelve su representación."""
    payload = {"nombre": "OSDE", "codigo": "OSDE-01", "activa": True}
    payload.update(overrides)
    r = client.post(f"{API}/obras-sociales", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_paciente(client, **overrides) -> dict:
    """Crea un paciente por API y devuelve su representación."""
    payload = {"nombre": "Ana Suárez", "telefono": "+5491155667788"}
    payload.update(overrides)
    r = client.post(f"{API}/pacientes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1) CRUD del catálogo
# ---------------------------------------------------------------------------


def test_crear_y_listar_obras_sociales(client):
    creada = _crear_obra_social(client)

    assert creada["nombre"] == "OSDE"
    assert creada["codigo"] == "OSDE-01"
    assert creada["activa"] is True

    listado = client.get(f"{API}/obras-sociales")
    assert listado.status_code == 200, listado.text
    assert [o["id"] for o in listado.json()] == [creada["id"]]

    detalle = client.get(f"{API}/obras-sociales/{creada['id']}")
    assert detalle.status_code == 200
    assert detalle.json()["nombre"] == "OSDE"


def test_el_nombre_se_normaliza_y_el_codigo_vacio_es_null(client):
    creada = _crear_obra_social(client, nombre="  Medicus  ", codigo="   ")

    assert creada["nombre"] == "Medicus"
    assert creada["codigo"] is None


def test_nombre_duplicado_devuelve_409(client):
    _crear_obra_social(client)

    r = client.post(f"{API}/obras-sociales", json={"nombre": "OSDE"})

    assert r.status_code == 409, r.text
    assert "osde" in r.json()["detail"].lower()


def test_nombre_duplicado_no_distingue_mayusculas(client):
    _crear_obra_social(client, nombre="Swiss Medical")

    r = client.post(f"{API}/obras-sociales", json={"nombre": "  swiss medical "})

    assert r.status_code == 409, r.text


def test_patch_actualiza_solo_los_campos_enviados(client):
    creada = _crear_obra_social(client)

    r = client.patch(f"{API}/obras-sociales/{creada['id']}", json={"codigo": "OSDE-99"})

    assert r.status_code == 200, r.text
    assert r.json()["codigo"] == "OSDE-99"
    assert r.json()["nombre"] == "OSDE"          # el resto no cambia
    assert r.json()["activa"] is True


def test_put_reemplaza_el_nombre_y_reactiva(client):
    creada = _crear_obra_social(client)
    assert client.delete(f"{API}/obras-sociales/{creada['id']}").status_code == 200

    r = client.put(
        f"{API}/obras-sociales/{creada['id']}", json={"nombre": "OSDE Binario"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "OSDE Binario"
    # PUT es un reemplazo total: sin ``activa`` la cobertura vuelve a estar vigente.
    assert r.json()["activa"] is True


def test_patch_no_permite_renombrar_a_una_cobertura_existente(client):
    primera = _crear_obra_social(client, nombre="OSDE")
    segunda = _crear_obra_social(client, nombre="IOMA")

    r = client.patch(f"{API}/obras-sociales/{segunda['id']}", json={"nombre": primera["nombre"]})

    assert r.status_code == 409, r.text
    # Renombrarse a sí misma (mismo nombre, otro espaciado) no es un conflicto.
    r2 = client.patch(f"{API}/obras-sociales/{segunda['id']}", json={"nombre": "  ioma "})
    assert r2.status_code == 200, r2.text
    assert r2.json()["nombre"] == "ioma"


# ---------------------------------------------------------------------------
# 2) Baja lógica y filtros del listado
# ---------------------------------------------------------------------------


def test_delete_es_baja_logica_y_se_puede_reactivar(client):
    creada = _crear_obra_social(client)

    baja = client.delete(f"{API}/obras-sociales/{creada['id']}")
    assert baja.status_code == 200, baja.text
    assert baja.json()["activa"] is False

    # La fila sigue existiendo (no se borró): el detalle responde igual.
    assert client.get(f"{API}/obras-sociales/{creada['id']}").status_code == 200

    # Dar de baja dos veces no es un error.
    assert client.delete(f"{API}/obras-sociales/{creada['id']}").status_code == 200

    reactivada = client.patch(f"{API}/obras-sociales/{creada['id']}", json={"activa": True})
    assert reactivada.status_code == 200, reactivada.text
    assert reactivada.json()["activa"] is True


def test_el_listado_filtra_por_activas_y_por_nombre(client):
    osde = _crear_obra_social(client, nombre="OSDE")
    ioma = _crear_obra_social(client, nombre="IOMA")
    swiss = _crear_obra_social(client, nombre="Swiss Medical")
    assert client.delete(f"{API}/obras-sociales/{swiss['id']}").status_code == 200

    activas = [
        o["id"] for o in client.get(f"{API}/obras-sociales", params={"activas": True}).json()
    ]
    assert osde["id"] in activas and ioma["id"] in activas
    assert swiss["id"] not in activas

    inactivas = [
        o["id"] for o in client.get(f"{API}/obras-sociales", params={"activas": False}).json()
    ]
    assert inactivas == [swiss["id"]]

    buscadas = client.get(f"{API}/obras-sociales", params={"nombre": "swiss"})
    assert [o["id"] for o in buscadas.json()] == [swiss["id"]]


def test_gestion_sobre_una_obra_social_inexistente_devuelve_404(client):
    inexistente = f"{API}/obras-sociales/987654"

    assert client.get(inexistente).status_code == 404
    assert client.put(inexistente, json={"nombre": "Nueva"}).status_code == 404
    assert client.patch(inexistente, json={"activa": True}).status_code == 404
    assert client.delete(inexistente).status_code == 404


# ---------------------------------------------------------------------------
# 3) El paciente consume el catálogo (clave foránea y texto heredado)
# ---------------------------------------------------------------------------


def test_el_paciente_se_vincula_por_obra_social_id(client, db_session):
    obra = _crear_obra_social(client)

    creado = _crear_paciente(
        client, obra_social_id=obra["id"], numero_afiliado="610012345678"
    )

    assert creado["obra_social_id"] == obra["id"]
    # La lectura expone el nombre resuelto para que la tabla lo muestre sin joins.
    assert creado["obra_social"] == "OSDE"

    # El vínculo se persiste como clave foránea, no como texto.
    en_db = db_session.get(Paciente, creado["id"])
    assert en_db.obra_social_id == obra["id"]
    assert "obra_social" not in Paciente.__table__.columns


def test_el_texto_heredado_resuelve_la_cobertura_del_catalogo(client):
    obra = _crear_obra_social(client, nombre="Medicus")

    creado = _crear_paciente(client, obra_social="  medicus  ")

    assert creado["obra_social_id"] == obra["id"]
    assert creado["obra_social"] == "Medicus"
    # No se duplicó el catálogo.
    assert len(client.get(f"{API}/obras-sociales").json()) == 1


def test_el_texto_heredado_crea_la_cobertura_si_no_existe(client):
    creado = _crear_paciente(client, obra_social="Swiss Medical")

    assert creado["obra_social"] == "Swiss Medical"
    catalogo = client.get(f"{API}/obras-sociales", params={"nombre": "Swiss Medical"}).json()
    assert [o["id"] for o in catalogo] == [creado["obra_social_id"]]


def test_asignar_una_obra_social_inexistente_devuelve_404(client):
    nueva = client.post(
        f"{API}/pacientes",
        json={
            "nombre": "Sin Cobertura",
            "telefono": "+5491100000001",
            "obra_social_id": 987654,
        },
    )
    assert nueva.status_code == 404, nueva.text

    paciente = _crear_paciente(client)
    cambio = client.patch(f"{API}/pacientes/{paciente['id']}", json={"obra_social_id": 987654})
    assert cambio.status_code == 404, cambio.text


def test_la_cobertura_se_puede_quitar_con_null(client):
    obra = _crear_obra_social(client)
    paciente = _crear_paciente(client, obra_social_id=obra["id"])

    r = client.patch(f"{API}/pacientes/{paciente['id']}", json={"obra_social_id": None})

    assert r.status_code == 200, r.text
    assert r.json()["obra_social_id"] is None
    assert r.json()["obra_social"] is None


def test_una_cobertura_dada_de_baja_sigue_en_la_ficha_del_paciente(client):
    """La baja no toca a los pacientes que ya la tenían ni al editar otros datos."""
    obra = _crear_obra_social(client)
    paciente = _crear_paciente(client, obra_social_id=obra["id"])

    assert client.delete(f"{API}/obras-sociales/{obra['id']}").status_code == 200

    detalle = client.get(f"{API}/pacientes/{paciente['id']}")
    assert detalle.status_code == 200
    assert detalle.json()["obra_social_id"] == obra["id"]
    assert detalle.json()["obra_social"] == "OSDE"

    # Un PATCH que no menciona la cobertura la conserva (el desplegable del panel
    # sólo ofrece las activas y el formulario reintroduce la opción vigente).
    r = client.patch(f"{API}/pacientes/{paciente['id']}", json={"telefono": "+5491100000009"})
    assert r.json()["obra_social_id"] == obra["id"]
    assert r.json()["obra_social"] == "OSDE"


# ---------------------------------------------------------------------------
# 4) Contrato HTTP y del panel
# ---------------------------------------------------------------------------


def test_la_api_declara_los_metodos_del_catalogo():
    rutas = app.openapi()["paths"]

    assert set(rutas[RUTA_COLECCION]) == {"get", "post"}
    assert set(rutas[RUTA_ITEM]) == {"get", "put", "patch", "delete"}


def test_la_vista_de_obras_sociales_esta_en_el_panel():
    """El panel trae la pestaña, la tabla, el modal y el desplegable de pacientes."""
    html = _leer_estatico("index.html")
    app_js = _leer_estatico("app.js")

    assert 'data-view="obras-sociales"' in html
    assert 'id="view-obras-sociales"' in html
    assert 'id="lista-obras-sociales"' in html
    assert 'id="form-obra-social"' in html
    # El formulario de pacientes usa la clave foránea del catálogo.
    assert 'name="obra_social_id"' in html
    assert "obra_social_id" in app_js

    # Botones de la tabla y llamadas a la API del catálogo.
    assert "data-editar-os=" in app_js
    assert "data-baja-os=" in app_js
    assert "data-reactivar-os=" in app_js
    assert re.search(r"function rutaObraSocial\(", app_js)
    # Sólo las coberturas activas alimentan el desplegable del paciente.
    assert "obrasSocialesActivas" in app_js


