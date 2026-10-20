"""Esquemas Pydantic del menú configurable del FAQ Bot de WhatsApp.

El número es un único dígito (``0``..``9``) y el título se guarda sin espacios
sobrantes. Una opción ``TEXTO`` exige el mensaje que se le envía al paciente; en
los tipos ``PRECIO``, ``OBRAS_SOCIALES``, ``UBICACION`` y ``SECRETARIA`` el texto
lo arma el bot con los datos del sistema y ``respuesta`` se ignora.

``OpcionMenuUpdate`` deja todos los campos opcionales porque es el payload de
``PATCH``: la combinación final (tipo + respuesta) se valida en la capa de
servicio, que es la que conoce el estado actual de la fila.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import TipoOpcionMenu

#: Un solo dígito: es la tecla que el paciente escribe en WhatsApp.
PATRON_NUMERO = r"^\d$"


class OpcionMenuBase(BaseModel):
    """Datos completos de una opción del menú."""

    numero: str = Field(
        ...,
        pattern=PATRON_NUMERO,
        description="Dígito que responde el paciente para elegir la opción (0-9).",
    )
    titulo: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description="Texto que se muestra en el menú, p. ej. «Precio de la consulta».",
    )
    tipo: TipoOpcionMenu = Field(
        TipoOpcionMenu.TEXTO,
        description=(
            "TEXTO envía la respuesta cargada; PRECIO, OBRAS_SOCIALES y UBICACION "
            "arman el mensaje con datos del sistema; SECRETARIA deriva a una persona."
        ),
    )
    respuesta: Optional[str] = Field(
        None,
        max_length=2000,
        description="Mensaje a enviar cuando el tipo es TEXTO (obligatorio en ese caso).",
    )
    activa: bool = Field(
        True,
        description="Una opción inactiva no se muestra ni se atiende, pero se conserva.",
    )
    orden: int = Field(
        0,
        ge=0,
        le=99,
        description="Orden de aparición en el menú (empate: por número).",
    )

    @field_validator("numero", mode="before")
    @classmethod
    def _normalizar_numero(cls, valor):
        """Acepta ``1`` y ``\" 1 \"`` y los guarda como el dígito limpio."""
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("titulo", mode="before")
    @classmethod
    def _normalizar_titulo(cls, valor):
        """Quita los espacios sobrantes del título."""
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("respuesta", mode="before")
    @classmethod
    def _normalizar_respuesta(cls, valor):
        """Convierte las cadenas vacías en ``None`` (el panel manda ``\"\"``)."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

    @model_validator(mode="after")
    def _texto_requiere_respuesta(self) -> "OpcionMenuBase":
        """Una opción de tipo ``TEXTO`` sin mensaje no tendría nada que enviar."""
        if self.tipo is TipoOpcionMenu.TEXTO and not self.respuesta:
            raise ValueError(
                "Una opción de tipo TEXTO necesita el texto de la respuesta."
            )
        return self


class OpcionMenuCreate(OpcionMenuBase):
    """Payload de creación de una opción (``POST /whatsapp/menu``)."""


class OpcionMenuUpdate(BaseModel):
    """Payload de actualización parcial de una opción (``PATCH``).

    Todos los campos son opcionales: sólo se aplican los enviados. La validación
    de ``tipo`` + ``respuesta`` se hace en el servicio con el estado final.
    ``null`` sólo es válido en ``respuesta``; en el resto de los campos hay que
    omitirlos para dejarlos sin cambios (``null`` se rechaza con ``422``).
    """

    numero: Optional[str] = Field(None, pattern=PATRON_NUMERO)
    titulo: Optional[str] = Field(None, min_length=1, max_length=80)
    tipo: Optional[TipoOpcionMenu] = None
    respuesta: Optional[str] = Field(None, max_length=2000)
    activa: Optional[bool] = None
    orden: Optional[int] = Field(None, ge=0, le=99)

    @field_validator("numero", "titulo", "tipo", "activa", "orden", mode="before")
    @classmethod
    def _rechazar_nulos(cls, valor):
        """En un ``PATCH``, omitir el campo es «sin cambios»; ``null`` no."""
        if valor is None:
            raise ValueError(
                "Este campo no admite null; omitilo para no modificarlo."
            )
        return valor

    @field_validator("numero", mode="before")
    @classmethod
    def _normalizar_numero(cls, valor):
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("titulo", mode="before")
    @classmethod
    def _normalizar_titulo(cls, valor):
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("respuesta", mode="before")
    @classmethod
    def _normalizar_respuesta(cls, valor):
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class OpcionMenuRead(OpcionMenuBase):
    """Representación de salida de una opción del menú."""

    model_config = ConfigDict(from_attributes=True)

    id: int


class MenuWhatsAppVistaPrevia(BaseModel):
    """Texto del menú activo (o el aviso de menú vacío) al escribir «hola»."""

    texto: str
    opciones: int = Field(
        ..., description="Cantidad de opciones activas incluidas en la vista previa."
    )
