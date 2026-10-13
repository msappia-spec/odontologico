"""Esquemas Pydantic de ``Paciente``."""

import re
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationInfo,
    field_validator,
)

# Documento del paciente (DNI, CUIT o pasaporte): entre 5 y 20 caracteres
# alfanuméricos, ya sin los separadores habituales (puntos, guiones, espacios).
_RE_DOCUMENTO = re.compile(r"^[0-9A-Za-z]{5,20}$")


class PacienteDatosAdministrativos(BaseModel):
    """Datos administrativos opcionales del paciente (alta, edición y lectura).

    Los tres campos son opcionales pero **normalizados**:

    * Las cadenas vacías o con sólo espacios se guardan como ``NULL`` (así el
      formulario web puede enviar los campos sin completar sin ensuciar la base).
    * El ``dni`` se limpia de puntos, guiones y espacios y se valida su formato,
      de modo que ``"30.123.456"`` y ``"30123456"`` sean el mismo documento
      (clave para el índice único ``uq_paciente_dni``).
    """

    dni: Optional[str] = Field(
        None,
        max_length=20,
        description="Documento del paciente (único). Se guarda sin puntos ni guiones.",
    )
    obra_social: Optional[str] = Field(
        None,
        max_length=120,
        description="Obra social o prepaga (p. ej. OSDE, Swiss Medical, IOMA).",
    )
    numero_afiliado: Optional[str] = Field(
        None,
        max_length=60,
        description="Número de afiliado dentro de la obra social.",
    )

    @field_validator("dni", "obra_social", "numero_afiliado", mode="before")
    @classmethod
    def _normalizar_opcionales(cls, valor, info: ValidationInfo):
        """Convierte cadenas vacías en ``None`` y normaliza el documento."""
        if valor is None:
            return None
        texto = str(valor).strip()
        if not texto:
            return None
        if info.field_name == "dni":
            texto = re.sub(r"[\s.\-]", "", texto)
            if not _RE_DOCUMENTO.match(texto):
                raise ValueError(
                    "El DNI debe tener entre 5 y 20 caracteres alfanuméricos "
                    "(se ignoran puntos, guiones y espacios)."
                )
        return texto


class PacienteBase(PacienteDatosAdministrativos):
    nombre: str = Field(..., min_length=1, max_length=120)
    telefono: str = Field(
        ...,
        min_length=6,
        max_length=30,
        description="Teléfono en formato internacional, p. ej. +5491122334455",
    )
    email: Optional[EmailStr] = None


class PacienteCreate(PacienteBase):
    """Payload de creación de un paciente."""


class PacienteUpdate(PacienteDatosAdministrativos):
    """Payload de actualización parcial de un paciente (``PATCH``).

    Todos los campos son opcionales: sólo se aplican los enviados. Enviar un
    campo con ``null`` lo limpia (p. ej. ``{"dni": null}`` borra el documento).
    """

    nombre: Optional[str] = Field(None, min_length=1, max_length=120)
    telefono: Optional[str] = Field(None, min_length=6, max_length=30)
    email: Optional[EmailStr] = None


class PacienteRead(PacienteBase):
    """Representación de salida de un paciente."""

    model_config = ConfigDict(from_attributes=True)

    id: int
