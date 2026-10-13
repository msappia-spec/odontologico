"""Contrato de la vista móvil del odontólogo (``/medico``).

La agenda del celular pasó de "sólo hoy" a un calendario consultable: se puede
navegar cualquier día, elegir pacientes ya registrados, editar un turno y
reprogramarlo. Estas pruebas atan el HTML, el JS y el CSS a esa funcionalidad —
igual que hace ``test_web.py`` con el panel— para que un cambio en el frontend
rompa la suite de pytest y no el celular del consultorio:

* navegador de fechas (flechas día/semana, tira de 7 días, «Hoy», selector);
* hoja de agendar/editar con buscador de fichas y campos de fecha/hora/estado;
* el PATCH de edición y el rango ``fecha_desde``/``fecha_hasta`` en el JS;
* los estilos nuevos (chips de día, fichas y atajos de horarios libres);
* que el JS no quede apuntando a ids o clases que el HTML/CSS ya no tienen.
"""

import re


def _html(client) -> str:
    r = client.get("/medico")
    assert r.status_code == 200, r.text
    return r.text


def _js(client) -> str:
    r = client.get("/static/medico.js")
    assert r.status_code == 200, r.text
    return r.text


def _css(client) -> str:
    r = client.get("/static/medico.css")
    assert r.status_code == 200, r.text
    return r.text


# --------------------------------------------------------------------------- #
# 1) El calendario permite consultar cualquier día
# --------------------------------------------------------------------------- #


def test_la_agenda_movil_navega_cualquier_dia_del_calendario(client):
    html = _html(client)

    # Flechas de día y de semana, tira con los siete días y salto directo.
    assert 'id="btn-dia-anterior"' in html
    assert 'id="btn-dia-siguiente"' in html
    assert 'id="btn-semana-anterior"' in html
    assert 'id="btn-semana-siguiente"' in html
    assert 'id="tira-semana"' in html
    assert 'id="btn-hoy"' in html
    assert 'id="input-fecha"' in html
    # La fecha visible se describe en dos líneas (día + «Hoy» / «Mañana»).
    assert 'id="m-fecha"' in html
    assert 'id="m-fecha-extra"' in html


def test_el_javascript_movil_pide_la_semana_por_rango_de_fechas(client):
    js = _js(client)

    # Una sola lectura cubre el día visible y los siete días de la tira.
    assert "fecha_desde=" in js
    assert "fecha_hasta=" in js
    assert "turnosSemana" in js and "turnosDia" in js
    # Utilidades de calendario en hora local (sin desfases por UTC).
    assert "function fechaDe(" in js
    assert "function sumarDias(" in js
    assert "function inicioSemana(" in js


# --------------------------------------------------------------------------- #
# 2) La hoja elige pacientes existentes y permite editar el turno
# --------------------------------------------------------------------------- #


def test_la_hoja_movil_busca_fichas_existentes_antes_de_crear_una(client):
    html = _html(client)

    assert 'id="nuevo-buscar"' in html
    assert 'type="search"' in html
    assert 'id="lista-pacientes"' in html
    assert 'id="paciente-elegido"' in html
    assert 'id="btn-cambiar-paciente"' in html
    assert 'id="btn-paciente-nuevo"' in html
    assert 'id="btn-buscar-existente"' in html
    assert 'id="bloque-paciente-nuevo"' in html

    js = _js(client)
    assert "function pacientesFiltrados(" in js
    assert "function elegirPaciente(" in js
    assert "function mostrarAltaPacienteNuevo(" in js
    assert "data-paciente=" in js          # cada ficha se elige por id
    assert "data-alta-nuevo" in js         # «registrar como paciente nuevo»


def test_la_hoja_movil_permite_reprogramar_fecha_hora_y_estado(client):
    html = _html(client)

    assert 'id="nuevo-fecha"' in html
    assert 'id="nuevo-hora"' in html
    assert 'id="nuevo-duracion"' in html
    assert 'id="nuevo-estado"' in html
    assert 'id="nuevo-libres"' in html     # atajos de horarios libres
    assert 'id="nuevo-turno-id"' in html   # identifica el turno en edición

    js = _js(client)
    assert "state.turnoEditId" in js
    assert "function isoInicioFin(" in js  # fin correcto si cruza la medianoche


def test_la_agenda_movil_edita_turnos_con_patch(client):
    """El lápiz del turno reusa ``PATCH /turnos/{id}`` de la API."""
    js = _js(client)
    html = _html(client)

    assert "data-editar=" in js
    assert "`/turnos/${state.turnoEditId}`" in js
    assert 'method: "PATCH"' in js
    assert '"PATCH"' in js and '"POST"' in js
    # El lápiz también aparece en los cancelados (permite reactivarlos).
    assert "m-editar" in js
    assert "lápiz" in html



# --------------------------------------------------------------------------- #
# 3) Los estilos de los elementos nuevos están definidos
# --------------------------------------------------------------------------- #


def test_los_estilos_del_calendario_movil_estan_definidos(client):
    css = _css(client)

    assert ".m-nav-dia" in css
    assert ".m-tira-semana" in css
    assert ".m-dia-chip" in css
    assert ".m-dia-chip-activo" in css
    assert ".m-dia-chip-punto" in css
    assert ".m-fecha-form" in css


def test_los_estilos_de_la_hoja_de_pacientes_estan_definidos(client):
    css = _css(client)

    assert ".m-lista-pacientes" in css
    assert ".m-ficha" in css
    assert ".m-elegido" in css
    assert ".m-alta" in css
    assert ".m-grilla-2" in css
    # Los botones de horario libre y el lápiz mantienen el objetivo táctil.
    assert ".m-chip-hora" in css
    assert ".m-acciones-turno" in css
    assert ".m-editar" in css


# --------------------------------------------------------------------------- #
# 4) El JS no busca elementos que ya no existen
# --------------------------------------------------------------------------- #


def test_el_js_solo_busca_ids_que_existen_en_el_html(client):
    """Un id borrado del HTML rompería la agenda en silencio: se verifica acá."""
    html = _html(client)
    js = _js(client)

    ids_html = set(re.findall(r'id="([^"]+)"', html))
    ids_js = set(re.findall(r'\$\("([^"]+)"\)', js))
    ids_js |= set(re.findall(r'getElementById\("([^"]+)"\)', js))

    assert ids_js, "el JS debería referenciar elementos por id"
    assert sorted(ids_js - ids_html) == []


def test_las_clases_del_js_tienen_estilo_en_el_css(client):
    """Las filas/hojas que construye el JS deben estar vestidas por el CSS."""
    js = _js(client)
    css = _css(client)

    clases_js = {
        clase
        for grupo in re.findall(r'class="([^"]+)"', js)
        for clase in grupo.split()
        if clase.startswith("m-")
    }
    clases_css = set(re.findall(r"\.(m-[a-z0-9-]+)", css))

    assert clases_js, "el JS debería construir elementos con clases propias"
    assert sorted(clases_js - clases_css) == []
