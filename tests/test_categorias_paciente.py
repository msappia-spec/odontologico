"""Pruebas del catálogo administrable de categorías de pacientes.

Cubren el CRUD del catálogo (``/categorias-paciente``), la siembra de las tres
categorías históricas y la relación con el paciente: una categoría nueva con
``alerta`` avisa antes del turno y admite observaciones, una inexistente o dada
de baja responde ``422`` y cambiar el código de una categoría actualiza en
bloque a los pacientes que la tenían.
"""

from app.services import categoria_paciente_service

API = "/api/v1"


def _payload_paciente(**extra):
    """Paciente mínimo válido para las pruebas de categoría."""
    datos = {"nombre": "Paciente Demo", "telefono": "+5491122334455"}
    datos.update(extra)
    return datos


def _categoria_por_codigo(client, codigo):
    categorias = client.get(f"{API}/categorias-paciente").json()
    return next(c for c in categorias if c["codigo"] == codigo)


def test_lista_las_categorias_historicas(client):
    """La tabla vacía se siembra con SIN_CATEGORIZAR, EXCELENTE y MALO."""
    r = client.get(f"{API}/categorias-paciente")
    assert r.status_code == 200
    por_codigo = {c["codigo"]: c for c in r.json()}
    assert {"SIN_CATEGORIZAR", "EXCELENTE", "MALO"} <= set(por_codigo)
    assert por_codigo["MALO"]["alerta"] is True
    assert por_codigo["EXCELENTE"]["alerta"] is False
    assert all(c["id"] for c in por_codigo.values())


