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
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models.cuenta_corriente import CuentaCorriente, PlanCuotas, TransaccionCC
from app.models.enums import EstadoPlanCuotas, TipoMovimientoCC
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






