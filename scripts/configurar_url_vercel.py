"""Guarda la cadena PostgreSQL de producción para ``limpiar_vercel.bat``.

Vercel clasifica ``DATABASE_URL`` como ``Secret``: el CLI no puede exportarla y
``vercel env pull`` escribe ``[SENSITIVE]`` en lugar del valor real. Este helper
pide la cadena una sola vez, la valida y la deja en ``.env.vercel.local``, que
está cubierto por ``.gitignore`` (patrón ``.env*``).

La entrada se oculta con ``getpass`` para que la contraseña no quede en pantalla
ni en el historial de la consola. También se puede crear el archivo a mano::

    DATABASE_URL="postgresql://usuario:clave@host/db?sslmode=require"

No se conecta a la base: eso lo hace después ``scripts/limpiar_base.py`` con un
simulacro antes de pedir confirmación.
"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urlsplit

ARCHIVO_POR_DEFECTO = Path(".env.vercel.local")
PREFIJOS = ("postgres://", "postgresql://", "postgresql+psycopg://")


def _validar(url: str) -> str:
    """Rechaza placeholders, SQLite y cadenas sin host o base."""
    url = url.strip().strip('"').strip("'")
    if not url:
        raise ValueError("La cadena está vacía.")
    if "SENSITIVE" in url.upper():
        raise ValueError(
            "Ese es el placeholder de Vercel, no la cadena real. Copiala desde "
            "Neon o desde Vercel -> Storage."
        )
    if not url.startswith(PREFIJOS):
        raise ValueError(
            "Debe empezar con postgresql:// (o postgres://). Esta limpieza no "
            "se puede hacer sobre la SQLite efímera de Vercel."
        )
    partes = urlsplit(url)
    if not partes.hostname:
        raise ValueError("La cadena no tiene host.")
    if not partes.path.strip("/"):
        raise ValueError("La cadena no indica el nombre de la base.")
    return url


def _guardar(archivo: Path, url: str) -> None:
    """Reemplaza sólo ``DATABASE_URL`` y conserva el resto del archivo."""
    lineas: list[str] = []
    if archivo.exists():
        lineas = [
            linea
            for linea in archivo.read_text(encoding="utf-8").splitlines()
            if not linea.strip().startswith("DATABASE_URL=")
        ]
    lineas.append("DATABASE_URL=" + json.dumps(url, ensure_ascii=False))
    archivo.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Punto de entrada del helper; devuelve el código de salida."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.configurar_url_vercel",
        description=(
            "Guarda DATABASE_URL de producción en .env.vercel.local para que "
            "limpiar_vercel.bat pueda conectarse a Neon."
        ),
    )
    parser.add_argument(
        "--archivo",
        type=Path,
        default=ARCHIVO_POR_DEFECTO,
        help="Archivo de destino (por defecto .env.vercel.local).",
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Reemplaza una DATABASE_URL que ya exista en el archivo.",
    )
    args = parser.parse_args(argv)

    if args.archivo.exists() and not args.forzar:
        contenido = args.archivo.read_text(encoding="utf-8", errors="replace")
        if any(
            linea.strip().startswith("DATABASE_URL=")
            for linea in contenido.splitlines()
        ):
            print(
                f"Ya existe DATABASE_URL en {args.archivo}. "
                "Usá --forzar para reemplazarla."
            )
            return 1

    print("Vercel no exporta las variables marcadas como Secret.")
    print("Copiá la cadena de conexión desde el panel de Neon")
    print("(o Vercel -> Storage -> Neon) con este formato:")
    print("  postgresql://usuario:clave@host/db?sslmode=require")
    print()
    print("La entrada no se muestra al escribirla. Si el pegado no funciona,")
    print("creá el archivo a mano con la línea DATABASE_URL=\"...\".")
    print()

    try:
        url = _validar(getpass.getpass("Cadena PostgreSQL de producción: "))
    except (KeyboardInterrupt, EOFError):
        print()
        print("Cancelado.")
        return 1
    except ValueError as exc:
        print()
        print(f"[ERROR] {exc}")
        return 1

    _guardar(args.archivo, url)
    partes = urlsplit(url)
    print()
    print(
        "Guardada en "
        f"{args.archivo.resolve()} para {partes.hostname}/{partes.path.lstrip('/')}"
    )
    print("El archivo está ignorado por git; no lo compartas.")
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())
