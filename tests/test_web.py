"""Pruebas de la interfaz web (dashboard de la secretaría).

Verifican que FastAPI sirve la página principal, que los recursos estáticos
están accesibles y que la API sigue operativa. No dependen de la base de datos.
"""

import re

from html.parser import HTMLParser


class _ArbolHTML(HTMLParser):
    """Ruta de ancestros de cada ``id`` del documento.

    Alcanza para vigilar el anidamiento de las vistas del panel: un
    ``</section>`` olvidado mete la vista siguiente (y los modales que vienen
    después) dentro de la anterior y, como ``cambiarVista()`` arranca con todas
    ocultas, los diálogos se abren pero no se ven. No valida todo el HTML:
    ignora los cierres huérfanos, igual que un navegador tolerante.
    """

    VACIAS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pila = []
        self.padres = {}

    def handle_starttag(self, tag, attrs):
        ident = dict(attrs).get("id")
        if ident and ident not in self.padres:
            self.padres[ident] = tuple(self.pila)
        if tag not in self.VACIAS:
            self.pila.append((tag, ident))

    def handle_endtag(self, tag):
        # Cierra hasta el primer tag coincidente, como un parser tolerante.
        for i in range(len(self.pila) - 1, -1, -1):
            if self.pila[i][0] == tag:
                del self.pila[i:]
                return


def _arbol_html(html):
    arbol = _ArbolHTML()
    arbol.feed(html)
    arbol.close()
    return arbol


def test_dashboard_raiz_devuelve_html(client):
    """La raíz sirve el dashboard en HTML."""
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "Franciosi Odontología" in r.text
    assert "/static/app.js" in r.text


def test_paginas_publicas_de_privacidad_y_eliminacion(client):
    privacidad = client.get("/privacidad")
    eliminacion = client.get("/eliminacion-datos")
    assert privacidad.status_code == 200
    assert "Política de privacidad" in privacidad.text
    assert eliminacion.status_code == 200
    assert "Solicitud de eliminación de datos" in eliminacion.text


def test_hoja_de_estilos_disponible(client):
    r = client.get("/static/styles.css")
    assert r.status_code == 200
    assert "--brand-500" in r.text


