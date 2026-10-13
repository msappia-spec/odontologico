"""Esquemas Pydantic de ``ObraSocial``.

El nombre se normaliza (sin espacios sobrantes) y un ``codigo`` vacío se guarda
como ``NULL`` para que el formulario web pueda enviar los campos sin completar
sin ensuciar la base, igual que los datos administrativos del paciente.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ObraSocialBase(BaseModel):
    """Datos de una cobertura médica."""

    nombre: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Nombre de la cobertura (único), p. ej. OSDE o Medicus.",
    )
    codigo: Optional[str] = Field(
        None,
        max_length=30,
        description="Código interno opcional con el que se factura la cobertura.",
    )
    activa: bool = Field(
        True,
        description=(
            "Una cobertura inactiva (dada de baja) deja de ofrecerse en los "
            "desplegables, pero se conserva para los pacientes que la tienen."
        ),
    )

    @field_validator("nombre", mode="before")
    @classmethod
    def _normalizar_nombre(cls, valor):
        """Quita los espacios sobrantes del nombre (la unicidad es por nombre)."""
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("codigo", mode="before")
    @classmethod
    def _normalizar_codigo(cls, valor):
        """Convierte las cadenas vacías en ``None``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class ObraSocialCreate(ObraSocialBase):
    """Payload de creación de una obra social (``POST``)."""


class ObraSocialUpdate(BaseModel):
    """Payload de actualización parcial de una obra social (``PATCH``/``PUT``).

    Todos los campos son opcionales: sólo se aplican los enviados. Enviar
    ``codigo`` con ``null`` lo limpia.
    """

    nombre: Optional[str] = Field(None, min_length=1, max_length=120)
    codigo: Optional[str] = Field(None, max_length=30)
    activa: Optional[bool] = None

    @field_validator("nombre", mode="before")
    @classmethod
    def _normalizar_nombre(cls, valor):
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("codigo", mode="before")
    @classmethod
    def _normalizar_codigo(cls, valor):
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class ObraSocialRead(ObraSocialBase):
    """Representación de salida de una obra social."""

    model_config = ConfigDict(from_attributes=True)

    id: int
