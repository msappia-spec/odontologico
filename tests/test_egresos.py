"""Pruebas del Módulo 6: proveedores, gastos recibidos y cuentas por pagar.

Se cubre el circuito de egresos completo (API + base de datos):

1. **Proveedores** (``POST``/``GET /egresos/proveedores``): el alta nace con
   saldo ``0.00``, el nombre es obligatorio y el rubro debe ser uno de los tres
   del enum; el listado va en orden alfabético y se filtra por rubro y por
   «sólo con deuda».
2. **Facturas** (``POST /egresos/facturas``): registrar un gasto **aumenta** el
   saldo del proveedor; ``paciente_id`` sólo se admite para un
   ``MECANICO_DENTAL``; el vencimiento por defecto son 30 días y las fechas se
   guardan en UTC naive aunque lleguen con zona.
3. **Pagos** (``POST /egresos/pagar``): un pago parcial deja la factura en
   ``PARCIAL`` y baja los dos saldos; dos pagos parciales la cierran en
   ``PAGADO``; el método (``EFECTIVO``/``TRANSFERENCIA``/``TARJETA``) se guarda
   en cada pago.
4. **Atomicidad**: un pago mayor al saldo responde ``422`` y **no** deja pago ni
   descuenta el saldo del proveedor; un error a mitad de una transacción
   revierte la factura y el saldo completo.
5. **Control monetario**: los importes se acumulan en ``Decimal`` (tres facturas
   de ``0.10`` dan ``0.30``, nunca ``0.30000000000000004``) y el saldo del
   proveedor se puede reconstruir desde las facturas.
6. **Tablero** (``GET /egresos/cuentas-por-pagar``): deuda total, deuda vencida
   y desglose por rubro.
7. **Portabilidad**: el DDL compila con el dialecto de PostgreSQL (``NUMERIC(10,
   2)`` y enums como ``VARCHAR`` con ``CHECK``) y la base rechaza un estado
   inventado o un pago acumulado mayor al total.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app.core.datetime_utils import utcnow
from app.db.migraciones import aplicar_columnas_faltantes
from app.main import app
from app.models.egreso import GastoFactura, PagoEgreso, Proveedor
from app.models.enums import EstadoPagoFactura, MetodoPagoEgreso, TipoProveedor
from app.schemas.egreso import PagoEgresoCreate
from app.services import egreso_service

API = "/api/v1"
RUTA_PROVEEDORES = f"{API}/egresos/proveedores"
RUTA_FACTURAS = f"{API}/egresos/facturas"
RUTA_PAGAR = f"{API}/egresos/pagar"
RUTA_RESUMEN = f"{API}/egresos/cuentas-por-pagar"


# --------------------------------------------------------------------------- #
# Fixtures y helpers
# --------------------------------------------------------------------------- #
def _crear_proveedor(client, nombre, tipo, telefono=None):
    """Da de alta un proveedor por la API y devuelve su JSON."""
    cuerpo = {"nombre": nombre, "tipo_proveedor": tipo}
    if telefono is not None:
        cuerpo["telefono_contacto"] = telefono
    respuesta = client.post(RUTA_PROVEEDORES, json=cuerpo)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _crear_factura(client, proveedor_id, monto, **extra):
    """Registra una factura por la API y devuelve la respuesta HTTP."""
    cuerpo = {
        "proveedor_id": proveedor_id,
        "concepto": extra.pop("concepto", "Gasto de prueba"),
        "monto_total": monto,
    }
    cuerpo.update(extra)
    return client.post(RUTA_FACTURAS, json=cuerpo)


@pytest.fixture()
def mecanico(client):
    """Proveedor mecánico dental (el único que admite ``paciente_id``)."""
    return _crear_proveedor(
        client, "Laboratorio Sonrisa", "MECANICO_DENTAL", "+5491155550000"
    )


@pytest.fixture()
def insumos(client):
    """Proveedor de insumos."""
    return _crear_proveedor(client, "Droguería Central", "INSUMOS")


# --------------------------------------------------------------------------- #
# 1. Proveedores
# --------------------------------------------------------------------------- #
def test_alta_de_proveedor_nace_con_saldo_cero(client):
    """El alta devuelve el UUID, el rubro y el saldo en 0.00 (nunca del cliente)."""
    proveedor = _crear_proveedor(
        client, "  Laboratorio Sonrisa  ", "MECANICO_DENTAL", "   "
    )

    assert proveedor["nombre"] == "Laboratorio Sonrisa"
    # Un teléfono en blanco se guarda como NULL, no como cadena vacía.
    assert proveedor["telefono_contacto"] is None
    assert proveedor["tipo_proveedor"] == "MECANICO_DENTAL"
    assert proveedor["saldo_pendiente"] == 0.0
    assert proveedor["facturas_abiertas"] == 0
    assert proveedor["deuda_vencida"] == 0.0
    # El identificador es un UUID real, no un entero autoincremental.
    assert len(proveedor["id"]) == 36 and proveedor["id"].count("-") == 4


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"nombre": "", "tipo_proveedor": "INSUMOS"},
        {"nombre": "   ", "tipo_proveedor": "INSUMOS"},
        {"nombre": "Droguería", "tipo_proveedor": "FERRETERIA"},
        {"tipo_proveedor": "INSUMOS"},
    ],
)
def test_alta_de_proveedor_invalido_responde_422(client, cuerpo):
    """Sin nombre, con nombre en blanco o con un rubro inexistente: 422."""
    assert client.post(RUTA_PROVEEDORES, json=cuerpo).status_code == 422


def test_listado_alfabetico_con_filtros(client):
    """El listado ordena alfabéticamente y filtra por rubro y por deuda."""
    _crear_proveedor(client, "zeta servicios", "SERVICIOS_IMPUESTOS")
    _crear_proveedor(client, "Alfa Insumos", "INSUMOS")
    con_deuda = _crear_proveedor(client, "Beta Mecánica", "MECANICO_DENTAL")
    assert _crear_factura(client, con_deuda["id"], "1000.00").status_code == 201

    completo = client.get(RUTA_PROVEEDORES).json()
    assert [p["nombre"] for p in completo] == [
        "Alfa Insumos",
        "Beta Mecánica",
        "zeta servicios",
    ]

    solo_insumos = client.get(
        RUTA_PROVEEDORES, params={"tipo_proveedor": "INSUMOS"}
    ).json()
    assert [p["nombre"] for p in solo_insumos] == ["Alfa Insumos"]

    solo_deuda = client.get(RUTA_PROVEEDORES, params={"solo_con_deuda": "true"}).json()
    assert [p["nombre"] for p in solo_deuda] == ["Beta Mecánica"]
    assert solo_deuda[0]["saldo_pendiente"] == 1000.0
    assert solo_deuda[0]["facturas_abiertas"] == 1


def test_detalle_de_proveedor_inexistente_responde_404(client):
    """Un UUID que no existe responde 404 con el mensaje del dominio."""
    respuesta = client.get(
        f"{RUTA_PROVEEDORES}/00000000-0000-4000-8000-000000000000"
    )
    assert respuesta.status_code == 404
    assert respuesta.json()["detail"] == "Proveedor no encontrado."



# --------------------------------------------------------------------------- #
# 2. Facturas recibidas
# --------------------------------------------------------------------------- #
def test_factura_de_mecanico_con_paciente_aumenta_el_saldo(
    client, mecanico, paciente
):
    """La factura nace PENDIENTE y el saldo del proveedor sube por el total."""
    respuesta = _crear_factura(
        client,
        mecanico["id"],
        "15000.00",
        concepto="Corona de porcelana molar 36",
        paciente_id=paciente.id,
    )
    assert respuesta.status_code == 201, respuesta.text
    factura = respuesta.json()

    assert factura["estado_pago"] == "PENDIENTE"
    assert factura["monto_total"] == 15000.0
    assert factura["monto_pagado"] == 0.0
    assert factura["saldo_pendiente"] == 15000.0
    assert factura["proveedor_nombre"] == "Laboratorio Sonrisa"
    assert factura["proveedor_tipo"] == "MECANICO_DENTAL"
    assert factura["paciente_id"] == paciente.id
    assert factura["paciente_nombre"] == paciente.nombre
    assert factura["pagos"] == []

    proveedor = client.get(f"{RUTA_PROVEEDORES}/{mecanico['id']}").json()
    assert proveedor["saldo_pendiente"] == 15000.0
    assert len(proveedor["facturas"]) == 1
    assert proveedor["facturas"][0]["id"] == factura["id"]


def test_factura_sin_paciente_es_valida_en_cualquier_rubro(client, insumos):
    """El paciente es opcional: una compra de insumos no se vincula a nadie."""
    respuesta = _crear_factura(
        client, insumos["id"], "2500.50", concepto="Compra de composites"
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["paciente_id"] is None
    assert respuesta.json()["paciente_nombre"] is None


def test_paciente_solo_se_admite_en_mecanico_dental(client, insumos, paciente):
    """``paciente_id`` en un proveedor de insumos es 422 y no crea nada."""
    respuesta = _crear_factura(
        client, insumos["id"], "900.00", paciente_id=paciente.id
    )
    assert respuesta.status_code == 422
    assert "mecánico dental" in respuesta.json()["detail"]

    # Atomicidad del alta: la factura no se guardó y el saldo sigue intacto.
    proveedor = client.get(f"{RUTA_PROVEEDORES}/{insumos['id']}").json()
    assert proveedor["saldo_pendiente"] == 0.0
    assert proveedor["facturas_abiertas"] == 0
    assert client.get(RUTA_FACTURAS).json() == []


@pytest.mark.parametrize("monto", ["0", "-100.00", "10.001", "99999999999.00"])
def test_monto_de_factura_invalido_responde_422(client, insumos, monto):
    """Cero, negativo, con tres decimales o fuera de rango: 422 antes de la base."""
    assert _crear_factura(client, insumos["id"], monto).status_code == 422


def test_vencimiento_anterior_a_la_emision_responde_422(client, insumos):
    """Un vencimiento anterior a la emisión es un dato imposible."""
    respuesta = _crear_factura(
        client,
        insumos["id"],
        "500.00",
        fecha_emision="2026-10-10T10:00:00",
        fecha_vencimiento="2026-10-01T10:00:00",
    )
    assert respuesta.status_code == 422
    assert client.get(RUTA_FACTURAS).json() == []


def test_vencimiento_por_defecto_son_treinta_dias(client, insumos):
    """Sin fecha de vencimiento se toman 30 días desde la emisión."""
    factura = _crear_factura(
        client, insumos["id"], "750.00", fecha_emision="2026-10-01T12:00:00"
    ).json()
    assert factura["fecha_vencimiento"].startswith("2026-10-31T12:00:00")


def test_fechas_con_zona_se_guardan_en_utc_naive(client, insumos):
    """Una fecha con offset -03:00 se normaliza a UTC (convención del sistema)."""
    factura = _crear_factura(
        client,
        insumos["id"],
        "300.00",
        fecha_emision="2026-10-01T10:00:00-03:00",
        fecha_vencimiento="2026-10-15T10:00:00-03:00",
    ).json()
    assert factura["fecha_emision"].startswith("2026-10-01T13:00:00")
    assert factura["fecha_vencimiento"].startswith("2026-10-15T13:00:00")


def test_factura_con_proveedor_o_paciente_inexistente_responde_404(
    client, mecanico
):
    """Un proveedor o un paciente que no existen cortan el alta con 404."""
    sin_proveedor = _crear_factura(
        client, "00000000-0000-4000-8000-000000000000", "100.00"
    )
    assert sin_proveedor.status_code == 404

    sin_paciente = _crear_factura(
        client, mecanico["id"], "100.00", paciente_id=999999
    )
    assert sin_paciente.status_code == 404
    assert client.get(RUTA_FACTURAS).json() == []



# --------------------------------------------------------------------------- #
# 3. Pagos: parciales, totales y método de pago
# --------------------------------------------------------------------------- #
def test_pago_parcial_deja_la_factura_en_parcial(client, insumos):
    """Un pago menor al total baja los dos saldos y marca PARCIAL."""
    factura = _crear_factura(client, insumos["id"], "1000.00").json()

    respuesta = client.post(
        RUTA_PAGAR,
        json={
            "gasto_factura_id": factura["id"],
            "monto_pagado": "400.00",
            "metodo_pago": "TRANSFERENCIA",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    pago = respuesta.json()

    assert pago["pago"]["monto_pagado"] == 400.0
    assert pago["pago"]["metodo_pago"] == "TRANSFERENCIA"
    assert pago["pago"]["gasto_factura_id"] == factura["id"]
    assert pago["factura_estado_pago"] == "PARCIAL"
    assert pago["factura_saldo_pendiente"] == 600.0
    assert pago["proveedor_saldo_pendiente"] == 600.0

    detalle = client.get(
        RUTA_FACTURAS, params={"proveedor_id": insumos["id"]}
    ).json()[0]
    assert detalle["monto_pagado"] == 400.0
    assert detalle["saldo_pendiente"] == 600.0
    assert len(detalle["pagos"]) == 1


def test_dos_pagos_parciales_cierran_la_factura_en_pagado(client, insumos):
    """Los pagos se acumulan: al cubrir el total queda PAGADO y saldo cero."""
    factura = _crear_factura(client, insumos["id"], "1000.00").json()

    primero = client.post(
        RUTA_PAGAR,
        json={"gasto_factura_id": factura["id"], "monto_pagado": "250.00"},
    ).json()
    assert primero["pago"]["metodo_pago"] == "EFECTIVO"  # default del esquema
    assert primero["factura_estado_pago"] == "PARCIAL"

    segundo = client.post(
        RUTA_PAGAR,
        json={
            "gasto_factura_id": factura["id"],
            "monto_pagado": "750.00",
            "metodo_pago": "TARJETA",
        },
    ).json()
    assert segundo["factura_estado_pago"] == "PAGADO"
    assert segundo["factura_saldo_pendiente"] == 0.0
    assert segundo["proveedor_saldo_pendiente"] == 0.0

    detalle = client.get(RUTA_FACTURAS, params={"estado_pago": "PAGADO"}).json()[0]
    assert detalle["monto_pagado"] == 1000.0
    assert [p["metodo_pago"] for p in detalle["pagos"]] == ["EFECTIVO", "TARJETA"]
    assert [p["monto_pagado"] for p in detalle["pagos"]] == [250.0, 750.0]

    proveedor = client.get(f"{RUTA_PROVEEDORES}/{insumos['id']}").json()
    assert proveedor["saldo_pendiente"] == 0.0
    assert proveedor["facturas_abiertas"] == 0


def test_pago_mayor_al_saldo_responde_422_y_no_toca_nada(client, insumos):
    """Un pago de más se rechaza y la transacción no descuenta ni un centavo."""
    factura = _crear_factura(client, insumos["id"], "1000.00").json()

    respuesta = client.post(
        RUTA_PAGAR,
        json={"gasto_factura_id": factura["id"], "monto_pagado": "1500.00"},
    )
    assert respuesta.status_code == 422
    assert "supera el saldo pendiente" in respuesta.json()["detail"]

    # Nada quedó escrito: ni el pago, ni el estado, ni el saldo del proveedor.
    detalle = client.get(RUTA_FACTURAS).json()[0]
    assert detalle["estado_pago"] == "PENDIENTE"
    assert detalle["monto_pagado"] == 0.0
    assert detalle["pagos"] == []
    assert (
        client.get(f"{RUTA_PROVEEDORES}/{insumos['id']}").json()["saldo_pendiente"]
        == 1000.0
    )


def test_pago_sobre_factura_pagada_responde_422(client, insumos):
    """Una factura sin saldo no admite un nuevo pago."""
    factura = _crear_factura(client, insumos["id"], "500.00").json()
    pago = {"gasto_factura_id": factura["id"], "monto_pagado": "500.00"}
    assert client.post(RUTA_PAGAR, json=pago).status_code == 201

    repetido = client.post(RUTA_PAGAR, json=pago)
    assert repetido.status_code == 422
    assert "ya está pagada" in repetido.json()["detail"]

    detalle = client.get(RUTA_FACTURAS).json()[0]
    assert detalle["monto_pagado"] == 500.0
    assert len(detalle["pagos"]) == 1


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},  # sin importe
        {"monto_pagado": "0"},  # importe cero
        {"monto_pagado": "-50.00"},  # importe negativo
        {"monto_pagado": "10.001"},  # tres decimales
        {"monto_pagado": "100.00", "metodo_pago": "CHEQUE"},  # método inexistente
    ],
)
def test_pago_invalido_responde_422(client, insumos, cuerpo):
    """El esquema rechaza importes y métodos inválidos antes de tocar la base."""
    factura = _crear_factura(client, insumos["id"], "1000.00").json()
    payload = {"gasto_factura_id": factura["id"], **cuerpo}
    assert client.post(RUTA_PAGAR, json=payload).status_code == 422


def test_pago_de_factura_inexistente_responde_404(client):
    """Un UUID de factura que no existe responde 404."""
    respuesta = client.post(
        RUTA_PAGAR,
        json={
            "gasto_factura_id": "00000000-0000-4000-8000-000000000000",
            "monto_pagado": "100.00",
        },
    )
    assert respuesta.status_code == 404
    assert respuesta.json()["detail"] == "Factura de egreso no encontrada."



# --------------------------------------------------------------------------- #
# 4. Atomicidad y control monetario (capa de servicio)
# --------------------------------------------------------------------------- #
def _proveedor_orm(db_session, nombre="Lab. Atomicidad") -> Proveedor:
    """Proveedor mecánico creado directo en la base (para probar el servicio)."""
    proveedor = Proveedor(
        nombre=nombre,
        tipo_proveedor=TipoProveedor.MECANICO_DENTAL,
        saldo_pendiente=Decimal("0.00"),
    )
    db_session.add(proveedor)
    db_session.commit()
    return proveedor


def _factura_orm(db_session, proveedor, total="100.00") -> GastoFactura:
    """Factura creada directo en la base, con vencimiento a 30 días."""
    ahora = utcnow()
    factura = GastoFactura(
        proveedor_id=proveedor.id,
        concepto="Pieza de prueba",
        monto_total=Decimal(total),
        monto_pagado=Decimal("0.00"),
        fecha_emision=ahora,
        fecha_vencimiento=ahora + timedelta(days=30),
        estado_pago=EstadoPagoFactura.PENDIENTE,
    )
    db_session.add(factura)
    db_session.commit()
    return factura


def test_la_transaccion_revierte_factura_y_saldo_si_falla(db_session):
    """Si algo explota después de escribir, no queda ni la factura ni el saldo."""
    proveedor = _proveedor_orm(db_session)
    ahora = utcnow()

    with pytest.raises(RuntimeError):
        with egreso_service.transaccion(db_session):
            db_session.add(
                GastoFactura(
                    proveedor_id=proveedor.id,
                    concepto="Se debe revertir",
                    monto_total=Decimal("500.00"),
                    monto_pagado=Decimal("0.00"),
                    fecha_emision=ahora,
                    fecha_vencimiento=ahora + timedelta(days=30),
                    estado_pago=EstadoPagoFactura.PENDIENTE,
                )
            )
            proveedor.saldo_pendiente = Decimal("500.00")
            db_session.flush()
            raise RuntimeError("falla simulada a mitad de la transacción")

    db_session.expire_all()
    assert db_session.execute(select(func.count()).select_from(GastoFactura)).scalar_one() == 0
    assert db_session.get(Proveedor, proveedor.id).saldo_pendiente == Decimal("0.00")


def test_los_importes_se_acumulan_en_decimal(client, insumos):
    """Tres facturas de 0.10 dejan 0.30 exactos (nunca 0.30000000000000004)."""
    for _ in range(3):
        assert _crear_factura(client, insumos["id"], "0.10").status_code == 201

    proveedor = client.get(f"{RUTA_PROVEEDORES}/{insumos['id']}").json()
    # La API publica un número JSON; el valor tiene que ser exactamente 0.3.
    assert proveedor["saldo_pendiente"] == 0.3
    assert Decimal(str(proveedor["saldo_pendiente"])) == Decimal("0.30")

    facturas = client.get(RUTA_FACTURAS, params={"proveedor_id": insumos["id"]}).json()
    assert sum(Decimal(str(f["monto_total"])) for f in facturas) == Decimal("0.30")


def test_el_pago_resta_sobre_el_saldo_bloqueado_y_no_sobre_la_copia_vieja(db_session):
    """El pago parte del saldo confirmado en la base, no del que ya tenía el ORM.

    Simula dos pagos simultáneos a **facturas distintas del mismo proveedor**: la
    primera transacción ya bajó el saldo en la base mientras la segunda sesión
    todavía conserva el valor anterior en su *identity map* (el que viaja en el
    ``joined`` de la factura). Sin la relectura del ``SELECT … FOR UPDATE``, el
    segundo pago pisaría la resta del primero (actualización perdida) y el
    consultorio quedaría debiendo de más.
    """
    proveedor = _proveedor_orm(db_session, "Lab. Concurrencia")
    factura = _factura_orm(db_session, proveedor, "100.00")
    proveedor.saldo_pendiente = Decimal("100.00")
    db_session.commit()

    # Otra transacción ya descontó 50 del mismo proveedor; este objeto no lo sabe.
    db_session.execute(
        text("UPDATE proveedores SET saldo_pendiente = 50 WHERE id = :id"),
        {"id": proveedor.id.hex},
    )
    assert proveedor.saldo_pendiente == Decimal("100.00")

    resultado = egreso_service.registrar_pago(
        db_session,
        PagoEgresoCreate(
            gasto_factura_id=factura.id, monto_pagado=Decimal("30.00")
        ),
    )

    # 50 (saldo confirmado) - 30 = 20. Con la copia vieja habría quedado 70.
    assert resultado.proveedor_saldo_pendiente == Decimal("20.00")
    assert resultado.factura_estado_pago is EstadoPagoFactura.PARCIAL
    db_session.expire_all()
    assert db_session.get(Proveedor, proveedor.id).saldo_pendiente == Decimal("20.00")


def test_recalcular_saldo_proveedor_reconstruye_la_deuda(db_session):
    """El saldo se puede reconstruir desde las facturas abiertas."""
    proveedor = _proveedor_orm(db_session)
    abierta = _factura_orm(db_session, proveedor, "300.00")
    pagada = _factura_orm(db_session, proveedor, "200.00")
    pagada.monto_pagado = Decimal("200.00")
    pagada.estado_pago = EstadoPagoFactura.PAGADO
    # Simula una carga manual que dejó el saldo desincronizado.
    proveedor.saldo_pendiente = Decimal("0.00")
    db_session.commit()

    saldo = egreso_service.recalcular_saldo_proveedor(
        db_session, proveedor.id, confirmar=False
    )

    assert saldo == Decimal("300.00")
    assert db_session.get(Proveedor, proveedor.id).saldo_pendiente == Decimal("300.00")
    # La factura abierta sigue pendiente y la paga no vuelve a contar.
    assert db_session.get(GastoFactura, abierta.id).estado_pago is (
        EstadoPagoFactura.PENDIENTE
    )


def test_recalcular_saldo_de_proveedor_inexistente_responde_404(db_session):
    """El recálculo de un UUID desconocido corta con el error de dominio."""
    from uuid import uuid4

    with pytest.raises(Exception) as error:
        egreso_service.recalcular_saldo_proveedor(db_session, uuid4())
    assert getattr(error.value, "status_code", None) == 404



# --------------------------------------------------------------------------- #
# 5. Cartera de facturas: orden, filtros y vencidas
# --------------------------------------------------------------------------- #
def test_listado_de_facturas_ordena_por_vencimiento(client, insumos):
    """La cartera se lee de lo que vence antes a lo que vence después."""
    for concepto, vencimiento in (
        ("Tercera", "2026-01-20T10:00:00"),
        ("Primera", "2026-01-05T10:00:00"),
        ("Segunda", "2026-01-10T10:00:00"),
    ):
        assert (
            _crear_factura(
                client,
                insumos["id"],
                "100.00",
                concepto=concepto,
                fecha_emision="2026-01-01T10:00:00",
                fecha_vencimiento=vencimiento,
            ).status_code
            == 201
        )

    conceptos = [f["concepto"] for f in client.get(RUTA_FACTURAS).json()]
    assert conceptos == ["Primera", "Segunda", "Tercera"]


def test_filtros_de_facturas_por_estado_paciente_y_vencidas(
    client, mecanico, insumos, paciente
):
    """Los filtros de la cartera acotan por proveedor, paciente, estado y vencidas."""
    vencida = _crear_factura(
        client,
        insumos["id"],
        "800.00",
        concepto="Luz de un mes viejo",
        fecha_emision="2020-01-01T10:00:00",
        fecha_vencimiento="2020-01-31T10:00:00",
    ).json()
    with_paciente = _crear_factura(
        client,
        mecanico["id"],
        "1200.00",
        concepto="Corona 36",
        paciente_id=paciente.id,
        fecha_vencimiento="2099-01-01T10:00:00",
    ).json()
    assert vencida["vencida"] is True
    assert with_paciente["vencida"] is False

    por_paciente = client.get(
        RUTA_FACTURAS, params={"paciente_id": paciente.id}
    ).json()
    assert [f["id"] for f in por_paciente] == [with_paciente["id"]]

    solo_vencidas = client.get(RUTA_FACTURAS, params={"solo_vencidas": "true"}).json()
    assert [f["id"] for f in solo_vencidas] == [vencida["id"]]

    por_estado = client.get(RUTA_FACTURAS, params={"estado_pago": "PAGADO"}).json()
    assert por_estado == []

    # Pagar la factura vencida la saca de «vencidas»: ya no tiene saldo.
    assert (
        client.post(
            RUTA_PAGAR,
            json={"gasto_factura_id": vencida["id"], "monto_pagado": "800.00"},
        ).status_code
        == 201
    )
    assert client.get(RUTA_FACTURAS, params={"solo_vencidas": "true"}).json() == []
    pagadas = client.get(RUTA_FACTURAS, params={"estado_pago": "PAGADO"}).json()
    assert [f["id"] for f in pagadas] == [vencida["id"]]


# --------------------------------------------------------------------------- #
# 6. Tablero de cuentas por pagar
# --------------------------------------------------------------------------- #
def test_resumen_de_cuentas_por_pagar(client, mecanico, insumos, paciente):
    """El tablero informa deuda total, deuda vencida y el desglose por rubro."""
    vencida = _crear_factura(
        client,
        insumos["id"],
        "1000.00",
        fecha_emision="2020-01-01T10:00:00",
        fecha_vencimiento="2020-01-31T10:00:00",
    ).json()
    con_paciente = _crear_factura(
        client,
        mecanico["id"],
        "500.00",
        paciente_id=paciente.id,
        fecha_vencimiento="2099-01-01T10:00:00",
    ).json()
    assert (
        client.post(
            RUTA_PAGAR,
            json={"gasto_factura_id": con_paciente["id"], "monto_pagado": "200.00"},
        ).status_code
        == 201
    )

    resumen = client.get(RUTA_RESUMEN).json()
    assert resumen["cantidad_proveedores"] == 2
    assert resumen["cantidad_facturas_abiertas"] == 2
    assert resumen["total_facturado"] == 1500.0
    assert resumen["total_pagado"] == 200.0
    assert resumen["deuda_total"] == 1300.0
    assert resumen["deuda_vencida"] == 1000.0
    assert resumen["por_tipo"] == [
        {"tipo_proveedor": "INSUMOS", "cantidad_facturas": 1, "deuda": 1000.0},
        {"tipo_proveedor": "MECANICO_DENTAL", "cantidad_facturas": 1, "deuda": 300.0},
    ]

    # La deuda del tablero coincide con la suma de los saldos de los proveedores.
    proveedores = client.get(RUTA_PROVEEDORES).json()
    assert sum(Decimal(str(p["saldo_pendiente"])) for p in proveedores) == (
        Decimal(str(resumen["deuda_total"]))
    )
    assert vencida["id"] == client.get(
        RUTA_FACTURAS, params={"solo_vencidas": "true"}
    ).json()[0]["id"]


def test_resumen_vacio(client):
    """Sin proveedores ni facturas el tablero responde en ceros, no 404."""
    resumen = client.get(RUTA_RESUMEN).json()
    assert resumen["cantidad_proveedores"] == 0
    assert resumen["cantidad_facturas_abiertas"] == 0
    assert resumen["deuda_total"] == 0.0
    assert resumen["deuda_vencida"] == 0.0
    assert resumen["por_tipo"] == []



# --------------------------------------------------------------------------- #
# 7. Portabilidad del esquema, integridad y contrato HTTP
# --------------------------------------------------------------------------- #
def test_ddl_compila_para_postgresql():
    """Las tres tablas compilan en PostgreSQL con NUMERIC y enums como VARCHAR."""
    ddl_proveedores = str(
        CreateTable(Proveedor.__table__).compile(dialect=postgresql.dialect())
    )
    assert "UUID" in ddl_proveedores
    assert "NUMERIC(10, 2)" in ddl_proveedores
    assert "ck_proveedor_saldo_no_negativo" in ddl_proveedores

    ddl_facturas = str(
        CreateTable(GastoFactura.__table__).compile(dialect=postgresql.dialect())
    )
    assert "NUMERIC(10, 2)" in ddl_facturas
    # ``native_enum=False``: el enum viaja como VARCHAR con su CHECK nombrado.
    assert "VARCHAR(10)" in ddl_facturas
    assert "estado_pago_factura" in ddl_facturas
    assert "ck_gasto_factura_pago_no_excede" in ddl_facturas

    ddl_pagos = str(
        CreateTable(PagoEgreso.__table__).compile(dialect=postgresql.dialect())
    )
    assert "NUMERIC(10, 2)" in ddl_pagos
    assert "VARCHAR(13)" in ddl_pagos
    assert "metodo_pago_egreso" in ddl_pagos


def test_la_base_rechaza_valores_inventados(db_session):
    """Los CHECK de la base son la última defensa de las invariantes contables."""
    proveedor = _proveedor_orm(db_session)
    ahora = utcnow()

    # 1) Un estado de pago que no existe en el enum.
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO gastos_facturas (id, proveedor_id, concepto, "
                "monto_total, monto_pagado, fecha_emision, fecha_vencimiento, "
                "estado_pago) VALUES (:id, :proveedor, 'Inventada', 100, 0, "
                ":emision, :vencimiento, 'INVENTADO')"
            ),
            {
                "id": uuid4().hex,
                "proveedor": proveedor.id.hex,
                "emision": ahora,
                "vencimiento": ahora,
            },
        )
    db_session.rollback()

    factura = _factura_orm(db_session, proveedor, "100.00")

    # 2) Pagar más que el total de la factura.
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "UPDATE gastos_facturas SET monto_pagado = monto_total + 1 "
                "WHERE id = :id"
            ),
            {"id": factura.id.hex},
        )
    db_session.rollback()

    # 3) Un saldo negativo con el proveedor.
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE proveedores SET saldo_pendiente = -1 WHERE id = :id"),
            {"id": proveedor.id.hex},
        )
    db_session.rollback()


def test_contrato_http_declarado():
    """Las rutas del Módulo 6 (y sólo esas operaciones) están en el OpenAPI."""
    rutas = app.openapi()["paths"]

    assert set(rutas["/api/v1/egresos/proveedores"]) == {"get", "post"}
    assert set(rutas["/api/v1/egresos/proveedores/{proveedor_id}"]) == {"get"}
    assert set(rutas["/api/v1/egresos/facturas"]) == {"get", "post"}
    assert set(rutas["/api/v1/egresos/pagar"]) == {"post"}
    assert set(rutas["/api/v1/egresos/cuentas-por-pagar"]) == {"get"}


def test_enums_del_modulo_no_se_mezclan_con_los_de_cobros():
    """``MetodoPagoEgreso`` agrega TARJETA sin tocar el ``MedioPago`` del paciente."""
    assert [m.value for m in MetodoPagoEgreso] == [
        "EFECTIVO",
        "TRANSFERENCIA",
        "TARJETA",
    ]
    assert [t.value for t in TipoProveedor] == [
        "MECANICO_DENTAL",
        "INSUMOS",
        "SERVICIOS_IMPUESTOS",
    ]
    assert [e.value for e in EstadoPagoFactura] == ["PENDIENTE", "PARCIAL", "PAGADO"]



def test_el_modulo_no_necesita_migracion_de_columnas(engine):
    """``create_all`` deja las tres tablas completas: no hay columnas pendientes."""
    insp = inspect(engine)
    tablas = set(insp.get_table_names())
    assert {"proveedores", "gastos_facturas", "pagos_egresos"} <= tablas

    # El módulo es puramente aditivo: ``aplicar_columnas_faltantes`` no tiene
    # nada que agregar y devuelve una lista vacía (idempotente).
    assert aplicar_columnas_faltantes(engine) == []

    columnas = {c["name"] for c in insp.get_columns("gastos_facturas")}
    assert {
        "proveedor_id",
        "paciente_id",
        "concepto",
        "monto_total",
        "monto_pagado",
        "fecha_emision",
        "fecha_vencimiento",
        "estado_pago",
    } <= columnas
    assert "metodo_pago" in {c["name"] for c in insp.get_columns("pagos_egresos")}

