"""Pruebas del Módulo 5: cuenta corriente, planes de cuotas y pagos.

Se cubre el circuito financiero completo de punta a punta (API + base de datos):

1. **Consulta** (``GET /finanzas/cuenta-corriente/paciente/{id}``): un paciente
   sin movimientos devuelve ``existe_cuenta = false`` sin crear la cuenta en la
   base; un paciente inexistente, ``404``. Con movimientos, el historial llega de
   lo más nuevo a lo más viejo con los totales de débitos y créditos.
2. **Apertura del plan** (``POST /finanzas/plan-cuotas``): se debita el total,
   se cobra la entrega inicial (50 % por defecto) y el saldo deudor queda en el
   valor financiado. Un porcentaje que cubre el total se rechaza con ``422``.
3. **Redondeo**: ``100 / 2 cuotas`` con entrega del 50 % da ``16.67`` por cuota y
   ``-0.01`` de ajuste en la última; nunca aparece un centavo perdido.
4. **Pagos** (``POST /finanzas/pagar``): cada pago es un crédito nuevo, el saldo
   se recalcula desde el libro mayor, el plan pasa a ``FINALIZADO`` al saldar la
   cuenta y un pago de más queda como saldo a favor.
5. **Atomicidad**: si algo falla a mitad de la apertura, el ``rollback`` no deja
   cuenta, movimientos ni plan a medio escribir.
6. **Estado del plan** (``PATCH .../estado``): ``RECOBRABLE`` ↔ ``ACTIVO``;
   ``FINALIZADO`` no se puede fijar a mano y una cuenta saldada no se deriva a
   cobranza.
7. **Portabilidad**: el DDL de las tres tablas nuevas compila con el dialecto de
   PostgreSQL (los enums viajan como ``VARCHAR``) y los importes quedan como
   ``NUMERIC(10, 2)`` con su ``Decimal`` intacto.
8. **Integridad en la base**: los dos enums llevan su ``CHECK`` nombrado
   (``tipo_movimiento_cc``, ``estado_plan_cuotas``), así que un ``INSERT`` por SQL
   con un valor inventado levanta ``IntegrityError``.
9. **Informe consolidado** (``GET /finanzas/cuentas-corrientes``): una fila por
   paciente con cuenta —plan, lo que pagó y lo que debe, en orden alfabético— y
   al pie la **deuda total**, que suma sólo los saldos deudores (el saldo a favor
   de un paciente no compensa la deuda de otro).
10. **Depuración** (``GET /finanzas/transacciones`` y ``DELETE
    /finanzas/cuenta-corriente/transacciones/{id}``): el listado global con el
    paciente de cada movimiento y el borrado físico de un movimiento, que
    recalcula el saldo desde el libro mayor, re-sincroniza los planes y elimina la
    cuenta si queda sin movimientos ni planes.
11. **Medio de pago y caja del día** (``GET
    /finanzas/cobros-cuenta-corriente``): cada crédito guarda si se cobró en
    efectivo o por transferencia (los débitos no llevan medio), y los cobros del
    período se listan con sus totales por medio. Los pagos cargados antes de
    existir el campo cuentan como efectivo, para que la caja histórica cierre.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models.cuenta_corriente import CuentaCorriente, PlanCuotas, TransaccionCC
from app.models.enums import EstadoPlanCuotas, MedioPago, TipoMovimientoCC
from app.schemas.finanzas import AbrirPlanCuotasRequest
from app.services import finanzas_service

API = "/api/v1"
CERO = Decimal("0.00")


def _crear_paciente(client, **overrides) -> dict:
    """Crea un paciente por API y devuelve su representación."""
    payload = {"nombre": "Paciente Finanzas", "telefono": "+5491100000001"}
    payload.update(overrides)
    r = client.post(f"{API}/pacientes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _abrir_plan(client, paciente_id: int, **overrides) -> dict:
    """Abre un plan de cuotas por API (500.000 en 3 cuotas, 50 % de entrega)."""
    payload = {
        "paciente_id": paciente_id,
        "monto_total": 500000,
        "cantidad_cuotas": 3,
        "descripcion": "Ortodoncia",
    }
    payload.update(overrides)
    r = client.post(f"{API}/finanzas/plan-cuotas", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _pagar(client, paciente_id: int, monto, **overrides) -> dict:
    """Registra un pago por API."""
    payload = {"paciente_id": paciente_id, "monto": monto}
    payload.update(overrides)
    r = client.post(f"{API}/finanzas/pagar", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _cuenta(client, paciente_id: int) -> dict:
    """Consulta la cuenta corriente por API."""
    r = client.get(f"{API}/finanzas/cuenta-corriente/paciente/{paciente_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _saldo_del_libro_mayor(db) -> Decimal:
    """``suma(DEBITO) - suma(CREDITO)`` calculado con SQL, en ``Decimal``."""
    filas = db.execute(
        select(TransaccionCC.tipo_movimiento, func.sum(TransaccionCC.monto)).group_by(
            TransaccionCC.tipo_movimiento
        )
    ).all()
    totales = {
        TipoMovimientoCC(tipo): Decimal(str(monto)) for tipo, monto in filas
    }
    return totales.get(TipoMovimientoCC.DEBITO, CERO) - totales.get(
        TipoMovimientoCC.CREDITO, CERO
    )


def _contar(db, modelo) -> int:
    """Cantidad de filas de una tabla del módulo."""
    return int(db.execute(select(func.count()).select_from(modelo)).scalar() or 0)


# ---------------------------------------------------------------------------
# Consulta de la cuenta
# ---------------------------------------------------------------------------


def test_el_paciente_sin_movimientos_no_genera_cuenta(client, db_session, paciente):
    """Un ``GET`` no escribe: informa «sin cuenta» y la base sigue vacía."""
    datos = _cuenta(client, paciente.id)

    assert datos["existe_cuenta"] is False
    assert datos["paciente_id"] == paciente.id
    assert datos["id"] is None
    assert datos["saldo_actual"] == 0.0
    assert datos["transacciones"] == []
    assert datos["planes"] == []
    assert _contar(db_session, CuentaCorriente) == 0


def test_la_cuenta_de_un_paciente_inexistente_es_404(client):
    """Se distingue «no tuvo movimientos» de «no existe el paciente»."""
    r = client.get(f"{API}/finanzas/cuenta-corriente/paciente/999999")

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Apertura del plan de cuotas
# ---------------------------------------------------------------------------


def test_el_plan_calcula_la_entrega_las_cuotas_y_el_ajuste(client, paciente):
    """50 % de entrega sobre 500.000: 250.000 a financiar en 3 cuotas."""
    plan = _abrir_plan(client, paciente.id)

    assert plan["estado"] == EstadoPlanCuotas.ACTIVO.value
    assert plan["monto_total_tratamiento"] == 500000.0
    assert plan["monto_entrega_inicial"] == 250000.0
    assert plan["monto_financiado"] == 250000.0
    assert plan["cantidad_cuotas"] == 3
    assert plan["monto_por_cuota"] == 83333.33
    # 250.000 - 3 * 83.333,33 = 0,01 que absorbe la última cuota.
    assert plan["ajuste_ultima_cuota"] == 0.01
    # Se debitó el total y se acreditó la entrega cobrada en el acto.
    assert plan["saldo_actual"] == 250000.0
    assert plan["entrega_inicial_pagada"] is True


def test_la_entrega_impaga_deja_la_deuda_completa(client, db_session, paciente):
    """Si la entrega no se cobra, el paciente debe el tratamiento entero."""
    plan = _abrir_plan(client, paciente.id, pago_entrega_inicial=False)

    assert plan["entrega_inicial_pagada"] is False
    assert plan["saldo_actual"] == 500000.0
    assert _contar(db_session, TransaccionCC) == 1  # sólo el débito del total

    cuenta = _cuenta(client, paciente.id)
    assert cuenta["saldo_actual"] == 500000.0
    assert cuenta["total_debitos"] == 500000.0
    assert cuenta["total_creditos"] == 0.0


def test_las_cuotas_se_redondean_al_centavo(client, paciente):
    """50,00 a financiar en 3 cuotas: 16,67 c/u y -0,01 en la última."""
    plan = _abrir_plan(client, paciente.id, monto_total=100, cantidad_cuotas=3)

    assert plan["monto_entrega_inicial"] == 50.0
    assert plan["monto_financiado"] == 50.0
    assert plan["monto_por_cuota"] == 16.67
    assert plan["ajuste_ultima_cuota"] == -0.01
    # El plan cierra al centavo: cuotas + ajuste == monto financiado.
    cuotas = Decimal(str(plan["monto_por_cuota"])) * plan["cantidad_cuotas"]
    assert cuotas + Decimal(str(plan["ajuste_ultima_cuota"])) == Decimal("50.00")


def test_un_porcentaje_que_cubre_el_total_es_rechazado(client, paciente):
    """Financiar el 0 % no tiene sentido: no quedaría importe en cuotas."""
    r = client.post(
        f"{API}/finanzas/plan-cuotas",
        json={
            "paciente_id": paciente.id,
            "monto_total": 200000,
            "cantidad_cuotas": 4,
            "porcentaje_entrega_inicial": 100,
        },
    )

    assert r.status_code == 422, r.text


def test_el_historial_y_los_planes_llegan_en_la_cuenta(client, paciente):
    """El alta deja un débito por el total y un crédito por la entrega."""
    _abrir_plan(client, paciente.id)

    cuenta = _cuenta(client, paciente.id)

    assert cuenta["existe_cuenta"] is True
    assert len(cuenta["transacciones"]) == 2
    assert len(cuenta["planes"]) == 1
    tipos = [mov["tipo_movimiento"] for mov in cuenta["transacciones"]]
    assert sorted(tipos) == sorted(
        [TipoMovimientoCC.CREDITO.value, TipoMovimientoCC.DEBITO.value]
    )
    # Todas las filas quedan ligadas a la misma cuenta.
    assert {mov["cuenta_corriente_id"] for mov in cuenta["transacciones"]} == {
        cuenta["id"]
    }
    assert cuenta["planes"][0]["cuenta_corriente_id"] == cuenta["id"]


# ---------------------------------------------------------------------------
# Pagos
# ---------------------------------------------------------------------------


def test_un_pago_baja_el_saldo_y_coincide_con_el_libro_mayor(
    client, db_session, paciente
):
    """Cada pago es un crédito nuevo y el saldo guardado sigue al mayor."""
    plan = _abrir_plan(client, paciente.id)  # queda debiendo 250.000

    pago = _pagar(client, paciente.id, 83333.33, plan_cuotas_id=plan["id"])

    assert pago["transaccion"]["tipo_movimiento"] == TipoMovimientoCC.CREDITO.value
    assert pago["transaccion"]["monto"] == 83333.33
    assert pago["saldo_actual"] == 166666.67
    assert pago["saldo_a_favor"] == 0.0
    assert pago["plan_cuotas"]["estado"] == EstadoPlanCuotas.ACTIVO.value

    cuenta = db_session.execute(select(CuentaCorriente)).scalar_one()
    assert cuenta.saldo_actual == Decimal("166666.67")
    assert cuenta.saldo_actual == _saldo_del_libro_mayor(db_session)


def test_el_pago_final_finaliza_el_plan(client, db_session, paciente):
    """Con la cuenta saldada el plan se cierra solo (nunca a mano)."""
    plan = _abrir_plan(client, paciente.id, monto_total=100000, cantidad_cuotas=2)

    pago = _pagar(client, paciente.id, 50000, plan_cuotas_id=plan["id"])

    assert pago["saldo_actual"] == 0.0
    assert pago["plan_cuotas"]["estado"] == EstadoPlanCuotas.FINALIZADO.value
    assert _cuenta(client, paciente.id)["planes"][0]["estado"] == (
        EstadoPlanCuotas.FINALIZADO.value
    )

    plan_db = db_session.get(PlanCuotas, UUID(plan["id"]))
    assert plan_db.estado is EstadoPlanCuotas.FINALIZADO


def test_pagar_de_mas_deja_saldo_a_favor(client, paciente):
    """El excedente no se pierde: queda a favor del paciente."""
    _abrir_plan(client, paciente.id, monto_total=100000, cantidad_cuotas=2)

    pago = _pagar(client, paciente.id, 70000)

    assert pago["saldo_actual"] == -20000.0
    assert pago["saldo_a_favor"] == 20000.0
    cuenta = _cuenta(client, paciente.id)
    assert cuenta["saldo_actual"] == -20000.0


def test_un_pago_no_reescribe_los_movimientos_anteriores(
    client, db_session, paciente
):
    """El libro mayor es append-only: el pago agrega, no corrige."""
    _abrir_plan(client, paciente.id)
    antes = {
        mov.id: mov.monto
        for mov in db_session.execute(select(TransaccionCC)).scalars().all()
    }

    _pagar(client, paciente.id, 5000)

    despues = {
        mov.id: mov.monto
        for mov in db_session.execute(select(TransaccionCC)).scalars().all()
    }
    assert len(despues) == len(antes) + 1
    for movimiento_id, monto in antes.items():
        assert despues[movimiento_id] == monto


def test_el_pago_no_puede_imputarse_al_plan_de_otro_paciente(
    client, db_session, paciente
):
    """El plan ajeno se rechaza y la cuenta del pagador no queda escrita."""
    otro = _crear_paciente(client, nombre="Otro Paciente", telefono="+5491100000002")
    plan_ajeno = _abrir_plan(client, otro["id"])
    movimientos_antes = _contar(db_session, TransaccionCC)

    r = client.post(
        f"{API}/finanzas/pagar",
        json={
            "paciente_id": paciente.id,
            "monto": 1000,
            "plan_cuotas_id": plan_ajeno["id"],
        },
    )

    assert r.status_code == 422, r.text
    assert _contar(db_session, TransaccionCC) == movimientos_antes
    # La cuenta que el intento abrió para el pagador se revirtió con el rechazo.
    assert _contar(db_session, CuentaCorriente) == 1


def test_un_pago_anterior_queda_con_su_fecha(client, paciente):
    """Se puede imputar un cobro de días atrás y el historial lo ordena."""
    _abrir_plan(client, paciente.id, fecha="2026-03-01T12:00:00Z")

    pago = _pagar(client, paciente.id, 1000, fecha="2026-01-15T09:30:00Z")

    assert pago["transaccion"]["fecha"].startswith("2026-01-15T09:30:00")
    fechas = [mov["fecha"] for mov in _cuenta(client, paciente.id)["transacciones"]]
    assert fechas == sorted(fechas, reverse=True)  # más nuevo primero
    assert fechas[-1].startswith("2026-01-15T09:30:00")


def test_una_sola_cuenta_por_paciente(client, db_session, paciente):
    """Los pagos sucesivos reutilizan la cuenta que abrió el primero."""
    _pagar(client, paciente.id, 1000)
    _pagar(client, paciente.id, 2000)

    assert _contar(db_session, CuentaCorriente) == 1
    cuenta = _cuenta(client, paciente.id)
    assert cuenta["total_creditos"] == 3000.0
    assert cuenta["saldo_actual"] == -3000.0


# ---------------------------------------------------------------------------
# Atomicidad
# ---------------------------------------------------------------------------


def test_un_error_a_mitad_del_alta_no_deja_rastros(db_session, paciente, monkeypatch):
    """Si falla la sincronización, el ``rollback`` no deja nada a medio escribir."""

    def _falla(*args, **kwargs):
        raise RuntimeError("falla simulada")

    monkeypatch.setattr(finanzas_service, "_sincronizar_planes", _falla)
    datos = AbrirPlanCuotasRequest(
        paciente_id=paciente.id,
        monto_total=Decimal("120000"),
        cantidad_cuotas=6,
    )

    with pytest.raises(RuntimeError):
        finanzas_service.abrir_plan_cuotas(db_session, datos)

    # Ni cuenta, ni movimientos, ni plan: la transacción se revirtió completa.
    assert _contar(db_session, CuentaCorriente) == 0
    assert _contar(db_session, TransaccionCC) == 0
    assert _contar(db_session, PlanCuotas) == 0


# ---------------------------------------------------------------------------
# Estado del plan
# ---------------------------------------------------------------------------


def test_marcar_recobrable_y_volver_a_activo(client, paciente):
    """La secretaría deriva el plan a cobranza y puede volver atrás."""
    plan = _abrir_plan(client, paciente.id)

    r = client.patch(
        f"{API}/finanzas/plan-cuotas/{plan['id']}/estado",
        json={"estado": "RECOBRABLE"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == EstadoPlanCuotas.RECOBRABLE.value

    r = client.patch(
        f"{API}/finanzas/plan-cuotas/{plan['id']}/estado", json={"estado": "ACTIVO"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == EstadoPlanCuotas.ACTIVO.value


def test_no_se_puede_finalizar_un_plan_a_mano(client, paciente):
    """``FINALIZADO`` lo calcula el sistema cuando la cuenta se salda."""
    plan = _abrir_plan(client, paciente.id)

    r = client.patch(
        f"{API}/finanzas/plan-cuotas/{plan['id']}/estado",
        json={"estado": "FINALIZADO"},
    )

    assert r.status_code == 422, r.text


def test_una_cuenta_saldada_no_se_deriva_a_cobranza(client, paciente):
    """Sin deuda no hay nada que cobrar: el plan ya quedó finalizado."""
    plan = _abrir_plan(client, paciente.id, monto_total=100000, cantidad_cuotas=2)
    _pagar(client, paciente.id, 50000, plan_cuotas_id=plan["id"])

    r = client.patch(
        f"{API}/finanzas/plan-cuotas/{plan['id']}/estado",
        json={"estado": "RECOBRABLE"},
    )

    assert r.status_code == 409, r.text


def test_cambiar_el_estado_de_un_plan_inexistente_es_404(client):
    r = client.patch(
        f"{API}/finanzas/plan-cuotas/{UUID(int=0)}/estado", json={"estado": "ACTIVO"}
    )

    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Portabilidad y tipo de dato
# ---------------------------------------------------------------------------

#: Las tres tablas que agrega el módulo.
TABLAS_NUEVAS = (CuentaCorriente, TransaccionCC, PlanCuotas)


def test_el_esquema_del_modulo_compila_en_postgresql():
    """Si el modelo usara algo exclusivo de SQLite, la migración fallaría."""
    dialecto = postgresql.dialect()

    for modelo in TABLAS_NUEVAS:
        ddl = str(CreateTable(modelo.__table__).compile(dialect=dialecto))
        assert f"CREATE TABLE {modelo.__tablename__}" in ddl
        # Los enums viajan como VARCHAR (``native_enum=False``): no hace falta
        # un ``CREATE TYPE`` previo al pasar de SQLite a PostgreSQL.
        assert "CREATE TYPE" not in ddl
        for indice in modelo.__table__.indexes:
            assert "CREATE" in str(CreateIndex(indice).compile(dialect=dialecto))


def test_los_importes_son_numeric_de_dos_decimales():
    """Nada de ``Float``: los importes del módulo no admiten error de redondeo."""
    for modelo, columna in (
        (CuentaCorriente, "saldo_actual"),
        (TransaccionCC, "monto"),
        (PlanCuotas, "monto_total_tratamiento"),
        (PlanCuotas, "monto_entrega_inicial"),
        (PlanCuotas, "monto_por_cuota"),
    ):
        tipo = modelo.__table__.c[columna].type
        assert (tipo.precision, tipo.scale) == (10, 2), f"{modelo.__name__}.{columna}"


def test_la_cuenta_es_unica_por_paciente_en_la_base():
    """El índice único es la última defensa ante dos altas simultáneas."""
    indice = next(
        indice
        for indice in CuentaCorriente.__table__.indexes
        if indice.name == "uq_cuenta_corriente_paciente"
    )

    assert indice.unique is True
    assert [columna.name for columna in indice.columns] == ["paciente_id"]


def test_las_claves_foraneas_protegen_la_contabilidad():
    """El paciente no arrastra su cuenta; los movimientos sí acompañan a la cuenta."""
    fk_paciente = next(iter(CuentaCorriente.__table__.c.paciente_id.foreign_keys))
    assert fk_paciente.ondelete == "RESTRICT"

    for modelo in (TransaccionCC, PlanCuotas):
        fk_cuenta = next(iter(modelo.__table__.c.cuenta_corriente_id.foreign_keys))
        assert fk_cuenta.ondelete == "CASCADE"


def test_los_importes_se_guardan_como_decimal(client, db_session, paciente):
    """El ``Decimal`` viaja a la base y vuelve sin pasar por ``float``."""
    _abrir_plan(
        client, paciente.id, monto_total="1234.56", cantidad_cuotas=4
    )

    cuenta = db_session.execute(select(CuentaCorriente)).scalar_one()
    assert isinstance(cuenta.saldo_actual, Decimal)
    assert cuenta.saldo_actual == Decimal("617.28")

    debito = db_session.execute(
        select(TransaccionCC).where(
            TransaccionCC.tipo_movimiento == TipoMovimientoCC.DEBITO
        )
    ).scalar_one()
    assert isinstance(debito.monto, Decimal)
    assert debito.monto == Decimal("1234.56")

    plan_db = db_session.execute(select(PlanCuotas)).scalar_one()
    assert plan_db.monto_entrega_inicial == Decimal("617.28")
    assert plan_db.monto_por_cuota == Decimal("154.32")
    assert plan_db.monto_financiado == Decimal("617.28")
    assert plan_db.ajuste_ultima_cuota == Decimal("0.00")


def test_los_enums_tienen_check_en_la_base():
    """El ``VARCHAR`` del enum no queda suelto: lleva su ``CHECK`` nombrado."""
    esperados = {
        (TransaccionCC, "tipo_movimiento_cc", "tipo_movimiento"),
        (PlanCuotas, "estado_plan_cuotas", "estado"),
    }

    for modelo, nombre, columna in esperados:
        checks = {
            restriccion.name: restriccion
            for restriccion in modelo.__table__.constraints
            if restriccion.__class__.__name__ == "CheckConstraint"
        }
        assert nombre in checks, f"{modelo.__tablename__} sin CHECK {nombre}"
        assert columna in str(checks[nombre].sqltext), f"{nombre} no mira {columna}"


def test_un_valor_de_enum_invalido_lo_rechaza_la_base(db_session, paciente):
    """Una escritura SQL directa tampoco puede inventar un tipo de movimiento."""
    datos = AbrirPlanCuotasRequest(
        paciente_id=paciente.id,
        monto_total=Decimal("1000"),
        cantidad_cuotas=2,
        pago_entrega_inicial=False,
    )
    finanzas_service.abrir_plan_cuotas(db_session, datos)
    cuenta = db_session.execute(select(CuentaCorriente)).scalar_one()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO transacciones_cc "
                "(id, cuenta_corriente_id, tipo_movimiento, monto, descripcion) "
                "VALUES (:id, :cuenta, 'REGALO', 10, 'tipo inventado')"
            ),
            {"id": UUID(int=1).hex, "cuenta": cuenta.id.hex},
        )
    db_session.rollback()

    # El débito del plan sigue siendo el único movimiento del libro.
    assert _contar(db_session, TransaccionCC) == 1


# ---------------------------------------------------------------------------
# Informe consolidado de cuentas corrientes
# ---------------------------------------------------------------------------


def _informe(client) -> dict:
    """Consulta el informe consolidado por API."""
    r = client.get(f"{API}/finanzas/cuentas-corrientes")
    assert r.status_code == 200, r.text
    return r.json()


def test_el_informe_muestra_plan_pagos_y_deuda_de_cada_paciente(client):
    """Una fila por paciente, en orden alfabético, con lo que pagó y lo que debe."""
    alberto = _crear_paciente(
        client, nombre="Alberto Informe", telefono="+5491100000010"
    )
    zulema = _crear_paciente(
        client, nombre="Zulema Informe", telefono="+5491100000011"
    )

    # Alberto: plan de 500.000 con la entrega del 50 % cobrada → debe 250.000.
    _abrir_plan(client, alberto["id"])
    # Zulema: 200.000 financiados sin entrega y un pago de 250.000 → 50.000 a favor.
    _abrir_plan(
        client,
        zulema["id"],
        monto_total=200000,
        cantidad_cuotas=2,
        pago_entrega_inicial=False,
    )
    _pagar(client, zulema["id"], 250000)

    informe = _informe(client)

    assert [c["paciente_nombre"] for c in informe["cuentas"]] == [
        "Alberto Informe",
        "Zulema Informe",
    ]
    assert informe["cantidad_pacientes"] == 2
    assert informe["cantidad_con_deuda"] == 1
    assert informe["cantidad_planes"] == 2
    assert informe["total_debitado"] == 700000.0
    assert informe["total_cobrado"] == 500000.0
    # La deuda total suma sólo los saldos deudores: lo que Zulema pagó de más no
    # descuenta la deuda de Alberto.
    assert informe["deuda_total"] == 250000.0
    assert informe["saldo_a_favor_total"] == 50000.0
    assert informe["generado"]

    primero, segundo = informe["cuentas"]
    assert primero["total_debitos"] == 500000.0
    assert primero["total_creditos"] == 250000.0
    assert primero["saldo_actual"] == 250000.0
    assert [p["monto_por_cuota"] for p in primero["planes"]] == [83333.33]
    assert primero["planes"][0]["estado"] == EstadoPlanCuotas.ACTIVO.value
    assert segundo["total_creditos"] == 250000.0
    assert segundo["saldo_actual"] == -50000.0


def test_el_informe_sin_cuentas_abiertas_vuelve_en_cero(client, paciente):
    """Sin movimientos no hay filas ni totales: el ``GET`` no abre cuentas."""
    informe = _informe(client)

    assert informe["cuentas"] == []
    assert informe["cantidad_pacientes"] == 0
    assert informe["cantidad_con_deuda"] == 0
    assert informe["cantidad_planes"] == 0
    assert informe["total_debitado"] == 0.0
    assert informe["total_cobrado"] == 0.0
    assert informe["deuda_total"] == 0.0
    assert informe["saldo_a_favor_total"] == 0.0


def test_el_informe_incluye_los_pagos_a_cuenta_sin_plan(client):
    """Un paciente sin plan pero con un pago a cuenta figura con saldo a favor."""
    solo_pago = _crear_paciente(
        client, nombre="Solo Pago", telefono="+5491100000012"
    )
    _pagar(client, solo_pago["id"], 10000)

    informe = _informe(client)

    assert informe["cantidad_pacientes"] == 1
    assert informe["cantidad_con_deuda"] == 0
    assert informe["saldo_a_favor_total"] == 10000.0
    fila = informe["cuentas"][0]
    assert fila["paciente_nombre"] == "Solo Pago"
    assert fila["planes"] == []
    assert fila["total_debitos"] == 0.0
    assert fila["total_creditos"] == 10000.0
    assert fila["saldo_actual"] == -10000.0


def test_la_deuda_del_informe_coincide_con_el_libro_mayor(client, db_session):
    """La deuda total es la del libro mayor, al centavo y sin contar dos veces."""
    unico = _crear_paciente(
        client, nombre="Unico Deudor", telefono="+5491100000013"
    )
    _abrir_plan(client, unico["id"], monto_total=100, cantidad_cuotas=3)

    informe = _informe(client)

    assert Decimal(str(informe["deuda_total"])) == _saldo_del_libro_mayor(db_session)
    assert informe["cantidad_planes"] == _contar(db_session, PlanCuotas) == 1


# ---------------------------------------------------------------------------
# Depuración: listado global y borrado de movimientos
# ---------------------------------------------------------------------------


def _transacciones(client, **params) -> list:
    """Listado global de movimientos del libro mayor (``GET /finanzas/transacciones``)."""
    r = client.get(f"{API}/finanzas/transacciones", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _borrar_transaccion(client, transaccion_id):
    """``DELETE`` de un movimiento del libro mayor (respuesta cruda)."""
    return client.delete(
        f"{API}/finanzas/cuenta-corriente/transacciones/{transaccion_id}"
    )


def test_el_listado_global_trae_el_paciente_y_filtra(client):
    """El módulo de borrado ve de quién es cada movimiento y filtra por fecha/tipo."""
    ana = _crear_paciente(client, nombre="Ana Corriente", telefono="+5491100000021")
    beto = _crear_paciente(client, nombre="Beto Corriente", telefono="+5491100000022")
    _abrir_plan(
        client, ana["id"], monto_total=500000, cantidad_cuotas=3,
        fecha="2026-11-02T10:00:00",
    )
    _pagar(client, beto["id"], 10000, fecha="2026-11-03T09:00:00")

    # Del más nuevo al más viejo, con el paciente en cada fila.
    todos = _transacciones(client)
    assert len(todos) == 3
    assert todos[0]["paciente_nombre"] == "Beto Corriente"
    assert todos[0]["paciente_id"] == beto["id"]
    assert todos[0]["tipo_movimiento"] == TipoMovimientoCC.CREDITO.value
    assert todos[0]["monto"] == 10000.0
    de_ana = [fila for fila in todos if fila["paciente_id"] == ana["id"]]
    assert {(fila["tipo_movimiento"], fila["monto"]) for fila in de_ana} == {
        (TipoMovimientoCC.DEBITO.value, 500000.0),
        (TipoMovimientoCC.CREDITO.value, 250000.0),
    }

    por_paciente = _transacciones(client, paciente_id=ana["id"])
    assert {fila["paciente_nombre"] for fila in por_paciente} == {"Ana Corriente"}
    assert len(por_paciente) == 2

    solo_creditos = _transacciones(client, tipo_movimiento="CREDITO")
    assert {fila["monto"] for fila in solo_creditos} == {10000.0, 250000.0}
    assert len(solo_creditos) == 2

    del_dia = _transacciones(client, desde="2026-11-02", hasta="2026-11-02")
    assert {fila["paciente_nombre"] for fila in del_dia} == {"Ana Corriente"}
    assert len(del_dia) == 2

    # El listado es de sólo lectura: no abre cuentas nuevas.
    assert _cuenta(client, beto["id"])["existe_cuenta"] is True


def test_el_listado_con_un_paciente_inexistente_es_404(client):
    """Un filtro por un paciente que no existe es un error, no una lista vacía."""
    r = client.get(f"{API}/finanzas/transacciones", params={"paciente_id": 999999})

    assert r.status_code == 404


def test_borrar_un_pago_devuelve_la_deuda_y_reactiva_el_plan(client, db_session):
    """Al borrar el crédito el saldo se reconstruye y el plan vuelve a estar vivo."""
    paciente = _crear_paciente(
        client, nombre="Vuelve a Deber", telefono="+5491100000023"
    )
    _abrir_plan(client, paciente["id"], monto_total=100, cantidad_cuotas=2)
    pago = _pagar(client, paciente["id"], 50)

    # Con el pago la cuenta queda saldada y el plan se cierra solo.
    saldada = _cuenta(client, paciente["id"])
    assert saldada["saldo_actual"] == 0.0
    assert saldada["planes"][0]["estado"] == EstadoPlanCuotas.FINALIZADO.value

    r = _borrar_transaccion(client, pago["transaccion"]["id"])

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["tipo_movimiento"] == TipoMovimientoCC.CREDITO.value
    assert datos["monto"] == 50.0
    assert datos["paciente_nombre"] == "Vuelve a Deber"
    assert datos["saldo_actual"] == 50.0
    assert datos["saldo_a_favor"] == 0.0
    assert datos["cuenta_eliminada"] is False

    cuenta = _cuenta(client, paciente["id"])
    assert cuenta["saldo_actual"] == 50.0
    # El saldo guardado coincide con el libro mayor, al centavo.
    assert Decimal(str(cuenta["saldo_actual"])) == _saldo_del_libro_mayor(db_session)
    # El plan vuelve a ACTIVO: sigue habiendo deuda.
    assert cuenta["planes"][0]["estado"] == EstadoPlanCuotas.ACTIVO.value


def test_borrar_el_ultimo_movimiento_deja_al_paciente_sin_cuenta(client, db_session):
    """Sin movimientos ni planes la cuenta se borra: vuelve a «no tiene cuenta»."""
    paciente = _crear_paciente(
        client, nombre="Solo Un Pago", telefono="+5491100000024"
    )
    pago = _pagar(client, paciente["id"], 1000)

    r = _borrar_transaccion(client, pago["transaccion"]["id"])

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["cuenta_eliminada"] is True
    assert datos["saldo_actual"] == 0.0
    assert _contar(db_session, CuentaCorriente) == 0
    assert _contar(db_session, TransaccionCC) == 0
    assert _cuenta(client, paciente["id"])["existe_cuenta"] is False


def test_borrar_el_debito_de_apertura_finaliza_el_plan_sin_deuda(client, db_session):
    """El plan sobrevive al borrado del débito, pero ya no debe nada."""
    paciente = _crear_paciente(
        client, nombre="Plan sin Deuda", telefono="+5491100000025"
    )
    _abrir_plan(
        client,
        paciente["id"],
        monto_total=100,
        cantidad_cuotas=2,
        pago_entrega_inicial=False,
    )
    debito = [
        mov
        for mov in _cuenta(client, paciente["id"])["transacciones"]
        if mov["tipo_movimiento"] == TipoMovimientoCC.DEBITO.value
    ][0]

    r = _borrar_transaccion(client, debito["id"])

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["saldo_actual"] == 0.0
    # Hay un plan abierto: la cuenta se conserva aunque no tenga movimientos.
    assert datos["cuenta_eliminada"] is False
    cuenta = _cuenta(client, paciente["id"])
    assert cuenta["existe_cuenta"] is True
    assert cuenta["transacciones"] == []
    assert cuenta["planes"][0]["estado"] == EstadoPlanCuotas.FINALIZADO.value
    assert _contar(db_session, PlanCuotas) == 1


def test_borrar_un_movimiento_inexistente_es_404_y_no_se_borra_dos_veces(client):
    """Borrar el mismo movimiento dos veces: 200 la primera, 404 la segunda."""
    paciente = _crear_paciente(
        client, nombre="Doble Borrado", telefono="+5491100000026"
    )
    pago = _pagar(client, paciente["id"], 500)
    otro = _pagar(client, paciente["id"], 300)

    assert _borrar_transaccion(client, pago["transaccion"]["id"]).status_code == 200
    assert _borrar_transaccion(client, pago["transaccion"]["id"]).status_code == 404
    assert (
        _borrar_transaccion(client, "00000000-0000-0000-0000-000000000000").status_code
        == 404
    )

    # El otro movimiento sigue intacto y la cuenta conserva su saldo a favor.
    cuenta = _cuenta(client, paciente["id"])
    assert [mov["id"] for mov in cuenta["transacciones"]] == [
        otro["transaccion"]["id"]
    ]
    assert cuenta["saldo_actual"] == -300.0


# ---------------------------------------------------------------------------
# Medio de pago y caja del día (cobros del período)
# ---------------------------------------------------------------------------


def test_el_pago_guarda_su_medio_y_por_defecto_es_efectivo(client, paciente):
    """El medio de pago viaja con el crédito; sin informarlo se asume efectivo."""
    transferencia = _pagar(client, paciente.id, 10000, medio_pago="TRANSFERENCIA")
    efectivo = _pagar(client, paciente.id, 5000)

    assert transferencia["transaccion"]["medio_pago"] == MedioPago.TRANSFERENCIA.value
    assert efectivo["transaccion"]["medio_pago"] == MedioPago.EFECTIVO.value
    cuenta = _cuenta(client, paciente.id)
    medios = {
        mov["monto"]: mov["medio_pago"] for mov in cuenta["transacciones"]
    }
    assert medios == {10000.0: "TRANSFERENCIA", 5000.0: "EFECTIVO"}


def test_la_entrega_inicial_guarda_su_medio_y_los_debitos_no_tienen(
    client, paciente
):
    """La entrega es un cobro (lleva medio); el débito del tratamiento, no."""
    _abrir_plan(
        client,
        paciente.id,
        monto_total=100000,
        cantidad_cuotas=2,
        medio_pago="TRANSFERENCIA",
    )

    movimientos = _cuenta(client, paciente.id)["transacciones"]
    debito = next(m for m in movimientos if m["tipo_movimiento"] == "DEBITO")
    credito = next(m for m in movimientos if m["tipo_movimiento"] == "CREDITO")

    assert debito["monto"] == 100000.0
    assert debito["medio_pago"] is None
    assert credito["monto"] == 50000.0
    assert credito["medio_pago"] == MedioPago.TRANSFERENCIA.value


def test_un_medio_de_pago_inventado_es_422(client, paciente):
    r = client.post(
        f"{API}/finanzas/pagar",
        json={"paciente_id": paciente.id, "monto": 1000, "medio_pago": "CHEQUE"},
    )

    assert r.status_code == 422, r.text


def test_los_cobros_del_periodo_traen_los_creditos_con_su_medio(client, paciente):
    """``GET /finanzas/cobros-cuenta-corriente``: los ingresos del día."""
    _abrir_plan(
        client,
        paciente.id,
        monto_total=100000,
        cantidad_cuotas=2,
        medio_pago="EFECTIVO",
        fecha="2026-11-03T10:00:00",
    )  # entrega del 50 % en efectivo: 50.000
    _pagar(
        client,
        paciente.id,
        20000,
        medio_pago="TRANSFERENCIA",
        fecha="2026-11-03T16:00:00",
    )
    # Un cobro de otro día no entra en la caja del 3.
    _pagar(client, paciente.id, 9999, fecha="2026-11-02T10:00:00")

    r = client.get(
        f"{API}/finanzas/cobros-cuenta-corriente?desde=2026-11-03&hasta=2026-11-03"
    )

    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["cantidad"] == 2
    assert datos["total"] == 70000.0
    assert datos["total_efectivo"] == 50000.0
    assert datos["total_transferencia"] == 20000.0
    # Del más nuevo al más viejo, y nunca un débito (el tratamiento no es ingreso).
    assert [c["monto"] for c in datos["cobros"]] == [20000.0, 50000.0]
    assert [c["medio_pago"] for c in datos["cobros"]] == ["TRANSFERENCIA", "EFECTIVO"]
    assert datos["cobros"][1]["descripcion"].startswith("Entrega inicial (50%)")
    assert datos["cobros"][1]["paciente_nombre"] == paciente.nombre


def test_los_cobros_del_periodo_filtran_por_medio_de_pago(client, paciente):
    _pagar(client, paciente.id, 8000, fecha="2026-11-03T09:00:00")
    _pagar(
        client,
        paciente.id,
        2000,
        medio_pago="TRANSFERENCIA",
        fecha="2026-11-03T11:00:00",
    )

    solo_transferencias = client.get(
        f"{API}/finanzas/cobros-cuenta-corriente"
        "?desde=2026-11-03&hasta=2026-11-03&medio_pago=TRANSFERENCIA"
    ).json()

    assert solo_transferencias["cantidad"] == 1
    assert solo_transferencias["total"] == 2000.0
    assert solo_transferencias["total_efectivo"] == 0.0
    assert solo_transferencias["total_transferencia"] == 2000.0


def test_un_pago_viejo_sin_medio_cuenta_como_efectivo(
    client, db_session, paciente
):
    """Las filas anteriores a la columna se informan como efectivo, no en null."""
    _abrir_plan(
        client, paciente.id, monto_total=100000, cantidad_cuotas=2,
        pago_entrega_inicial=False,
    )
    cuenta = db_session.execute(select(CuentaCorriente)).scalar_one()
    db_session.add(
        TransaccionCC(
            cuenta_corriente_id=cuenta.id,
            tipo_movimiento=TipoMovimientoCC.CREDITO,
            monto=Decimal("1000.00"),
            descripcion="Pago cargado antes del medio de pago",
            fecha=datetime(2026, 11, 3, 10, 0),
        )
    )
    db_session.commit()

    datos = client.get(
        f"{API}/finanzas/cobros-cuenta-corriente?desde=2026-11-03&hasta=2026-11-03"
    ).json()

    assert datos["cantidad"] == 1
    assert datos["total_efectivo"] == 1000.0
    assert datos["total_transferencia"] == 0.0
    assert datos["cobros"][0]["medio_pago"] == MedioPago.EFECTIVO.value

    # También entra en el filtro por efectivo (no desaparece de la caja).
    efectivo = client.get(
        f"{API}/finanzas/cobros-cuenta-corriente"
        "?desde=2026-11-03&hasta=2026-11-03&medio_pago=EFECTIVO"
    ).json()
    assert efectivo["cantidad"] == 1








