"""Esquemas Pydantic de ``Prestacion`` (catálogo de prácticas del Módulo 3)."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.money import redondear_monto


class PrestacionBase(BaseModel):
    """Datos de una práctica facturable."""

    codigo_nomenclador: str = Field(
        ...,
        min_length=1,
        max_length=30,
        description=(
            "Código con el que la obra social reconoce la práctica (p. ej. "
            "``01.01`` del Nomenclador Nacional). Es único."
        ),
    )
    descripcion: str = Field(
        ..., min_length=1, max_length=200, description="Nombre de la práctica."
    )
    valor_base: float = Field(
        0,
        ge=0,
        description=(
            "Precio de referencia cuando el paciente no tiene cobertura o la "
            "cobertura no tiene arancel cargado para esta práctica."
        ),
    )
    activa: bool = Field(
        True,
        description=(
            "Una práctica inactiva (dada de baja) deja de ofrecerse al facturar, "
            "pero se conserva: sus aranceles y las facturas ya emitidas la usan."
        ),
    )

    @field_validator("codigo_nomenclador", mode="before")
    @classmethod
    def _normalizar_codigo(cls, valor):
        """Recorta y pasa a mayúsculas el código (la unicidad no distingue caso)."""
        if valor is None:
            return valor
        return str(valor).strip().upper()

    @field_validator("descripcion", mode="before")
    @classmethod
    def _normalizar_descripcion(cls, valor):
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("valor_base", mode="after")
    @classmethod
    def _redondear_valor(cls, valor: float) -> float:
        """Guarda el importe con dos decimales (convención del Módulo 3)."""
        return redondear_monto(valor)


class PrestacionCreate(PrestacionBase):
    """Payload de creación de una práctica (``POST``)."""


class PrestacionUpdate(BaseModel):
    """Payload de actualización parcial de una práctica (``PATCH``)."""

    codigo_nomenclador: Optional[str] = Field(None, min_length=1, max_length=30)
    descripcion: Optional[str] = Field(None, min_length=1, max_length=200)
    valor_base: Optional[float] = Field(None, ge=0)
    activa: Optional[bool] = None

    @field_validator("codigo_nomenclador", mode="before")
    @classmethod
    def _normalizar_codigo(cls, valor):
        if valor is None:
            return valor
        return str(valor).strip().upper()

    @field_validator("descripcion", mode="before")
    @classmethod
    def _normalizar_descripcion(cls, valor):
        if valor is None:
            return valor
        return str(valor).strip()

    @field_validator("valor_base", mode="after")
    @classmethod
    def _redondear_valor(cls, valor: Optional[float]) -> Optional[float]:
        return None if valor is None else redondear_monto(valor)


class PrestacionRead(PrestacionBase):
    """Representación de salida de una práctica."""

    model_config = ConfigDict(from_attributes=True)

    id: int
