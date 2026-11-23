"""Servicio del Chatbot de Respuestas Automáticas (FAQ Bot) de WhatsApp.

Atiende los mensajes de **texto libre** que el paciente le escribe al número del
consultorio y los contesta sin intervención humana con un menú configurable:

    "hola" / "buenas" / "info" / "turnos"   ->  menú configurado (activas)
    1  ->  tarifa de la consulta (opción de tipo PRECIO)
    2  ->  obras sociales con convenio (opción de tipo OBRAS_SOCIALES)
    3  ->  dirección y horarios del consultorio (opción de tipo UBICACION)
    4  ->  *Atención Manual Requerida* (opción de tipo SECRETARIA)
    cualquier otro texto  ->  *Atención Manual Requerida*

Los números del ejemplo son los del menú histórico por defecto: la secretaría
puede renumerarlos, cambiar los títulos o reemplazar la acción de cada tecla.

Las opciones ya no están fijas en el código: se leen de la tabla
``opciones_menu_whatsapp``, que la secretaría administra desde el panel
(*Bot WhatsApp*). Cada opción tiene un dígito, un título y un tipo (texto libre,
precio, obras sociales, ubicación o derivar a una secretaria). Si la tabla está
vacía se usa el menú histórico de cuatro opciones como respaldo
(``whatsapp_menu_service.opciones_por_defecto``).

El envío hacia WAHA no se duplica acá: lo hace
``whatsapp_service.enviar_mensaje_texto`` (``POST`` **real** con ``httpx`` a
``{WAHA_URL}/api/sendText`` cuando están ``WAHA_URL`` y ``WAHA_API_KEY``;
**simulado** cuando no, igual que los recordatorios). Este módulo sólo decide
*qué* contestar, de modo que el transporte quede en un único lugar y se pueda
probar el árbol de respuestas sin tocar la red.
"""

import logging
import re
import unicodedata
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.money import redondear_monto
from app.core.whatsapp_utils import (
    formatear_fecha_recordatorio,
    normalizar_telefono_waha,
    profesional_con_articulo,
)
from app.models.enums import EstadoTurno, EstadoWhatsApp, TipoOpcionMenu
from app.models.obra_social import ObraSocial
from app.models.opcion_menu_whatsapp import OpcionMenuWhatsApp
from app.models.prestacion import Prestacion
from app.schemas.whatsapp import RespuestaBot
from app.services import whatsapp_conversacion_service, whatsapp_menu_service
from app.services.whatsapp_service import enviar_mensaje_texto

logger = logging.getLogger("app.services.whatsapp_bot")

#: Texto exacto del registro que busca la secretaría en los logs.
MENSAJE_ATENCION_MANUAL = "Atención Manual Requerida"

# --- Números históricos del menú (respaldo si la tabla está vacía) -----------
OPCION_PRECIO_CONSULTA = "1"
OPCION_OBRAS_SOCIALES = "2"
OPCION_UBICACION = "3"
OPCION_SECRETARIA = "4"

#: Palabras (y frases) que abren el menú. La comparación es por palabra completa
#: sobre el texto normalizado, así que "hola!" y "Hola, quería turnos" entran,
#: pero "informe" no entra por "info". Para agregar sinónimos, sumar acá.
PALABRAS_CLAVE_INICIALES: frozenset[str] = frozenset(
    {
        "hola",
        "buenas",
        "buenas tardes",
        "buenas noches",
        "buen dia",
        "info",
        "informacion",
        "turno",
        "turnos",
        "menu",
        "menu principal",
        "consulta",
        "consultas",
        "precio",
        "precios",
        "ayuda",
        "inicio",
        "empezar",
    }
)

#: Acepta "1", "1.", "1)", "(1)", "n°1", "opcion 1", "opción: 3" y el 1️⃣ del
#: teclado de WhatsApp. Un solo dígito: "23" no es una opción del menú y cae en
#: atención manual.
PATRON_OPCION = re.compile(
    r"(?:opcion|menu|numero|n)?\s*[n°º]?\s*:?\s*(\d)\s*[).\-:]?"
)

