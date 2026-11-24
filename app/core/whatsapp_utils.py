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


def _quitar_prefijo_movil_15(numero: str) -> str:
    """Quita el ``15`` que se intercala entre el área y el abonado en Argentina.

    El formato internacional que usa WAHA no lleva ese prefijo: ``351 15
    643-2863`` se convierte en ``351 643-2863``. Se prueban largos de área de 2,
    3 y 4 dígitos y se exige que el número resultante tenga los 10 dígitos
    nacionales habituales, para no confundir un ``15`` que forme parte del
    abonado.
    """
    for largo_area in (2, 3, 4):
        if (
            len(numero) - 2 == 10
            and numero[largo_area:largo_area + 2] == "15"
        ):
            return numero[:largo_area] + numero[largo_area + 2:]
    return numero


def normalizar_telefono_waha(telefono: Optional[str]) -> str:
    """Deja un teléfono argentino en el formato que espera WAHA.

    Reglas:

    1. Sólo dígitos; se descarta el prefijo internacional ``00`` y los ceros de
       larga distancia.
    2. Si el número ya viene con ``549`` o ``54`` se respeta el país, pero se
       normaliza el ``9`` de móvil.
    3. Para números locales se antepone ``549``.
    4. Se quita el ``15`` que va después del código de área.

    Ejemplos::

        "+54 9 351 643-2863"  -> "5493516432863"
        "0351 15 643-2863"    -> "5493516432863"
        "351 643-2863"        -> "5493516432863"
    """
    digitos = solo_digitos(telefono)
    if not digitos:
        return ""

    if digitos.startswith("00"):
        digitos = digitos[2:]
    digitos = digitos.lstrip("0")
    if not digitos:
        return ""

    if digitos.startswith("549"):
        resto = digitos[3:]
        return "549" + _quitar_prefijo_movil_15(resto)

    if digitos.startswith("54"):
        resto = digitos[2:]
        if resto.startswith("9"):
            resto = resto[1:]
        return "549" + _quitar_prefijo_movil_15(resto)

    return "549" + _quitar_prefijo_movil_15(digitos)


#: Dominio de los identificadores opacos de WhatsApp (privacidad del número).
#: No son teléfonos: ``50783827554549@lid`` es un chat válido pero sin dígitos
#: de teléfono, así que normalizarlo lo convierte en un destinatario inválido.
DOMINIO_LID = "@lid"

#: Dominios de los JID que sí contienen el número real del contacto.
DOMINIOS_TELEFONO = ("@s.whatsapp.net", "@c.us")


def es_identificador_lid(valor: Optional[str]) -> bool:
    """Indica si ``valor`` es un ``chatId`` opaco de WhatsApp (``...@lid``)."""
    return (valor or "").strip().lower().endswith(DOMINIO_LID)


def numero_de_jid(jid: Optional[str]) -> str:
    """Extrae el teléfono (``549...``) de un JID de WhatsApp.

    Devuelve ``""`` cuando el valor no trae un número: identificadores
    ``@lid``, grupos (``@g.us``), estados (``@broadcast``) o texto sin dígitos.
    También descarta el sufijo de dispositivo que agrega whatsmeow
    (``5493515945075:12@s.whatsapp.net``).

    Ejemplos::

        "5493515945075@s.whatsapp.net" -> "5493515945075"
        "5493515945075@c.us"           -> "5493515945075"
        "+54 9 351 594-5075"           -> "5493515945075"
        "50783827554549@lid"           -> ""
    """
    crudo = (jid or "").strip()
    if not crudo or es_identificador_lid(crudo):
        return ""

    bajo = crudo.lower()
    for dominio in DOMINIOS_TELEFONO:
        if bajo.endswith(dominio):
            crudo = crudo[: -len(dominio)]
            break
    else:
        if "@" in crudo:
            # Grupo (@g.us), estado (@broadcast) u otro JID sin teléfono.
            return ""

    return normalizar_telefono_waha(crudo.split(":", 1)[0])


def normalizar_destinatario_waha(valor: Optional[str]) -> str:
    """Clave canónica de un contacto: ``549...`` para teléfonos, ``@lid`` intacto.

    WAHA entrega algunos mensajes con un identificador ``@lid`` en lugar del
    número. Ese valor **no** es un teléfono: si se le antepone ``549`` y se le
    cambia el dominio, la respuesta no llega a nadie ni coincide con el
    teléfono guardado en la ficha del paciente. Por eso se conserva tal cual.
    """
    crudo = (valor or "").strip()
    if not crudo:
        return ""
    if es_identificador_lid(crudo):
        return crudo
    return normalizar_telefono_waha(crudo)


def construir_chat_id_waha(telefono: Optional[str]) -> str:
    """Arma el ``chatId`` que exige WAHA (``<número>@c.us``).

    Reglas:

    * Un ``chatId`` ya armado (``...@c.us`` o ``...@lid``) se devuelve tal
      cual: el ``@lid`` es un identificador opaco y normalizarlo lo rompe.
    * Un JID de teléfono (``...@s.whatsapp.net``) se pasa a ``...@c.us``.
    * Un teléfono limpio (``5493515945075`` o ``+54 9 351 594-5075``) se
      normaliza al formato argentino y se le agrega ``@c.us``.

    :return: el identificador listo para ``/api/sendText`` o ``""`` si no hay
        un teléfono utilizable.
    """
    crudo = (telefono or "").strip()
    if not crudo:
        return ""

    bajo = crudo.lower()
    if bajo.endswith(DOMINIO_LID) or bajo.endswith("@c.us"):
        return crudo

    numero = numero_de_jid(crudo)
    return f"{numero}@c.us" if numero else ""


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
