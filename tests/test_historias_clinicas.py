"""Pruebas del Módulo 2: historia clínica y evoluciones médicas.

Cubren las tres promesas del módulo:

1. **Alta**: se registra una evolución médica, la ficha clínica del paciente se
   crea sola con su ``numero_ficha`` (``HC-000123``) y se reutiliza después.
2. **Cronología**: la línea de tiempo se lee de la atención **más nueva a la más
   vieja**, con el odontólogo que atendió y los archivos adjuntos.
3. **Inmutabilidad** (normativa de salud): la API **no expone** ``PUT``,
   ``PATCH`` ni ``DELETE`` para las evoluciones (responden ``405``), el servicio
   no tiene funciones de edición/baja y el ORM bloquea cualquier ``UPDATE`` o
   ``DELETE`` sobre una fila ya guardada.

Además se controla la **búsqueda de pacientes** (nombre sin acentos o DNI con
puntos) y que la vista del odontólogo y el Panel Principal de la secretaría
tengan la sección de historia clínica (el panel, en modo sólo lectura).
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.datetime_utils import utcnow
from app.core.exceptions import RegistroInmutableError
from app.main import app
from app.models.archivo_adjunto import ArchivoAdjunto
from app.models.evolucion_medica import EvolucionMedica
from app.models.historia_clinica import HistoriaClinica
from app.services.historia_clinica_service import numero_ficha_de
from app.web.router import STATIC_DIR

API = "/api/v1"


def _leer_estatico(nombre: str) -> str:
    """Devuelve el contenido de un recurso de ``app/static``."""
    return (Path(STATIC_DIR) / nombre).read_text(encoding="utf-8")


def _ruta_evoluciones(paciente_id: int) -> str:
    return f"{API}/pacientes/{paciente_id}/evoluciones"


def _ruta_historia(paciente_id: int) -> str:
    return f"{API}/pacientes/{paciente_id}/historia-clinica"


def _crear_paciente(client, **overrides) -> dict:
    """Crea un paciente por API y devuelve su representación."""
    payload = {"nombre": "Ana Suárez", "telefono": "+5491155667788"}
    payload.update(overrides)
    r = client.post(f"{API}/pacientes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _crear_evolucion(client, paciente_id: int, **overrides) -> dict:
    """Registra una evolución médica por API y devuelve su representación."""
    payload = {
        "motivo_consulta": "Dolor en el molar inferior derecho",
        "diagnostico": "Pulpitis irreversible",
        "tratamiento_efectuado": "Endodoncia",
    }
    payload.update(overrides)
    r = client.post(_ruta_evoluciones(paciente_id), json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _contar(db_session, modelo) -> int:
    """Cantidad de filas de un modelo."""
    return int(db_session.execute(select(func.count()).select_from(modelo)).scalar())


# ---------------------------------------------------------------------------
# 1) Alta de evoluciones y ficha clínica
# ---------------------------------------------------------------------------


def test_crear_evolucion_crea_la_ficha_clinica_del_paciente(client, doctor, paciente):
    """La primera evolución abre la ficha del paciente y devuelve el registro."""
    creada = _crear_evolucion(client, paciente.id, doctor_id=doctor.id)

    assert creada["id"] > 0
    assert creada["doctor_id"] == doctor.id
    assert creada["doctor_nombre"] == doctor.nombre
    assert creada["doctor_especialidad"] == doctor.especialidad
    assert creada["motivo_consulta"] == "Dolor en el molar inferior derecho"
    assert creada["diagnostico"] == "Pulpitis irreversible"
    assert creada["tratamiento_efectuado"] == "Endodoncia"
    assert creada["observaciones"] is None
    assert creada["archivos"] == []

    historia = client.get(_ruta_historia(paciente.id))
    assert historia.status_code == 200, historia.text
    detalle = historia.json()
    assert detalle["id"] == creada["historia_clinica_id"]
    assert detalle["paciente_id"] == paciente.id
    assert detalle["numero_ficha"] == numero_ficha_de(paciente.id)
    assert detalle["total_evoluciones"] == 1


def test_la_ficha_se_reutiliza_en_las_siguientes_evoluciones(
    client, db_session, doctor, paciente
):
    """Varias atenciones del mismo paciente cuelgan de una única ficha."""
    primera = _crear_evolucion(client, paciente.id, doctor_id=doctor.id)
    segunda = _crear_evolucion(
        client, paciente.id, doctor_id=doctor.id, fecha_atencion="2026-02-01T10:00:00"
    )

    assert primera["historia_clinica_id"] == segunda["historia_clinica_id"]
    assert _contar(db_session, HistoriaClinica) == 1
    assert _contar(db_session, EvolucionMedica) == 2
    assert client.get(_ruta_historia(paciente.id)).json()["total_evoluciones"] == 2


def test_los_textos_se_limpian_y_las_observaciones_vacias_son_nulas(
    client, doctor, paciente
):
    """Los campos clínicos viajan recortados y ``""`` en observaciones es ``NULL``."""
    creada = _crear_evolucion(
        client,
        paciente.id,
        doctor_id=doctor.id,
        motivo_consulta="   Control post operatorio   ",
        observaciones="   ",
    )

    assert creada["motivo_consulta"] == "Control post operatorio"
    assert creada["observaciones"] is None


def test_faltan_campos_clinicos_devuelve_422(client, doctor, paciente):
    """El motivo, el diagnóstico y el tratamiento son obligatorios."""
    r = client.post(_ruta_evoluciones(paciente.id), json={"doctor_id": doctor.id})

    assert r.status_code == 422
    detalle = str(r.json())
    for campo in ("motivo_consulta", "diagnostico", "tratamiento_efectuado"):
        assert campo in detalle


def test_un_campo_clinico_en_blanco_devuelve_422(client, doctor, paciente):
    """Un texto obligatorio con sólo espacios no es una evolución válida."""
    r = client.post(
        _ruta_evoluciones(paciente.id),
        json={
            "doctor_id": doctor.id,
            "motivo_consulta": "    ",
            "diagnostico": "Caries",
            "tratamiento_efectuado": "Obturación",
        },
    )

    assert r.status_code == 422
    assert "motivo_consulta no puede estar vacío" in str(r.json())


def test_evolucion_con_paciente_o_doctor_inexistente_devuelve_404(
    client, doctor, paciente
):
    """No hay atención sin paciente ni odontólogo reales detrás."""
    sin_paciente = client.post(
        _ruta_evoluciones(9999),
        json={
            "doctor_id": doctor.id,
            "motivo_consulta": "Control",
            "diagnostico": "Sano",
            "tratamiento_efectuado": "Ninguno",
        },
    )
    assert sin_paciente.status_code == 404

    sin_doctor = client.post(
        _ruta_evoluciones(paciente.id),
        json={
            "doctor_id": 9999,
            "motivo_consulta": "Control",
            "diagnostico": "Sano",
            "tratamiento_efectuado": "Ninguno",
        },
    )
    assert sin_doctor.status_code == 404


def test_la_ficha_no_se_crea_al_consultar_un_paciente_sin_evoluciones(
    client, db_session, paciente
):
    """La consulta del historial es de sólo lectura: no inventa una ficha vacía."""
    r = client.get(_ruta_historia(paciente.id))

    assert r.status_code == 200, r.text
    detalle = r.json()
    assert detalle["id"] is None
    assert detalle["numero_ficha"] == numero_ficha_de(paciente.id)
    assert detalle["total_evoluciones"] == 0
    assert detalle["evoluciones"] == []
    assert _contar(db_session, HistoriaClinica) == 0
    assert _contar(db_session, EvolucionMedica) == 0

    listado = client.get(_ruta_evoluciones(paciente.id))
    assert listado.status_code == 200
    assert listado.json() == []


def test_historia_de_un_paciente_inexistente_devuelve_404(client):
    assert client.get(_ruta_historia(9999)).status_code == 404
    assert client.get(_ruta_evoluciones(9999)).status_code == 404


def test_el_numero_de_ficha_se_deriva_del_paciente():
    """La ficha visible es determinística: ``HC-`` + id del paciente a 6 dígitos."""
    assert numero_ficha_de(1) == "HC-000001"
    assert numero_ficha_de(123) == "HC-000123"


# ---------------------------------------------------------------------------
# 2) Cronología, fechas y adjuntos
# ---------------------------------------------------------------------------


def test_la_linea_de_tiempo_va_de_la_mas_nueva_a_la_mas_vieja(client, doctor, paciente):
    """Las evoluciones se listan en orden cronológico inverso (atención más reciente primero)."""
    _crear_evolucion(
        client, paciente.id, doctor_id=doctor.id, fecha_atencion="2026-01-10T09:00:00"
    )
    _crear_evolucion(
        client, paciente.id, doctor_id=doctor.id, fecha_atencion="2026-03-05T16:30:00"
    )
    _crear_evolucion(
        client, paciente.id, doctor_id=doctor.id, fecha_atencion="2026-02-01T11:15:00"
    )

    listado = client.get(_ruta_evoluciones(paciente.id))
    assert listado.status_code == 200, listado.text
    fechas = [ev["fecha_atencion"] for ev in listado.json()]
    assert fechas == [
        "2026-03-05T16:30:00",
        "2026-02-01T11:15:00",
        "2026-01-10T09:00:00",
    ]

    # El detalle de la ficha (lo que consumen las dos pantallas) usa el mismo orden.
    detalle = client.get(_ruta_historia(paciente.id)).json()
    assert [ev["id"] for ev in detalle["evoluciones"]] == [
        ev["id"] for ev in listado.json()
    ]
    assert detalle["total_evoluciones"] == 3


def test_las_evoluciones_del_mismo_dia_se_ordenan_por_carga(client, doctor, paciente):
    """Con la misma fecha, el desempate es el orden de carga (la última, arriba)."""
    primera = _crear_evolucion(
        client, paciente.id, doctor_id=doctor.id, fecha_atencion="2026-04-02T08:00:00"
    )
    segunda = _crear_evolucion(
        client, paciente.id, doctor_id=doctor.id, fecha_atencion="2026-04-02T08:00:00"
    )

    ids = [ev["id"] for ev in client.get(_ruta_evoluciones(paciente.id)).json()]
    assert ids == [segunda["id"], primera["id"]]


def test_la_fecha_de_atencion_por_defecto_es_utc_naive(client, doctor, paciente):
    """Sin fecha explícita se guarda el momento del alta, en UTC naive."""
    creada = _crear_evolucion(client, paciente.id, doctor_id=doctor.id)

    fecha = datetime.fromisoformat(creada["fecha_atencion"])
    assert fecha.tzinfo is None
    assert abs(fecha - utcnow()) < timedelta(minutes=5)


def test_la_fecha_de_atencion_con_zona_se_normaliza_a_utc(client, doctor, paciente):
    """Una fecha con offset se guarda en UTC naive (convención del proyecto)."""
    creada = _crear_evolucion(
        client,
        paciente.id,
        doctor_id=doctor.id,
        fecha_atencion="2026-03-10T14:30:00-03:00",
    )

    assert creada["fecha_atencion"] == "2026-03-10T17:30:00"


def test_los_archivos_adjuntos_viajan_con_la_evolucion(client, db_session, doctor, paciente):
    """Las radiografías de la atención se guardan con la evolución y se listan."""
    creada = _crear_evolucion(
        client,
        paciente.id,
        doctor_id=doctor.id,
        archivos=[
            {"url_archivo": "https://estudios.local/rx-1082.png", "tipo_archivo": "foto"},
            {"url_archivo": "https://estudios.local/panoramica-1082.jpg"},
        ],
    )

    archivos = creada["archivos"]
    assert [a["tipo_archivo"] for a in archivos] == ["FOTO", "RADIOGRAFIA"]
    assert archivos[0]["url_archivo"] == "https://estudios.local/rx-1082.png"
    assert all(a["fecha_subida"] for a in archivos)
    assert _contar(db_session, ArchivoAdjunto) == 2

    detalle = client.get(_ruta_historia(paciente.id)).json()
    assert len(detalle["evoluciones"][0]["archivos"]) == 2


def test_la_lista_de_evoluciones_se_puede_paginar(client, doctor, paciente):
    """``skip``/``limit`` acotan la línea de tiempo sin romper el orden."""
    for dia in ("2026-05-01", "2026-05-02", "2026-05-03"):
        _crear_evolucion(
            client,
            paciente.id,
            doctor_id=doctor.id,
            fecha_atencion=f"{dia}T09:00:00",
        )

    r = client.get(_ruta_evoluciones(paciente.id), params={"skip": 1, "limit": 1})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    assert r.json()[0]["fecha_atencion"] == "2026-05-02T09:00:00"


def test_obtener_una_evolucion_puntual(client, doctor, paciente):
    """El detalle de una evolución se puede leer por su id (sólo lectura)."""
    creada = _crear_evolucion(client, paciente.id, doctor_id=doctor.id)

    r = client.get(f"{API}/evoluciones/{creada['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["diagnostico"] == "Pulpitis irreversible"
    assert r.json()["doctor_nombre"] == doctor.nombre

    assert client.get(f"{API}/evoluciones/9999").status_code == 404


# ---------------------------------------------------------------------------
# 3) Regla de inmutabilidad: sin PUT, PATCH ni DELETE
# ---------------------------------------------------------------------------

# Rutas del módulo (plantilla del OpenAPI): ninguna puede aceptar escritura más
# allá del ``POST`` de alta.
RUTAS_DEL_MODULO = (
    "/api/v1/pacientes/{paciente_id}/evoluciones",
    "/api/v1/pacientes/{paciente_id}/historia-clinica",
    "/api/v1/evoluciones/{evolucion_id}",
)


def test_la_api_no_declara_rutas_de_modificacion_para_la_historia_clinica():
    """El esquema de la API sólo admite ``GET`` y ``POST`` en el módulo."""
    rutas = app.openapi()["paths"]

    for ruta in RUTAS_DEL_MODULO:
        assert set(rutas[ruta]) <= {"get", "post"}, ruta
        assert not {"put", "patch", "delete"} & set(rutas[ruta]), ruta

    # Barrido completo: ninguna ruta de la historia clínica expone escritura
    # (ni ahora ni si algún día se agrega otra).
    for ruta, metodos in rutas.items():
        if "evolucion" in ruta or "historia-clinica" in ruta:
            assert not {"put", "patch", "delete"} & set(metodos), ruta


def test_editar_o_borrar_una_evolucion_por_la_api_responde_405(
    client, doctor, paciente
):
    """Cualquier intento de modificar o borrar un registro clínico se rechaza."""
    creada = _crear_evolucion(client, paciente.id, doctor_id=doctor.id)
    item = f"{API}/evoluciones/{creada['id']}"
    coleccion = _ruta_evoluciones(paciente.id)

    for metodo in (client.put, client.patch, client.delete):
        assert metodo(item).status_code == 405
        assert metodo(coleccion).status_code == 405
        assert metodo(
            f"{API}/pacientes/{paciente.id}/historia-clinica"
        ).status_code == 405

    # Tampoco existe una ruta anidada de la evolución dentro de la colección.
    assert client.delete(f"{coleccion}/{creada['id']}").status_code == 404

    # El registro sigue intacto y la línea de tiempo no cambió.
    assert client.get(item).status_code == 200
    detalle = client.get(_ruta_historia(paciente.id)).json()
    assert detalle["total_evoluciones"] == 1


def test_el_orm_tambien_bloquea_editar_y_borrar_una_evolucion(
    db_session, doctor, paciente
):
    """Última red: aunque se salga de la API, el ORM no deja tocar el registro."""
    historia = HistoriaClinica(
        paciente_id=paciente.id, numero_ficha=numero_ficha_de(paciente.id)
    )
    db_session.add(historia)
    db_session.flush()
    evolucion = EvolucionMedica(
        historia_clinica_id=historia.id,
        doctor_id=doctor.id,
        motivo_consulta="Control",
        diagnostico="Sano",
        tratamiento_efectuado="Ninguno",
    )
    db_session.add(evolucion)
    db_session.commit()
    evolucion_id = evolucion.id

    evolucion.diagnostico = "Otro diagnóstico"
    with pytest.raises(RegistroInmutableError):
        db_session.commit()
    db_session.rollback()

    with pytest.raises(RegistroInmutableError):
        db_session.delete(db_session.get(EvolucionMedica, evolucion_id))
        db_session.commit()
    db_session.rollback()

    guardada = db_session.get(EvolucionMedica, evolucion_id)
    assert guardada is not None
    assert guardada.diagnostico == "Sano"
    assert guardada.motivo_consulta == "Control"


def test_una_evolucion_no_se_puede_dirigir_a_la_ficha_de_otro_paciente(
    client, doctor, paciente
):
    """La ficha la decide la URL: el payload no puede apuntar a otro paciente."""
    otro = _crear_paciente(
        client, nombre="Beto Gómez", telefono="+5491100000001", dni="20000001"
    )
    de_otro = _crear_evolucion(client, otro["id"], doctor_id=doctor.id)

    # Se intenta "colar" la ficha del otro paciente (y su id) en el payload: la
    # evolución debe caer igual en la ficha del paciente de la URL.
    r = client.post(
        _ruta_evoluciones(paciente.id),
        json={
            "doctor_id": doctor.id,
            "motivo_consulta": "Control",
            "diagnostico": "Sano",
            "tratamiento_efectuado": "Ninguno",
            "historia_clinica_id": de_otro["historia_clinica_id"],
            "paciente_id": otro["id"],
        },
    )
    assert r.status_code == 201, r.text
    creada = r.json()

    assert creada["historia_clinica_id"] != de_otro["historia_clinica_id"]
    assert len(client.get(_ruta_evoluciones(paciente.id)).json()) == 1
    # La ficha del otro paciente quedó con su propia evolución, intacta.
    assert len(client.get(_ruta_evoluciones(otro["id"])).json()) == 1


def test_el_detalle_de_la_ficha_muestra_los_datos_del_paciente(client, doctor):
    """La ficha lleva el nombre y el DNI normalizado del paciente."""
    paciente = _crear_paciente(
        client, nombre="Ana Suárez", telefono="+5491155667788", dni="30.123.456"
    )
    _crear_evolucion(client, paciente["id"], doctor_id=doctor.id)

    detalle = client.get(_ruta_historia(paciente["id"])).json()
    assert detalle["paciente_nombre"] == "Ana Suárez"
    assert detalle["paciente_dni"] == "30123456"
    assert detalle["numero_ficha"] == numero_ficha_de(paciente["id"])


# ---------------------------------------------------------------------------
# 4) Búsqueda de pacientes por nombre o DNI (la consumen las dos pantallas)
# ---------------------------------------------------------------------------


def test_buscar_pacientes_por_nombre_ignora_acentos_y_mayusculas(client):
    """``suarez`` encuentra a *Suárez*: el nombre se compara sin acentos."""
    _crear_paciente(
        client, nombre="Ana Suárez", telefono="+5491100000011", dni="30123456"
    )
    _crear_paciente(
        client, nombre="Beto Gómez", telefono="+5491100000022", dni="20000001"
    )

    def nombres(consulta: str) -> list[str]:
        r = client.get(f"{API}/pacientes", params={"buscar": consulta})
        assert r.status_code == 200, r.text
        return [p["nombre"] for p in r.json()]

    assert nombres("suarez") == ["Ana Suárez"]
    assert nombres("SUÁREZ") == ["Ana Suárez"]
    assert nombres("gomez") == ["Beto Gómez"]
    assert nombres("zzz") == []
    # Sin búsqueda (o con la cadena vacía) se sigue listando todo.
    assert len(nombres("")) == 2


def test_buscar_pacientes_por_dni_ignora_puntos_y_guiones(client):
    """El documento se busca parcialmente y sin separadores."""
    _crear_paciente(
        client, nombre="Ana Suárez", telefono="+5491100000011", dni="30.123.456"
    )
    _crear_paciente(
        client, nombre="Beto Gómez", telefono="+5491100000022", dni="20000001"
    )

    def encontrados(consulta: str) -> list[str]:
        r = client.get(f"{API}/pacientes", params={"buscar": consulta})
        assert r.status_code == 200, r.text
        return [p["nombre"] for p in r.json()]

    assert encontrados("30.123.456") == ["Ana Suárez"]
    assert encontrados("301234") == ["Ana Suárez"]
    assert encontrados("2000") == ["Beto Gómez"]
    assert encontrados("99.999.999") == []


# ---------------------------------------------------------------------------
# 5) Interfaz: celular del odontólogo (alta) y panel de la secretaría (lectura)
# ---------------------------------------------------------------------------


def test_la_vista_del_medico_permite_buscar_y_cargar_evoluciones():
    """El celular del odontólogo trae el buscador, la línea de tiempo y el formulario."""
    html = _leer_estatico("medico.html")
    js = _leer_estatico("medico.js")

    # Botón de acceso, hoja de búsqueda y pantalla de la historia clínica.
    assert 'id="btn-historia-clinica"' in html
    assert 'id="hoja-historia"' in html
    assert 'id="hist-buscar"' in html
    assert 'id="hist-resultados"' in html
    assert 'id="pantalla-historia"' in html
    assert 'id="hist-timeline"' in html

    # Formulario de la nueva evolución (motivo, diagnóstico, tratamiento).
    assert 'id="form-evolucion"' in html
    for campo in ("evo-motivo", "evo-diagnostico", "evo-tratamiento", "evo-observaciones"):
        assert f'id="{campo}"' in html

    # Lógica: busca pacientes por nombre o DNI y crea la evolución con POST.
    assert "historia-clinica" in js
    assert "/evoluciones" in js
    assert 'method: "POST"' in js
    assert "data-hist-paciente" in js


def test_el_panel_de_la_secretaria_consulta_la_historia_en_solo_lectura():
    """El Panel Principal muestra la línea de tiempo, pero no puede escribir."""
    html = _leer_estatico("index.html")
    js = _leer_estatico("app.js")

    assert "Historias clínicas" in html
    assert 'id="hist-panel-buscar"' in html
    assert 'id="hist-panel-resultados"' in html
    assert 'id="hist-panel-timeline"' in html

    assert "historia-clinica" in js
    assert "data-hist-paciente" in js
    assert "data-historia=" in js
    # Lectura estricta: el panel no llama al endpoint de alta de evoluciones.
    assert "/evoluciones" not in js