#: Puntuación que se descarta de los bordes antes de buscar una opción ("(1)").
PUNTUACION_BORDES = " ¿¡!.,;:()[]{}-—–"

#: Menú histórico que recibe el paciente si la tabla de opciones está vacía.
ENCABEZADO_MENU = (
    "¡Hola! 👋 Soy el asistente virtual del consultorio odontológico.\n\n"
    "Elegí una opción respondiendo con el número:\n\n"
)
MENU_PRINCIPAL = (
    ENCABEZADO_MENU
    + "1️⃣ Precio de la consulta\n"
    "2️⃣ Obras sociales con convenio\n"
    "3️⃣ Ubicación y horarios\n"
    "4️⃣ Hablar con una secretaria"
)

#: Se responde cuando la tabla de prestaciones todavía no tiene el precio cargado.
MENSAJE_PRECIO_NO_DISPONIBLE = (
    "🦷 *Consulta odontológica*\n\n"
    "En este momento no pudimos consultar el valor de la consulta.\n\n"
    "Escribí *4* y una secretaria te lo informa a la brevedad."
)

#: Respuestas aceptadas cuando hay un recordatorio esperando confirmación.
RESPUESTAS_CONFIRMAR: frozenset[str] = frozenset(
    {"1", "si", "confirmar", "confirmo", "confirmado"}
)
RESPUESTAS_CANCELAR: frozenset[str] = frozenset(
    {"2", "no", "cancelar", "cancelo", "cancelado"}
)

TEXTO_CONFIRMACION_INVALIDA = (
    "Para el turno del {fecha} a las {hora}:\n\n"
    "Respondé *1* (o *SI*) para confirmar.\n"
    "Respondé *2* (o *NO*) para cancelar."
)
TEXTO_TURNO_CONFIRMADO = (
    "✅ *Turno confirmado*\n\n"
    "{paciente}, te esperamos el {fecha} a las {hora} con {profesional}."
)
TEXTO_TURNO_CANCELADO = (
    "❌ *Turno cancelado*\n\n"
    "{paciente}, cancelamos el turno del {fecha} a las {hora} con {profesional}. "
    "Si querés reprogramarlo, escribí *MENÚ* y elegí una opción."
)


# ===========================================================================
#  Utilidades de texto, teléfono e importes
# ===========================================================================


def normalizar_texto(texto: Optional[str]) -> str:
    """Pasa el texto a minúsculas y le quita acentos, emojis y espacios de más.

    ``NFKD`` separa cada letra de su acento y cada emoji de sus modificadores (el
    1️⃣ del teclado de WhatsApp llega como ``'1' + VS16 + KEYCAP``): al descartar
    las marcas combinatorias (categorías ``Mn`` y ``Me``) queda ``"1"`` y ``"Holá"
    -> "hola"``, que es lo que comparan las palabras clave y el patrón de opción.
    """
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_marcas = "".join(c for c in descompuesto if unicodedata.category(c)[0] != "M")
    return " ".join(sin_marcas.lower().split())


def limpiar_palabra(palabra: str) -> str:
    """Quita la puntuación de los bordes de una palabra ("hola!" -> "hola")."""
    return palabra.strip("!.,;:¿?¡\"'()[]{}—-")


def detectar_opcion(texto_normalizado: str) -> Optional[str]:
    """Devuelve el dígito elegido del menú (``"0"``..``"9"``) o ``None``.

    Reconoce las formas en que la gente contesta de verdad: el número solo, con
    paréntesis o punto, con la palabra "opción" adelante o con el emoji del
    teclado. Devuelve también un dígito que no esté en el menú para que el
    llamador pueda registrarlo en el log de atención manual.
    """
    candidato = texto_normalizado.strip(PUNTUACION_BORDES)
    coincidencia = PATRON_OPCION.fullmatch(candidato)
    return coincidencia.group(1) if coincidencia else None


def es_saludo_inicial(texto_normalizado: str) -> bool:
    """Indica si el texto arranca con un saludo o palabra clave del bot."""
    if texto_normalizado in PALABRAS_CLAVE_INICIALES:
        return True
    palabras = {limpiar_palabra(p) for p in texto_normalizado.split()}
    return bool(palabras & PALABRAS_CLAVE_INICIALES)


