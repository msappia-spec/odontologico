"""Configuración de la forma de pago del profesional y su liquidación (Módulo 3).

La vista *Doctores* del panel permite configurar **cómo se le paga** a cada
odontólogo con dos campos del modelo ``Doctor``:

* ``tipo_pago``: ``DUENO`` (se lleva el 100 % de la prestación), ``COMISION``
  (un porcentaje de lo que produjo) o ``SUELDO`` (un importe fijo mensual).
* ``valor_pago``: el porcentaje (0-100) para los dos primeros tipos o el importe
  mensual para el sueldo.

Estas pruebas fijan el contrato desde los dos lados:

* **API**: los campos se persisten de verdad (alta, ``PUT`` del modal y
  ``PATCH``), la incoherencia entre ambos devuelve **422** y la liquidación de
  profesionales del mes calcula los honorarios con la modalidad configurada.
* **Frontend**: el formulario de alta y el modal de edición traen el desplegable
  *Tipo de pago* que habilita el campo numérico correspondiente, ``app.js`` manda
  ambos campos al guardar y la tabla de profesionales muestra con qué regla se
  liquida cada uno. También cubre el buscador rápido del formulario de cobro y
  la tabla de subtotales de la liquidación de médicos.
"""

import re
from datetime import datetime, timedelta

import pytest

from app.main import app
from app.models.enums import TipoPagoDoctor

API = "/api/v1"

# Día de noviembre de 2026 usado para facturar y liquidar (UTC naive, la
# convención de fechas del proyecto).
DIA = datetime(2026, 11, 3, 12, 0)
ANIO, MES = 2026, 11


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------


