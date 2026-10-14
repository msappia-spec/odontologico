"""Copia los datos locales a la app desplegada usando su **API HTTP**.

Contexto
--------
``scripts/migrar_sqlite_a_postgres`` copia la base local a PostgreSQL, pero
necesita la cadena de conexión del destino. Cuando esa cadena no está a mano
—por ejemplo si el proveedor la guardó como *sensitive* en Vercel y
``vercel env pull`` devuelve ``[SENSITIVE]``— queda la vía de la **API pública**
de la app desplegada, que es la que ya escribe en la base de la nube.

Qué hace
--------
1. Lee la base local (SQLite) con los modelos del proyecto.
2. Lee lo que ya existe en la nube con ``GET`` (doctores, obras sociales,
   pacientes y turnos).
3. Crea sólo lo que falta y **nunca** actualiza ni borra registros existentes.
   Como los ``id`` los asigna el servidor, el emparejamiento es por clave
   **natural**: nombre de la obra social y del odontólogo, documento o teléfono
   del paciente, y odontólogo + fecha/hora de inicio del turno.
4. Traduce las claves foráneas: cada paciente, agenda y turno se crea con los
   ``id`` que devolvió la API en el paso anterior.
5. Copia la agenda semanal con ``PUT .../disponibilidad`` (deja la semana del
   odontólogo igual a la local) y conserva el ``estado_turno`` y el
   ``estado_whatsapp`` de cada turno.

Sin ``--ejecutar`` es un **simulacro** (*dry run*): informa qué crearía y no
escribe nada.

Limitaciones: no replica las excepciones por fecha
(``disponibilidad_especifica``, se cargan desde la pestaña *Disponibilidad* del
panel) ni los ``whatsapp_mensaje_id`` de los turnos.

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.replicar_datos_api
    python -m scripts.replicar_datos_api --url https://odontologico-omega.vercel.app
    python -m scripts.replicar_datos_api --ejecutar

Controles: ``tests/test_replicar_datos_api.py`` (servidor HTTP falso, sin red).
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Callable, Optional

from app.core.config import settings
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.doctor import Doctor
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.models.turno import Turno
from scripts.migrar_sqlite_a_postgres import _crear_engine, _filas, _sesion

# App desplegada que se usa cuando no se pasa ``--url``.
URL_POR_DEFECTO = "https://odontologico-omega.vercel.app"
TIEMPO_ESPERA = 60


class ErrorApi(RuntimeError):
    """Respuesta de error de la API (incluye el detalle devuelto por el backend)."""


@dataclass
class Resultado:
    """Conteos de una corrida de la replicación (por entidad)."""

    url: str
    ejecutado: bool
    origen: str = ""
    locales: dict[str, int] = field(default_factory=dict)
    creados: dict[str, int] = field(default_factory=dict)
    salteados: dict[str, int] = field(default_factory=dict)
    errores: list[str] = field(default_factory=list)

    @property
    def total_creados(self) -> int:
        """Total de registros creados en la nube."""
        return sum(self.creados.values())


@dataclass
class Remoto:
    """Registros que ya existen en la nube, indexados por clave natural."""

    obras: dict[str, int] = field(default_factory=dict)
    doctores: dict[str, int] = field(default_factory=dict)
    pacientes: dict[str, int] = field(default_factory=dict)
    turnos: dict[tuple, int] = field(default_factory=dict)



def _pedir(url: str, metodo: str = "GET", payload: Any = None) -> Any:
    """Ejecuta una petición JSON y devuelve el cuerpo decodificado."""
    datos = None
    if payload is not None:
        datos = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    peticion = urllib.request.Request(
        url,
        data=datos,
        method=metodo,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(peticion, timeout=TIEMPO_ESPERA) as respuesta:
            cuerpo = respuesta.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "replace")
        raise ErrorApi(f"{metodo} {url} -> HTTP {error.code}: {detalle}") from error
    except urllib.error.URLError as error:
        raise ErrorApi(f"{metodo} {url} -> {error.reason}") from error

    return json.loads(cuerpo) if cuerpo else None


def _clave_nombre(texto: Any) -> str:
    """Clave natural de un nombre (sin espacios sobrantes ni mayúsculas)."""
    return " ".join(str(texto or "").split()).casefold()


def _clave_paciente(fila: dict) -> str:
    """Clave natural de un paciente: el documento si lo tiene, si no el teléfono."""
    dni = str(fila.get("dni") or "").strip()
    if dni:
        return f"dni:{dni}"
    return f"tel:{str(fila.get('telefono') or '').strip()}"


def _iso(valor: Any) -> str:
    """Fecha/hora en el mismo formato ISO que devuelve la API."""
    if isinstance(valor, (datetime, date, time)):
        return valor.isoformat()
    return str(valor)


def _hhmm(valor: Any) -> str:
    """Hora como ``HH:MM``, el formato del editor de disponibilidad."""
    if isinstance(valor, time):
        return valor.strftime("%H:%M")
    return str(valor)[:5]


def _leer_remoto(base: str) -> Remoto:
    """Lee con ``GET`` lo que ya existe en la app desplegada."""
    return Remoto(
        obras={
            _clave_nombre(fila["nombre"]): fila["id"]
            for fila in _pedir(f"{base}/api/v1/obras-sociales") or []
        },
        doctores={
            _clave_nombre(fila["nombre"]): fila["id"]
            for fila in _pedir(f"{base}/api/v1/doctores") or []
        },
        pacientes={
            _clave_paciente(fila): fila["id"]
            for fila in _pedir(f"{base}/api/v1/pacientes") or []
        },
        turnos={
            (fila["doctor_id"], _iso(fila["fecha_hora_inicio"])): fila["id"]
            for fila in _pedir(f"{base}/api/v1/turnos?limit=500") or []
        },
    )


def replicar(url: str = URL_POR_DEFECTO, *, ejecutar: bool = False) -> Resultado:
    """Copia a ``url`` lo que falte de la base local y devuelve el resultado.

    Sin ``ejecutar=True`` es un simulacro: recorre y cuenta, pero no envía
    ningún ``POST``/``PUT``.
    """
    base = url.rstrip("/")
    resultado = Resultado(url=base, ejecutado=ejecutar)

    engine = _crear_engine(settings.database_url)
    sesion = _sesion(engine)
    try:
        resultado.origen = engine.url.render_as_string(hide_password=True)
        obras_local = _filas(sesion, ObraSocial.__table__)
        doctores_local = _filas(sesion, Doctor.__table__)
        pacientes_local = _filas(sesion, Paciente.__table__)
        agenda_local = _filas(sesion, DisponibilidadDoctor.__table__)
        turnos_local = _filas(sesion, Turno.__table__)
    finally:
        sesion.close()
        engine.dispose()

    resultado.locales = {
        "doctores": len(doctores_local),
        "obras_sociales": len(obras_local),
        "pacientes": len(pacientes_local),
        "disponibilidad_doctores": len(agenda_local),
        "turnos": len(turnos_local),
    }

    remoto = _leer_remoto(base)

    # 1) Obras sociales: van primero porque los pacientes las referencian.
    _sincronizar(
        resultado,
        "obras_sociales",
        obras_local,
        lambda fila: _clave_nombre(fila["nombre"]),
        remoto.obras,
        lambda fila: _pedir(
            f"{base}/api/v1/obras-sociales",
            "POST",
            {
                "nombre": fila["nombre"],
                "codigo": fila["codigo"],
                "activa": bool(fila["activa"]),
            },
        )["id"],
    )

    # 2) Doctores: los referencian la agenda semanal y los turnos.
    _sincronizar(
        resultado,
        "doctores",
        doctores_local,
        lambda fila: _clave_nombre(fila["nombre"]),
        remoto.doctores,
        lambda fila: _pedir(
            f"{base}/api/v1/doctores",
            "POST",
            {
                "nombre": fila["nombre"],
                "especialidad": fila["especialidad"],
                "activo": bool(fila["activo"]),
            },
        )["id"],
    )

    # 3) Pacientes: la cobertura se resuelve por el id remoto del catálogo y, si
    #    la obra social local no llegó a copiarse, por su nombre como texto libre
    #    (el backend la vincula al catálogo o la crea).
    nombre_obra_local = {fila["id"]: fila["nombre"] for fila in obras_local}
    obra_remota_local = {
        fila["id"]: remoto.obras[_clave_nombre(fila["nombre"])]
        for fila in obras_local
        if _clave_nombre(fila["nombre"]) in remoto.obras
        and remoto.obras[_clave_nombre(fila["nombre"])] is not None
    }

    def _payload_paciente(fila: dict) -> dict:
        obra_local = fila["obra_social_id"]
        cobertura: dict = {}
        if obra_local in obra_remota_local:
            cobertura = {"obra_social_id": obra_remota_local[obra_local]}
        elif obra_local is not None:
            cobertura = {"obra_social": nombre_obra_local.get(obra_local)}
        return {
            "nombre": fila["nombre"],
            "dni": fila["dni"],
            "telefono": fila["telefono"],
            "email": fila["email"],
            "numero_afiliado": fila["numero_afiliado"],
            **cobertura,
        }

    _sincronizar(
        resultado,
        "pacientes",
        pacientes_local,
        _clave_paciente,
        remoto.pacientes,
        lambda fila: _pedir(
            f"{base}/api/v1/pacientes", "POST", _payload_paciente(fila)
        )["id"],
    )

    # 4) Agenda semanal: se reemplaza la semana de cada odontólogo por la local.
    nombre_doctor_local = {fila["id"]: fila["nombre"] for fila in doctores_local}
    for doctor_local_id, nombre in nombre_doctor_local.items():
        clave_doctor = _clave_nombre(nombre)
        if clave_doctor not in remoto.doctores:
            resultado.errores.append(
                f"La agenda de {nombre!r} no se copió: falta el odontólogo en la nube."
            )
            continue
        rangos = [
            {
                "dia_semana": fila["dia_semana"],
                "hora_inicio": _hhmm(fila["hora_inicio"]),
                "hora_fin": _hhmm(fila["hora_fin"]),
            }
            for fila in agenda_local
            if fila["doctor_id"] == doctor_local_id
        ]
        if ejecutar:
            try:
                _pedir(
                    f"{base}/api/v1/doctores/"
                    f"{remoto.doctores[clave_doctor]}/disponibilidad",
                    "PUT",
                    {"rangos": rangos},
                )
            except ErrorApi as error:
                resultado.errores.append(str(error))
                continue
        resultado.creados["disponibilidad_doctores"] = (
            resultado.creados.get("disponibilidad_doctores", 0) + len(rangos)
        )

    # 5) Turnos: el payload usa los id remotos del paciente y del odontólogo.
    paciente_remoto_local = {
        fila["id"]: remoto.pacientes.get(_clave_paciente(fila))
        for fila in pacientes_local
    }

    def _clave_turno(fila: dict) -> tuple:
        doctor_remoto = remoto.doctores.get(
            _clave_nombre(nombre_doctor_local.get(fila["doctor_id"], ""))
        )
        return (doctor_remoto, _iso(fila["fecha_hora_inicio"]))

    def _crear_turno(fila: dict) -> int:
        doctor_remoto, inicio = _clave_turno(fila)
        paciente_remoto = paciente_remoto_local.get(fila["paciente_id"])
        if doctor_remoto is None or paciente_remoto is None:
            raise ErrorApi(
                f"El turno {fila['id']} ({inicio}) no se copió: falta el id "
                "remoto del paciente o del odontólogo."
            )
        payload = {
            "paciente_id": paciente_remoto,
            "doctor_id": doctor_remoto,
            "fecha_hora_inicio": inicio,
            "fecha_hora_fin": _iso(fila["fecha_hora_fin"]),
            "estado_turno": fila["estado_turno"],
            "estado_whatsapp": fila["estado_whatsapp"],
        }
        try:
            return _pedir(f"{base}/api/v1/turnos", "POST", payload)["id"]
        except ErrorApi:
            if payload["estado_turno"] != "CANCELADO":
                raise
        # Un turno cancelado puede chocar con el control de solapamiento del
        # servicio: se crea vigente y se cancela enseguida para que quede igual.
        payload["estado_turno"] = "PENDIENTE"
        turno = _pedir(f"{base}/api/v1/turnos", "POST", payload)
        _pedir(
            f"{base}/api/v1/turnos/{turno['id']}",
            "PATCH",
            {"estado_turno": "CANCELADO"},
        )
        return turno["id"]

    _sincronizar(
        resultado,
        "turnos",
        turnos_local,
        _clave_turno,
        remoto.turnos,
        _crear_turno,
    )

    return resultado


def _sincronizar(
    resultado: Resultado,
    entidad: str,
    filas: list[dict],
    clave_de: Callable[[dict], Any],
    existentes: dict,
    crear: Callable[[dict], int],
) -> None:
    """Crea las filas cuya clave natural no esté en la nube.

    ``crear`` recibe la fila local y devuelve el ``id`` remoto; sólo se invoca con
    ``resultado.ejecutado``. ``existentes`` se completa con lo creado (o con
    ``None`` en el simulacro) para que las entidades dependientes lo encuentren.
    """
    for fila in filas:
        clave = clave_de(fila)
        if clave in existentes:
            resultado.salteados[entidad] = resultado.salteados.get(entidad, 0) + 1
            continue
        if not resultado.ejecutado:
            existentes[clave] = None
            resultado.creados[entidad] = resultado.creados.get(entidad, 0) + 1
            continue
        try:
            existentes[clave] = crear(fila)
        except ErrorApi as error:
            resultado.errores.append(str(error))
            continue
        resultado.creados[entidad] = resultado.creados.get(entidad, 0) + 1


def _imprimir(resultado: Resultado) -> None:
    """Reporta la corrida en formato tabular."""
    print(f"Origen : {resultado.origen}")
    print(f"Destino: {resultado.url}")
    print(f"Modo   : {'EJECUCION' if resultado.ejecutado else 'SIMULACRO (no se escribe)'}")
    print()
    print(f"{'entidad':<26}{'local':>7}{'escritas':>9}{'salteadas':>11}")
    print("-" * 53)
    for entidad, locales in resultado.locales.items():
        print(
            f"{entidad:<26}{locales:>7}"
            f"{resultado.creados.get(entidad, 0):>9}"
            f"{resultado.salteados.get(entidad, 0):>11}"
        )
    print("-" * 53)
    print(f"Total creado en la nube: {resultado.total_creados}")
    if resultado.creados.get("disponibilidad_doctores"):
        print()
        print(
            "Nota: la agenda semanal se reemplaza con PUT, por eso siempre se "
            "reescribe (repetir la corrida no la duplica)."
        )
    if resultado.errores:
        print()
        print(f"Errores ({len(resultado.errores)}):")
        for error in resultado.errores:
            print(f"  - {error}")
    if not resultado.ejecutado:
        print()
        print("Simulacro terminado: repetir con --ejecutar para crear los datos.")


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del script; devuelve el código de salida."""
    parser = argparse.ArgumentParser(
        description=(
            "Copia los datos de la base local a la app desplegada usando su API "
            "HTTP (alternativa cuando no se tiene la cadena de PostgreSQL)."
        )
    )
    parser.add_argument(
        "--url",
        default=URL_POR_DEFECTO,
        help="Base de la app desplegada (por defecto, la de producción).",
    )
    parser.add_argument(
        "--ejecutar",
        action="store_true",
        help="Crea los datos en la nube. Sin esta opción sólo se informa.",
    )
    args = parser.parse_args(argv)

    _imprimir(replicar(args.url, ejecutar=args.ejecutar))
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())