def normalizar_destinatario(telefono: Optional[str]) -> str:
    """Deja el número en el formato canónico de WAHA (``549...``)."""
    return normalizar_telefono_waha(telefono)


def formatear_importe(valor: Optional[float]) -> str:
    """Devuelve el importe con separadores argentinos (``18000 -> "$18.000,00"``)."""
    monto = redondear_monto(valor)
    return "$" + f"{monto:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ===========================================================================
#  Consultas a los catálogos (opciones 1 y 2)
# ===========================================================================


def obtener_prestacion_consulta(db: Session) -> Optional[Prestacion]:
    """Devuelve la práctica de la consulta (código ``whatsapp_consulta_codigo``).

    Se busca el código ``'10100'`` (configurable) del Nomenclador de Córdoba
    *vigente*: una práctica dada de baja no se le ofrece al paciente. La
    comparación ignora los espacios del código para tolerar datos cargados a mano.
    """
    codigo = (settings.whatsapp_consulta_codigo or "10100").strip()
    stmt = (
        select(Prestacion)
        .where(
            func.replace(Prestacion.codigo_nomenclador, " ", "") == codigo,
            Prestacion.activa.is_(True),
        )
        .order_by(Prestacion.id)
    )
    return db.execute(stmt).scalars().first()


def listar_obras_sociales_activas(db: Session) -> Sequence[ObraSocial]:
    """Obras sociales vigentes, ordenadas alfabéticamente (opción 2)."""
    stmt = (
        select(ObraSocial)
        .where(ObraSocial.activa.is_(True))
        .order_by(func.lower(ObraSocial.nombre))
    )
    return db.execute(stmt).scalars().all()


# ===========================================================================
#  Redacción de las respuestas
# ===========================================================================


def _emoji_numero(numero: str) -> str:
    """Devuelve el dígito con el emoji de tecla que usa WhatsApp (``1️⃣``)."""
    return f"{numero}\ufe0f\u20e3" if numero.isdigit() else numero


def _numero_de_tipo(
    opciones: Sequence[OpcionMenuWhatsApp],
    tipo: TipoOpcionMenu,
    por_defecto: Optional[str] = None,
) -> Optional[str]:
    """Número de la primera opción activa del ``tipo`` (``None`` si no hay)."""
    for opcion in opciones:
        if opcion.tipo == tipo:
            return opcion.numero
    return por_defecto


def mensaje_menu(
    opciones: Optional[Sequence[OpcionMenuWhatsApp]] = None,
) -> str:
    """Texto del menú.

    Sin argumentos devuelve el menú histórico (``MENU_PRINCIPAL``); con la lista
    configurada arma un renglón por opción, en el mismo formato que ya conocen los
    pacientes (``1️⃣ Título``). El orden lo define el servicio del menú.
    """
    if opciones is None:
        return MENU_PRINCIPAL
    if not opciones:
        return ENCABEZADO_MENU + "(No hay opciones activas en este momento)"
    renglones = "\n".join(
        f"{_emoji_numero(opcion.numero)} {opcion.titulo}" for opcion in opciones
    )
    return ENCABEZADO_MENU + renglones


def mensaje_precio_consulta(
    prestacion: Prestacion,
    *,
    numero_obras_sociales: Optional[str] = OPCION_OBRAS_SOCIALES,
    numero_secretaria: Optional[str] = OPCION_SECRETARIA,
) -> str:
    """Tarifa de la consulta, con la descripción y el valor actuales.

    Los números de las opciones se toman del menú configurado: si la secretaría
    renumeró «Obras sociales» o «Hablar con una secretaria», el texto las
    referencia con el dígito correcto (y las omite si no existen).
    """
    mensaje = (
        "🦷 *Consulta odontológica*\n\n"
        f"{prestacion.descripcion}\n"
        f"Valor: *{formatear_importe(prestacion.valor_base)}*\n\n"
        "Es el arancel de referencia del Nomenclador de la Provincia de Córdoba: "
        "incluye el examen, el diagnóstico y el fichado del paciente."
    )

    referencias = []
    if numero_obras_sociales:
        referencias.append(
            f"escribí *{numero_obras_sociales}* para ver las coberturas con convenio"
        )
    if numero_secretaria:
        referencias.append(f"*{numero_secretaria}* para hablar con una secretaria")
    if referencias:
        mensaje += (
            "\n\nSi tenés obra social el valor puede estar cubierto: "
            + " o ".join(referencias)
            + "."
        )
    return mensaje