def _crear_doctor(client, **overrides) -> dict:
    """Crea un odontólogo por API y devuelve su representación."""
    payload = {"nombre": "Dr. Nuevo", "especialidad": "Ortodoncia"}
    payload.update(overrides)
    r = client.post(f"{API}/doctores", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_prestacion(client, **overrides) -> dict:
    """Crea una práctica del nomenclador (``valor_base`` = precio de referencia)."""
    payload = {
        "codigo_nomenclador": "01.01",
        "descripcion": "Consulta y diagnóstico",
        "valor_base": 10000,
    }
    payload.update(overrides)
    r = client.post(f"{API}/prestaciones", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_paciente(client, **overrides) -> dict:
    """Crea un paciente particular (sin cobertura: todo se cobra en efectivo)."""
    payload = {"nombre": "Ana Suárez", "telefono": "+5491155667788"}
    payload.update(overrides)
    r = client.post(f"{API}/pacientes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _facturar_visita(
    client, doctor_id: int, inicio: datetime = DIA, valor_base: float = 10000
) -> dict:
    """Agenda y factura una visita particular del profesional (100 % efectivo).

    Devuelve el movimiento de caja: es la *producción* que después liquida el
    reporte de profesionales.
    """
    paciente = _crear_paciente(client, nombre=f"Paciente {inicio.hour}h")
    prestacion = _crear_prestacion(
        client, codigo_nomenclador=f"01.{inicio.hour:02d}", valor_base=valor_base
    )
    turno = client.post(
        f"{API}/turnos",
        json={
            "paciente_id": paciente["id"],
            "doctor_id": doctor_id,
            "fecha_hora_inicio": inicio.isoformat(),
            "fecha_hora_fin": (inicio + timedelta(hours=1)).isoformat(),
        },
    )
    assert turno.status_code == 201, turno.text
    r = client.post(
        f"{API}/turnos/{turno.json()['id']}/facturar",
        json={
            "prestaciones": [{"prestacion_id": prestacion["id"]}],
            "fecha": inicio.replace(hour=10).isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _liquidacion(client, doctor_id=None) -> dict:
    """Reporte de liquidación del mes de trabajo (de todos o de uno solo)."""
    consulta = f"anio={ANIO}&mes={MES}"
    if doctor_id is not None:
        consulta += f"&doctor_id={doctor_id}"
    r = client.get(f"{API}/liquidaciones/profesionales?{consulta}")
    assert r.status_code == 200, r.text
    return r.json()


def _select_html(html: str, identificador: str) -> str:
    """Bloque del desplegable con ese ``id`` (sirve para revisar sus opciones)."""
    encontrado = re.search(
        rf'<select id="{identificador}"[^>]*>.*?</select>', html, re.S
    )
    assert encontrado is not None, f"no se encontró el select #{identificador}"
    return encontrado.group(0)


# ---------------------------------------------------------------------------
# 1) Persistencia de la modalidad de pago
# ---------------------------------------------------------------------------


def test_el_alta_guarda_la_comision_y_su_porcentaje(client):
    creado = _crear_doctor(client, tipo_pago="COMISION", valor_pago=40)

    assert creado["tipo_pago"] == "COMISION"
    assert creado["valor_pago"] == 40

    # Round-trip: quedó guardado y se lee igual en el detalle y en el listado.
    assert client.get(f"{API}/doctores/{creado['id']}").json()["valor_pago"] == 40
    listado = {d["id"]: d for d in client.get(f"{API}/doctores").json()}
    assert listado[creado["id"]]["tipo_pago"] == "COMISION"


def test_el_alta_guarda_el_sueldo_fijo_mensual(client):
    creado = _crear_doctor(client, tipo_pago="SUELDO", valor_pago=250000)

    assert creado["tipo_pago"] == "SUELDO"
    assert creado["valor_pago"] == 250000


def test_el_dueno_puede_retener_un_porcentaje_parcial(client):
    """Un dueño sin ``valor_pago`` se lleva todo, pero puede retener una parte."""
    creado = _crear_doctor(client, tipo_pago="DUENO", valor_pago=60)

    assert creado["tipo_pago"] == "DUENO"
    assert creado["valor_pago"] == 60


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
    assert "comisión" in r.text.lower()


def test_un_sueldo_sin_importe_es_422(client):
    r = client.post(
        f"{API}/doctores",
        json={
            "nombre": "Dr. Sueldo",
            "especialidad": "Endodoncia",
            "tipo_pago": "SUELDO",
        },
    )

    assert r.status_code == 422, r.text
    assert "sueldo" in r.text.lower()


@pytest.mark.parametrize("porcentaje", [-1, 101])
def test_un_porcentaje_fuera_de_rango_es_422(client, porcentaje):
    """Fuera de 0-100 el alta se rechaza (``-1`` lo corta el esquema; ``101`` la regla)."""
    r = client.post(
        f"{API}/doctores",
        json={
            "nombre": "Dr. Comisión",
            "especialidad": "Endodoncia",
            "tipo_pago": "COMISION",
            "valor_pago": porcentaje,
        },
    )

    assert r.status_code == 422, r.text
    assert "valor_pago" in r.text


def test_una_comision_del_cero_por_ciento_es_valida(client):
    """El 0 % es un borde válido (el rango es 0-100, no 1-100)."""
    creado = _crear_doctor(client, tipo_pago="COMISION", valor_pago=0)

    assert creado["tipo_pago"] == "COMISION"
    assert creado["valor_pago"] == 0


def test_el_patch_cambia_la_modalidad_sin_tocar_los_datos_personales(client):
    creado = _crear_doctor(client, nombre="Dr. Comisión", especialidad="Ortodoncia")

    r = client.patch(
        f"{API}/doctores/{creado['id']}",
        json={"tipo_pago": "COMISION", "valor_pago": 35},
    )

    assert r.status_code == 200, r.text
    data = r.json()
    assert (data["tipo_pago"], data["valor_pago"]) == ("COMISION", 35)
    assert data["nombre"] == "Dr. Comisión"
    assert data["especialidad"] == "Ortodoncia"


def test_el_put_guarda_la_modalidad_que_manda_el_modal(client):
    """El modal edita con ``PUT`` (payload completo), incluida la forma de pago."""
    creado = _crear_doctor(client, tipo_pago="COMISION", valor_pago=40)

    r = client.put(
        f"{API}/doctores/{creado['id']}",
        json={
            "nombre": "Dr. Renombrado",
            "especialidad": "Ortodoncia",
            "tipo_pago": "SUELDO",
            "valor_pago": 180000,
        },
    )

    assert r.status_code == 200, r.text
    assert r.json()["tipo_pago"] == "SUELDO"
    assert r.json()["valor_pago"] == 180000


def test_el_patch_vuelve_al_dueno_con_valor_nulo(client):
    """``PATCH {"tipo_pago": "DUENO", "valor_pago": null}`` = 100 % de nuevo."""
    creado = _crear_doctor(client, tipo_pago="SUELDO", valor_pago=90000)

    r = client.patch(
        f"{API}/doctores/{creado['id']}",
        json={"tipo_pago": "DUENO", "valor_pago": None},
    )

    assert r.status_code == 200, r.text
    assert r.json()["tipo_pago"] == "DUENO"
    assert r.json()["valor_pago"] is None


# ---------------------------------------------------------------------------
# 2) La liquidación del mes usa la modalidad configurada
# ---------------------------------------------------------------------------


def test_la_liquidacion_usa_la_comision_configurada_por_el_panel(client):
    comision = _crear_doctor(
        client, nombre="Dr. Comisión", tipo_pago="COMISION", valor_pago=40
    )
    _facturar_visita(client, comision["id"], valor_base=10000)

    doctor = _liquidacion(client, comision["id"])["doctores"][0]

    assert doctor["tipo_pago"] == "COMISION"
    assert doctor["valor_pago"] == 40
    assert doctor["produccion_total"] == 10000
    assert doctor["honorarios"] == 4000          # 40 % de 10000
    assert "40%" in doctor["detalle_honorarios"]
    assert "comisión" in doctor["detalle_honorarios"]


def test_la_liquidacion_usa_el_sueldo_fijo_configurado(client):
    sueldo = _crear_doctor(
        client, nombre="Dr. Sueldo", tipo_pago="SUELDO", valor_pago=250000
    )
    _facturar_visita(client, sueldo["id"], valor_base=10000)

    doctor = _liquidacion(client, sueldo["id"])["doctores"][0]

    assert doctor["honorarios"] == 250000        # el fijo, sin importar la producción
    assert doctor["produccion_total"] == 10000   # …pero la producción se sigue midiendo
    assert "sueldo fijo" in doctor["detalle_honorarios"].lower()


def test_el_dueno_sin_porcentaje_liquida_el_cien_por_ciento(client):
    dueno = _crear_doctor(client, nombre="Dra. Dueña")
    _facturar_visita(client, dueno["id"], valor_base=10000)

    doctor = _liquidacion(client, dueno["id"])["doctores"][0]

    assert doctor["valor_pago"] is None
    assert doctor["honorarios"] == 10000         # 100 % de la prestación
    assert "100%" in doctor["detalle_honorarios"]


def test_el_dueno_con_porcentaje_liquida_solo_esa_parte(client):
    dueno = _crear_doctor(
        client, nombre="Dr. Socio", tipo_pago="DUENO", valor_pago=60
    )
    _facturar_visita(client, dueno["id"], valor_base=10000)

    doctor = _liquidacion(client, dueno["id"])["doctores"][0]

    assert doctor["honorarios"] == 6000          # 60 % de 10000


def test_cambiar_la_modalidad_cambia_la_liquidacion_del_mismo_mes(client):
    """La regla vigente es la que se aplica: comisión → sueldo → dueño."""
    doctor = _crear_doctor(
        client, nombre="Dr. Cambia", tipo_pago="COMISION", valor_pago=40
    )
    _facturar_visita(client, doctor["id"], valor_base=20000)
    assert _liquidacion(client, doctor["id"])["doctores"][0]["honorarios"] == 8000

    a_sueldo = client.patch(
        f"{API}/doctores/{doctor['id']}",
        json={"tipo_pago": "SUELDO", "valor_pago": 300000},
    )
    assert a_sueldo.status_code == 200, a_sueldo.text
    assert _liquidacion(client, doctor["id"])["doctores"][0]["honorarios"] == 300000

    a_dueno = client.patch(
        f"{API}/doctores/{doctor['id']}",
        json={"tipo_pago": "DUENO", "valor_pago": None},
    )
    assert a_dueno.status_code == 200, a_dueno.text
    assert _liquidacion(client, doctor["id"])["doctores"][0]["honorarios"] == 20000


def test_la_liquidacion_de_todos_los_activos_informa_la_modalidad(client):
    """El reporte sin filtro trae la regla de cada profesional y los totales."""
    dueno = _crear_doctor(client, nombre="Dra. Dueña")
    comision = _crear_doctor(
        client, nombre="Dr. Comisión", tipo_pago="COMISION", valor_pago=50
    )
    _facturar_visita(client, dueno["id"], valor_base=10000)
    _facturar_visita(client, comision["id"], valor_base=10000, inicio=DIA + timedelta(hours=1))

    reporte = _liquidacion(client)
    por_nombre = {d["doctor_nombre"]: d for d in reporte["doctores"]}

    assert por_nombre["Dra. Dueña"]["tipo_pago"] == "DUENO"
    assert por_nombre["Dr. Comisión"]["valor_pago"] == 50
    assert reporte["total_produccion"] == 20000
    assert reporte["total_honorarios"] == 15000  # 10000 (dueño) + 5000 (50 %)


def test_la_api_de_la_liquidacion_admite_el_filtro_por_profesional():
    """Contrato del backend: el reporte se consulta por período y por doctor."""
    parametros = app.openapi()["paths"]["/api/v1/liquidaciones/profesionales"]["get"][
        "parameters"
    ]
    nombres = {parametro["name"] for parametro in parametros}

    assert {"anio", "mes", "doctor_id"} <= nombres


# ---------------------------------------------------------------------------
# 3) Panel: formulario del profesional, cobro y liquidación
# ---------------------------------------------------------------------------


def test_el_formulario_de_alta_tiene_el_desplegable_de_tipo_de_pago(client):
    html = client.get("/").text
    bloque = _select_html(html, "doc-tipo-pago")

    # Las tres opciones son las del enum que usa la API (dueño, comisión, sueldo).
    for tipo in TipoPagoDoctor:
        assert f'value="{tipo.value}"' in bloque
    assert "Dueño (se lleva el 100% de la prestación)" in bloque
    assert "Comisión por porcentaje" in bloque
    assert "Sueldo fijo mensual" in bloque

    # El importe arranca oculto (dueño = 100 %) y la lista tiene su columna.
    assert '<div id="doc-valor-grupo" class="hide">' in html
    assert 'id="doc-valor-pago"' in html
    assert "<th>Forma de pago</th>" in html


def test_el_modal_de_edicion_tiene_los_campos_de_liquidacion(client):
    html = client.get("/").text

    for identificador in (
        "doc-edit-tipo-pago",
        "doc-edit-valor-grupo",
        "doc-edit-valor-pago",
        "doc-edit-valor-label",
        "doc-edit-pago-ayuda",
    ):
        assert f'id="{identificador}"' in html
    assert '<div id="doc-edit-valor-grupo" class="hide">' in html

    bloque = _select_html(html, "doc-edit-tipo-pago")
    for tipo in TipoPagoDoctor:
        assert f'value="{tipo.value}"' in bloque


def test_el_javascript_manda_la_modalidad_de_pago_al_guardar(client):
    js = client.get("/static/app.js").text

    assert "function leerPagoFormulario(" in js
    assert "return { tipo_pago: tipoPago, valor_pago: valor };" in js
    assert "const pago = leerPagoFormulario(IDS_PAGO_ALTA);" in js
    assert "const pago = leerPagoFormulario(IDS_PAGO_EDIT);" in js
    assert "...pago," in js                        # viaja en el POST y en el PUT
    assert 'document.getElementById("doc-tipo-pago").addEventListener("change"' in js
    assert 'document.getElementById("doc-edit-tipo-pago").addEventListener("change"' in js


def test_el_tipo_de_pago_habilita_el_campo_numerico(client):
    js = client.get("/static/app.js").text

    assert "function configurarCamposPago(" in js
    assert 'grupo.classList.toggle("hide", !mostrar);' in js
    assert "entrada.required = datos.requerido;" in js
    assert 'if (tipoPago === "SUELDO") entrada.removeAttribute("max");' in js
    # Cada modalidad del enum tiene su configuración en el panel.
    for tipo in TipoPagoDoctor:
        assert f"    {tipo.value}: {{" in js
    assert 'campo: "Porcentaje de la producción (%)"' in js
    assert 'campo: "Porcentaje de la comisión (%)"' in js
    assert 'campo: "Sueldo mensual ($)"' in js


def test_la_modalidad_incoherente_la_avisa_el_formulario(client):
    """El panel valida antes de llamar a la API y muestra el error en el form."""
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert "function errorDePago(" in js
    assert 'return "Ingresá el porcentaje de la comisión (0 a 100).";' in js
    assert 'return "Ingresá el sueldo mensual del profesional (mayor que 0).";' in js
    assert 'mostrarAviso("doc-error", errorPago);' in js
    assert 'mostrarAviso("doctor-error", errorPago);' in js
    assert 'id="doc-error"' in html
    assert 'id="doctor-error"' in html


def test_la_tabla_de_doctores_muestra_la_forma_de_pago(client):
    js = client.get("/static/app.js").text

    assert "function textoPagoDoctor(" in js
    assert "TIPO_PAGO_TEXTO[doctor.tipo_pago] || doctor.tipo_pago" in js
    assert '<td><span class="fact-med-tipo">${esc(textoPagoDoctor(d))}</span></td>' in js
    # La tabla tiene seis columnas: la fila vacía usa el mismo colspan.
    assert '<tr><td colspan="6">${vacio("Aún no hay doctores registrados")}' in js


def test_la_liquidacion_de_medicos_se_dibuja_en_una_tabla_con_subtotales(client):
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert 'id="fact-lista-medicos"' in html
    # Una fila por profesional y el total del consultorio al pie.
    assert "<th>Profesional</th><th>Forma de pago</th>" in js
    assert "<th>Cálculo del mes</th></tr></thead>" in js
    assert '<tfoot><tr class="bg-slate-50">' in js
    assert "Totales del período" in js
    assert "atenciones} atención(es) facturada(s)" in js
    # El subtotal de honorarios y el detalle del cálculo salen del backend.
    assert 'class="text-right font-bold text-brand-700">${fmtMoneda(d.honorarios)}' in js
    assert "${esc(d.detalle_honorarios)}" in js
    for identificador in ("fact-med-produccion", "fact-med-honorarios"):
        assert f'id="{identificador}"' in html


def test_el_pdf_de_la_liquidacion_respeta_el_profesional_elegido(client):
    js = client.get("/static/app.js").text

    assert "function slug(" in js
    assert 'state.factMedDoctor ? `&doctor_id=${state.factMedDoctor}` : ""' in js
    assert "liquidacion-${slug(profesional.nombre)}-" in js


def test_el_formulario_de_cobro_tiene_el_buscador_y_el_arancel(client):
    html = client.get("/").text
    js = client.get("/static/app.js").text

    # Buscador rápido del nomenclador: filtra sin perder lo que está tildado.
    assert 'id="fact-cobro-buscar"' in html
    assert 'id="fact-cobro-contador"' in html
    assert "function filtrarPrestacionesCobro(" in js
    assert "normalizarTexto(`${p.codigo_nomenclador} ${p.descripcion}`)" in js
    assert 'cobroBuscar.addEventListener("input"' in js

    # Los tres importes del mostrador: arancel, cubre la OS y coseguro.
    assert '<th class="text-right">Arancel</th>' in html
    assert 'id="fact-cobro-total"' in html
    assert "const arancel =" in js
    assert '`<td class="text-right">${fmtMoneda(arancel)}</td>` +' in js
    assert 'set("fact-cobro-total", fmtMoneda(efectivo + os));' in js


def test_el_cierre_de_la_visita_avisa_que_el_turno_quedo_finalizado(client):
    """Al confirmar, el panel informa el turno cerrado y su movimiento de caja."""
    js = client.get("/static/app.js").text
    html = client.get("/").text

    assert "finalizado · movimiento de caja #${movimiento.id}" in js
    assert 'id="modal-cobro-save"' in html
    assert "Confirmar y cerrar atención" in html
    assert "marca el turno como finalizado" in html