def test_javascript_disponible_y_apunta_a_la_api(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "/api/v1" in r.text


def test_la_marca_franciosi_esta_en_las_tres_interfaces(client):
    """El panel, la vista móvil y la ficha pública comparten nombre y favicon."""
    panel = client.get("/").text
    movil = client.get("/medico").text
    ficha = client.get("/anamnesis/token-de-prueba").text

    for html in (panel, movil, ficha):
        assert "Franciosi Odontología" in html
        assert "/static/img/favicon-franciosi.svg" in html

    # Cada interfaz usa además la pieza de marca adecuada a su tamaño.
    assert "/static/img/marca-franciosi.svg" in panel
    assert "/static/img/logo-franciosi.svg" in movil
    assert "/static/img/marca-franciosi.svg" in ficha


def test_los_recursos_de_marca_se_sirven_como_svg(client):
    """Los logos viajan como SVG estático y no dependen de un CDN externo."""
    for recurso in (
        "/static/img/marca-franciosi.svg",
        "/static/img/logo-franciosi.svg",
        "/static/img/logo-franciosi-horizontal.svg",
        "/static/img/favicon-franciosi.svg",
    ):
        r = client.get(recurso)
        assert r.status_code == 200, recurso
        assert "image/svg+xml" in r.headers["content-type"]
        assert "<svg" in r.text


def test_health_y_api_siguen_operativos(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/doctores").status_code == 200
    assert client.get("/api/v1/pacientes").status_code == 200
    assert client.get("/api/v1/turnos").status_code == 200
    assert client.get("/api/v1/obras-sociales").status_code == 200


def test_dashboard_incluye_la_vista_de_disponibilidad(client):
    """La pestaña "Disponibilidad" y su editor semanal viajan en el HTML servido."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'data-view="disponibilidad"' in r.text
    assert 'id="view-disponibilidad"' in r.text
    assert 'id="disp-semana"' in r.text
    assert 'id="disp-doctor"' in r.text


def test_dashboard_incluye_la_vista_de_obras_sociales(client):
    """La pestaña "Obras Sociales" (catálogo) y su modal viajan en el HTML."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'data-view="obras-sociales"' in r.text
    assert 'id="view-obras-sociales"' in r.text
    assert 'id="lista-obras-sociales"' in r.text
    assert 'id="form-obra-social"' in r.text
    # El formulario de pacientes elige la cobertura del catálogo por id.
    assert 'name="obra_social_id"' in r.text


def test_el_calendario_lee_la_agenda_semanal_de_la_api(client):
    """calendar.js ya no tiene una agenda fija: la pide al backend."""
    r = client.get("/static/calendar.js")
    assert r.status_code == 200
    assert "/doctores/" in r.text
    assert "disponibilidad" in r.text
    assert "AGENDA_SEMANAL" not in r.text


def test_los_bloques_no_disponibles_del_calendario_explican_el_motivo(client):
    """Un bloque gris también responde al clic: dice por qué no se agenda.

    Antes los bloques no disponibles se pintaban con ``disabled``, así que el
    navegador se comía el clic y la secretaría no recibía ninguna reacción (el
    panel parecía roto cuando el día elegido era hoy o estaba lleno). Ahora se
    marcan con ``aria-disabled`` + ``data-motivo`` y el clic muestra un aviso.
    """
    calendario = client.get("/static/calendar.js").text

    assert 'aria-disabled="${deshabilitado ? "true" : "false"}"' in calendario
    assert 'data-motivo="${motivo}"' in calendario
    assert "MOTIVO_TITULO" in calendario and "MOTIVO_DETALLE" in calendario
    assert "avisarBloqueNoDisponible" in calendario
    # Los tres motivos previstos y el atajo para pedir el turno a mano.
    for motivo in ("pasado", "ocupado", "fuera"):
        assert f'"{motivo}"' in calendario
    assert "data-nuevo-turno" in calendario
    # El bloque libre sigue abriendo el formulario con la hora ya cargada.
    assert "agendarEnSlot(btn.dataset.slot, btn.dataset.doctor)" in calendario
    # Ya no se usa el atributo `disabled`, que impide enterarse del clic.
    assert 'deshabilitado ? "disabled"' not in calendario


def test_el_panel_de_horarios_avisa_cuando_no_queda_ningun_bloque_libre(client):
    """Sin bloques libres el panel lo dice y ofrece el formulario igual."""
    calendario = client.get("/static/calendar.js").text

    assert "function diaTerminado(" in calendario
    assert "los horarios de este día ya pasaron" in calendario.lower()
    assert 'class="cal-aviso"' in calendario
    assert 'data-nuevo-turno="1"' in calendario

    css = client.get("/static/styles.css").text
    assert ".cal-slot-off" in css
    assert '.cal-slot[aria-disabled="true"]' in css
    assert ".cal-aviso" in css and ".cal-aviso-btn" in css

    html = client.get("/").text
    # La ayuda del panel explica qué significan los bloques grises.
    assert "Los bloques en gris no se pueden" in html



    """CONFIRMADO verde, CANCELADO gris y PENDIENTE con el color del odontólogo."""
    calendario = client.get("/static/calendar.js").text
    assert "bg-green-500 text-white" in calendario
    assert "bg-slate-100 text-slate-400" in calendario

    app_js = client.get("/static/app.js").text
    assert "hora-confirmada bg-green-500 text-white" in app_js
    assert "hora-cancelada bg-slate-200 text-slate-500" in app_js

    css = client.get("/static/styles.css").text
    assert ".cal-evento.confirmado" in css
    assert ".cal-evento.cancelado" in css
    assert ".disp-rango" in css


def test_el_encabezado_superior_lleva_el_titulo_de_la_operacion(client):
    """El <h1> de arriba de todo es grande, a todo el ancho y lleva la operación.

    Reemplaza a los viejos «Dashboard» / «Panel de secretaría», se ubica por
    encima de la barra de acciones (Recordatorios / Nuevo turno) y del
    calendario —sin tocar el menú lateral ni la grilla mensual— y arranca con el
    título de la vista inicial (calendario, igual que `state.vista`).
    """
    html = client.get("/").text
    # El nombre oficial reemplazó a todos los rótulos anteriores.
    assert "Dashboard" not in html
    assert "Panel de secretaría" not in html
    # <title>, subtítulo de la marca en el menú, <h1> y la pestaña del menú.
    assert html.count("Panel Principal") >= 3
    # El <h1> conserva el id y usa las clases de jerarquía pedidas.
    assert 'id="page-title"' in html
    assert 'class="text-3xl font-bold text-slate-800 tracking-tight"' in html
    # Orden del documento: título -> barra de acciones -> contenido/calendario.
    assert html.index('id="page-title"') < html.index("<header")
    assert html.index("<header") < html.index("<main")
    assert html.index('id="page-title"') < html.index('id="view-calendario"')
    # El subtítulo sigue existiendo: app.js lo reescribe en cada cambio de vista.
    assert 'id="page-subtitle"' in html


def test_el_encabezado_queda_fuera_de_las_secciones_de_la_spa(client):
    """El bloque del título no es una <section>: `main > section` sigue siendo
    sólo las vistas, así que cambiarVista() no puede ocultar el encabezado ni
    romper el calendario mensual al mover el bloque."""
    html = client.get("/").text
    titulo, main = html.index('id="page-title"'), html.index("<main")
    assert titulo < main  # vive fuera del contenedor de las vistas
    assert "<section" not in html[titulo:main]
    # Un único encabezado, ni duplicado ni dentro de una vista.
    assert html.count('id="page-title"') == 1
    assert html.count('id="page-subtitle"') == 1


def test_las_vistas_son_hermanas_y_los_modales_quedan_fuera_de_ellas(client):
    """Cada ``view-*`` cuelga directo de ``main`` y ningún modal vive adentro.

    Si a una vista le falta su ``</section>``, la siguiente (y todos los modales
    que vienen después en el documento) queda anidada dentro de ella. Como
    ``cambiarVista()`` mantiene ocultas las vistas que no son la activa, el
    diálogo se abre con ``classList.remove("hide")`` pero sigue dentro de un
    ``display: none``: «Nuevo turno» y «Cobrar» parecían no responder según la
    operación elegida en el menú.
    """
    arbol = _arbol_html(client.get("/").text)

    vistas = [i for i in arbol.padres if i.startswith("view-")]
    assert len(vistas) >= 11   # panel, calendario, disponibilidad, ... y Módulo 5
    for vista in vistas:
        assert arbol.padres[vista][-1] == ("main", None), vista

    # Los modales (y sus títulos, que también empiezan con «modal-») tienen que
    # quedar fuera de main y de cualquier sección de vista.
    modales = [i for i in arbol.padres if i.startswith("modal-")]
    assert "modal-turno" in modales
    assert "modal-cobro" in modales
    for modal in modales:
        ancestros = arbol.padres[modal]
        assert ("main", None) not in ancestros, modal
        assert not any(i and i.startswith("view-") for _, i in ancestros), modal


def test_cada_operacion_del_menu_escribe_su_titulo_en_el_encabezado(client):
    """cambiarVista() pinta el nombre de la operación elegida, no siempre el mismo."""
    app_js = client.get("/static/app.js").text
    # La pestaña «dashboard» se renombró a «Panel Principal»...
    assert '"Panel Principal"' in app_js
    assert '"Dashboard"' not in app_js
    # ...y el <h1> sale del diccionario de vistas, junto con su descripción.
    assert 'getElementById("page-title").textContent = VISTAS[v][0]' in app_js
    assert 'getElementById("page-subtitle").textContent = subtituloVista(v)' in app_js
    # Recordatorios sólo refresca la descripción (su ventana de días) sin pisar
    # el nombre de la operación.
    assert 'getElementById("page-subtitle").textContent = texto' in app_js


def test_el_panel_principal_es_la_primera_operacion_del_menu(client):
    """En el grupo «Operación» el orden es Panel Principal, Calendario y
    Disponibilidad; la pantalla se abre en el calendario."""
    html = client.get("/").text
    operacion = html.index(">Operación<")
    panel = html.index('data-view="dashboard"')
    calendario = html.index('data-view="calendario"')
    disponibilidad = html.index('data-view="disponibilidad"')
    assert operacion < panel < calendario < disponibilidad
    # El primer renglón del menú de operaciones es el «Panel Principal».
    assert "Panel Principal" in html[panel:calendario]
    # La vista inicial (y su título) siguen siendo el calendario.
    assert 'class="nav-link active" data-view="calendario"' in html
    assert 'id="page-title" class="text-3xl font-bold text-slate-800 tracking-tight">Calendario</h1>' in html


def test_la_agenda_de_hoy_del_panel_se_arma_en_columnas_por_doctor(client):
    """«Agenda de hoy» reparte los turnos del día en una columna por odontólogo.

    El contenedor es una grilla (tres columnas en pantallas grandes que se apilan
    en las chicas) y cada columna lleva la cabecera del profesional, su cantidad
    de turnos y las tarjetas compactas del día, con las mismas acciones
    (editar/cancelar/reactivar) que resuelve calendar.js por delegación.
    """
    html = client.get("/").text
    app_js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text

    # El contenedor de la agenda es la grilla de columnas y ya no la lista apilada.
    assert ('id="dash-agenda-hoy" class="grid grid-cols-1 md:grid-cols-2 '
            'xl:grid-cols-3 gap-4"') in html
    assert 'id="dash-hoy-total"' in html

    # app.js agrupa por doctor y dibuja una columna por cada uno.
    assert "function agendaPorDoctor(" in app_js
    assert "function columnaDoctor(" in app_js
    assert "function turnoColumna(" in app_js
    assert "agendaPorDoctor(delDia)" in app_js
    assert "map((c) => columnaDoctor(c.doctor, c.turnos))" in app_js
    assert '.getElementById("dash-hoy-total")' in app_js
    # Las tarjetas de la columna conservan los atajos de acción de la tarjeta.
    assert "panelAccionesTurno(t)" in app_js

    # Las columnas tienen sus propias reglas de estilo.
    assert ".agenda-doctor-cabecera" in css
    assert ".agenda-doctor-turnos" in css
    assert ".agenda-doctor-vacio" in css

# --------------------------------------------------------------------------- #
# Vista móvil del odontólogo (/medico)
# --------------------------------------------------------------------------- #


def test_la_vista_movil_del_odontologo_se_sirve_en_medico(client):
    """``/medico`` sirve la página mobile-first (viewport, Tailwind y hojas)."""
    r = client.get("/medico")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]

    # Optimizada para celular: viewport con ``viewport-fit`` y Tailwind por CDN.
    assert 'name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover"' in r.text
    assert "cdn.tailwindcss.com" in r.text
    assert "/static/medico.css" in r.text
    assert "/static/medico.js" in r.text

    # Las dos pantallas (selección del profesional y agenda) y la línea del día.
    assert 'id="pantalla-profesional"' in r.text
    assert 'id="lista-profesionales"' in r.text
    assert 'id="pantalla-agenda"' in r.text
    assert 'id="agenda-lista"' in r.text
    assert 'id="m-resumen"' in r.text

    # Las dos hojas deslizantes con el formulario mínimo del paciente.
    assert 'id="hoja-nuevo-turno"' in r.text
    assert 'id="nuevo-nombre"' in r.text
    assert 'id="nuevo-telefono"' in r.text
    assert 'id="nuevo-dni"' in r.text
    assert 'id="hoja-cancelar"' in r.text
    assert 'id="btn-confirmar-cancelar"' in r.text


def test_la_hoja_de_estilos_movil_esta_disponible(client):
    """Los estilos móviles traen áreas táctiles, hojas y el verde confirmado."""
    r = client.get("/static/medico.css")
    assert r.status_code == 200
    assert ".m-icon-btn" in r.text
    assert "width: 44px" in r.text          # objetivo táctil cómodo
    assert ".m-sheet-panel" in r.text       # hoja deslizante
    assert ".m-turno.m-confirmado" in r.text
    assert "background: #ecfdf5" in r.text  # fondo verde del CONFIRMADO
    assert ".m-libre" in r.text and "border-style: dashed" in r.text


def test_el_javascript_movil_consume_la_api_y_recuerda_la_seleccion(client):
    """La vista móvil pega contra los MISMOS endpoints que el panel."""
    js = client.get("/static/medico.js").text
    assert "/api/v1" in js

    # 1) Selección del profesional recordada en el celular.
    assert "localStorage" in js
    assert "odonto.medico.doctorId" in js

    # 2) Agenda del día: turnos del odontólogo y bloques libres del backend.
    assert "/turnos?doctor_id=" in js
    assert "/horarios-disponibles" in js
    assert "turnosDia" in js and "bloques" in js

    # 3) Cancelar (PATCH) y agendar (POST de paciente + POST de turno).
    assert 'method: "PATCH"' in js
    assert 'estado_turno: "CANCELADO"' in js
    assert 'method: "POST"' in js
    assert '"/pacientes"' in js
    assert '"/turnos"' in js


def test_la_agenda_movil_marca_los_estados_y_los_horarios_libres(client):
    """Los turnos ocupados y los bloques libres se distinguen en el listado."""
    js = client.get("/static/medico.js").text
    assert "m-item m-turno m-confirmado" in js
    assert "m-item m-turno m-pendiente" in js
    assert "m-item m-turno m-cancelado" in js
    assert "m-item m-libre" in js
    assert 'data-agendar="' in js
    assert 'data-cancelar="' in js
    # La línea del día se ordena por hora (ocupados y libres intercalados).
    assert "items.sort" in js


def test_se_puede_confirmar_la_asistencia_del_turno_desde_la_ui(client):
    """Confirmar la asistencia es una acción de un toque, en el panel y en el celular.

    El panel la ofrece como botón de la tarjeta del turno (*Confirmar asistencia*)
    y la agenda móvil como botón de tilde; los dos ejecutan
    ``PATCH /turnos/{id}`` con ``estado_turno = CONFIRMADO``. No dependen de que
    el paciente responda el WhatsApp —los botones *Responder: Confirma/Cancela*
    necesitan el ``whatsapp_mensaje_id`` de un mensaje realmente enviado—, así que
    la opción sigue disponible aunque el turno nunca haya recibido el recordatorio.
    """
    app_js = client.get("/static/app.js").text
    assert 'data-confirmar-turno="${t.id}"' in app_js
    assert "Confirmar asistencia" in app_js
    # Sólo se ofrece mientras el turno está pendiente: el confirmado ya está en
    # verde y el cancelado se reactiva desde *Editar* / *Reactivar*.
    assert 't.estado_turno === "PENDIENTE"' in app_js

    calendario = client.get("/static/calendar.js").text
    assert '"[data-confirmar-turno]"' in calendario
    assert "confirmarAsistencia" in calendario
    assert 'AVISO_ESTADO' in calendario and "Asistencia confirmada" in calendario

    movil = client.get("/static/medico.js").text
    assert 'class="m-check" data-confirmar="${t.id}"' in movil
    assert 'estado_turno: "CONFIRMADO"' in movil
    assert 'objetivo.closest("[data-confirmar]")' in movil

    css = client.get("/static/medico.css").text
    assert ".m-check" in css
    assert ".m-check:active" in css


def test_el_panel_enlaza_la_vista_movil(client):
    """El menú del panel ofrece el acceso a la agenda móvil del odontólogo."""
    html = client.get("/").text
    assert 'href="/medico"' in html
    assert "Vista móvil" in html


def test_dashboard_incluye_la_pestana_de_cuenta_corriente(client):
    """La pestaña «Cuenta corriente» (Módulo 5) viaja en el menú y en el HTML.

    Es la interfaz del módulo financiero: sin ella, la cuenta corriente y los
    planes de cuotas sólo se podían operar por API.
    """
    r = client.get("/")
    assert r.status_code == 200
    assert 'data-view="cuenta-corriente"' in r.text
    assert "Cuenta corriente" in r.text
    assert 'id="view-cuenta-corriente"' in r.text
    # Controles de la vista: paciente, KPIs, planes, libro mayor y formularios.
    for control in (
        "cc-paciente",
        "cc-kpi-saldo",
        "cc-kpi-debitos",
        "cc-kpi-creditos",
        "cc-planes",
        "cc-movimientos",
        "cc-plan-form",
        "cc-pago-form",
    ):
        assert f'id="{control}"' in r.text, control


def test_el_javascript_de_la_cuenta_corriente_consume_los_endpoints_del_modulo(
    client,
):
    """Los controles de la pestaña pegan contra las rutas de ``finanzas.py``."""
    js = client.get("/static/app.js").text
    assert "/finanzas/cuenta-corriente/paciente/" in js
    assert "/finanzas/plan-cuotas" in js
    assert "/finanzas/pagar" in js
    assert "/estado" in js
    # La vista se refresca al entrar y tiene su propia función de carga.
    assert 'v === "cuenta-corriente"' in js
    assert "cargarCuentaCorriente" in js
    assert 'method: "PATCH"' in js


def test_la_tabla_de_planes_permite_pasar_a_cobranza_y_reactivar(client):
    """El plan se deriva a cobranza o se reactiva desde la fila de la tabla."""
    js = client.get("/static/app.js").text
    assert 'data-cc-estado="RECOBRABLE"' in js
    assert 'data-cc-estado="ACTIVO"' in js
    assert "En cobranza" in js
    # El atajo «Registrar pago» precarga el plan y el valor de una cuota.
    assert "data-cc-pagar=" in js
    assert "precargarPagoCC" in js


def test_el_pago_avisa_y_pide_confirmacion_cuando_supera_la_deuda(client):
    """Pagar de más se avisa y se pregunta antes de imputarlo a la cuenta.

    El servidor acepta el excedente como saldo a favor, pero el panel compara el
    importe con la deuda y pide confirmación: la secretaría decide si ese saldo
    queda a favor del paciente en lugar de que aparezca sin avisar.
    """
    js = client.get("/static/app.js").text
    # La deuda sale del saldo que informó el servidor (nunca se recalcula acá).
    assert "function deudaPendienteCC(" in js
    assert "saldo_actual" in js
    # El importe y la deuda se comparan al centavo, como el resto del módulo.
    assert "Math.round((Number(valor) || 0) * 100)" in js
    assert "const excedente = centavosCC(monto - deuda) / 100;" in js
    # La pregunta es explícita y nombra el saldo a favor que va a quedar.
    assert "window.confirm(" in js
    assert "¿Registrar el pago igual y dejar ese saldo a favor?" in js
    assert "como saldo a favor del paciente" in js
    # Si la secretaría dice que no, el pago no se envía y se corrige el importe.
    assert '"Pago no registrado"' in js
    assert "input.select()" in js


def test_al_elegir_un_plan_el_pago_precarga_el_valor_de_la_cuota(client):
    """«Imputar al plan» completa el importe con el valor de una cuota."""
    html = client.get("/").text
    js = client.get("/static/app.js").text
    # El aviso en vivo viaja en el formulario de pago.
    assert 'id="cc-pago-hint"' in html
    assert "function precargarCuotaPagoCC(" in js
    # El valor lo informa el servidor en el plan; el panel no lo recalcula.
    assert "plan.monto_por_cuota" in js
    assert 'ccPagoPlan.addEventListener("change", precargarCuotaPagoCC)' in js
    # Un importe escrito a mano no se pisa: sólo se reemplaza la precarga previa.
    assert "ccPagoAuto" in js
    # El aviso cuenta la cuota, la deuda y el excedente que quedaría a favor.
    assert "function hintPagoCC(" in js
    assert "Cuota del plan:" in js
    assert "El importe supera la deuda:" in js


def test_el_nombre_del_paciente_se_destaca_en_la_cuenta_corriente(client):
    """El paciente elegido es el identificador de la vista: negrita y +20 %.

    ``.field`` fija la letra en ``.925rem``; el nombre va en negrita y a
    ``1.11rem`` (un 20 % más) para encontrarlo de un vistazo en el mostrador.
    """
    html = client.get("/").text

    assert 'id="cc-paciente" class="field font-bold text-slate-900"' in html
    assert "font-size: 1.11rem;" in html


def test_el_boton_de_informe_abre_el_consolidado_de_todas_las_cuentas(client):
    """El informe muestra el plan, lo pagado, lo que se debe y la deuda total."""
    html = client.get("/").text
    js = client.get("/static/app.js").text

    # El botón vive en el encabezado de la vista y el informe en su modal.
    assert 'id="cc-informe"' in html
    assert 'id="modal-informe-cc"' in html
    for control in (
        "informe-cc-filas",
        "informe-cc-pie",
        "informe-cc-deuda",
        "informe-cc-cobrado",
        "informe-cc-favor",
        "informe-cc-imprimir",
    ):
        assert f'id="{control}"' in html, control

    # Una sola llamada trae el consolidado del servidor (que es el que suma).
    assert "/finanzas/cuentas-corrientes" in js
    assert "function abrirInformeCC(" in js
    assert "function renderInformeCC(" in js
    assert 'ccInformeBtn.addEventListener("click", abrirInformeCC)' in js
    # Cada fila muestra el plan con sus cuotas, lo que pagó y lo que debe.
    assert "plan.monto_por_cuota" in js or "planTexto(p)" in js
    assert "cuenta.total_creditos" in js
    assert "cuenta.saldo_actual" in js
    # Al pie, la deuda total de los pacientes y los totales del consultorio.
    assert "Deuda total de los pacientes" in js
    assert "datos.deuda_total" in js
    assert "datos.total_debitado" in js
    # Se puede imprimir: el informe sale en una pestaña con su propia hoja.
    assert "function imprimirInformeCC(" in js
    assert "ventana.print()" in js


def test_los_controles_de_la_cuenta_corriente_que_usa_el_js_existen_en_el_html(
    client,
):
    """Ningún ``getElementById`` de la pestaña apunta a un id inexistente."""
    html = client.get("/").text
    js = client.get("/static/app.js").text
    usados = set(re.findall(r'getElementById\("(cc-[a-z0-9-]+)"\)', js))
    assert usados, "app.js debe manejar los controles de la cuenta corriente"
    faltantes = sorted(i for i in usados if f'id="{i}"' not in html)
    assert not faltantes, f"ids usados por app.js sin HTML: {faltantes}"

    # El informe consolidado sigue la misma regla (ids ``informe-cc-*``).
    usados_informe = set(
        re.findall(r'getElementById\("((?:modal-)?informe-cc[a-z0-9-]*)"\)', js)
    )
    assert usados_informe, "app.js debe manejar el modal del informe"
    faltantes = sorted(i for i in usados_informe if f'id="{i}"' not in html)
    assert not faltantes, f"ids del informe usados por app.js sin HTML: {faltantes}"


def test_el_panel_y_sus_estaticos_se_revalidan_en_cada_carga(client):
    """El navegador no debe quedarse con una versión vieja de ``app.js``.

    ``StaticFiles`` no envía ``Cache-Control`` por sí solo y el navegador aplica
    su propia heurística. Cuando eso pasaba, el HTML nuevo mostraba una pestaña
    que el JS cacheado no conocía y el click no hacía nada: el panel y sus
    recursos se sirven con ``no-cache`` para revalidar contra el ``ETag``.
    """
    for ruta in ("/", "/medico", "/privacidad", "/eliminacion-datos"):
        r = client.get(ruta)
        assert r.status_code == 200, ruta
        assert "no-cache" in r.headers.get("cache-control", ""), ruta
    for ruta in ("/static/app.js", "/static/styles.css", "/static/calendar.js"):
        r = client.get(ruta)
        assert r.status_code == 200, ruta
        assert "no-cache" in r.headers.get("cache-control", ""), ruta


def test_la_ficha_de_salud_sigue_sin_guardarse_en_cache(client):
    """La excepción: la ficha pública del paciente no se cachea en absoluto."""
    r = client.get("/anamnesis/token-de-prueba")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "")