def mensaje_obras_sociales(
    obras: Sequence[ObraSocial],
    *,
    numero_secretaria: Optional[str] = OPCION_SECRETARIA,
) -> str:
    """Listado (en un **solo** mensaje) de las coberturas con convenio."""
    if not obras:
        mensaje = (
            "🏥 *Obras sociales*\n\n"
            "Por el momento no tenemos obras sociales con convenio cargadas."
        )
        if numero_secretaria:
            mensaje += (
                f"\n\nEscribí *{numero_secretaria}* y una secretaria te cuenta "
                "las opciones de pago."
            )
        return mensaje

    listado = "\n".join(f"• {obra.nombre}" for obra in obras)
    mensaje = (
        "🏥 *Obras sociales con convenio*\n\n"
        f"{listado}\n\n"
        f"Son {len(obras)} coberturas. Traé tu credencial a la consulta y te "
        "informamos el coseguro que corresponda."
    )
    if numero_secretaria:
        mensaje += (
            f"\n\nEscribí *{numero_secretaria}* si querés que una secretaria te "
            "confirme tu cobertura."
        )
    return mensaje


def mensaje_ubicacion(
    *, numero_secretaria: Optional[str] = OPCION_SECRETARIA
) -> str:
    """Dirección y horarios del consultorio (se configuran en el ``.env``)."""
    mensaje = (
        "📍 *Ubicación y horarios*\n\n"
        f"Dirección: {settings.whatsapp_consultorio_direccion}\n"
        f"Horarios de atención: {settings.whatsapp_consultorio_horarios}\n\n"
        "Te esperamos."
    )
    if numero_secretaria:
        mensaje += (
            f" Si querés agendar un turno escribí *{numero_secretaria}* y te damos "
            "una fecha."
        )
    return mensaje


# ===========================================================================
#  Árbol de decisión
# ===========================================================================


def _responder(
    *,
    tipo: str,
    destinatario: str,
    texto_entrante: str,
    respuesta: str,
    opcion: Optional[str] = None,
) -> RespuestaBot:
    """Envía la respuesta por WhatsApp y arma el resultado del bot."""
    mensaje_id = enviar_mensaje_texto(destinatario, respuesta)
    logger.info(
        "FAQ Bot: respuesta %s enviada a %s (opcion=%s mensaje_id=%s)",
        tipo,
        destinatario,
        opcion,
        mensaje_id,
    )
    return RespuestaBot(
        telefono=destinatario,
        texto_entrante=texto_entrante,
        tipo=tipo,  # type: ignore[arg-type]
        opcion=opcion,
        respuesta=respuesta,
        mensaje_id_saliente=mensaje_id,
        atencion_manual=False,
    )


def _atencion_manual(
    *,
    destinatario: str,
    texto_entrante: str,
    opcion: Optional[str],
    motivo: str,
) -> RespuestaBot:
    """Registra la conversación y **omite** la respuesta automática.

    Es el camino de las opciones de tipo ``SECRETARIA`` y de todo texto que el
    bot no entiende: no se envía nada por WhatsApp y queda el log **'Atención
    Manual Requerida'** con el teléfono, el mensaje y el motivo, que es lo que
    revisa la secretaría para contestar a mano.
    """
    logger.warning(
        "%s: telefono=%s opcion=%s motivo=%s mensaje=%r",
        MENSAJE_ATENCION_MANUAL,
        destinatario,
        opcion,
        motivo,
        texto_entrante,
    )
    return RespuestaBot(
        telefono=destinatario,
        texto_entrante=texto_entrante,
        tipo="MANUAL",
        opcion=opcion,
        respuesta=None,
        mensaje_id_saliente=None,
        atencion_manual=True,
    )


