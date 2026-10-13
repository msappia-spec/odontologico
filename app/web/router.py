"""Router de la interfaz web (dashboard para la secretaría).

Sirve la página HTML del dashboard y expone la raíz ``/``. Los datos se
consumen desde el navegador mediante ``fetch`` contra la API de ``/api/v1``.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# Directorio de recursos estáticos (CSS/JS/imágenes) e índice del dashboard.
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
INDEX_FILE = STATIC_DIR / "index.html"

# ``include_in_schema=False``: es una página, no un endpoint de la API.
router = APIRouter(tags=["Interfaz web"], include_in_schema=False)


@router.get("/")
def dashboard() -> FileResponse:
    """Devuelve la página principal (dashboard) de la secretaría."""
    return FileResponse(INDEX_FILE, media_type="text/html")
