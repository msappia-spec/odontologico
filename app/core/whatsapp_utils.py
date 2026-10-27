"""Utilidades de WhatsApp sin dependencias de base de datos ni de envío.

Acá vive el **formato** que comparten los dos servicios que arman mensajes:

* :mod:`app.services.whatsapp_service` (recordatorios y respuestas del bot),
* :mod:`app.services.anamnesis_service` (envío manual del enlace de la ficha).

Estaban dentro de ``whatsapp_service``, pero ese módulo importa
``anamnesis_service`` (para saber si la ficha sigue pendiente). Al moverlas a
``app/core`` el servicio de anamnesis puede usarlas sin crear un import
circular, y ``whatsapp_service`` las reexporta para no romper a quien ya las
importaba desde ahí.

Todo es Python puro: no toca la sesión de SQLAlchemy ni el transporte HTTP.
"""

from datetime import datetime
from typing import Optional
from urllib.parse import quote

from app.core.config import settings

#: URL oficial de Click-to-Chat. Marcadores: número (sólo dígitos) y mensaje.
URL_CLICK_TO_CHAT = "https://wa.me/{}?text={}"

#: Nombres en español para la fecha "en letras" (sin depender del locale del SO).
DIAS_SEMANA = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def solo_digitos(valor: Optional[str]) -> str:
    """Devuelve únicamente los dígitos de ``valor`` (``""`` si no hay ninguno)."""
    return "".join(c for c in str(valor or "") if c.isdigit())


def normalizar_telefono_whatsapp(
    telefono: Optional[str], codigo_pais: Optional[str] = None
) -> str:
    """Adapta un teléfono al formato que exige el enlace ``wa.me`` (sólo dígitos).

    Orden de las reglas:

    1. Se conservan sólo los dígitos (se descartan ``+``, espacios, guiones,
       paréntesis y puntos).
    2. El prefijo internacional ``00`` se elimina (``0054911...`` -> ``54911...``).
    3. Se quitan los ceros de larga distancia (``011 2233-4455`` -> ``1122334455``).
    4. Si ya empieza con el código configurado (``549``) o con ese código sin el
       ``9`` de móvil (``54``), se devuelve sin cambios.
    5. En cualquier otro caso se le antepone el código configurado
       (``WHATSAPP_CODIGO_PAIS``, por defecto ``549``: Argentina móvil).

    Ejemplos con la configuración por defecto::

        "+54 9 11 2233-4455" -> "5491122334455"
        "(011) 2233-4455"    -> "5491122334455"
        "5491122334455"      -> "5491122334455"
        ""                   -> ""

    :param telefono: teléfono tal como está cargado en el sistema.
    :param codigo_pais: código a aplicar (por defecto ``settings.whatsapp_codigo_pais``).
    """
    digitos = solo_digitos(telefono)
    if not digitos:
        return ""

    prefijo = solo_digitos(codigo_pais) or solo_digitos(settings.whatsapp_codigo_pais)
    if not prefijo:
        return digitos

    if digitos.startswith("00"):
        digitos = digitos[2:]
    digitos = digitos.lstrip("0")
    if not digitos:
        return ""

    # En Argentina el móvil lleva un "9" después del código de país (549...).
    pais = prefijo[:-1] if prefijo.startswith("549") else prefijo
    if digitos.startswith(prefijo) or digitos.startswith(pais):
        return digitos

    return prefijo + digitos


def formatear_fecha_recordatorio(fecha: datetime) -> str:
    """Devuelve la fecha "en letras" (``viernes 16 de octubre de 2026``)."""
    return (
        f"{DIAS_SEMANA[fecha.weekday()]} {fecha.day} de "
        f"{MESES[fecha.month - 1]} de {fecha.year}"
    )


def profesional_con_articulo(nombre: Optional[str]) -> str:
    """Agrega el artículo al profesional para que el saludo sea natural.

    ``"Dra. Gómez"`` -> ``"la Dra. Gómez"`` y ``"Dr. House"`` -> ``"el Dr. House"``.
    Si el nombre no trae tratamiento (``Dr.``/``Dra.``) se devuelve tal cual, para
    no generar frases como "con el Juan".
    """
    limpio = (nombre or "").strip()
    minus = limpio.lower()
    if minus.startswith(("dra", "doctora")):
        return f"la {limpio}"
    if minus.startswith(("dr", "doctor")):
        return f"el {limpio}"
    return limpio


def construir_enlace_click_to_chat(
    telefono: Optional[str],
    texto: str,
    codigo_pais: Optional[str] = None,
) -> str:
    """Arma el enlace ``wa.me`` con el mensaje ya codificado (URL Encoding).

    El texto se codifica con ``urllib.parse.quote(..., safe="")``, de modo que
    los acentos, el ``¡`` y los espacios viajen como ``%C3%A9`` y ``%20`` sin
    romper la URL.

    :return: la URL lista para abrir en WhatsApp, o ``""`` si el teléfono no
        tiene dígitos utilizables.
    """
    numero = normalizar_telefono_whatsapp(telefono, codigo_pais)
    if not numero:
        return ""
    return URL_CLICK_TO_CHAT.format(numero, quote(texto or "", safe=""))
