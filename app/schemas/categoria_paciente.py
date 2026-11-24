"""Esquemas Pydantic del catálogo de categorías de pacientes.

El **código** es el identificador interno que se guarda en
``pacientes.categoria`` (por ejemplo ``MALO``): se normaliza a mayúsculas, los
espacios se convierten en guiones bajos y sólo admite letras, números y ``_``,
empezando con una letra. El **nombre** es el texto visible para la secretaría.

``alerta`` marca las categorías que disparan el aviso previo al alta de un turno
(las que además admiten observaciones): es el comportamiento que históricamente
tenía ``MALO`` y que ahora cualquier categoría puede activar.

``CategoriaPacienteUpdate`` deja todos los campos opcionales porque es el
payload de ``PATCH``; en un ``PATCH`` omitir un campo significa «sin cambios»,
por eso ``null`` se rechaza (salvo que se quiera limpiar algo que no aplica).
"""

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Largo máximo del código (coincide con ``pacientes.categoria``).
MAX_LARGO_CODIGO = 30

#: Código interno: letras, números y guion bajo, empezando con una letra.
PATRON_CODIGO = r"^[A-Z][A-Z0-9_]{0,29}$"

_RE_ESPACIOS = re.compile(r"\s+")


def normalizar_codigo_categoria(valor: object) -> str:
    """Normaliza el código tal como se guarda y se compara en la base.

    * Quita los espacios de los extremos.
    * Convierte los espacios internos en guiones bajos («Paciente VIP» ->
      ``PACIENTE_VIP``).
    * Pasa todo a mayúsculas, de modo que ``malo`` y ``MALO`` sean el mismo
      código y el índice único sea realmente útil.
    """
    texto = _RE_ESPACIOS.sub("_", str(valor).strip())
    return texto.upper()


class CategoriaPacienteBase(BaseModel):
    """Datos completos de una categoría."""

    codigo: str = Field(
        ...,
        max_length=MAX_LARGO_CODIGO,
        pattern=PATRON_CODIGO,
        description=(
            "Identificador interno (letras, números y «_», empezando con una "
            "letra). Se guarda en mayúsculas y no se repite."
        ),
    )
    nombre: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Nombre visible en el panel, p. ej. «Paciente VIP».",
    )
    alerta: bool = Field(
        False,
        description=(
            "Si es true, el panel pide observaciones y avisa antes de asignar "
            "un turno al paciente."
        ),
    )
    activa: bool = Field(
        True,
        description="Una categoría inactiva no se ofrece, pero se conserva.",
    )
    orden: int = Field(
        0,
        ge=0,
        le=99,
        description="Orden en el desplegable (0 = al final; empate: por nombre).",
    )

    @field_validator("codigo", mode="before")
    @classmethod
    def _normalizar_codigo(cls, valor):
        """Acepta «malo», «Mal o» o « MALO » y guarda el código canónico."""
        if valor is None:
            return valor
        return normalizar_codigo_categoria(valor)

    @field_validator("nombre", mode="before")
    @classmethod
    def _normalizar_nombre(cls, valor):
        """Quita los espacios sobrantes del nombre visible."""
        if valor is None:
            return valor
        return str(valor).strip()


class CategoriaPacienteCreate(CategoriaPacienteBase):
    """Payload de creación de una categoría (``POST /categorias-paciente``)."""


class CategoriaPacienteUpdate(BaseModel):
    """Payload de actualización parcial de una categoría (``PATCH``).

    Todos los campos son opcionales: sólo se aplican los enviados. ``null`` se
    rechaza en todos ellos; para dar de baja la categoría se envía
    ``{"activa": false}`` o se usa ``DELETE``.
    """

    codigo: Optional[str] = Field(None, max_length=MAX_LARGO_CODIGO, pattern=PATRON_CODIGO)
    nombre: Optional[str] = Field(None, min_length=1, max_length=60)
    alerta: Optional[bool] = None
    activa: Optional[bool] = None
    orden: Optional[int] = Field(None, ge=0, le=99)

    @field_validator("codigo", "nombre", "alerta", "activa", "orden", mode="before")
    @classmethod
    def _rechazar_nulos(cls, valor):
        """En un ``PATCH``, omitir el campo es «sin cambios»; ``null`` no."""
        if valor is None:
            raise ValueError(
                "Este campo no admite null; omitilo para no modificarlo."
            )
        return valor

    @field_validator("codigo", mode="before")
    @classmethod
    def _normalizar_codigo(cls, valor):
        if valor is None:
            return valor
        return normalizar_codigo_categoria(valor)

    @field_validator("nombre", mode="before")
    @classmethod
    def _normalizar_nombre(cls, valor):
        if valor is None:
            return valor
        return str(valor).strip()


class CategoriaPacienteRead(CategoriaPacienteBase):
    """Representación de salida de una categoría."""

    model_config = ConfigDict(from_attributes=True)

    id: int
