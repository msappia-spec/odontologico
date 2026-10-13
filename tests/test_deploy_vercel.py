"""Contrato del despliegue en Vercel (FastAPI *zero-config*).

Vercel detecta el framework leyendo ``requirements.txt`` y busca una instancia
``app`` en los *entrypoints* soportados (``app.py``, ``index.py``, ``server.py``,
``main.py``, ``wsgi.py``, ``asgi.py``, y los mismos nombres dentro de ``app/`` o
``src/``). El ``vercel.json`` con ``builds``/``routes`` pertenece a la API v2 y
ya no se admite, por lo que esos archivos/claves rompen el *build*.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.core.config import SQLITE_SERVERLESS_URL, Settings
from app.main import app

RAIZ = Path(__file__).resolve().parents[1]
ENTRYPOINTS_SOPORTADOS = {
    "app.py",
    "index.py",
    "server.py",
    "main.py",
    "wsgi.py",
    "asgi.py",
}


def test_entrypoint_con_instancia_app() -> None:
    """La instancia ``app`` vive en un nombre de archivo que Vercel detecta."""
    assert isinstance(app, FastAPI)
    candidatos = [
        archivo
        for carpeta in (RAIZ, RAIZ / "app", RAIZ / "src")
        for archivo in (carpeta / nombre for nombre in ENTRYPOINTS_SOPORTADOS)
    ]
    assert any(ruta.is_file() for ruta in candidatos), (
        "Vercel no encontraría la instancia 'app': hace falta app/main.py (o "
        "uno de los entrypoints soportados)."
    )


def test_requirements_permite_detectar_fastapi() -> None:
    """El preset de Python se activa por la dependencia declarada."""
    contenido = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^fastapi==", contenido, flags=re.MULTILINE)


def test_python_version_fijada() -> None:
    """El runtime toma la versión de ``.python-version``."""
    version = (RAIZ / ".python-version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"3\.\d+", version)


def test_vercel_json_sin_formato_legacy() -> None:
    """No se usa ``builds``/``routes`` (API v2, reemplazada por rewrites)."""
    archivo = RAIZ / "vercel.json"
    if not archivo.is_file():
        return
    config = json.loads(archivo.read_text(encoding="utf-8"))
    assert "builds" not in config, "El formato 'builds' ya no es válido."
    assert "routes" not in config, "Usar 'rewrites' en lugar de 'routes'."


@pytest.mark.parametrize(
    "url_inicial",
    ["sqlite:///./odontologico.db", "sqlite:///odontologico.db"],
)
def test_sqlite_relativo_se_redirige_a_tmp_en_vercel(
    monkeypatch: pytest.MonkeyPatch, url_inicial: str
) -> None:
    """En Vercel, un SQLite relativo no se puede abrir: se usa ``/tmp``."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DATABASE_URL", url_inicial)
    assert Settings(_env_file=None).database_url == SQLITE_SERVERLESS_URL


def test_postgres_no_se_modifica_en_vercel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con PostgreSQL configurado, la URL se respeta tal cual."""
    url = "postgresql+psycopg://usuario:clave@host:5432/odontologico"
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DATABASE_URL", url)
    assert Settings(_env_file=None).database_url == url


def test_sqlite_ya_apuntado_a_tmp_no_se_reescribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La adaptación es idempotente."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DATABASE_URL", SQLITE_SERVERLESS_URL)
    assert Settings(_env_file=None).database_url == SQLITE_SERVERLESS_URL


def test_sin_vercel_conserva_la_url_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fuera de Vercel (uso local) no se toca la configuración."""
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./odontologico.db")
    assert Settings(_env_file=None).database_url == "sqlite:///./odontologico.db"


def test_documentacion_del_despliegue() -> None:
    """El README explica el despliegue en Vercel y la variable obligatoria."""
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "Vercel" in readme
    assert "DATABASE_URL" in readme
