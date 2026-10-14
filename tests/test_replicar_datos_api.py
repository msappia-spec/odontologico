"""Pruebas de ``scripts/replicar_datos_api.py``.

Se usa un **servidor HTTP falso** (``http.server`` en un hilo sobre
``127.0.0.1``) que implementa los mismos contratos que la API real, de modo que
las pruebas no tocan la red ni la base de producción.
"""

import json
import threading
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine

from app.db.base import Base
from scripts import replicar_datos_api as replicador
from scripts.replicar_datos_api import (
    URL_POR_DEFECTO,
    ErrorApi,
    _clave_nombre,
    _clave_paciente,
    _hhmm,
    _iso,
    main,
    replicar,
)
from tests.test_migracion_sqlite_a_postgres import _cargar_datos


class ApiFalsa(BaseHTTPRequestHandler):
    """Doble en memoria de la API (mismos contratos, sin base de datos)."""

    datos: dict = {}
    escrituras: int = 0
    fallar_en: str = ""
    rechazar_cancelados: bool = False

    def log_message(self, *args) -> None:  # pragma: no cover - silencia el log
        """Evita que cada petición ensucie la salida de pytest."""

    # --- utilidades ---------------------------------------------------------
    def _responder(self, codigo: int, cuerpo=None) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        if cuerpo is not None:
            self.wfile.write(json.dumps(cuerpo).encode("utf-8"))

    def _payload(self) -> dict:
        largo = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(largo).decode("utf-8")) if largo else {}

    def _crear(self, entidad: str, payload: dict) -> dict:
        registros = self.datos[entidad]
        nuevo = {"id": len(registros) + 1, **payload}
        registros.append(nuevo)
        type(self).escrituras += 1
        return nuevo

    @staticmethod
    def _doctor_id(ruta: str) -> int:
        partes = ruta.strip("/").split("/")
        return int(partes[3])

    # --- verbos -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - nombre exigido por http.server
        ruta = urlparse(self.path).path
        if ruta.endswith("/disponibilidad") and ruta.startswith("/api/v1/doctores/"):
            doctor_id = self._doctor_id(ruta)
            return self._responder(200, self.datos["agenda"].get(doctor_id, []))
        if ruta == "/api/v1/doctores":
            return self._responder(200, self.datos["doctores"])
        if ruta == "/api/v1/obras-sociales":
            return self._responder(200, self.datos["obras_sociales"])
        if ruta == "/api/v1/pacientes":
            return self._responder(200, self.datos["pacientes"])
        if ruta == "/api/v1/turnos":
            return self._responder(200, self.datos["turnos"])
        self._responder(404, {"detail": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802 - nombre exigido por http.server
        ruta = urlparse(self.path).path
        payload = self._payload()
        if self.fallar_en and self.fallar_en == f"POST {ruta}":
            return self._responder(500, {"detail": "falla simulada"})
        if ruta == "/api/v1/doctores":
            return self._responder(201, self._crear("doctores", payload))
        if ruta == "/api/v1/obras-sociales":
            return self._responder(201, self._crear("obras_sociales", payload))
        if ruta == "/api/v1/pacientes":
            return self._responder(201, self._crear("pacientes", payload))
        if ruta == "/api/v1/turnos":
            if self.rechazar_cancelados and payload.get("estado_turno") == "CANCELADO":
                return self._responder(409, {"detail": "horario ocupado"})
            return self._responder(201, self._crear("turnos", payload))
        self._responder(404, {"detail": "Not Found"})

    def do_PUT(self) -> None:  # noqa: N802 - nombre exigido por http.server
        ruta = urlparse(self.path).path
        if ruta.endswith("/disponibilidad") and ruta.startswith("/api/v1/doctores/"):
            doctor_id = self._doctor_id(ruta)
            self.datos["agenda"][doctor_id] = self._payload().get("rangos", [])
            type(self).escrituras += 1
            return self._responder(200, self.datos["agenda"][doctor_id])
        self._responder(404, {"detail": "Not Found"})

    def do_PATCH(self) -> None:  # noqa: N802 - nombre exigido por http.server
        partes = urlparse(self.path).path.strip("/").split("/")
        if len(partes) == 4 and partes[:3] == ["api", "v1", "turnos"]:
            turno_id = int(partes[3])
            payload = self._payload()
            for turno in self.datos["turnos"]:
                if turno["id"] == turno_id:
                    turno.update(payload)
                    type(self).escrituras += 1
                    return self._responder(200, turno)
            return self._responder(404, {"detail": "No existe el turno"})
        self._responder(404, {"detail": "Not Found"})


@pytest.fixture()
def api_falsa():
    """Servidor falso en un puerto libre; devuelve su URL base."""
    ApiFalsa.datos = {
        "doctores": [],
        "obras_sociales": [],
        "pacientes": [],
        "turnos": [],
        "agenda": {},
    }
    ApiFalsa.escrituras = 0
    ApiFalsa.fallar_en = ""
    ApiFalsa.rechazar_cancelados = False

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), ApiFalsa)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"http://127.0.0.1:{servidor.server_port}"
    finally:
        servidor.shutdown()
        servidor.server_close()
        hilo.join(timeout=5)


@pytest.fixture()
def origen_sqlite(tmp_path, monkeypatch) -> str:
    """Base SQLite temporal con datos, con ``settings`` apuntando ahí."""
    ruta = tmp_path / "origen.db"
    url = f"sqlite:///{ruta.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    _cargar_datos(engine)
    engine.dispose()

    monkeypatch.setattr(replicador.settings, "database_url", url)
    return url


