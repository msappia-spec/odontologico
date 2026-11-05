"""Router de la interfaz web (dashboard para la secretaría y vista móvil).

Sirve la página HTML del dashboard (``/``), la **vista móvil del odontólogo**
(``/medico``) y la **ficha pública de salud** (``/anamnesis/{token}``). Los datos
se consumen desde el navegador mediante ``fetch`` contra la API de ``/api/v1``.
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
# Ficha pública de salud (Módulo 4): se sirve con el token en la URL para que el
# paciente no tenga que iniciar sesión. El JS valida el token contra la API y
# muestra el formulario o el resumen ya enviado.
ANAMNESIS_FILE = STATIC_DIR / "anamnesis.html"
PRIVACIDAD_FILE = STATIC_DIR / "privacidad.html"
ELIMINACION_DATOS_FILE = STATIC_DIR / "eliminacion_datos.html"

# ``include_in_schema=False``: son páginas, no endpoints de la API.
router = APIRouter(tags=["Interfaz web"], include_in_schema=False)

# Las páginas del panel se sirven con ``no-cache``: el navegador las revalida en
# cada carga contra el ``ETag``/``Last-Modified`` (respuesta 304 si nada cambió).
# Sin esto el navegador guardaba ``index.html`` y ``app.js`` por su cuenta y,
# después de una actualización, el menú mostraba la pestaña nueva pero el click
# no hacía nada, porque el JS cacheado no la conocía. El HTML del panel es chico
# y la validación devuelve 304, así que el costo es despreciable.
SIN_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


@router.get("/")
def dashboard() -> FileResponse:
    """Devuelve la página principal (dashboard) de la secretaría."""
    return FileResponse(INDEX_FILE, media_type="text/html", headers=SIN_CACHE)


@router.get("/medico")
def agenda_odontologo() -> FileResponse:
    """Devuelve la vista móvil del odontólogo (su agenda diaria)."""
    return FileResponse(MEDICO_FILE, media_type="text/html", headers=SIN_CACHE)


@router.get("/anamnesis/{token}")
def ficha_de_salud(token: str) -> FileResponse:
    """Devuelve la ficha pública que el paciente recibe por WhatsApp.

    El token viaja en la URL de la página pero **no se registra en caché** ni se
    envía como referrer a terceros (``Referrer-Policy: no-referrer``): es una
    credencial de acceso a la ficha de una persona. El JS lo lee del path y lo
    valida contra ``/api/v1/anamnesis/{token}``.
    """
    return FileResponse(
        ANAMNESIS_FILE,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.get("/privacidad")
def politica_de_privacidad() -> FileResponse:
    """Política pública requerida por Meta para la integración de WhatsApp."""
    return FileResponse(PRIVACIDAD_FILE, media_type="text/html", headers=SIN_CACHE)


@router.get("/eliminacion-datos")
def instrucciones_eliminacion_datos() -> FileResponse:
    """Canal público para solicitudes de eliminación de datos personales."""
    return FileResponse(
        ELIMINACION_DATOS_FILE, media_type="text/html", headers=SIN_CACHE
    )
