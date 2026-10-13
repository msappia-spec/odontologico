"""Router de la interfaz web (dashboard para la secretaría y vista móvil).

Sirve la página HTML del dashboard (``/``) y la **vista móvil del odontólogo**
(``/medico``). Los datos se consumen desde el navegador mediante ``fetch``
contra la API de ``/api/v1``.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# Directorio de recursos estáticos (CSS/JS/imágenes) e índices de las páginas.
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
INDEX_FILE = STATIC_DIR / "index.html"
# Vista *mobile-first* para que el odontólogo vea su agenda del día desde el
# celular: pantalla de selección del profesional, turnos del día con sus
# horarios libres intercalados y acciones táctiles (cancelar / agendar).
MEDICO_FILE = STATIC_DIR / "medico.html"

# ``include_in_schema=False``: son páginas, no endpoints de la API.
router = APIRouter(tags=["Interfaz web"], include_in_schema=False)


@router.get("/")
def dashboard() -> FileResponse:
    """Devuelve la página principal (dashboard) de la secretaría."""
    return FileResponse(INDEX_FILE, media_type="text/html")


@router.get("/medico")
def agenda_odontologo() -> FileResponse:
    """Devuelve la vista móvil del odontólogo (su agenda diaria)."""
    return FileResponse(MEDICO_FILE, media_type="text/html")
