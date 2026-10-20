"""Esquemas Pydantic para el flujo de WhatsApp (webhook y recordatorios)."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EstadoTurno, EstadoWhatsApp

# ---------------------------------------------------------------------------
# Payload entrante del webhook (estructura simplificada de WhatsApp Cloud API)
# ---------------------------------------------------------------------------


class WhatsAppText(BaseModel):
    """Cuerpo de un mensaje de **texto** entrante (``type`` = ``text``)."""

    model_config = ConfigDict(extra="ignore")

    body: Optional[str] = Field(
        default=None, description="Texto que escribió el paciente"
    )
    preview_url: Optional[bool] = None


class WhatsAppButtonReply(BaseModel):
    """Respuesta de botón/interactiva enviada por el paciente."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(..., description="Identificador del botón, p. ej. 'confirmar_turno'")
    title: Optional[str] = Field(default=None, description="Texto visible del botón")


class WhatsAppContext(BaseModel):
    """Contexto que referencia el mensaje saliente que originó la respuesta."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: Optional[str] = Field(default=None, alias="from")
    id: Optional[str] = Field(
        default=None,
        description="ID (wamid) del mensaje originalmente enviado por el negocio",
    )


class WhatsAppInteractive(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Optional[str] = None
    button_reply: Optional[WhatsAppButtonReply] = None


class WhatsAppInboundMessage(BaseModel):
    """Mensaje entrante de WhatsApp."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: Optional[str] = Field(default=None, alias="from")
    id: str = Field(..., description="ID del mensaje entrante")
    timestamp: Optional[str] = None
    type: Optional[str] = None
    context: Optional[WhatsAppContext] = None
    interactive: Optional[WhatsAppInteractive] = None
    text: Optional[WhatsAppText] = None


class WhatsAppChangeValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messaging_product: Optional[str] = None
    messages: Optional[List[WhatsAppInboundMessage]] = None


class WhatsAppChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    field: Optional[str] = None
    value: WhatsAppChangeValue


class WhatsAppEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    changes: List[WhatsAppChange] = Field(default_factory=list)


class WhatsAppWebhookPayload(BaseModel):
    """Cuerpo completo (JSON) que WhatsApp envía a nuestro webhook."""

    model_config = ConfigDict(extra="ignore")

    object: Optional[str] = None
    entry: List[WhatsAppEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Esquemas de salida
# ---------------------------------------------------------------------------


class WebhookProcesadoResponse(BaseModel):
    """Resultado del procesamiento de un webhook entrante de WhatsApp.

    El mismo endpoint atiende dos tipos de mensaje y el campo ``tipo`` dice cuál
    se procesó:

    * ``BOTON`` — respuesta a un recordatorio (**Confirmar** / **Cancelar**): se
      devuelve el turno actualizado (``turno_id``, ``estado_turno``,
      ``estado_whatsapp``, ``boton``).
    * ``BOT`` — mensaje de **texto libre** del paciente: lo contesta el FAQ Bot
      (``app.services.whatsapp_bot_service``) y se devuelve la ``opcion``
      detectada, el ``respuesta`` enviado y el ``mensaje_id_saliente`` de Meta.
      ``atencion_manual`` queda en ``True`` cuando el texto no es una opción del
      menú: no hay respuesta automática y lo tiene que atender una secretaria.

    Los campos de cada rama son opcionales para poder tipar las dos respuestas
    con un único modelo (retrocompatible con el contrato anterior del webhook).
    """

    status: str = "ok"
    tipo: Literal["BOTON", "BOT"] = Field(
        default="BOTON", description="Tipo de mensaje procesado: 'BOTON' o 'BOT'"
    )

    # --- Rama 1: respuesta de botón de un recordatorio ---
    turno_id: Optional[int] = None
    mensaje_id_contextual: Optional[str] = None
    boton: Optional[str] = None
    estado_turno: Optional[EstadoTurno] = None
    estado_whatsapp: Optional[EstadoWhatsApp] = None

    # --- Rama 2: diálogo del FAQ Bot ---
    telefono: Optional[str] = None
    opcion: Optional[str] = None
    respuesta: Optional[str] = None
    mensaje_id_saliente: Optional[str] = None
    atencion_manual: bool = Field(
        default=False,
        description="True cuando el mensaje necesita atención humana (opción 4 o texto libre)",
    )

    # --- Común a las dos ramas ---
    mensaje_id_entrante: Optional[str] = None


class RespuestaBot(BaseModel):
    """Resultado del FAQ Bot para un mensaje entrante de texto.

    Lo devuelve ``whatsapp_bot_service.responder_mensaje`` y sirve tanto para el
    webhook como para invocar el bot a mano (pruebas, consola, scripts).
    """

    telefono: str
    texto_entrante: str
    tipo: Literal[
        "MENU", "PRECIO", "OBRAS_SOCIALES", "UBICACION", "TEXTO", "MANUAL"
    ]
    opcion: Optional[str] = Field(
        default=None, description="Opción del menú detectada ('1'..'4'), si hubo"
    )
    respuesta: Optional[str] = Field(
        default=None, description="Texto enviado al paciente; None si requiere atención manual"
    )
    mensaje_id_saliente: Optional[str] = Field(
        default=None, description="wamid devuelto por Meta (o 'wamid.SIMULADO...')"
    )
    atencion_manual: bool = Field(
        default=False, description="True cuando no hubo respuesta automática"
    )


class RecordatorioEnviado(BaseModel):
    """Detalle del envío de un recordatorio individual."""

    turno_id: int
    telefono: str
    doctor: str
    fecha_hora_inicio: str
    resultado: str  # ENVIADO | FALLIDO
    whatsapp_mensaje_id: Optional[str] = None
    error: Optional[str] = None


class RecordatorioResultado(BaseModel):
    """Resumen de la ejecución de la tarea programada de recordatorios."""

    fecha_objetivo: str
    dias_antelacion: int
    evaluados: int
    enviados: int
    fallidos: int
    detalles: List[RecordatorioEnviado] = Field(default_factory=list)


class RecordatorioPendiente(BaseModel):
    """Recordatorio pendiente listo para enviar por enlace Click-to-Chat.

    Es el ítem que consume la pestaña *Recordatorios Pendientes* del panel: trae
    los datos del turno, el mensaje ya redactado y el enlace oficial de WhatsApp
    (``https://wa.me/<telefono>?text=<mensaje>``) con el texto codificado
    (URL Encoding), de modo que la secretaría sólo deba pulsar el botón verde.
    """

    turno_id: int
    paciente_id: int
    paciente: str
    doctor: str
    telefono: str = Field(..., description="Teléfono tal como está registrado")
    telefono_whatsapp: str = Field(
        ...,
        description=(
            "Sólo dígitos y con código de país, en el formato que exige wa.me "
            "(por ejemplo '5491122334455'). Cadena vacía si no hay teléfono."
        ),
    )
    fecha_hora_inicio: datetime
    fecha_hora_fin: datetime
    estado_turno: EstadoTurno
    estado_whatsapp: EstadoWhatsApp
    dias_restantes: int = Field(
        ..., description="Días que faltan para el turno (0 = es hoy)"
    )
    mensaje: str = Field(..., description="Texto personalizado del recordatorio")
    enlace_whatsapp: str = Field(
        ...,
        description=(
            "URL de Click-to-Chat (wa.me) con el mensaje ya codificado, lista "
            "para abrir en WhatsApp. Cadena vacía si el paciente no tiene un "
            "teléfono válido cargado."
        ),
    )
