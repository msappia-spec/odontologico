"""Pruebas de la interfaz web (dashboard de la secretaría).

Verifican que FastAPI sirve la página principal, que los recursos estáticos
están accesibles y que la API sigue operativa. No dependen de la base de datos.
"""


def test_dashboard_raiz_devuelve_html(client):
    """La raíz sirve el dashboard en HTML."""
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "OdontoCare" in r.text
    assert "/static/app.js" in r.text


def test_hoja_de_estilos_disponible(client):
    r = client.get("/static/styles.css")
    assert r.status_code == 200
    assert "--brand-500" in r.text


def test_javascript_disponible_y_apunta_a_la_api(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "/api/v1" in r.text


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


def test_los_estados_del_turno_tienen_color_en_el_frontend(client):
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


def test_el_panel_enlaza_la_vista_movil(client):
    """El menú del panel ofrece el acceso a la agenda móvil del odontólogo."""
    html = client.get("/").text
    assert 'href="/medico"' in html
    assert "Vista móvil" in html