def test_crea_edita_y_da_de_baja_una_categoria(client):
    """El código se normaliza, el nombre/alerta se editan y la baja es lógica."""
    r = client.post(
        f"{API}/categorias-paciente",
        json={"codigo": "paciente vip", "nombre": "  Paciente VIP  ", "alerta": True},
    )
    assert r.status_code == 201, r.text
    categoria = r.json()
    assert categoria["codigo"] == "PACIENTE_VIP"
    assert categoria["nombre"] == "Paciente VIP"
    assert categoria["alerta"] is True
    assert categoria["activa"] is True
    assert categoria["orden"] > 0

    r = client.patch(
        f"{API}/categorias-paciente/{categoria['id']}",
        json={"nombre": "VIP", "alerta": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "VIP"
    assert r.json()["alerta"] is False

    r = client.delete(f"{API}/categorias-paciente/{categoria['id']}")
    assert r.status_code == 200
    assert r.json()["activa"] is False

    activas = client.get(f"{API}/categorias-paciente?activas=true").json()
    assert categoria["id"] not in {c["id"] for c in activas}

    r = client.patch(
        f"{API}/categorias-paciente/{categoria['id']}", json={"activa": True}
    )
    assert r.status_code == 200
    assert r.json()["activa"] is True


def test_codigo_duplicado_devuelve_409(client):
    """El código es único sin distinguir mayúsculas ni espacios."""
    assert (
        client.post(
            f"{API}/categorias-paciente",
            json={"codigo": "VIP", "nombre": "VIP"},
        ).status_code
        == 201
    )
    r = client.post(
        f"{API}/categorias-paciente", json={"codigo": " vip ", "nombre": "Otra"}
    )
    assert r.status_code == 409
    assert "VIP" in r.json()["detail"]


def test_codigo_invalido_devuelve_422(client):
    """El código sólo admite letras, números y «_», empezando con una letra."""
    for codigo in ("con-guion", "1VIP", "con espacio!", ""):
        r = client.post(
            f"{API}/categorias-paciente",
            json={"codigo": codigo, "nombre": "Inválida"},
        )
        assert r.status_code == 422, codigo


def test_la_categoria_por_defecto_no_se_puede_dar_de_baja_ni_renombrar(client):
    """SIN_CATEGORIZAR es el valor que usa el esquema cuando no se informa."""
    por_defecto = _categoria_por_codigo(client, "SIN_CATEGORIZAR")

    r = client.delete(f"{API}/categorias-paciente/{por_defecto['id']}")
    assert r.status_code == 422

    r = client.patch(
        f"{API}/categorias-paciente/{por_defecto['id']}",
        json={"codigo": "SIN_CATEGORIA"},
    )
    assert r.status_code == 422

    r = client.patch(
        f"{API}/categorias-paciente/{por_defecto['id']}",
        json={"activa": False},
    )
    assert r.status_code == 422


def test_categoria_inexistente_devuelve_404(client):
    r = client.patch(
        f"{API}/categorias-paciente/9999", json={"nombre": "No existe"}
    )
    assert r.status_code == 404


def test_paciente_con_categoria_nueva_avisa_y_admite_observaciones(client):
    """Una categoría con ``alerta`` se comporta como el MALO histórico."""
    r = client.post(
        f"{API}/categorias-paciente",
        json={"codigo": "MOROSO", "nombre": "Moroso", "alerta": True},
    )
    assert r.status_code == 201, r.text

    r = client.post(
        f"{API}/pacientes",
        json=_payload_paciente(
            categoria="moroso",
            categoria_observaciones="  Debe tres cuotas.  ",
        ),
    )
    assert r.status_code == 201, r.text
    paciente = r.json()
    assert paciente["categoria"] == "MOROSO"
    assert paciente["categoria_nombre"] == "Moroso"
    assert paciente["categoria_alerta"] is True
    assert paciente["categoria_observaciones"] == "Debe tres cuotas."

    # Al pasar a una categoría sin aviso, las observaciones se rechazan y, al
    # guardar sin ellas, se limpian.
    r = client.patch(
        f"{API}/pacientes/{paciente['id']}",
        json={"categoria": "EXCELENTE", "categoria_observaciones": "No va."},
    )
    assert r.status_code == 422

    r = client.patch(
        f"{API}/pacientes/{paciente['id']}", json={"categoria": "EXCELENTE"}
    )
    assert r.status_code == 200
    assert r.json()["categoria"] == "EXCELENTE"
    assert r.json()["categoria_alerta"] is False
    assert r.json()["categoria_observaciones"] is None


def test_paciente_con_categoria_inexistente_devuelve_422(client):
    r = client.post(
        f"{API}/pacientes", json=_payload_paciente(categoria="REGULAR")
    )
    assert r.status_code == 422
    assert "REGULAR" in r.json()["detail"]


def test_paciente_con_categoria_dada_de_baja_devuelve_422(client):
    r = client.post(
        f"{API}/categorias-paciente",
        json={"codigo": "TURISTA", "nombre": "Turista"},
    )
    assert r.status_code == 201
    categoria = r.json()
    assert (
        client.delete(f"{API}/categorias-paciente/{categoria['id']}").status_code
        == 200
    )

    r = client.post(
        f"{API}/pacientes", json=_payload_paciente(categoria="TURISTA")
    )
    assert r.status_code == 422
    assert "baja" in r.json()["detail"].lower()


def test_un_paciente_de_categoria_dada_de_baja_conserva_el_dato(client):
    """La baja del catálogo no borra ni reescribe la categoría del paciente."""
    r = client.post(
        f"{API}/categorias-paciente",
        json={"codigo": "PROVISORIO", "nombre": "Provisorio"},
    )
    categoria = r.json()
    r = client.post(
        f"{API}/pacientes", json=_payload_paciente(categoria="PROVISORIO")
    )
    paciente = r.json()

    assert (
        client.delete(f"{API}/categorias-paciente/{categoria['id']}").status_code
        == 200
    )
    r = client.get(f"{API}/pacientes/{paciente['id']}")
    assert r.status_code == 200
    assert r.json()["categoria"] == "PROVISORIO"
    assert r.json()["categoria_nombre"] == "Provisorio"


def test_cambiar_el_codigo_actualiza_los_pacientes(client):
    """Renombrar el código arrastra a las fichas que lo tenían."""
    r = client.post(
        f"{API}/categorias-paciente",
        json={"codigo": "VIP", "nombre": "VIP"},
    )
    categoria = r.json()
    r = client.post(f"{API}/pacientes", json=_payload_paciente(categoria="VIP"))
    paciente = r.json()

    r = client.patch(
        f"{API}/categorias-paciente/{categoria['id']}",
        json={"codigo": "VIP_PLUS", "nombre": "VIP Plus"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["codigo"] == "VIP_PLUS"

    r = client.get(f"{API}/pacientes/{paciente['id']}")
    assert r.json()["categoria"] == "VIP_PLUS"
    assert r.json()["categoria_nombre"] == "VIP Plus"


def test_la_categoria_nula_vuelve_a_la_por_defecto(client):
    """Un PATCH con ``categoria: null`` restablece SIN_CATEGORIZAR."""
    r = client.post(
        f"{API}/pacientes", json=_payload_paciente(categoria="MALO")
    )
    paciente = r.json()
    r = client.patch(f"{API}/pacientes/{paciente['id']}", json={"categoria": None})
    assert r.status_code == 200
    assert r.json()["categoria"] == "SIN_CATEGORIZAR"


def test_sembrar_el_catalogo_es_idempotente(db_session):
    """``init_db`` puede ejecutarse en cada arranque sin duplicar filas."""
    assert categoria_paciente_service.contar_categorias(db_session) == 3
    assert categoria_paciente_service.sembrar_categorias_por_defecto(db_session) == 0
    assert categoria_paciente_service.contar_categorias(db_session) == 3

