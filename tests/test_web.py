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


def test_dashboard_incluye_la_vista_de_disponibilidad(client):
    """La pestaña "Disponibilidad" y su editor semanal viajan en el HTML servido."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'data-view="disponibilidad"' in r.text
    assert 'id="view-disponibilidad"' in r.text
    assert 'id="disp-semana"' in r.text
    assert 'id="disp-doctor"' in r.text


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