def test_replicar_crea_todo_y_traduce_las_claves_foraneas(api_falsa, origen_sqlite):
    """Los registros se crean en orden y las referencias usan los id remotos."""
    resultado = replicar(api_falsa, ejecutar=True)

    assert resultado.errores == []
    assert resultado.salteados == {}
    assert resultado.creados == {
        "obras_sociales": 1,
        "doctores": 1,
        "pacientes": 2,
        "disponibilidad_doctores": 1,
        "turnos": 2,
    }
    assert resultado.origen.endswith("origen.db")

    datos = ApiFalsa.datos
    assert [doctor["nombre"] for doctor in datos["doctores"]] == ["Dra. Test"]
    assert datos["agenda"] == {
        1: [{"dia_semana": 1, "hora_inicio": "09:00", "hora_fin": "13:00"}]
    }
    # La paciente con cobertura quedó vinculada al id remoto del catálogo; la
    # que no tiene obra social no envía la clave.
    assert [p.get("obra_social_id") for p in datos["pacientes"]] == [1, None]
    assert [t["estado_turno"] for t in datos["turnos"]] == ["PENDIENTE", "CANCELADO"]
    assert [t["fecha_hora_inicio"] for t in datos["turnos"]] == [
        "2026-11-03T12:00:00",
        "2026-11-03T14:00:00",
    ]
    assert {t["doctor_id"] for t in datos["turnos"]} == {1}
    assert {t["paciente_id"] for t in datos["turnos"]} == {1, 2}


def test_simulacro_no_escribe_en_la_nube(api_falsa, origen_sqlite):
    resultado = replicar(api_falsa, ejecutar=False)

    assert resultado.total_creados == 7
    assert ApiFalsa.escrituras == 0
    assert ApiFalsa.datos["doctores"] == []
    assert ApiFalsa.datos["turnos"] == []


def test_segunda_corrida_no_duplica(api_falsa, origen_sqlite):
    replicar(api_falsa, ejecutar=True)
    segunda = replicar(api_falsa, ejecutar=True)

    assert segunda.errores == []
    # Sólo la agenda se reescribe: se copia con PUT (reemplazo total).
    assert segunda.total_creados == 1
    assert segunda.salteados == {
        "obras_sociales": 1,
        "doctores": 1,
        "pacientes": 2,
        "turnos": 2,
    }
    assert len(ApiFalsa.datos["doctores"]) == 1
    assert len(ApiFalsa.datos["pacientes"]) == 2
    assert len(ApiFalsa.datos["turnos"]) == 2


def test_turno_cancelado_se_reintenta_vigente_y_se_cancela(api_falsa, origen_sqlite):
    """Si la API rechaza crear un turno cancelado, se crea vigente y se cancela."""
    ApiFalsa.rechazar_cancelados = True

    resultado = replicar(api_falsa, ejecutar=True)

    assert resultado.errores == []
    assert len(ApiFalsa.datos["turnos"]) == 2
    assert [t["estado_turno"] for t in ApiFalsa.datos["turnos"]] == [
        "PENDIENTE",
        "CANCELADO",
    ]


def test_los_errores_de_la_api_se_reportan_sin_frenar_el_resto(api_falsa, origen_sqlite):
    ApiFalsa.fallar_en = "POST /api/v1/doctores"

    resultado = replicar(api_falsa, ejecutar=True)

    # 1 falla del POST, 1 de la agenda y 2 de los turnos (necesitan el doctor).
    assert len(resultado.errores) == 4
    assert "HTTP 500" in resultado.errores[0]
    assert ApiFalsa.datos["doctores"] == []
    assert len(ApiFalsa.datos["obras_sociales"]) == 1
    assert len(ApiFalsa.datos["pacientes"]) == 2
    assert ApiFalsa.datos["turnos"] == []


def test_cli_imprime_el_reporte(api_falsa, origen_sqlite, capsys):
    assert main(["--url", api_falsa, "--ejecutar"]) == 0

    salida = capsys.readouterr().out
    assert "EJECUCION" in salida
    assert "Total creado en la nube: 7" in salida
    assert "Nota: la agenda semanal se reemplaza con PUT" in salida


def test_cli_sin_ejecutar_avisa_que_es_simulacro(api_falsa, origen_sqlite, capsys):
    assert main(["--url", api_falsa]) == 0

    assert "SIMULACRO" in capsys.readouterr().out
    assert ApiFalsa.escrituras == 0


def test_la_url_por_defecto_es_la_de_produccion():
    assert URL_POR_DEFECTO == "https://odontologico-omega.vercel.app"


def test_claves_naturales_ignoran_mayusculas_y_espacios():
    assert _clave_nombre("  Dra.   Cecilia ") == "dra. cecilia"
    assert _clave_nombre(None) == ""
    assert _clave_paciente({"dni": "30123456", "telefono": "x"}) == "dni:30123456"
    assert _clave_paciente({"dni": None, "telefono": " +54911 "}) == "tel:+54911"


def test_formato_de_horas_y_fechas_para_la_api():
    assert _hhmm(time(9, 5)) == "09:05"
    assert _hhmm("09:05:00") == "09:05"
    assert _iso(datetime(2026, 11, 3, 12, 0)) == "2026-11-03T12:00:00"


def test_una_api_inalcanzable_es_un_error_claro(origen_sqlite):
    with pytest.raises(ErrorApi):
        replicar("http://127.0.0.1:1")
