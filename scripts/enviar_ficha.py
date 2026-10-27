"""Genera (y opcionalmente envía) el enlace de la ficha de salud de un paciente.

Contexto
--------
La ficha de salud (Módulo 4) se abre con un enlace firmado
(``/anamnesis/<token>``). El token lo firma **el servidor** con
``ANAMNESIS_TOKEN_SECRET``: sin ese secreto no se puede armar el enlace desde
afuera. Por eso este script no toca la base ni firma nada: le pide el enlace a
la app desplegada, que es la que ya habla con Neon y la que conoce el secreto.

Qué hace
--------
1. Busca al paciente (``--telefono``, ``--dni`` o ``--nombre``) o usa el turno
   indicado con ``--turno``.
2. Se queda con el **turno vigente más próximo** (hoy en adelante, sin contar
   los cancelados); si no hay ninguno, muestra los últimos para elegir uno con
   ``--turno``.
3. Pide ``GET /api/v1/anamnesis/panel/{turno}/enlace``: enlace firmado, texto
   sugerido con la fecha y el profesional, y el Click-to-Chat (``wa.me``).
4. Con ``--api`` lo manda por la WhatsApp Cloud API
   (``POST /api/v1/anamnesis/panel/{turno}/enviar``). Sin ``--api`` no se envía
   nada: imprime el enlace y el mensaje para copiarlos en WhatsApp Web.

Ese envío **no** completa la ficha ni marca el recordatorio del turno: la ficha
se completa cuando el paciente envía el formulario y el recordatorio sigue
pendiente hasta que salga de verdad (misma regla que el panel).

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.enviar_ficha --telefono "11 2233-4455"
    python -m scripts.enviar_ficha --dni 30111222
    python -m scripts.enviar_ficha --nombre "Ana" --listar
    python -m scripts.enviar_ficha --turno 42 --api
    python -m scripts.enviar_ficha --telefono 1155550000 --destino 1155551111

``--json`` devuelve la respuesta cruda de la API (para encadenar con otros
scripts). Código de salida ``0`` si salió bien y ``1`` si hubo un error.

Controles: ``tests/test_enviar_ficha.py`` (servidor HTTP falso, sin red).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Optional

from app.core.whatsapp_utils import solo_digitos
from app.models.enums import EstadoTurno

#: App desplegada que se usa cuando no se pasa ``--url`` (la misma que usa
#: ``scripts/replicar_datos_api``: es la que escribe en la base de Neon).
URL_POR_DEFECTO = "https://odontologico-omega.vercel.app"
TIEMPO_ESPERA = 60


class ErrorApi(RuntimeError):
    """Falla al hablar con la API o datos que no permiten armar el enlace."""


def _pedir(
    url: str,
    metodo: str = "GET",
    payload: Optional[dict] = None,
    tiempo_espera: int = TIEMPO_ESPERA,
) -> Any:
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
        with urllib.request.urlopen(peticion, timeout=tiempo_espera) as respuesta:
            cuerpo = respuesta.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "replace")
        raise ErrorApi(f"{metodo} {url} -> HTTP {error.code}: {detalle}") from error
    except urllib.error.URLError as error:
        raise ErrorApi(f"{metodo} {url} -> {error.reason}") from error

    return json.loads(cuerpo) if cuerpo else None


def _hora_local(valor: datetime) -> datetime:
    """Lleva un ``datetime`` a la hora local sin zona (la que se muestra al usuario)."""
    if valor.tzinfo is None:
        return valor
    return valor.astimezone().replace(tzinfo=None)


def _fecha_turno(valor: Any) -> datetime:
    """Lee la fecha ISO del turno en la hora que muestra el panel.

    La agenda guarda la hora del consultorio *sin* zona (el panel la manda con
    ``isoLocal`` y la dibuja con ``iso.slice(11, 16)``), así que un valor naive se
    usa tal cual: convertirlo como si fuera UTC mostraría una hora distinta a la
    del panel y a la del mensaje que recibe el paciente. Si la API devolviera una
    zona (``Z`` o ``-03:00``) sí se lleva a la hora local de esta máquina.
    """
    texto = str(valor or "").strip()
    try:
        fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError as error:
        raise ErrorApi(f"La API devolvió una fecha inválida: {valor!r}") from error
    return _hora_local(fecha)


def _hhmm(valor: Any) -> str:
    """Fecha y hora del turno tal como las muestra el panel (``DD/MM/AAAA HH:MM``)."""
    return _fecha_turno(valor).strftime("%d/%m/%Y %H:%M")


def buscar_pacientes(
    url: str,
    *,
    dni: Optional[str] = None,
    telefono: Optional[str] = None,
    nombre: Optional[str] = None,
) -> list[dict]:
    """Busca pacientes por documento, teléfono o nombre.

    El filtro ``telefono`` de la API es **exacto** y en la base el número puede
    estar guardado con espacios, guiones o ``+54``. Por eso se prueba tal como
    lo escribió la persona y, si no aparece, comparando sólo los dígitos contra
    la lista completa (con el mismo criterio que usa WhatsApp).

    :raises ErrorApi: no se indicó ningún criterio de búsqueda.
    """
    base = f"{url}/api/v1/pacientes"
    if dni:
        return _pedir(f"{base}?{urllib.parse.urlencode({'dni': dni})}") or []
    if nombre:
        return _pedir(f"{base}?{urllib.parse.urlencode({'buscar': nombre})}") or []
    if telefono:
        for variante in (telefono, solo_digitos(telefono)):
            if not variante:
                continue
            consulta = urllib.parse.urlencode({"telefono": variante})
            encontrados = _pedir(f"{base}?{consulta}") or []
            if encontrados:
                return encontrados
        buscado = solo_digitos(telefono)
        if not buscado:
            return []
        todos = _pedir(base) or []
        return [p for p in todos if solo_digitos(p.get("telefono")) == buscado]
    raise ErrorApi(
        "Indicá a quién buscar: --telefono, --dni o --nombre (o un --turno directo)."
    )


def elegir_paciente(pacientes: list[dict]) -> dict:
    """Devuelve el único paciente de la lista (falla si no hay o hay varios)."""
    if not pacientes:
        raise ErrorApi(
            "No se encontró ningún paciente con esos datos. Revisá el número, "
            "probá con --dni o creá al paciente desde el panel."
        )
    if len(pacientes) > 1:
        listado = "; ".join(
            f"#{p.get('id')} {p.get('nombre')} ({p.get('telefono') or 'sin teléfono'})"
            for p in pacientes[:10]
        )
        raise ErrorApi(
            f"Hay {len(pacientes)} pacientes que coinciden: {listado}. "
            "Repetí el comando con --dni o --turno."
        )
    return pacientes[0]


def turnos_de_paciente(url: str, paciente_id: int) -> list[dict]:
    """Todos los turnos del paciente (hasta 500), sin importar la fecha."""
    consulta = urllib.parse.urlencode({"paciente_id": paciente_id, "limit": 500})
    return _pedir(f"{url}/api/v1/turnos?{consulta}") or []


def elegir_turno(turnos: list[dict], ahora: Optional[datetime] = None) -> dict:
    """Turno vigente más próximo; si no hay, explica cuáles puede elegir.

    Los turnos cancelados se descartan: la ficha de un turno cancelado no se
    pide y el panel interno la rechaza con **409**. La comparación se hace en la
    hora "de pared" que guarda la agenda (la misma del panel): un turno de hoy a
    las 15:30 cuenta como próximo hasta que el reloj marque las 15:30.
    """
    referencia = _hora_local(ahora) if ahora is not None else datetime.now()
    vigentes = [
        turno
        for turno in turnos
        if str(turno.get("estado_turno")) != EstadoTurno.CANCELADO.value
    ]
    proximos = sorted(
        (
            turno
            for turno in vigentes
            if _fecha_turno(turno.get("fecha_hora_inicio")) >= referencia
        ),
        key=lambda turno: _fecha_turno(turno.get("fecha_hora_inicio")),
    )
    if proximos:
        return proximos[0]

    if not vigentes:
        raise ErrorApi("El paciente no tiene turnos vigentes (todos cancelados).")
    ultimos = sorted(
        vigentes,
        key=lambda turno: _fecha_turno(turno.get("fecha_hora_inicio")),
        reverse=True,
    )[:5]
    detalle = ", ".join(
        f"#{turno.get('id')} {_hhmm(turno.get('fecha_hora_inicio'))}"
        for turno in ultimos
    )
    raise ErrorApi(
        "El paciente no tiene turnos de hoy en adelante. "
        f"Los últimos fueron: {detalle}. Repetí el comando con --turno <id>."
    )


def obtener_enlace(
    url: str, turno_id: int, telefono: Optional[str] = None
) -> dict:
    """Pide el enlace firmado y el mensaje ya redactado (no envía nada)."""
    consulta = ""
    if telefono:
        consulta = "?" + urllib.parse.urlencode({"telefono": telefono})
    return (
        _pedir(f"{url}/api/v1/anamnesis/panel/{int(turno_id)}/enlace{consulta}") or {}
    )


def enviar_ficha(
    url: str,
    turno_id: int,
    telefono: Optional[str] = None,
    canal: str = "api",
) -> dict:
    """Manda la ficha por la Cloud API (o prepara el envío manual desde la API)."""
    payload: dict[str, Any] = {"canal": canal}
    if telefono:
        payload["telefono"] = telefono
    return (
        _pedir(
            f"{url}/api/v1/anamnesis/panel/{int(turno_id)}/enviar",
            metodo="POST",
            payload=payload,
        )
        or {}
    )


def _listar_turnos(paciente: dict, turnos: list[dict]) -> None:
    """Muestra los turnos del paciente para elegir uno con ``--turno``."""
    print(
        f"Paciente: #{paciente.get('id')} {paciente.get('nombre')} "
        f"({paciente.get('telefono') or 'sin teléfono'})"
    )
    if not turnos:
        print("No tiene turnos cargados.")
        return
    print(f"{'turno':>6}  {'fecha':<17}{'estado':<12}doctor")
    print("-" * 55)
    for turno in sorted(
        turnos, key=lambda item: _fecha_turno(item.get("fecha_hora_inicio"))
    ):
        print(
            f"{str(turno.get('id')):>6}  {_hhmm(turno.get('fecha_hora_inicio')):<17}"
            f"{str(turno.get('estado_turno')):<12}{turno.get('doctor_id')}"
        )
    print()
    print("Generá el enlace con: python -m scripts.enviar_ficha --turno <id>")


def _imprimir(datos: dict, resultado: Optional[dict] = None) -> None:
    """Muestra la ficha, el enlace y el mensaje listo para copiar."""
    print(
        f"Paciente : {datos.get('paciente_nombre') or '-'}"
        f"  (DNI {datos.get('paciente_dni') or '-'})"
    )
    print(
        f"Teléfono : {datos.get('paciente_telefono') or '-'}"
        f"  -> WhatsApp {datos.get('telefono_whatsapp') or '(sin número válido)'}"
    )
    print(
        f"Turno    : #{datos.get('turno_id')} "
        f"{_hhmm(datos.get('fecha_hora_inicio'))} con "
        f"{datos.get('doctor_nombre') or '-'} ({datos.get('estado_turno')})"
    )
    if datos.get("completado"):
        fecha = str(datos.get("fecha_completado") or "")[:10]
        print(f"Ficha    : COMPLETADA el {fecha or '(sin fecha)'}")
    else:
        print("Ficha    : PENDIENTE (el paciente todavía no la envió)")
    print()
    print("Enlace para el paciente:")
    print(f"  {datos.get('enlace')}")
    print()
    print("Mensaje sugerido (copiar y pegar en WhatsApp):")
    for linea in str(datos.get("mensaje") or "").splitlines() or [""]:
        print(f"  {linea}")
    if datos.get("enlace_whatsapp"):
        print()
        print("Abrir WhatsApp con el mensaje ya cargado:")
        print(f"  {datos['enlace_whatsapp']}")

    if resultado is None:
        return
    print()
    if not resultado.get("enviado"):
        print("No se envió nada: copiá el mensaje o abrí el enlace de WhatsApp.")
    elif resultado.get("simulado"):
        print(
            "Envío SIMULADO: el servidor no tiene credenciales de la WhatsApp "
            "Cloud API. Usá el enlace de WhatsApp para mandarlo a mano."
        )
    else:
        print(
            "Enviado por la WhatsApp Cloud API "
            f"(mensaje id {resultado.get('whatsapp_mensaje_id') or 'sin id'})."
        )


def _preparar_consola() -> None:
    """Fuerza UTF-8 en la salida para que los acentos se vean bien en Windows."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # pragma: no cover - consolas viejas
            pass


