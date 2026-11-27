"""Crea el primer usuario maestro (superadmin) y asegura la ``SECRET_KEY``.

Uso (desde la raíz del proyecto, con el venv activo)::

    # Crea el administrador (pide la contraseña dos veces, no queda en el shell)
    python -m scripts.crear_admin --email admin@consultorio.com --nombre "Ana Pérez"

    # Contra otra base (por ejemplo la PostgreSQL de producción en Neon)
    python -m scripts.crear_admin --env-file .env.vercel.local \
        --email admin@consultorio.com

    # Si la cuenta ya existe y hay que blanquear la clave / reafirmar el rol
    python -m scripts.crear_admin --email admin@consultorio.com --forzar

Qué hace:

1. Genera una ``SECRET_KEY`` aleatoria y la guarda en el archivo de entorno si
   todavía no hay una (las sesiones JWT se firman con esa clave).
2. Carga ese archivo en el proceso **antes** de importar la aplicación, para que
   ``DATABASE_URL`` (y el resto de las variables) apunten a la base indicada.
3. Verifica/crea el esquema con ``init_db()`` (no borra nada).
4. Crea el usuario con ``es_superadmin=True`` y todos los módulos habilitados, o
   lo actualiza con ``--forzar``.
5. Nunca imprime ni registra la contraseña en claro.

La contraseña se valida con la misma regla del panel: mínimo 8 caracteres, una
mayúscula, una minúscula y un número.
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


RAIZ = Path(__file__).resolve().parents[1]
RUTA_ENV_POR_DEFECTO = RAIZ / ".env"
LARGO_SECRET_KEY = 48


def _url_sin_clave(url: str) -> str:
    """Devuelve la URL de conexión con la contraseña reemplazada por ``***``.

    Se usa sólo para mostrar en consola a qué base se va a conectar; nunca se
    imprime la credencial.
    """
    partes = urlsplit(url)
    if not partes.password:
        return url
    netloc = f"{partes.username}:***@{partes.hostname}"
    if partes.port:
        netloc += f":{partes.port}"
    return urlunsplit(
        (partes.scheme, netloc, partes.path, partes.query, partes.fragment)
    )


def cargar_entorno(ruta_env: Path) -> None:
    """Carga ``ruta_env`` en el entorno del proceso antes de importar la app.

    ``pydantic-settings`` lee ``.env`` al importar ``app.core.config`` y el engine
    de SQLAlchemy se construye al importar ``app.db.session``. Si el script
    importara la aplicación antes de cargar ``--env-file``, todo apuntaría a la
    base del ``.env`` local aunque se hubiera indicado la de producción. Por eso
    las variables del archivo se cargan acá con prioridad (``override=True``) y
    los imports de la app son tardíos.
    """
    from dotenv import load_dotenv

    if not ruta_env.exists():
        raise SystemExit(f"No existe el archivo de entorno {ruta_env}.")
    load_dotenv(ruta_env, override=True)


def _leer_env(ruta: Path) -> list[str]:
    """Devuelve las líneas del ``.env`` (vacío si no existe)."""
    if not ruta.exists():
        return []
    return ruta.read_text(encoding="utf-8").splitlines()


def _clave_del_env(lineas: list[str]) -> str:
    """Extrae el valor actual de ``SECRET_KEY`` (vacío si no está definida)."""
    for linea in lineas:
        if linea.strip().startswith("SECRET_KEY="):
            valor = linea.split("=", 1)[1].strip().strip('"').strip("'")
            if valor:
                return valor
    return ""


def asegurar_secret_key(ruta_env: Path = RUTA_ENV_POR_DEFECTO) -> tuple[str, bool]:
    """Garantiza que el ``.env`` tenga una ``SECRET_KEY`` y devuelve ``(clave, creada)``.

    Si la variable existe pero está vacía (pasa al copiar ``.env.example``) se
    reemplaza esa línea; si no existe, se agrega al final con un comentario. Se
    escribe en UTF-8 sin BOM para no romper el resto del archivo.
    """
    lineas = _leer_env(ruta_env)
    existente = _clave_del_env(lineas)
    if existente:
        return existente, False

    clave = secrets.token_urlsafe(LARGO_SECRET_KEY)
    nuevas: list[str] = []
    reemplazada = False
    for linea in lineas:
        if linea.strip().startswith("SECRET_KEY="):
            if not reemplazada:
                nuevas.append(f'SECRET_KEY="{clave}"')
                reemplazada = True
            # Se descartan definiciones duplicadas (sólo vale una clave).
            continue
        nuevas.append(linea)
    if not reemplazada:
        if nuevas and nuevas[-1].strip():
            nuevas.append("")
        nuevas.extend(
            [
                "# Clave de firma de las sesiones JWT (generada por crear_admin).",
                f'SECRET_KEY="{clave}"',
            ]
        )
    ruta_env.write_text("\n".join(nuevas) + "\n", encoding="utf-8")
    return clave, True


def _validar_password(password: str) -> str:
    """Aplica la regla de contraseña fuerte del panel y devuelve un error legible."""
    from pydantic import ValidationError

    from app.schemas.usuario import PasswordFuerte

    try:
        PasswordFuerte(password=password)
    except ValidationError as exc:
        detalle = exc.errors()[0].get("msg", "contraseña inválida")
        raise SystemExit(f"Contraseña inválida: {detalle}") from exc
    return password


def _pedir_password() -> str:
    """Pide la contraseña dos veces por consola (sin mostrarla)."""
    while True:
        primera = getpass.getpass("Contraseña del administrador: ")
        segunda = getpass.getpass("Repetir la contraseña: ")
        if primera != segunda:
            print("Las contraseñas no coinciden. Probá de nuevo.", file=sys.stderr)
            continue
        try:
            return _validar_password(primera)
        except SystemExit as exc:
            print(exc, file=sys.stderr)


def _crear_o_actualizar(
    *,
    email: str,
    nombre: str,
    password: str,
    doctor_id: Optional[int],
    forzar: bool,
) -> tuple[str, str]:
    """Crea el superadmin o lo actualiza; devuelve ``(acción, email)``."""
    from app.core.security import CODIGOS_MODULO, hash_password
    from app.db.session import SessionLocal
    from app.models.doctor import Doctor
    from app.schemas.usuario import UsuarioCreate
    from app.services import usuario_service

    with SessionLocal() as db:
        existente = usuario_service.obtener_por_email(db, email)
        if existente is not None and not forzar:
            raise SystemExit(
                f"Ya existe un usuario con el email {email}. "
                "Usá --forzar para blanquear la contraseña y reafirmar el rol."
            )
        if existente is not None:
            if doctor_id is not None and db.get(Doctor, doctor_id) is None:
                raise SystemExit("El odontólogo indicado no existe.")
            existente.nombre_completo = nombre
            existente.hashed_password = hash_password(password)
            existente.es_superadmin = True
            existente.activo = True
            existente.modulos_permitidos = list(CODIGOS_MODULO)
            if doctor_id is not None:
                existente.doctor_id = doctor_id
            db.commit()
            return "actualizado", existente.email

        payload = UsuarioCreate(
            email=email,
            nombre_completo=nombre,
            password=password,
            es_superadmin=True,
            doctor_id=doctor_id,
            modulos_permitidos=list(CODIGOS_MODULO),
        )
        creado = usuario_service.crear_usuario(db, payload)
        return "creado", creado.email


def construir_parser() -> argparse.ArgumentParser:
    """Argumentos de línea de comandos del script."""
    parser = argparse.ArgumentParser(
        description=(
            "Crea (o actualiza con --forzar) el usuario maestro del panel con "
            "es_superadmin=True y asegura la SECRET_KEY del archivo de entorno."
        )
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Email del administrador (será el usuario del login).",
    )
    parser.add_argument(
        "--nombre",
        default="Administrador",
        help="Nombre completo que se muestra en el panel (por defecto: Administrador).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Contraseña en claro (opcional). Si se omite se pide por consola sin "
            "mostrarla; pasar la clave por argumento deja rastro en el historial."
        ),
    )
    parser.add_argument(
        "--doctor-id",
        type=int,
        default=None,
        help="Id del odontólogo a vincular (opcional).",
    )
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Actualiza la cuenta existente: nueva contraseña, superadmin y activo.",
    )
    parser.add_argument(
        "--env-file",
        default=str(RUTA_ENV_POR_DEFECTO),
        help=(
            "Archivo de entorno del que se leen DATABASE_URL/SECRET_KEY (y donde "
            "se asegura SECRET_KEY). Por defecto: ./.env. Para producción, "
            "pasá .env.vercel.local."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Punto de entrada: asegura la clave, prepara el esquema y crea el admin."""
    args = construir_parser().parse_args(argv)
    ruta_env = Path(args.env_file)

    clave, creada = asegurar_secret_key(ruta_env)
    if creada:
        print(f"SECRET_KEY generada y guardada en {ruta_env}.")
        if ruta_env.resolve() != RUTA_ENV_POR_DEFECTO.resolve():
            print(
                "Atención: la app usa el .env del proyecto. Copiá esa clave al "
                "entorno del despliegue para que las sesiones sean estables."
            )

    # Las variables del archivo se cargan ANTES de importar la app: así el
    # engine de SQLAlchemy apunta a la base indicada (Neon, SQLite, etc.).
    cargar_entorno(ruta_env)

    from sqlalchemy.exc import OperationalError

    from app.core.config import settings
    from app.core.security import CODIGOS_MODULO
    from app.db.init_db import init_db

    print(f"Base de datos: {_url_sin_clave(settings.database_url)}")

    password = args.password or _pedir_password()
    password = _validar_password(password)

    try:
        init_db()
    except OperationalError as exc:
        detalle = getattr(exc, "orig", exc)
        raise SystemExit(
            "No se pudo conectar a la base de datos configurada "
            f"({_url_sin_clave(settings.database_url)}). Revisá DATABASE_URL.\n"
            f"{detalle}"
        ) from exc

    accion, email = _crear_o_actualizar(
        email=str(args.email),
        nombre=args.nombre,
        password=password,
        doctor_id=args.doctor_id,
        forzar=args.forzar,
    )

    print(f"\nSuperadmin {accion}: {email}")
    print("  Nombre     :", args.nombre)
    print("  Rol        : superadmin (acceso total, sin restricciones)")
    print("  Módulos    :", ", ".join(CODIGOS_MODULO))
    if creada:
        print(
            "\nReiniciá el servidor para que tome la nueva SECRET_KEY. "
            "En producción definí la misma clave en el entorno."
        )
    print("\nYa podés entrar al panel en / con ese email y la contraseña elegida.")
    del clave  # la clave no se imprime nunca
    return 0


if __name__ == "__main__":  # pragma: no cover - ejecución manual
    raise SystemExit(main())
