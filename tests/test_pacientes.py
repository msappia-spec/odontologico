"""Pruebas del registro de pacientes con sus datos administrativos.

Cubren los campos incorporados al modelo (``dni`` único, ``obra_social`` y
``numero_afiliado``), los **datos filiatorios** de la Historia Clínica General
(con la edad calculada) y la actualización parcial (``PATCH``).
"""

from datetime import date

API = "/api/v1"


def _payload(**overrides) -> dict:
    """Datos de alta de un paciente, con posibilidad de sobreescribir campos."""
    datos = {
        "nombre": "Ana Suárez",
        "telefono": "+5491155667788",
        "email": "ana.suarez@example.com",
        "dni": "30.123.456",
        "obra_social": "OSDE",
        "numero_afiliado": "610012345678",
    }
    datos.update(overrides)
    return datos


def test_crear_paciente_con_dni_y_cobertura(client):
    r = client.post(f"{API}/pacientes", json=_payload())

    assert r.status_code == 201, r.text
    data = r.json()
    assert data["dni"] == "30123456"  # se normaliza: sin puntos
    assert data["obra_social"] == "OSDE"
    assert data["numero_afiliado"] == "610012345678"


def test_campos_administrativos_vacios_se_guardan_como_null(client):
    r = client.post(
        f"{API}/pacientes",
        json=_payload(dni="", obra_social="   ", numero_afiliado=""),
    )

    assert r.status_code == 201, r.text
    data = r.json()
    assert data["dni"] is None
    assert data["obra_social"] is None
    assert data["numero_afiliado"] is None


def test_crear_paciente_sin_datos_administrativos(client):
    """DNI y cobertura siguen siendo opcionales."""
    r = client.post(
        f"{API}/pacientes", json={"nombre": "Sin Datos", "telefono": "+5491100000000"}
    )

    assert r.status_code == 201, r.text
    assert r.json()["dni"] is None


def test_varios_pacientes_sin_dni_no_colisionan(client):
    """El índice único admite tantos ``NULL`` como pacientes sin documento."""
    for i in range(2):
        r = client.post(
            f"{API}/pacientes",
            json={"nombre": f"Paciente {i}", "telefono": f"+549110000{i:04d}"},
        )
        assert r.status_code == 201, r.text


def test_dni_duplicado_devuelve_409(client):
    assert client.post(f"{API}/pacientes", json=_payload()).status_code == 201

    r = client.post(
        f"{API}/pacientes", json=_payload(telefono="+5491177889900", email=None)
    )

    assert r.status_code == 409
    assert "dni" in r.json()["detail"].lower()


def test_dni_duplicado_con_otro_formato_devuelve_409(client):
    """``30.123.456`` y ``30123456`` son el mismo documento."""
    assert client.post(f"{API}/pacientes", json=_payload()).status_code == 201

    r = client.post(
        f"{API}/pacientes",
        json=_payload(dni="30123456", telefono="+5491177889901", email=None),
    )

    assert r.status_code == 409


def test_dni_invalido_devuelve_422(client):
    r = client.post(f"{API}/pacientes", json=_payload(dni="12"))

    assert r.status_code == 422
    assert "dni" in r.text.lower()


def test_listar_filtrar_y_obtener_paciente(client):
    creado = client.post(f"{API}/pacientes", json=_payload()).json()

    assert client.get(f"{API}/pacientes").status_code == 200

    detalle = client.get(f"{API}/pacientes/{creado['id']}")
    assert detalle.status_code == 200
    assert detalle.json()["numero_afiliado"] == "610012345678"

    filtrado = client.get(f"{API}/pacientes", params={"dni": "30123456"})
    assert filtrado.status_code == 200
    assert [p["id"] for p in filtrado.json()] == [creado["id"]]


def test_obtener_paciente_inexistente_devuelve_404(client):
    assert client.get(f"{API}/pacientes/987654").status_code == 404