def _responder_opcion(
    db: Session,
    opcion: OpcionMenuWhatsApp,
    opciones: Sequence[OpcionMenuWhatsApp],
    *,
    destinatario: str,
    texto_entrante: str,
) -> RespuestaBot:
    """Ejecuta la acción configurada para la opción que eligió el paciente."""
    numero_obras = _numero_de_tipo(opciones, TipoOpcionMenu.OBRAS_SOCIALES)
    numero_secretaria = _numero_de_tipo(opciones, TipoOpcionMenu.SECRETARIA)

    # --- Precio de la consulta (datos del nomenclador) -----------------------
    if opcion.tipo == TipoOpcionMenu.PRECIO:
        prestacion = obtener_prestacion_consulta(db)
        if prestacion is None:
            logger.warning(
                "FAQ Bot: no hay una prestación activa con el código de consulta %r.",
                settings.whatsapp_consulta_codigo,
            )
            if numero_secretaria:
                respuesta = MENSAJE_PRECIO_NO_DISPONIBLE.replace(
                    f"*{OPCION_SECRETARIA}*", f"*{numero_secretaria}*"
                )
            else:
                respuesta = MENSAJE_PRECIO_NO_DISPONIBLE.replace(
                    f"Escribí *{OPCION_SECRETARIA}* y una secretaria",
                    "Una secretaria",
                )
            return _responder(
                tipo="PRECIO",
                destinatario=destinatario,
                texto_entrante=texto_entrante,
                respuesta=respuesta,
                opcion=opcion.numero,
            )
        return _responder(
            tipo="PRECIO",
            destinatario=destinatario,
            texto_entrante=texto_entrante,
            respuesta=mensaje_precio_consulta(
                prestacion,
                numero_obras_sociales=numero_obras,
                numero_secretaria=numero_secretaria,
            ),
            opcion=opcion.numero,
        )

    # --- Obras sociales vigentes ---------------------------------------------
    if opcion.tipo == TipoOpcionMenu.OBRAS_SOCIALES:
        return _responder(
            tipo="OBRAS_SOCIALES",
            destinatario=destinatario,
            texto_entrante=texto_entrante,
            respuesta=mensaje_obras_sociales(
                listar_obras_sociales_activas(db),
                numero_secretaria=numero_secretaria,
            ),
            opcion=opcion.numero,
        )

    # --- Ubicación y horarios -------------------------------------------------
    if opcion.tipo == TipoOpcionMenu.UBICACION:
        return _responder(
            tipo="UBICACION",
            destinatario=destinatario,
            texto_entrante=texto_entrante,
            respuesta=mensaje_ubicacion(numero_secretaria=numero_secretaria),
            opcion=opcion.numero,
        )

    # --- Derivar a una persona ------------------------------------------------
    if opcion.tipo == TipoOpcionMenu.SECRETARIA:
        return _atencion_manual(
            destinatario=destinatario,
            texto_entrante=texto_entrante,
            opcion=opcion.numero,
            motivo=f"el paciente eligió «{opcion.titulo}» (atención humana)",
        )

    # --- Texto libre cargado por la secretaría -------------------------------
    if opcion.tipo == TipoOpcionMenu.TEXTO:
        if not (opcion.respuesta or "").strip():
            return _atencion_manual(
                destinatario=destinatario,
                texto_entrante=texto_entrante,
                opcion=opcion.numero,
                motivo=f"la opción «{opcion.titulo}» no tiene texto configurado",
            )
        return _responder(
            tipo="TEXTO",
            destinatario=destinatario,
            texto_entrante=texto_entrante,
            respuesta=opcion.respuesta,
            opcion=opcion.numero,
        )

    # Tipo desconocido (dato viejo o corrupto): no se inventa una respuesta.
    return _atencion_manual(
        destinatario=destinatario,
        texto_entrante=texto_entrante,
        opcion=opcion.numero,
        motivo=f"la opción «{opcion.titulo}» tiene un tipo desconocido",
    )