def main(argv: Optional[list[str]] = None) -> int:
    """Punto de entrada del script; devuelve el código de salida."""
    # Se configura antes de argparse para que `--help` también salga en UTF-8.
    _preparar_consola()
    parser = argparse.ArgumentParser(
        prog="python -m scripts.enviar_ficha",
        description=(
            "Genera el enlace de la ficha de salud de un paciente y, si se pide, "
            "lo envía por WhatsApp. El enlace lo firma la app desplegada, así que "
            "apunta a la base de Neon sin usar credenciales locales."
        ),
        epilog=(
            "Ejemplos:\n"
            '  python -m scripts.enviar_ficha --telefono "11 2233-4455"\n'
            "  python -m scripts.enviar_ficha --dni 30111222 --api\n"
            "  python -m scripts.enviar_ficha --nombre Ana --listar\n"
            "  python -m scripts.enviar_ficha --turno 42 --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=URL_POR_DEFECTO,
        help="Base de la app desplegada (por defecto, la de producción).",
    )
    parser.add_argument(
        "--turno",
        type=int,
        help="Id del turno: saltea la búsqueda del paciente.",
    )
    parser.add_argument(
        "--telefono",
        help='Teléfono del paciente (con o sin +54, espacios o guiones): "11 2233-4455".',
    )
    parser.add_argument("--dni", help="Documento del paciente.")
    parser.add_argument("--nombre", help="Nombre (o parte) del paciente.")
    parser.add_argument(
        "--destino",
        help=(
            "Teléfono alternativo al que se manda el mensaje (por ejemplo el "
            "celular desde el que llamó el paciente)."
        ),
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help=(
            "Envía la ficha por la WhatsApp Cloud API. Sin esta opción sólo se "
            "genera el enlace para copiarlo en WhatsApp."
        ),
    )
    parser.add_argument(
        "--listar",
        action="store_true",
        help="Muestra los turnos del paciente y termina (para elegir con --turno).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprime la respuesta cruda de la API en JSON.",
    )
    args = parser.parse_args(argv)

    url = str(args.url).rstrip("/")
    try:
        if args.turno:
            turno_id = int(args.turno)
        else:
            pacientes = buscar_pacientes(
                url,
                dni=args.dni,
                telefono=args.telefono,
                nombre=args.nombre,
            )
            paciente = elegir_paciente(pacientes)
            turnos = turnos_de_paciente(url, int(paciente["id"]))
            if args.listar:
                _listar_turnos(paciente, turnos)
                return 0
            turno_id = int(elegir_turno(turnos)["id"])

        if args.api:
            datos = enviar_ficha(url, turno_id, telefono=args.destino)
        else:
            datos = obtener_enlace(url, turno_id, telefono=args.destino)
    except ErrorApi as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(datos, ensure_ascii=False, indent=2))
    else:
        _imprimir(datos, datos if args.api else None)
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())
