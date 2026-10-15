"""Pruebas del Módulo 3: obras sociales, liquidación y facturación mixta.

Cubren las cuatro piezas del módulo de punta a punta (API + base de datos):

1. **Catálogo de prestaciones** (``/prestaciones``): código único (``409``) y baja
   lógica (``DELETE`` → ``activa = false``); una práctica dada de baja no se puede
   facturar.
2. **Aranceles por cobertura** (``/obras-sociales/{id}/aranceles``, ``/aranceles``):
   un par (obra social, práctica) por vez (``409``) y montos de cobertura/coseguro.
3. **Facturación mixta** (``POST /turnos/{id}/facturar``): separa lo que paga la
   obra social del coseguro del paciente, cobra particular cuando no hay
   cobertura, rechaza una factura sin arancel (**422**), no factura dos veces el
   mismo turno (**409**) ni una visita cancelada (**422**).
4. **Liquidaciones**: la presentación del período de una obra social (``PENDIENTE``
   → ``EN_PROCESO_LIQUIDACION``, con cobros y débitos individuales) y el reporte
   de producción/honorarios por odontólogo (dueño, comisión o sueldo).

Además se verifica que el esquema siga siendo **compatible con PostgreSQL** (el
DDL de las tablas nuevas compila con el dialecto) y que el panel tenga la sección
*Facturación y Caja* con el cartel del coseguro.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.main import app
from app.models.arancel_obra_social import ArancelObraSocial
from app.models.doctor import Doctor
from app.models.enums import EstadoPagoMovimiento, TipoPagoDoctor
from app.models.movimiento_caja import DetalleMovimientoCaja, MovimientoCaja
from app.models.prestacion import Prestacion
from app.web.router import STATIC_DIR

API = "/api/v1"

# Fechas de trabajo: un día del mes de noviembre de 2026 para los turnos
# (UTC naive, como todo el proyecto) y ese mes para las liquidaciones.
DIA = datetime(2026, 11, 3, 12, 0)
ANIO, MES = 2026, 11


def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


def _crear_prestacion(client, **overrides) -> dict:
    """Crea una práctica por API y devuelve su representación."""
    payload = {
        "codigo_nomenclador": "01.01",
        "descripcion": "Consulta y diagnóstico",
        "valor_base": 15000,
    }
    payload.update(overrides)
    r = client.post(f"{API}/prestaciones", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_obra_social(client, **overrides) -> dict:
    """Crea una cobertura por API y devuelve su representación."""
    payload = {"nombre": "OSDE", "codigo": "OSDE-01"}
    payload.update(overrides)
    r = client.post(f"{API}/obras-sociales", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_arancel(
    client, obra_social_id: int, prestacion_id: int, **overrides
) -> dict:
    """Carga el arancel de una práctica para una cobertura."""
    payload = {
        "prestacion_id": prestacion_id,
        "monto_cubre_os": 10000,
        "monto_coseguro_paciente": 5000,
    }
    payload.update(overrides)
    r = client.post(f"{API}/obras-sociales/{obra_social_id}/aranceles", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_paciente(client, **overrides) -> dict:
    """Crea un paciente por API y devuelve su representación."""
    payload = {"nombre": "Ana Suárez", "telefono": "+5491155667788"}
    payload.update(overrides)
    r = client.post(f"{API}/pacientes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_turno(
    client,
    doctor_id: int,
    paciente_id: int,
    inicio: datetime = DIA,
    duracion_horas: int = 1,
    **overrides,
) -> dict:
    """Agenda un turno por API y devuelve su representación."""
    payload = {
        "paciente_id": paciente_id,
        "doctor_id": doctor_id,
        "fecha_hora_inicio": inicio.isoformat(),
        "fecha_hora_fin": (inicio + timedelta(hours=duracion_horas)).isoformat(),
    }
    payload.update(overrides)
    r = client.post(f"{API}/turnos", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _facturar(client, turno_id: int, prestaciones, **extra):
    """Llama al endpoint de facturación y devuelve la respuesta cruda."""
    payload = {"prestaciones": prestaciones}
    payload.update(extra)
    return client.post(f"{API}/turnos/{turno_id}/facturar", json=payload)


def _facturar_ok(client, turno_id: int, prestaciones, **extra) -> dict:
    """Factura un turno y exige que salga bien (201)."""
    r = _facturar(client, turno_id, prestaciones, **extra)
    assert r.status_code == 201, r.text
    return r.json()


def _contar(db_session, modelo) -> int:
    """Cantidad de filas de un modelo."""
    return int(db_session.execute(select(func.count()).select_from(modelo)).scalar())


# ---------------------------------------------------------------------------
# 1) Catálogo de prestaciones
# ---------------------------------------------------------------------------


def test_crear_y_listar_prestaciones(client):
    creada = _crear_prestacion(client, codigo_nomenclador=" 01.02 ", valor_base=12345.678)

    assert creada["codigo_nomenclador"] == "01.02"   # normalizado a mayúsculas
    assert creada["activa"] is True
    assert creada["valor_base"] == 12345.68          # redondeado a dos decimales

    listado = client.get(f"{API}/prestaciones")
    assert listado.status_code == 200, listado.text
    assert [p["id"] for p in listado.json()] == [creada["id"]]

    detalle = client.get(f"{API}/prestaciones/{creada['id']}")
    assert detalle.status_code == 200
    assert detalle.json()["descripcion"] == "Consulta y diagnóstico"


def test_codigo_de_prestacion_duplicado_devuelve_409(client):
    _crear_prestacion(client, codigo_nomenclador="01.01")

    r = client.post(
        f"{API}/prestaciones",
        json={"codigo_nomenclador": "01.01", "descripcion": "Otra", "valor_base": 1},
    )

    assert r.status_code == 409, r.text
    assert "01.01" in r.json()["detail"]


def test_la_unicidad_del_codigo_no_distingue_mayusculas(client):
    _crear_prestacion(client, codigo_nomenclador="01.01")

    r = client.post(
        f"{API}/prestaciones",
        json={"codigo_nomenclador": "01.01", "descripcion": "Radiografía"},
    )

    assert r.status_code == 409, r.text


def test_patch_y_baja_logica_de_prestacion(client):
    creada = _crear_prestacion(client)

    patch = client.patch(
        f"{API}/prestaciones/{creada['id']}", json={"valor_base": 20000}
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["valor_base"] == 20000
    assert patch.json()["descripcion"] == "Consulta y diagnóstico"  # el resto no cambia

    baja = client.delete(f"{API}/prestaciones/{creada['id']}")
    assert baja.status_code == 200, baja.text
    assert baja.json()["activa"] is False

    # Sigue existiendo (borrado lógico) y se puede reactivar.
    assert (
        client.get(f"{API}/prestaciones?activas=false").json()[0]["id"] == creada["id"]
    )
    reactivada = client.patch(
        f"{API}/prestaciones/{creada['id']}", json={"activa": True}
    )
    assert reactivada.json()["activa"] is True


def test_una_prestacion_dada_de_baja_no_se_puede_facturar(client, doctor, paciente):
    prestacion = _crear_prestacion(client)
    turno = _crear_turno(client, doctor.id, paciente.id)
    client.delete(f"{API}/prestaciones/{prestacion['id']}")

    r = _facturar(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    assert r.status_code == 404, r.text
    assert "dada de baja" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 2) Aranceles por obra social
# ---------------------------------------------------------------------------


def test_cargar_arancel_y_listarlo_por_obra_social(client):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)

    arancel = _crear_arancel(client, obra["id"], prestacion["id"])

    assert arancel["obra_social_id"] == obra["id"]
    assert arancel["obra_social_nombre"] == "OSDE"
    assert arancel["prestacion_codigo"] == "01.01"
    assert arancel["prestacion_descripcion"] == "Consulta y diagnóstico"
    assert arancel["monto_total"] == 15000      # 10000 + 5000

    listado = client.get(f"{API}/obras-sociales/{obra['id']}/aranceles")
    assert listado.status_code == 200, listado.text
    assert [a["id"] for a in listado.json()] == [arancel["id"]]


def test_arancel_duplicado_devuelve_409(client):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)
    _crear_arancel(client, obra["id"], prestacion["id"])

    r = client.post(
        f"{API}/obras-sociales/{obra['id']}/aranceles",
        json={"prestacion_id": prestacion["id"], "monto_cubre_os": 1},
    )

    assert r.status_code == 409, r.text
    assert "arancel" in r.json()["detail"].lower()


def test_arancel_con_obra_social_o_prestacion_inexistente_404(client):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)

    sin_obra = client.post(
        f"{API}/obras-sociales/999/aranceles", json={"prestacion_id": prestacion["id"]}
    )
    sin_prestacion = client.post(
        f"{API}/obras-sociales/{obra['id']}/aranceles", json={"prestacion_id": 999}
    )

    assert sin_obra.status_code == 404, sin_obra.text
    assert sin_prestacion.status_code == 404, sin_prestacion.text


def test_patch_put_y_delete_de_arancel(client):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)
    arancel = _crear_arancel(client, obra["id"], prestacion["id"])

    patch = client.patch(
        f"{API}/aranceles/{arancel['id']}", json={"monto_coseguro_paciente": 8000}
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["monto_coseguro_paciente"] == 8000
    assert patch.json()["monto_cubre_os"] == 10000   # el resto no cambia

    put = client.put(
        f"{API}/aranceles/{arancel['id']}",
        json={
            "prestacion_id": prestacion["id"],
            "monto_cubre_os": 12000,
            "monto_coseguro_paciente": 3000,
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["monto_cubre_os"] == 12000
    assert put.json()["monto_total"] == 15000

    borrado = client.delete(f"{API}/aranceles/{arancel['id']}")
    assert borrado.status_code == 204, borrado.text
    assert client.delete(f"{API}/aranceles/{arancel['id']}").status_code == 404


def test_el_put_no_puede_cambiar_la_prestacion_del_arancel(client):
    obra = _crear_obra_social(client)
    primera = _crear_prestacion(client, codigo_nomenclador="01.01")
    segunda = _crear_prestacion(client, codigo_nomenclador="01.02")
    arancel = _crear_arancel(client, obra["id"], primera["id"])

    r = client.put(
        f"{API}/aranceles/{arancel['id']}",
        json={"prestacion_id": segunda["id"], "monto_cubre_os": 1},
    )

    assert r.status_code == 422, r.text
    assert "otra prestación" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 3) Facturación de la visita (facturación mixta)
# ---------------------------------------------------------------------------


def test_facturar_con_obra_social_separa_coseguro_y_saldo_os(client, doctor, paciente):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)
    _crear_arancel(client, obra["id"], prestacion["id"], monto_cubre_os=8000,
                   monto_coseguro_paciente=2500)
    paciente = _crear_paciente(client, obra_social_id=obra["id"], numero_afiliado="6100")
    turno = _crear_turno(client, doctor.id, paciente["id"])

    movimiento = _facturar_ok(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    assert movimiento["monto_efectivo"] == 2500
    assert movimiento["monto_pendiente_os"] == 8000
    assert movimiento["monto_total"] == 10500
    assert movimiento["estado_pago"] == EstadoPagoMovimiento.PENDIENTE.value
    assert movimiento["monto_cobrado_os"] is None
    # Datos planos del paciente y de la cobertura (para el panel y la presentación).
    assert movimiento["paciente_nombre"] == "Ana Suárez"
    assert movimiento["obra_social_nombre"] == "OSDE"
    assert movimiento["doctor_nombre"] == doctor.nombre
    assert movimiento["turno_fecha_hora_inicio"].startswith("2026-11-03T12:00")
    assert movimiento["prestaciones"][0]["codigo_nomenclador"] == "01.01"
    assert movimiento["prestaciones"][0]["monto_coseguro_paciente"] == 2500


def test_facturar_particular_cobra_todo_en_efectivo(client, doctor):
    paciente = _crear_paciente(client, nombre="Pedro Sin Cobertura")
    prestacion = _crear_prestacion(client, valor_base=12000)
    turno = _crear_turno(client, doctor.id, paciente["id"])

    movimiento = _facturar_ok(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    assert movimiento["monto_efectivo"] == 12000
    assert movimiento["monto_pendiente_os"] == 0
    # Sin saldo para la OS, el cierre nace saldado.
    assert movimiento["estado_pago"] == EstadoPagoMovimiento.COBRADO.value
    assert movimiento["obra_social_nombre"] is None


def test_facturar_con_cantidad_multiplica_los_importes(client, doctor):
    prestacion = _crear_prestacion(client, valor_base=10000)
    paciente = _crear_paciente(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])

    movimiento = _facturar_ok(
        client, turno["id"], [{"prestacion_id": prestacion["id"], "cantidad": 3}]
    )

    assert movimiento["monto_efectivo"] == 30000
    assert movimiento["prestaciones"][0]["cantidad"] == 3


def test_facturar_sin_arancel_devuelve_422(client, doctor):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client, codigo_nomenclador="23.45")
    paciente = _crear_paciente(client, obra_social_id=obra["id"])
    turno = _crear_turno(client, doctor.id, paciente["id"])

    r = _facturar(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    assert r.status_code == 422, r.text
    assert "23.45" in r.json()["detail"]
    # Nada se escribió: el turno sigue sin facturar.
    assert client.get(f"{API}/turnos/{turno['id']}/facturacion").json()["facturado"] is False


def test_permitir_particular_cobra_a_valor_de_referencia(client, doctor):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client, codigo_nomenclador="23.45", valor_base=9000)
    paciente = _crear_paciente(client, obra_social_id=obra["id"])
    turno = _crear_turno(client, doctor.id, paciente["id"])

    movimiento = _facturar_ok(
        client,
        turno["id"],
        [{"prestacion_id": prestacion["id"]}],
        permitir_particular=True,
        observaciones="Paciente sin carnet",
    )

    assert movimiento["monto_efectivo"] == 9000
    assert movimiento["monto_pendiente_os"] == 0
    # La advertencia queda registrada junto con la observación de la secretaría.
    assert "sin carnet" in movimiento["observaciones"].lower()
    assert "arancel" in movimiento["observaciones"].lower()


def test_facturar_confirma_el_turno_pendiente(client, doctor):
    paciente = _crear_paciente(client)
    prestacion = _crear_prestacion(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])
    assert turno["estado_turno"] == "PENDIENTE"

    _facturar_ok(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    assert (
        client.get(f"{API}/turnos/{turno['id']}").json()["estado_turno"]
        == "CONFIRMADO"
    )


def test_un_turno_no_se_factura_dos_veces(client, doctor):
    paciente = _crear_paciente(client)
    prestacion = _crear_prestacion(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])
    _facturar_ok(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    r = _facturar(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    assert r.status_code == 409, r.text
    assert "facturado" in r.json()["detail"]


def test_no_se_puede_facturar_un_turno_cancelado(client, doctor):
    paciente = _crear_paciente(client)
    prestacion = _crear_prestacion(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])
    assert (
        client.patch(
            f"{API}/turnos/{turno['id']}", json={"estado_turno": "CANCELADO"}
        ).status_code
        == 200
    )

    r = _facturar(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    assert r.status_code == 422, r.text
    assert "cancelada" in r.json()["detail"]


def test_facturar_un_turno_inexistente_404(client):
    prestacion = _crear_prestacion(client)

    r = _facturar(client, 999, [{"prestacion_id": prestacion["id"]}])

    assert r.status_code == 404, r.text


def test_facturar_sin_practicas_es_422(client, doctor):
    paciente = _crear_paciente(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])

    r = _facturar(client, turno["id"], [])

    assert r.status_code == 422, r.text


def test_la_factura_guarda_los_importes_congelados(client, db_session, doctor):
    """El detalle copia la práctica: la factura no cambia si luego se edita."""
    prestacion = _crear_prestacion(client, valor_base=10000)
    paciente = _crear_paciente(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])
    _facturar_ok(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    detalle = db_session.execute(select(DetalleMovimientoCaja)).scalars().one()
    assert detalle.prestacion_id == prestacion["id"]
    assert detalle.codigo_nomenclador == "01.01"
    assert detalle.descripcion == "Consulta y diagnóstico"
    assert detalle.monto_coseguro_paciente == 10000
    assert _contar(db_session, MovimientoCaja) == 1
    # La práctica sigue en el catálogo: el detalle es una copia, no un reemplazo.
    assert _contar(db_session, Prestacion) == 1
    assert _contar(db_session, ArancelObraSocial) == 0


def test_prestaciones_disponibles_resuelve_el_arancel(client, doctor):
    obra = _crear_obra_social(client)
    con_arancel = _crear_prestacion(client, codigo_nomenclador="01.01")
    _crear_prestacion(
        client, codigo_nomenclador="02.02", descripcion="Obturación", valor_base=7000
    )
    _crear_arancel(
        client,
        obra["id"],
        con_arancel["id"],
        monto_cubre_os=6000,
        monto_coseguro_paciente=1000,
    )
    paciente = _crear_paciente(client, obra_social_id=obra["id"])
    turno = _crear_turno(client, doctor.id, paciente["id"])

    r = client.get(f"{API}/turnos/{turno['id']}/prestaciones-disponibles")

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["obra_social_nombre"] == "OSDE"
    assert datos["doctor_nombre"] == doctor.nombre
    assert datos["facturado"] is False
    por_codigo = {p["codigo_nomenclador"]: p for p in datos["prestaciones"]}
    assert por_codigo["01.01"]["origen"] == "ARANCEL"
    assert por_codigo["01.01"]["cubierta_por_os"] is True
    assert por_codigo["01.01"]["monto_coseguro_paciente"] == 1000
    assert por_codigo["02.02"]["origen"] == "SIN_ARANCEL"
    assert por_codigo["02.02"]["monto_coseguro_paciente"] == 7000


def test_prestaciones_disponibles_de_un_particular_usa_el_valor_base(client, doctor):
    prestacion = _crear_prestacion(client, valor_base=8000)
    paciente = _crear_paciente(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])

    datos = client.get(f"{API}/turnos/{turno['id']}/prestaciones-disponibles").json()

    item = datos["prestaciones"][0]
    assert item["prestacion_id"] == prestacion["id"]
    assert item["origen"] == "VALOR_BASE"
    assert item["monto_coseguro_paciente"] == 8000
    assert item["monto_cubre_os"] == 0


def test_el_presupuesto_calcula_el_coseguro_sin_escribir(client, db_session, doctor):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)
    _crear_arancel(
        client,
        obra["id"],
        prestacion["id"],
        monto_cubre_os=4000,
        monto_coseguro_paciente=1200,
    )
    paciente = _crear_paciente(client, obra_social_id=obra["id"])
    turno = _crear_turno(client, doctor.id, paciente["id"])

    r = client.post(
        f"{API}/turnos/{turno['id']}/presupuesto",
        json={"prestaciones": [{"prestacion_id": prestacion["id"], "cantidad": 2}]},
    )

    assert r.status_code == 200, r.text
    presupuesto = r.json()
    assert presupuesto["monto_efectivo"] == 2400    # 2 × 1200: cartel del mostrador
    assert presupuesto["monto_pendiente_os"] == 8000
    assert presupuesto["monto_total"] == 10400
    assert presupuesto["paciente_nombre"] == "Ana Suárez"
    assert presupuesto["cantidad_prestaciones"] == 2
    assert _contar(db_session, MovimientoCaja) == 0    # no escribió nada


def test_el_estado_de_facturacion_del_turno(client, doctor):
    paciente = _crear_paciente(client)
    prestacion = _crear_prestacion(client)
    turno = _crear_turno(client, doctor.id, paciente["id"])

    antes = client.get(f"{API}/turnos/{turno['id']}/facturacion").json()
    assert antes == {"turno_id": turno["id"], "facturado": False, "movimiento": None}

    _facturar_ok(client, turno["id"], [{"prestacion_id": prestacion["id"]}])

    despues = client.get(f"{API}/turnos/{turno['id']}/facturacion").json()
    assert despues["facturado"] is True
    assert despues["movimiento"]["turno_id"] == turno["id"]
    assert despues["movimiento"]["monto_efectivo"] == 15000   # particular por defecto


# ---------------------------------------------------------------------------
# 4) Caja: consulta, cobros y débitos
# ---------------------------------------------------------------------------


def _escenario_caja(client, doctor) -> dict:
    """Arma un escenario reutilizable: una visita con OS y una particular."""
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)
    _crear_arancel(
        client,
        obra["id"],
        prestacion["id"],
        monto_cubre_os=9000,
        monto_coseguro_paciente=1000,
    )
    con_obra = _crear_paciente(client, nombre="Con Cobertura", obra_social_id=obra["id"])
    particular = _crear_paciente(client, nombre="Sin Cobertura")

    turno_os = _crear_turno(client, doctor.id, con_obra["id"], inicio=DIA)
    turno_part = _crear_turno(
        client, doctor.id, particular["id"], inicio=DIA + timedelta(hours=2)
    )

    mov_os = _facturar_ok(
        client,
        turno_os["id"],
        [{"prestacion_id": prestacion["id"]}],
        fecha="2026-11-03T13:00:00",
    )
    mov_part = _facturar_ok(
        client,
        turno_part["id"],
        [{"prestacion_id": prestacion["id"]}],
        fecha="2026-11-03T15:00:00",
    )
    return {
        "obra": obra,
        "prestacion": prestacion,
        "mov_os": mov_os,
        "mov_particular": mov_part,
        "turno_os": turno_os,
    }


def test_listar_movimientos_y_filtrar_la_caja_del_dia(client, doctor):
    datos = _escenario_caja(client, doctor)

    todos = client.get(f"{API}/movimientos-caja?desde=2026-11-03&hasta=2026-11-03")
    assert todos.status_code == 200, todos.text
    assert {m["id"] for m in todos.json()} == {
        datos["mov_os"]["id"],
        datos["mov_particular"]["id"],
    }

    por_obra_social = client.get(
        f"{API}/movimientos-caja?obra_social_id={datos['obra']['id']}"
    ).json()
    assert [m["id"] for m in por_obra_social] == [datos["mov_os"]["id"]]

    por_estado = client.get(f"{API}/movimientos-caja?estado_pago=PENDIENTE").json()
    assert [m["id"] for m in por_estado] == [datos["mov_os"]["id"]]

    por_turno = client.get(
        f"{API}/movimientos-caja?turno_id={datos['turno_os']['id']}"
    ).json()
    assert [m["turno_id"] for m in por_turno] == [datos["turno_os"]["id"]]

    otro_dia = client.get(f"{API}/movimientos-caja?desde=2026-11-04&hasta=2026-11-04")
    assert otro_dia.json() == []


def test_resumen_de_la_caja_del_dia(client, doctor):
    _escenario_caja(client, doctor)

    resumen = client.get(
        f"{API}/movimientos-caja/resumen?desde=2026-11-03&hasta=2026-11-03"
    )

    assert resumen.status_code == 200, resumen.text
    datos = resumen.json()
    assert datos["cantidad_movimientos"] == 2
    assert datos["monto_efectivo"] == 16000        # 1000 de coseguro + 15000 particular
    assert datos["monto_pendiente_os"] == 9000     # sólo lo presentado a la OS
    assert datos["monto_cobrado_os"] == 0
    assert datos["monto_debitado_os"] == 0


def test_cobrar_movimiento_admite_un_importe_distinto_al_presentado(client, doctor):
    datos = _escenario_caja(client, doctor)
    movimiento_id = datos["mov_os"]["id"]

    r = client.post(
        f"{API}/movimientos-caja/{movimiento_id}/cobrar",
        json={"monto_cobrado_os": 8500, "observaciones": "Depósito 12345"},
    )

    assert r.status_code == 200, r.text
    cobrado = r.json()
    assert cobrado["estado_pago"] == EstadoPagoMovimiento.COBRADO.value
    assert cobrado["monto_cobrado_os"] == 8500
    assert cobrado["monto_pendiente_os"] == 9000   # lo presentado no se pisa
    assert cobrado["observaciones"] == "Depósito 12345"

    # Idempotente: volver a cobrarlo no falla ni cambia el importe.
    repetido = client.post(f"{API}/movimientos-caja/{movimiento_id}/cobrar", json={})
    assert repetido.status_code == 200, repetido.text
    assert repetido.json()["monto_cobrado_os"] == 8500

    resumen = client.get(
        f"{API}/movimientos-caja/resumen?desde=2026-11-03&hasta=2026-11-03"
    ).json()
    assert resumen["monto_cobrado_os"] == 8500
    assert resumen["monto_pendiente_os"] == 0


def test_cobrar_un_movimiento_particular_es_idempotente(client, doctor):
    """Un particular nace saldado: cobrarlo no cambia nada (no hay saldo de OS)."""
    datos = _escenario_caja(client, doctor)

    r = client.post(
        f"{API}/movimientos-caja/{datos['mov_particular']['id']}/cobrar", json={}
    )

    assert r.status_code == 200, r.text
    assert r.json()["estado_pago"] == EstadoPagoMovimiento.COBRADO.value
    assert r.json()["monto_cobrado_os"] == 0
    assert r.json()["monto_pendiente_os"] == 0


def test_debitar_movimiento_registra_el_motivo_y_el_debito_parcial(client, doctor):
    datos = _escenario_caja(client, doctor)
    movimiento_id = datos["mov_os"]["id"]

    r = client.post(
        f"{API}/movimientos-caja/{movimiento_id}/debitar",
        json={
            "observaciones": "Práctica rechazada por auditoría",
            "monto_cobrado_os": 4000,
        },
    )

    assert r.status_code == 200, r.text
    debitado = r.json()
    assert debitado["estado_pago"] == EstadoPagoMovimiento.DEBITADO_OS.value
    assert debitado["monto_cobrado_os"] == 4000
    assert "auditoría" in debitado["observaciones"]

    resumen = client.get(
        f"{API}/movimientos-caja/resumen?desde=2026-11-03&hasta=2026-11-03"
    ).json()
    assert resumen["monto_debitado_os"] == 5000    # 9000 presentados − 4000 depositados


def test_el_debito_exige_el_motivo(client, doctor):
    datos = _escenario_caja(client, doctor)

    r = client.post(
        f"{API}/movimientos-caja/{datos['mov_os']['id']}/debitar",
        json={"observaciones": "   "},
    )

    assert r.status_code == 422, r.text


def test_no_se_puede_debitar_un_movimiento_cobrado(client, doctor):
    datos = _escenario_caja(client, doctor)
    movimiento_id = datos["mov_os"]["id"]
    client.post(f"{API}/movimientos-caja/{movimiento_id}/cobrar", json={})

    r = client.post(
        f"{API}/movimientos-caja/{movimiento_id}/debitar",
        json={"observaciones": "Rechazada después del pago"},
    )

    assert r.status_code == 409, r.text


def test_no_se_puede_cobrar_un_movimiento_debitado(client, doctor):
    datos = _escenario_caja(client, doctor)
    movimiento_id = datos["mov_os"]["id"]
    client.post(
        f"{API}/movimientos-caja/{movimiento_id}/debitar",
        json={"observaciones": "Afiliado sin cobertura al día"},
    )

    r = client.post(f"{API}/movimientos-caja/{movimiento_id}/cobrar", json={})

    assert r.status_code == 409, r.text


def test_movimiento_inexistente_responde_404(client):
    assert client.get(f"{API}/movimientos-caja/999").status_code == 404
    assert client.post(f"{API}/movimientos-caja/999/cobrar", json={}).status_code == 404
    r = client.post(
        f"{API}/movimientos-caja/999/debitar", json={"observaciones": "motivo"}
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 5) Liquidación de obras sociales
# ---------------------------------------------------------------------------


def _escenario_liquidacion(client, doctor) -> dict:
    """Dos visitas de OSDE en noviembre, una de IOMA y una de OSDE en diciembre."""
    osde = _crear_obra_social(client, nombre="OSDE")
    ioma = _crear_obra_social(client, nombre="IOMA")
    prestacion = _crear_prestacion(client)
    _crear_arancel(
        client,
        osde["id"],
        prestacion["id"],
        monto_cubre_os=9000,
        monto_coseguro_paciente=1000,
    )
    _crear_arancel(
        client,
        ioma["id"],
        prestacion["id"],
        monto_cubre_os=7000,
        monto_coseguro_paciente=3000,
    )
    afiliado_uno = _crear_paciente(client, nombre="Afiliado Uno", obra_social_id=osde["id"])
    afiliado_dos = _crear_paciente(client, nombre="Afiliado Dos", obra_social_id=osde["id"])
    afiliado_ioma = _crear_paciente(client, nombre="Afiliado IOMA", obra_social_id=ioma["id"])

    turno_uno = _crear_turno(client, doctor.id, afiliado_uno["id"], inicio=DIA)
    turno_dos = _crear_turno(
        client, doctor.id, afiliado_dos["id"], inicio=DIA + timedelta(hours=2)
    )
    turno_ioma = _crear_turno(
        client, doctor.id, afiliado_ioma["id"], inicio=DIA + timedelta(hours=4)
    )
    turno_diciembre = _crear_turno(
        client,
        doctor.id,
        afiliado_uno["id"],
        inicio=datetime(2026, 12, 1, 12, 0),
    )

    movimientos = [
        _facturar_ok(
            client,
            turno_uno["id"],
            [{"prestacion_id": prestacion["id"]}],
            fecha="2026-11-05T10:00:00",
        ),
        _facturar_ok(
            client,
            turno_dos["id"],
            [{"prestacion_id": prestacion["id"]}],
            fecha="2026-11-20T10:00:00",
        ),
        _facturar_ok(
            client,
            turno_ioma["id"],
            [{"prestacion_id": prestacion["id"]}],
            fecha="2026-11-20T11:00:00",
        ),
        _facturar_ok(
            client,
            turno_diciembre["id"],
            [{"prestacion_id": prestacion["id"]}],
            fecha="2026-12-01T12:30:00",
        ),
    ]
    return {
        "osde": osde,
        "ioma": ioma,
        "movimientos": movimientos,
    }


def test_presentacion_del_periodo_agrupa_los_pendientes_de_la_obra_social(
    client, doctor
):
    datos = _escenario_liquidacion(client, doctor)

    r = client.get(
        f"{API}/liquidaciones/obras-sociales"
        f"?obra_social_id={datos['osde']['id']}&anio={ANIO}&mes={MES}"
    )

    assert r.status_code == 200, r.text
    presentacion = r.json()
    assert presentacion["numero_presentacion"] == (
        f"PRES-OS{datos['osde']['id']}-202611"
    )
    assert presentacion["obra_social_nombre"] == "OSDE"
    assert presentacion["generada"] is False          # todavía no se presentó
    assert presentacion["cantidad_movimientos"] == 2  # sólo las dos visitas de OSDE
    assert presentacion["total_presentado"] == 18000  # 2 × 9000
    assert presentacion["total_efectivo"] == 2000     # 2 × 1000 de coseguro
    assert presentacion["total_pendiente_os"] == 18000
    assert presentacion["total_cobrado_os"] == 0
    assert presentacion["total_debitado_os"] == 0
    assert {m["obra_social_nombre"] for m in presentacion["movimientos"]} == {"OSDE"}


def test_generar_la_presentacion_pasa_los_movimientos_a_en_proceso(client, doctor):
    datos = _escenario_liquidacion(client, doctor)
    osde = datos["osde"]["id"]

    r = client.post(
        f"{API}/liquidaciones/obras-sociales",
        json={"obra_social_id": osde, "anio": ANIO, "mes": MES},
    )

    assert r.status_code == 200, r.text
    presentacion = r.json()
    assert presentacion["generada"] is True
    assert presentacion["cantidad_movimientos"] == 2
    assert {
        m["estado_pago"] for m in presentacion["movimientos"]
    } == {EstadoPagoMovimiento.EN_PROCESO_LIQUIDACION.value}

    # La otra cobertura y el mes siguiente quedan intactos.
    de_ioma = client.get(
        f"{API}/liquidaciones/obras-sociales"
        f"?obra_social_id={datos['ioma']['id']}&anio={ANIO}&mes={MES}"
    ).json()
    assert {m["estado_pago"] for m in de_ioma["movimientos"]} == {
        EstadoPagoMovimiento.PENDIENTE.value
    }
    de_diciembre = client.get(
        f"{API}/liquidaciones/obras-sociales?obra_social_id={osde}&anio=2026&mes=12"
    ).json()
    assert {m["estado_pago"] for m in de_diciembre["movimientos"]} == {
        EstadoPagoMovimiento.PENDIENTE.value
    }


def test_generar_la_presentacion_sin_pendientes_es_422(client, doctor):
    _escenario_liquidacion(client, doctor)
    cuerpo = {"obra_social_id": 1, "anio": ANIO, "mes": MES}
    cuerpo["obra_social_id"] = client.get(f"{API}/obras-sociales?nombre=OSDE").json()[0]["id"]

    primera = client.post(f"{API}/liquidaciones/obras-sociales", json=cuerpo)
    segunda = client.post(f"{API}/liquidaciones/obras-sociales", json=cuerpo)

    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 422, segunda.text
    assert "pendientes" in segunda.json()["detail"]


def test_liquidacion_de_una_obra_social_inexistente_404(client):
    r = client.get(
        f"{API}/liquidaciones/obras-sociales?obra_social_id=999&anio={ANIO}&mes={MES}"
    )

    assert r.status_code == 404, r.text


def test_el_mes_de_la_liquidacion_se_valida(client):
    r = client.get(f"{API}/liquidaciones/obras-sociales?obra_social_id=1&anio=2026&mes=13")

    assert r.status_code == 422, r.text


def test_marcar_cobros_y_debitos_individuales_en_la_presentacion(client, doctor):
    """Al recibir el depósito se tilda cada orden: cobrada o debitada."""
    datos = _escenario_liquidacion(client, doctor)
    osde = datos["osde"]["id"]
    presentacion = client.post(
        f"{API}/liquidaciones/obras-sociales",
        json={"obra_social_id": osde, "anio": ANIO, "mes": MES},
    ).json()
    primero, segundo = (m["id"] for m in presentacion["movimientos"])

    cobrado = client.post(
        f"{API}/movimientos-caja/{primero}/cobrar",
        json={"observaciones": "Depósito del período"},
    )
    debitado = client.post(
        f"{API}/movimientos-caja/{segundo}/debitar",
        json={"observaciones": "Orden rechazada por la auditoría"},
    )

    assert cobrado.status_code == 200, cobrado.text
    assert debitado.status_code == 200, debitado.text

    cerrada = client.get(
        f"{API}/liquidaciones/obras-sociales"
        f"?obra_social_id={osde}&anio={ANIO}&mes={MES}"
    ).json()
    assert cerrada["total_cobrado_os"] == 9000
    assert cerrada["total_debitado_os"] == 9000
    assert cerrada["total_pendiente_os"] == 0
    assert cerrada["generada"] is True


# ---------------------------------------------------------------------------
# 6) Liquidación de profesionales
# ---------------------------------------------------------------------------


def _crear_doctor(client, **overrides) -> dict:
    """Crea un odontólogo por API y devuelve su representación."""
    payload = {"nombre": "Dr. Nuevo", "especialidad": "Ortodoncia"}
    payload.update(overrides)
    r = client.post(f"{API}/doctores", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_el_doctor_nace_como_dueno_sin_valor(client, doctor):
    r = client.get(f"{API}/doctores/{doctor.id}")

    assert r.status_code == 200, r.text
    assert r.json()["tipo_pago"] == TipoPagoDoctor.DUENO.value
    assert r.json()["valor_pago"] is None


def test_una_comision_sin_porcentaje_es_422(client):
    r = client.post(
        f"{API}/doctores",
        json={
            "nombre": "Dr. Comisión",
            "especialidad": "Endodoncia",
            "tipo_pago": "COMISION",
        },
    )

    assert r.status_code == 422, r.text


def test_pasar_a_sueldo_sin_importe_es_422(client, doctor):
    r = client.patch(f"{API}/doctores/{doctor.id}", json={"tipo_pago": "SUELDO"})

    assert r.status_code == 422, r.text
    assert "sueldo" in r.text.lower()


def test_liquidacion_de_profesionales_por_tipo_de_pago(client):
    """Dueño (100 %), comisión (40 %) y sueldo fijo: los tres casos del mes."""
    prestacion = _crear_prestacion(client, valor_base=10000)
    dueno = _crear_doctor(client, nombre="Dra. Dueña")
    comision = _crear_doctor(
        client, nombre="Dr. Comisión", tipo_pago="COMISION", valor_pago=40
    )
    sueldo = _crear_doctor(
        client, nombre="Dr. Sueldo", tipo_pago="SUELDO", valor_pago=200000
    )

    for indice, profesional in enumerate((dueno, comision, sueldo)):
        paciente = _crear_paciente(client, nombre=f"Paciente {indice}")
        turno = _crear_turno(
            client,
            profesional["id"],
            paciente["id"],
            inicio=DIA + timedelta(hours=indice),
        )
        _facturar_ok(
            client,
            turno["id"],
            [{"prestacion_id": prestacion["id"]}],
            fecha="2026-11-10T10:00:00",
        )

    r = client.get(f"{API}/liquidaciones/profesionales?anio={ANIO}&mes={MES}")

    assert r.status_code == 200, r.text
    reporte = r.json()
    por_nombre = {d["doctor_nombre"]: d for d in reporte["doctores"]}
    assert por_nombre["Dra. Dueña"]["honorarios"] == 10000       # 100 % de 10000
    assert por_nombre["Dr. Comisión"]["honorarios"] == 4000      # 40 % de 10000
    assert por_nombre["Dr. Sueldo"]["honorarios"] == 200000      # fijo del mes
    # La producción se mide igual para los tres (el sueldo también produce).
    assert por_nombre["Dr. Sueldo"]["produccion_total"] == 10000
    assert reporte["total_produccion"] == 30000
    assert reporte["total_honorarios"] == 214000
    assert reporte["total_efectivo"] == 30000       # los tres son particulares
    assert reporte["total_os"] == 0


def test_la_liquidacion_del_mes_tambien_reparte_lo_de_las_obras_sociales(client, doctor):
    obra = _crear_obra_social(client)
    prestacion = _crear_prestacion(client)
    _crear_arancel(
        client,
        obra["id"],
        prestacion["id"],
        monto_cubre_os=6000,
        monto_coseguro_paciente=2000,
    )
    paciente = _crear_paciente(client, obra_social_id=obra["id"])
    turno = _crear_turno(client, doctor.id, paciente["id"], inicio=DIA)
    _facturar_ok(
        client,
        turno["id"],
        [{"prestacion_id": prestacion["id"]}],
        fecha="2026-11-10T10:00:00",
    )

    reporte = client.get(
        f"{API}/liquidaciones/profesionales?anio={ANIO}&mes={MES}&doctor_id={doctor.id}"
    ).json()

    liquidacion = reporte["doctores"][0]
    assert liquidacion["produccion_efectivo"] == 2000
    assert liquidacion["produccion_os"] == 6000
    assert liquidacion["produccion_total"] == 8000
    assert liquidacion["honorarios"] == 8000       # dueño sin valor: 100 %
    assert liquidacion["cantidad_movimientos"] == 1
    assert liquidacion["cantidad_turnos"] == 1
    assert liquidacion["tipo_pago"] == TipoPagoDoctor.DUENO.value
    assert liquidacion["movimientos"][0]["turno_id"] == turno["id"]


def test_liquidacion_de_un_solo_profesional(client, doctor):
    r = client.get(
        f"{API}/liquidaciones/profesionales?anio={ANIO}&mes={MES}&doctor_id={doctor.id}"
    )

    assert r.status_code == 200, r.text
    reporte = r.json()
    assert [d["doctor_id"] for d in reporte["doctores"]] == [doctor.id]
    assert reporte["doctores"][0]["honorarios"] == 0      # sin producción en el mes
    assert reporte["total_produccion"] == 0


def test_liquidacion_de_un_profesional_inexistente_404(client):
    r = client.get(
        f"{API}/liquidaciones/profesionales?anio={ANIO}&mes={MES}&doctor_id=999"
    )

    assert r.status_code == 404, r.text


def test_la_liquidacion_ignora_los_meses_vecinos(client, doctor):
    prestacion = _crear_prestacion(client, valor_base=10000)
    paciente = _crear_paciente(client)
    turno_noviembre = _crear_turno(client, doctor.id, paciente["id"], inicio=DIA)
    turno_diciembre = _crear_turno(
        client, doctor.id, paciente["id"], inicio=datetime(2026, 12, 2, 12, 0)
    )
    _facturar_ok(
        client,
        turno_noviembre["id"],
        [{"prestacion_id": prestacion["id"]}],
        fecha="2026-11-10T10:00:00",
    )
    _facturar_ok(
        client,
        turno_diciembre["id"],
        [{"prestacion_id": prestacion["id"]}],
        fecha="2026-12-10T10:00:00",
    )

    noviembre = client.get(
        f"{API}/liquidaciones/profesionales?anio={ANIO}&mes={MES}&doctor_id={doctor.id}"
    ).json()
    diciembre = client.get(
        f"{API}/liquidaciones/profesionales?anio=2026&mes=12&doctor_id={doctor.id}"
    ).json()

    assert noviembre["doctores"][0]["produccion_total"] == 10000
    assert noviembre["doctores"][0]["cantidad_turnos"] == 1
    assert diciembre["doctores"][0]["produccion_total"] == 10000


# ---------------------------------------------------------------------------
# 7) Esquema: modelos, enums y compatibilidad con PostgreSQL
# ---------------------------------------------------------------------------

# Tablas que agrega el Módulo 3.
TABLAS_NUEVAS = (Prestacion, ArancelObraSocial, MovimientoCaja, DetalleMovimientoCaja)


def test_el_ddl_de_las_tablas_nuevas_compila_en_postgresql():
    """El mismo modelo sirve para SQLite (local) y PostgreSQL (nube).

    Se compila el ``CREATE TABLE`` con el dialecto de PostgreSQL: si el modelo
    usara algo exclusivo de SQLite, la migración a la nube fallaría.
    """
    dialecto = postgresql.dialect()

    for modelo in TABLAS_NUEVAS:
        ddl = str(CreateTable(modelo.__table__).compile(dialect=dialecto))
        assert f"CREATE TABLE {modelo.__tablename__}" in ddl
        # Los enums viajan como VARCHAR (``native_enum=False``): no hace falta un
        # ``CREATE TYPE`` previo ni migrar tipos al pasar de SQLite a PostgreSQL.
        assert "CREATE TYPE" not in ddl


def test_los_enums_del_modulo_son_portatiles():
    """``tipo_pago`` y ``estado_pago`` son VARCHAR, no tipos nativos."""
    dialecto = postgresql.dialect()

    tipo_pago = Doctor.__table__.c.tipo_pago.type
    estado_pago = MovimientoCaja.__table__.c.estado_pago.type
    assert tipo_pago.native_enum is False
    assert estado_pago.native_enum is False
    assert "VARCHAR(15)" in str(tipo_pago.compile(dialect=dialecto))
    assert "VARCHAR(25)" in str(estado_pago.compile(dialect=dialecto))


def test_los_enums_tienen_los_valores_del_modulo():
    assert {t.value for t in TipoPagoDoctor} == {"DUENO", "COMISION", "SUELDO"}
    assert {e.value for e in EstadoPagoMovimiento} == {
        "PENDIENTE",
        "EN_PROCESO_LIQUIDACION",
        "COBRADO",
        "DEBITADO_OS",
    }


def test_los_indices_unicos_del_modulo_existen_y_compilan():
    """Un turno tiene un cierre; una práctica y un arancel, un código único."""
    esperados = {
        Prestacion: "uq_prestacion_codigo",
        ArancelObraSocial: "uq_arancel_os_prestacion",
        MovimientoCaja: "uq_movimiento_caja_turno",
    }
    dialecto = postgresql.dialect()

    for modelo, nombre in esperados.items():
        indice = next(i for i in modelo.__table__.indexes if i.name == nombre)
        assert indice.unique is True
        ddl = str(CreateIndex(indice).compile(dialect=dialecto))
        assert f"CREATE UNIQUE INDEX {nombre}" in ddl


def test_las_columnas_del_modulo_estan_en_el_esquema():
    assert set(Prestacion.__table__.c.keys()) >= {
        "id",
        "codigo_nomenclador",
        "descripcion",
        "valor_base",
        "activa",
    }
    assert set(ArancelObraSocial.__table__.c.keys()) >= {
        "id",
        "obra_social_id",
        "prestacion_id",
        "monto_cubre_os",
        "monto_coseguro_paciente",
    }
    assert set(MovimientoCaja.__table__.c.keys()) >= {
        "id",
        "turno_id",
        "doctor_id",
        "fecha",
        "monto_efectivo",
        "monto_pendiente_os",
        "estado_pago",
    }
    assert set(Doctor.__table__.c.keys()) >= {"tipo_pago", "valor_pago"}


# ---------------------------------------------------------------------------
# 8) Panel: pestaña "Facturación y Caja" y su contrato con la API
# ---------------------------------------------------------------------------


def test_el_panel_tiene_la_seccion_de_facturacion_y_caja(client):
    html = client.get("/").text

    assert 'data-view="facturacion"' in html
    assert 'id="view-facturacion"' in html
    assert "Facturación y Caja" in html
    # Los cinco paneles de la vista (el último: convenios por obra social).
    assert html.count("data-fact-tab=") == 5
    assert html.count("data-fact-panel=") == 5
    for panel in ("caja", "obras", "medicos", "precios", "convenios"):
        assert f'data-fact-panel="{panel}"' in html
    # Tablas y modales del módulo.
    for identificador in (
        "fact-lista-caja",
        "fact-lista-presentacion",
        "fact-lista-medicos",
        "fact-lista-prestaciones",
        "fact-lista-aranceles",
        "conv-lista",
        "modal-cobro",
        "modal-prestacion",
        "modal-arancel",
        "modal-debito",
    ):
        assert f'id="{identificador}"' in html


def test_el_cartel_del_coseguro_esta_en_el_formulario_de_cobro(client):
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert "Cobrar Coseguro en mostrador" in html
    assert 'id="fact-cobro-efectivo"' in html
    assert 'id="fact-cobro-os"' in html
    assert 'id="fact-cobro-particular"' in html
    # El cartel se recalcula al tildar prácticas y al registrar el cobro el
    # backend devuelve el importe definitivo (se muestra en el toast).
    assert "function recalcularCobro()" in js
    assert "Cobrar Coseguro en mostrador: ${fmtMoneda(movimiento.monto_efectivo)}" in js


def test_el_javascript_de_facturacion_consume_los_endpoints_del_modulo(client):
    js = client.get("/static/app.js").text

    assert 'const RUTA_CAJA = "/movimientos-caja";' in js
    assert 'const RUTA_PRESTACIONES = "/prestaciones";' in js
    assert 'const RUTA_ARANCELES = "/aranceles";' in js
    assert 'const RUTA_LIS_OS = "/liquidaciones/obras-sociales";' in js
    assert 'const RUTA_LIS_PROF = "/liquidaciones/profesionales";' in js
    assert "/facturar" in js                      # cierre de la visita
    assert "/prestaciones-disponibles" in js
    assert "permitir_particular" in js
    assert "/aranceles" in js                     # aranceles de la cobertura
    assert "data-fact-tab" in js


def test_la_tarjeta_del_turno_ofrece_cobrar_la_visita(client):
    """El cierre de la visita arranca desde la agenda (botón "Cobrar")."""
    js = client.get("/static/app.js").text

    assert 'data-cobrar-turno="${t.id}"' in js
    assert "abrirModalCobro" in js


def test_todos_los_ids_que_usa_el_panel_existen_en_el_html(client):
    """Contrato HTML ↔ JS: ningún ``getElementById`` apunta a un id inexistente."""
    import re

    html = client.get("/").text
    js = client.get("/static/app.js").text
    usados = set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))
    definidos = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))

    faltantes = sorted(identificador for identificador in usados if identificador not in definidos)
    assert faltantes == [], f"ids usados por app.js y ausentes en index.html: {faltantes}"


def test_la_hoja_de_estilos_tiene_los_estilos_del_modulo(client):
    css = client.get("/static/styles.css").text

    for clase in (
        ".fact-tabs",
        ".fact-tab",
        ".fact-kpi",
        ".fact-chip",
        ".fact-cartel",
        ".fact-cartel-vacio",
        ".fact-check",
        ".fact-med",
    ):
        assert clase in css