def _datos_del_turno(turno) -> dict:
    """Datos que se muestran en los mensajes de confirmación/cancelación."""
    return {
        "paciente": (
            (turno.paciente.nombre if turno.paciente else "").strip() or "paciente"
        ),
        "fecha": formatear_fecha_recordatorio(turno.fecha_hora_inicio),
        "hora": turno.fecha_hora_inicio.strftime("%H:%M"),
        "profesional": (
            profesional_con_articulo(turno.doctor.nombre if turno.doctor else "")
            or "tu profesional"
        ),
    }


def _clasificar_confirmacion(texto_normalizado: str) -> Optional[str]:
    """Devuelve ``'CONFIRMAR'``, ``'CANCELAR'`` o ``None``.

    Acepta las formas que indica el recordatorio (``1``/``si`` y ``2``/``no``) y
    sus variantes naturales («confirmo», «cancelar»). La comparación usa el
    texto ya normalizado (sin acentos ni mayúsculas).
    """
    candidato = (texto_normalizado or "").strip(PUNTUACION_BORDES)
    if not candidato:
        return None

    primera = candidato.split()[0]
    for valor in (candidato, primera):
        if valor in RESPUESTAS_CONFIRMAR:
            return "CONFIRMAR"
        if valor in RESPUESTAS_CANCELAR:
            return "CANCELAR"

    if candidato.startswith("confirm"):
        return "CONFIRMAR"
    if candidato.startswith("cancel"):
        return "CANCELAR"
    return None


def _responder_confirmacion(
    db: Session,
    *,
    destinatario: str,
    texto_entrante: str,
    texto_normalizado: str,
    turno,
) -> RespuestaBot:
    """Resuelve la respuesta de texto de un recordatorio pendiente.

    Si el paciente no escribió una opción válida se vuelve a preguntar y el
    estado sigue en ``CONFIRMACION_TURNO``. Cuando confirma o cancela, se envía
    el acuse **antes** de actualizar la base: si WAHA falla, el turno queda como
    estaba y el paciente puede reintentar.
    """
    datos = _datos_del_turno(turno)
    accion = _clasificar_confirmacion(texto_normalizado)

    if accion is None:
        respuesta = TEXTO_CONFIRMACION_INVALIDA.format(
            fecha=datos["fecha"], hora=datos["hora"]
        )
        mensaje_id = enviar_mensaje_texto(destinatario, respuesta)
        logger.info(
            "FAQ Bot: respuesta de confirmación no reconocida de %s (turno=%s)",
            destinatario,
            turno.id,
        )
        return RespuestaBot(
            telefono=destinatario,
            texto_entrante=texto_entrante,
            tipo="CONFIRMACION_PENDIENTE",
            respuesta=respuesta,
            mensaje_id_saliente=mensaje_id,
            turno_id=turno.id,
            estado_turno=turno.estado_turno,
            estado_whatsapp=turno.estado_whatsapp,
        )

    if accion == "CONFIRMAR":
        nuevo_estado = EstadoTurno.CONFIRMADO
        tipo = "CONFIRMACION"
        opcion = "1"
        respuesta = TEXTO_TURNO_CONFIRMADO.format(**datos)
    else:
        nuevo_estado = EstadoTurno.CANCELADO
        tipo = "CANCELACION"
        opcion = "2"
        respuesta = TEXTO_TURNO_CANCELADO.format(**datos)

    mensaje_id = enviar_mensaje_texto(destinatario, respuesta)

    turno.estado_turno = nuevo_estado
    turno.estado_whatsapp = EstadoWhatsApp.RESPONDIDO
    whatsapp_conversacion_service.cerrar_confirmacion(db, destinatario)

    logger.info(
        "FAQ Bot: turno id=%s -> %s por respuesta %r de %s",
        turno.id,
        nuevo_estado,
        texto_entrante,
        destinatario,
    )
    return RespuestaBot(
        telefono=destinatario,
        texto_entrante=texto_entrante,
        tipo=tipo,
        opcion=opcion,
        respuesta=respuesta,
        mensaje_id_saliente=mensaje_id,
        turno_id=turno.id,
        estado_turno=turno.estado_turno,
        estado_whatsapp=turno.estado_whatsapp,
    )


