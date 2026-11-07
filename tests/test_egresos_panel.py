"""Pruebas de la pestaña «Proveedores y egresos» del panel (Módulo 6).

El circuito de egresos existía sólo por API: dar de alta un proveedor, cargar
una factura recibida y pagarla obligaba a usar `/docs`. Estas pruebas cubren la
interfaz que ahora lo expone en el menú:

1. el enlace del menú, la sección de la SPA y los controles de las tres
   tarjetas (tablero de cuentas por pagar, proveedores y facturas recibidas);
2. que ``app.js`` consuma los endpoints del módulo y filtre la cartera en el
   servidor (proveedor, estado y «sólo vencidas»);
3. que cada ``getElementById`` del módulo apunte a un id que existe en el HTML
   (un id mal escrito deja un panel que no responde);
4. las reglas del backend que la vista refleja: ``paciente_id`` sólo se ofrece
   en un mecánico dental y un pago no puede superar el saldo de la factura.
"""

import re

#: Controles de la vista (tablero, alta de proveedor y las dos tablas).
CONTROLES_VISTA = (
    "nav-egresos-deuda",
    "egr-refrescar",
    "egr-nueva-factura",
    "egr-kpi-deuda",
    "egr-kpi-vencida",
    "egr-kpi-facturas",
    "egr-kpi-proveedores",
    "egr-por-tipo",
    "egr-nota",
    "form-proveedor-egreso",
    "egr-prov-nombre",
    "egr-prov-tipo",
    "egr-prov-telefono",
    "egr-prov-guardar",
    "egr-prov-error",
    "egr-prov-filtro-tipo",
    "egr-prov-solo-deuda",
    "egr-prov-contador",
    "egr-proveedores",
    "egr-fact-proveedor",
    "egr-fact-estado",
    "egr-fact-vencidas",
    "egr-fact-buscar",
    "egr-fact-contador",
    "egr-facturas",
)

#: Controles de los dos modales (factura recibida y pago).
CONTROLES_MODALES = (
    "modal-factura-egreso",
    "egr-fact-titulo",
    "egr-fact-proveedor-modal",
    "egr-fact-concepto",
    "egr-fact-monto",
    "egr-fact-emision",
    "egr-fact-vencimiento",
    "egr-fact-paciente",
    "egr-fact-paciente-nota",
    "egr-fact-error",
    "modal-factura-close",
    "modal-factura-cancel",
    "modal-factura-save",
    "modal-pago-egreso",
    "egr-pago-titulo",
    "egr-pago-detalle",
    "egr-pago-monto",
    "egr-pago-metodo",
    "egr-pago-fecha",
    "egr-pago-error",
    "modal-pago-close",
    "modal-pago-cancel",
    "modal-pago-save",
)


def _estatico(client, nombre):
    """Devuelve el contenido de un recurso de ``app/static``."""
    respuesta = client.get(f"/static/{nombre}")
    assert respuesta.status_code == 200, nombre
    return respuesta.text


def test_el_menu_ofrece_la_pestana_de_proveedores_y_egresos(client):
    """La pestaña viaja en el menú junto al resto de la operación del mostrador."""
    html = client.get("/").text
    assert 'data-view="egresos"' in html
    assert 'id="view-egresos"' in html
    assert "Proveedores y egresos" in html
    assert "Proveedores y cuentas por pagar" in html
    # Queda entre «Cuenta corriente» y «Borrar movimientos».
    cuenta = html.index('data-view="cuenta-corriente"')
    egresos = html.index('data-view="egresos"')
    borrar = html.index('data-view="borrar-movimientos"')
    assert cuenta < egresos < borrar
    # El encabezado superior toma el nombre del diccionario de vistas de app.js.
    js = _estatico(client, "app.js")
    assert "egresos: [" in js
    assert '"Proveedores y egresos"' in js


def test_la_vista_trae_el_tablero_las_dos_tablas_y_los_modales(client):
    """Los controles que el JS maneja existen en la página servida."""
    html = client.get("/").text
    for control in CONTROLES_VISTA + CONTROLES_MODALES:
        assert f'id="{control}"' in html, control

def test_los_ids_que_usa_el_javascript_existen_en_el_html(client):
    """Ningún ``getElementById('egr-…')`` apunta a un id inexistente."""
    html = client.get("/").text
    js = _estatico(client, "app.js")

    usados = set(re.findall(r'getElementById\("(egr-[a-z0-9-]+)"\)', js))
    assert usados, "app.js debe manejar los controles del módulo de egresos"
    faltantes = sorted(i for i in usados if f'id="{i}"' not in html)
    assert not faltantes, f"ids usados por app.js sin HTML: {faltantes}"

    # Los modales se abren y cierran por su id: también tienen que existir.
    modales = set(
        re.findall(r'(?:mostrarModal|ocultarModal)\("(modal-[a-z-]*egreso)"\)', js)
    )
    assert modales == {"modal-factura-egreso", "modal-pago-egreso"}
    for modal in modales:
        assert f'id="{modal}"' in html, modal

    botones = set(re.findall(r'"(modal-(?:factura|pago)-(?:close|cancel|save))"', js))
    for boton in botones:
        assert f'id="{boton}"' in html, boton


def test_el_javascript_consume_los_endpoints_del_modulo_de_egresos(client):
    """Las rutas del Módulo 6 se arman en un solo lugar y son las de la API."""
    js = _estatico(client, "app.js")
    assert 'const RUTA_EGRESOS = "/egresos"' in js
    assert "`${RUTA_EGRESOS}/proveedores`" in js
    assert "`${RUTA_EGRESOS}/facturas`" in js
    assert "`${RUTA_EGRESOS}/pagar`" in js
    assert "`${RUTA_EGRESOS}/cuentas-por-pagar`" in js

    # Alta de proveedor, alta de factura y pago: los tres POST del circuito.
    assert 'method: "POST"' in js
    assert "proveedor_id: proveedorId" in js
    assert "gasto_factura_id: factura.id" in js

    # La cartera se filtra en el servidor: es la que ordena por vencimiento.
    assert 'params.set("proveedor_id", state.egrProveedorId)' in js
    assert 'params.set("estado_pago", state.egrEstadoFactura)' in js
    assert 'params.set("solo_vencidas", "true")' in js

    # Las filas se redibujan en cada carga: las acciones van por delegación.
    for marca in ("data-egr-facturar", "data-egr-ver", "data-egr-pagar"):
        assert marca in js, marca


def test_la_vista_respeta_las_reglas_del_backend(client):
    """Paciente sólo en el mecánico dental y pago nunca mayor al saldo."""
    js = _estatico(client, "app.js")
    # El backend responde 422 si llega ``paciente_id`` en otro rubro: el campo
    # se deshabilita y se limpia al cambiar de proveedor.
    assert 'proveedor.tipo_proveedor === "MECANICO_DENTAL"' in js
    assert "select.disabled = !mecanico" in js
    # El pago mayor al saldo se avisa en el modal antes de viajar.
    assert "El pago no puede superar el saldo de la factura" in js
    # Los importes se muestran con el formateador del panel (es-AR).
    assert "fmtMoneda(saldo)" in js