def test_el_panel_avisa_si_el_navegador_sirvio_un_javascript_viejo(client):
    """Un ``app.js`` cacheado no puede dejar las pestañas nuevas sin respuesta.

    La página compara los enlaces del menú con las vistas que conoce el JS
    cargado y, si falta alguna, muestra un aviso con un botón para recargar en
    lugar de dejar un menú que parece roto.
    """
    html = client.get("/").text
    assert 'aviso.id = "aviso-version"' in html
    assert "aviso-version-recargar" in html
    assert "comprobarVersion" in html
    assert "!app.VISTAS || !(v in app.VISTAS)" in html
    # El JS deja rastro en la consola si llega una vista desconocida.
    js = client.get("/static/app.js").text
    assert "Vista desconocida en esta versión del panel:" in js


def test_dashboard_incluye_el_modulo_de_borrado_de_movimientos(client):
    """La pestaña «Borrar movimientos» viaja en el menú con sus dos paneles.

    Es la interfaz de depuración: sin ella, borrar un cierre de caja o un
    movimiento del libro mayor sólo se podía hacer por API.
    """
    html = client.get("/").text
    assert 'data-view="borrar-movimientos"' in html
    assert "Borrar movimientos" in html
    assert 'id="view-borrar-movimientos"' in html
    # Panel de caja, panel de cuenta corriente y la advertencia de que no se
    # puede deshacer.
    for control in (
        "dep-caja-desde",
        "dep-caja-hasta",
        "dep-caja-buscar",
        "dep-caja-todos",
        "dep-caja-filas",
        "dep-caja-contador",
        "dep-caja-eliminar",
        "dep-cc-paciente",
        "dep-cc-desde",
        "dep-cc-hasta",
        "dep-cc-tipo",
        "dep-cc-todos",
        "dep-cc-filas",
        "dep-cc-contador",
        "dep-cc-eliminar",
        "dep-refrescar",
    ):
        assert f'id="{control}"' in html, control
    assert "No se puede deshacer" in html