def responder_mensaje(db: Session, telefono: str, texto: str) -> RespuestaBot:
    """Contesta un mensaje entrante de texto siguiendo el menú configurado.

    Los pasos, en orden:

    1. **Normaliza** el texto (minúsculas, sin acentos) y el teléfono (sólo dígitos).
    2. Si el teléfono tiene una **confirmación pendiente** (se le envió un
       recordatorio), interpreta ``1`` / ``si`` como confirmar y ``2`` / ``no``
       como cancelar. Cualquier otra respuesta vuelve a preguntar por el turno.
    3. Si no hay confirmación pendiente, lee el **menú vigente** (opciones
       activas) y ejecuta la acción de la tecla elegida.
    4. Un saludo o palabra clave envía el menú.
    5. **Cualquier otro mensaje** también muestra el menú: es la forma de volver
       al inicio cuando el paciente escribe algo que el bot no reconoce.

    :param db: sesión para leer el menú, ``Prestacion`` y ``ObraSocial``.
    :param telefono: número del remitente tal como llega en el webhook (``from``).
    :param texto: cuerpo del mensaje (``body``).
    """
    destinatario = normalizar_destinatario(telefono)
    normalizado = normalizar_texto(texto)
    logger.info("FAQ Bot: mensaje entrante de %s: %r", destinatario or "?", texto)

    # --- ¿Está respondiendo un recordatorio? ---------------------------------
    pendiente = whatsapp_conversacion_service.obtener_confirmacion_pendiente(
        db, destinatario
    )
    if pendiente is not None:
        _, turno = pendiente
        return _responder_confirmacion(
            db,
            destinatario=destinatario,
            texto_entrante=texto,
            texto_normalizado=normalizado,
            turno=turno,
        )

    # Sin confirmación pendiente se registra el estado de menú del paciente.
    whatsapp_conversacion_service.marcar_menu(db, destinatario)

    if not normalizado:
        return _atencion_manual(
            destinatario=destinatario,
            texto_entrante=texto,
            opcion=None,
            motivo="el mensaje no tiene texto",
        )

    opciones = whatsapp_menu_service.obtener_menu_vigente(db)
    numero = detectar_opcion(normalizado)

    # --- Una tecla del menú: se busca la opción configurada ------------------
    if numero is not None:
        opcion = next((o for o in opciones if o.numero == numero), None)
        if opcion is None:
            logger.info(
                "FAQ Bot: la opción %s no existe en el menú; se muestra el menú a %s",
                numero,
                destinatario or "?",
            )
            if not opciones:
                return _atencion_manual(
                    destinatario=destinatario,
                    texto_entrante=texto,
                    opcion=numero,
                    motivo="el menú no tiene opciones activas",
                )
            return _responder(
                tipo="MENU",
                destinatario=destinatario,
                texto_entrante=texto,
                respuesta=mensaje_menu(opciones),
            )
        logger.info(
            "FAQ Bot: opción %s (%s) elegida por %s",
            opcion.numero,
            opcion.titulo,
            destinatario or "?",
        )
        return _responder_opcion(
            db,
            opcion,
            opciones,
            destinatario=destinatario,
            texto_entrante=texto,
        )

    # --- Saludo o palabra clave: se manda el menú ----------------------------
    if es_saludo_inicial(normalizado):
        if not opciones:
            return _atencion_manual(
                destinatario=destinatario,
                texto_entrante=texto,
                opcion=None,
                motivo="el menú no tiene opciones activas",
            )
        return _responder(
            tipo="MENU",
            destinatario=destinatario,
            texto_entrante=texto,
            respuesta=mensaje_menu(opciones),
        )

    # --- Texto libre no reconocido: se vuelve al menú ------------------------
    if not opciones:
        return _atencion_manual(
            destinatario=destinatario,
            texto_entrante=texto,
            opcion=None,
            motivo="el menú no tiene opciones activas",
        )
    return _responder(
        tipo="MENU",
        destinatario=destinatario,
        texto_entrante=texto,
        respuesta=mensaje_menu(opciones),
    )