def test_actualizar_paciente_actualiza_dni_y_cobertura(client):
    creado = client.post(f"{API}/pacientes", json=_payload()).json()

    r = client.patch(
        f"{API}/pacientes/{creado['id']}",
        json={
            "dni": "27.890.123",
            "obra_social": "Swiss Medical",
            "numero_afiliado": "SM-123456",
        },
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["dni"] == "27890123"
    assert data["obra_social"] == "Swiss Medical"
    assert data["numero_afiliado"] == "SM-123456"
    assert data["nombre"] == "Ana Suárez"  # los campos omitidos no cambian


def test_actualizar_paciente_permite_limpiar_campos(client):
    creado = client.post(f"{API}/pacientes", json=_payload()).json()

    r = client.patch(
        f"{API}/pacientes/{creado['id']}", json={"dni": None, "numero_afiliado": None}
    )

    assert r.status_code == 200, r.text
    assert r.json()["dni"] is None
    assert r.json()["numero_afiliado"] is None


def test_actualizar_con_dni_duplicado_devuelve_409(client):
    primero = client.post(f"{API}/pacientes", json=_payload()).json()
    segundo = client.post(
        f"{API}/pacientes",
        json=_payload(dni="27890123", telefono="+5491177889902", email=None),
    ).json()

    r = client.patch(f"{API}/pacientes/{segundo['id']}", json={"dni": primero["dni"]})

    assert r.status_code == 409
    assert "dni" in r.json()["detail"].lower()


def test_actualizar_paciente_inexistente_devuelve_404(client):
    r = client.patch(f"{API}/pacientes/987654", json={"obra_social": "OSDE"})
    assert r.status_code == 404


def test_guarda_los_datos_filiatorios_y_calcula_la_edad(client):
    """Los filiatorios de la Historia Clínica General viajan en el paciente."""
    hoy = date.today()
    nacimiento = date(hoy.year - 40, hoy.month, hoy.day)
    edad_esperada = hoy.year - nacimiento.year - (
        (hoy.month, hoy.day) < (nacimiento.month, nacimiento.day)
    )

    r = client.post(
        f"{API}/pacientes",
        json=_payload(
            fecha_nacimiento=nacimiento.isoformat(),
            estado_civil="  Casado/a  ",
            nacionalidad="Argentina",
            domicilio="Av. Colón 123, Córdoba",
            profesion="Docente",
            titular="Juan Pérez",
            lugar_trabajo="Escuela N.º 12",
            jerarquia="Titular",
        ),
    )

    assert r.status_code == 201, r.text
    data = r.json()
    assert data["fecha_nacimiento"] == nacimiento.isoformat()
    assert data["estado_civil"] == "Casado/a"  # se limpian los espacios
    assert data["nacionalidad"] == "Argentina"
    assert data["domicilio"] == "Av. Colón 123, Córdoba"
    assert data["profesion"] == "Docente"
    assert data["titular"] == "Juan Pérez"
    assert data["lugar_trabajo"] == "Escuela N.º 12"
    assert data["jerarquia"] == "Titular"
    assert data["edad"] == edad_esperada


def test_los_filiatorios_vacios_quedan_en_null_y_la_fecha_se_valida(client):
    r = client.post(
        f"{API}/pacientes",
        json=_payload(
            fecha_nacimiento="",
            estado_civil="   ",
            domicilio="",
            jerarquia="",
            email=None,
        ),
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["fecha_nacimiento"] is None
    assert data["estado_civil"] is None
    assert data["domicilio"] is None
    assert data["jerarquia"] is None
    assert data["edad"] is None

    futura = client.post(
        f"{API}/pacientes",
        json=_payload(fecha_nacimiento="2999-01-01", dni=None, email=None),
    )
    assert futura.status_code == 422

    mal_escrita = client.post(
        f"{API}/pacientes",
        json=_payload(fecha_nacimiento="12/03/1985", dni=None, email=None),
    )
    assert mal_escrita.status_code == 422


def test_patch_permite_corregir_y_limpiar_los_filiatorios(client):
    creado = client.post(
        f"{API}/pacientes",
        json=_payload(fecha_nacimiento="1985-03-12", domicilio="Calle Falsa 123"),
    ).json()

    r = client.patch(
        f"{API}/pacientes/{creado['id']}",
        json={"domicilio": "Av. Siempre Viva 742", "fecha_nacimiento": None},
    )

    assert r.status_code == 200, r.text
    assert r.json()["domicilio"] == "Av. Siempre Viva 742"
    assert r.json()["fecha_nacimiento"] is None
    assert r.json()["edad"] is None