def test_el_javascript_del_modulo_de_borrado_usa_los_delete_de_la_api(client):
    """Los botones «Eliminar» pegan contra los ``DELETE`` del backend."""
    js = client.get("/static/app.js").text
    # Caja: DELETE /movimientos-caja/{id} y libro mayor: DELETE /finanzas/...
    assert 'method: "DELETE"' in js
    assert "`${RUTA_CAJA}/${id}`" in js
    assert "/finanzas/cuenta-corriente/transacciones" in js
    assert "/finanzas/transacciones" in js
    # La vista se carga al entrar y tiene sus funciones de datos.
    assert 'v === "borrar-movimientos"' in js
    assert "function cargarModuloBorrado(" in js
    assert "function cargarCajaDep(" in js
    assert "function cargarTransaccionesDep(" in js
    # Antes de borrar se pide confirmación con el detalle del movimiento.
    assert "function borrarMovimientoCajaDep(" in js
    assert "function borrarTransaccionCCDep(" in js
    assert "¿Eliminar el cierre de caja #" in js
    assert "¿Eliminar este movimiento de la cuenta corriente?" in js
    # El resultado informa el saldo recalculado por el servidor.
    assert "datos.saldo_actual" in js
    assert "datos.cuenta_eliminada" in js
    # Se puede borrar de a uno o lo tildado en lote.
    assert "function borrarSeleccionadosDep(" in js
    assert "data-dep-borrar-caja" in js
    assert "data-dep-borrar-cc" in js


def test_los_controles_del_modulo_de_borrado_que_usa_el_js_existen_en_el_html(
    client,
):
    """Ningún ``getElementById`` del módulo de borrado apunta a un id inexistente."""
    html = client.get("/").text
    js = client.get("/static/app.js").text
    usados = set(re.findall(r'getElementById\("(dep-[a-z0-9-]+)"\)', js))
    assert usados, "app.js debe manejar los controles del módulo de borrado"
    faltantes = sorted(i for i in usados if f'id="{i}"' not in html)
    assert not faltantes, f"ids usados por app.js sin HTML: {faltantes}"



