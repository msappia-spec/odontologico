"""Esquemas Pydantic del odontograma (Módulo 4).

El odontograma reproduce el cuadro de la Historia Clínica General: el
profesional elige un estado (**rojo** = prestación existente, **azul** =
prestación requerida, **X** = ausente o a extraer) y lo aplica a cada diente en
notación FDI (11 a 48).

La lectura viaja con la cabecera (prótesis, coronas, cantidad de dientes) y un
resumen de cuántos dientes hay en cada estado, para que la pantalla pinte la
leyenda sin recorrer la lista.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.odontograma import DIENTES_PERMANENTES, MAX_TEXTO_ODONTOGRAMA

#: Estado de un diente, con el color que usa el PDF.
EstadoDiente = Literal["EXISTENTE", "REQUERIDO", "AUSENTE"]

#: Campos de texto libre del odontograma.
_CAMPOS_TEXTO: tuple[str, ...] = (
    "protesis_fija",
    "protesis_removible",
    "coronas",
    "observaciones",
)


def _limpiar_texto(valor, info: ValidationInfo):
    """Recorta el texto y convierte el vacío en ``NULL``."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if len(texto) > MAX_TEXTO_ODONTOGRAMA:
        raise ValueError(
            f"{info.field_name} no puede superar los "
            f"{MAX_TEXTO_ODONTOGRAMA} caracteres."
        )
    return texto or None


class OdontogramaDienteCreate(BaseModel):
    """Estado que se le pinta a un diente."""

    diente: int = Field(
        ...,
        description=(
            "Número FDI del diente permanente: 11 a 18, 21 a 28, 31 a 38 o "
            "41 a 48."
        ),
    )
    estado: EstadoDiente = Field(
        ...,
        description=(
            "``EXISTENTE`` (rojo: prestación existente), ``REQUERIDO`` (azul: "
            "prestación requerida) o ``AUSENTE`` (X: ausente o a extraer)."
        ),
    )
    notas: Optional[str] = Field(
        None, description="Detalle libre del diente (opcional)."
    )

    @field_validator("diente")
    @classmethod
    def _diente_fdi(cls, valor: int) -> int:
        """Rechaza números que no correspondan a un diente permanente."""
        if valor not in DIENTES_PERMANENTES:
            raise ValueError(
                "El diente debe estar en notación FDI entre 11 y 48 "
                "(por ejemplo 11, 26 o 47)."
            )
        return valor

    @field_validator("estado", mode="before")
    @classmethod
    def _estado_en_mayusculas(cls, valor):
        """Acepta ``existente`` y lo normaliza a ``EXISTENTE``."""
        return str(valor).strip().upper() if valor is not None else valor

    @field_validator("notas", mode="before")
    @classmethod
    def _notas(cls, valor, info: ValidationInfo):
        return _limpiar_texto(valor, info)


class OdontogramaUpdate(BaseModel):
    """Payload del ``PUT``: reemplaza el odontograma completo del paciente."""

    doctor_id: Optional[int] = Field(
        None,
        gt=0,
        description=(
            "Profesional que revisó al paciente. Si se omite se conserva el "
            "último que guardó el odontograma."
        ),
    )
    protesis_fija: Optional[str] = Field(
        None, description="Prótesis fija (texto libre)."
    )
    protesis_removible: Optional[str] = Field(
        None, description="Prótesis removible (texto libre)."
    )
    coronas: Optional[str] = Field(None, description="Coronas (texto libre).")
    dientes_existentes: Optional[int] = Field(
        None,
        ge=0,
        le=32,
        description="Cantidad de dientes existentes (0 a 32).",
    )
    observaciones: Optional[str] = None
    dientes: List[OdontogramaDienteCreate] = Field(
        default_factory=list,
        description=(
            "Dientes pintados. El conjunto reemplaza al anterior: un diente que "
            "no viaja queda sin estado."
        ),
    )

    @field_validator(*_CAMPOS_TEXTO, mode="before")
    @classmethod
    def _textos(cls, valor, info: ValidationInfo):
        return _limpiar_texto(valor, info)

    @model_validator(mode="after")
    def _sin_dientes_repetidos(self) -> "OdontogramaUpdate":
        """Un diente no puede venir dos veces en el mismo guardado."""
        vistos = set()
        for item in self.dientes:
            if item.diente in vistos:
                raise ValueError(
                    f"El diente {item.diente} está repetido en el odontograma."
                )
            vistos.add(item.diente)
        return self


class OdontogramaDienteRead(BaseModel):
    """Diente pintado tal como se devuelve al panel."""

    model_config = ConfigDict(from_attributes=True)

    diente: int
    estado: str
    notas: Optional[str] = None


class OdontogramaRead(BaseModel):
    """Odontograma de un paciente (``id = null`` si todavía no se cargó)."""

    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    historia_clinica_id: Optional[int] = None
    doctor_id: Optional[int] = None
    protesis_fija: Optional[str] = None
    protesis_removible: Optional[str] = None
    coronas: Optional[str] = None
    dientes_existentes: Optional[int] = None
    observaciones: Optional[str] = None
    actualizado_en: Optional[datetime] = None
    dientes: List[OdontogramaDienteRead] = Field(default_factory=list)

    def _contar(self, estado: str) -> int:
        """Cuenta los dientes pintados con un estado concreto."""
        return sum(1 for item in self.dientes if item.estado == estado)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_existentes(self) -> int:
        """Dientes marcados como prestación existente (rojo)."""
        return self._contar("EXISTENTE")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_requeridos(self) -> int:
        """Dientes marcados como prestación requerida (azul)."""
        return self._contar("REQUERIDO")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_ausentes(self) -> int:
        """Dientes marcados como ausentes o a extraer (X)."""
        return self._contar("AUSENTE")
