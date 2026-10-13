"""Esquemas Pydantic de ``Doctor``."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DoctorBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120)
    especialidad: str = Field(..., min_length=1, max_length=120)
    activo: bool = True


class DoctorCreate(DoctorBase):
    """Payload de creación de un doctor."""


class DoctorReplace(DoctorBase):
    """Payload del reemplazo **total** de un doctor (``PUT``).

    Es un ``PUT`` clásico: el cliente envía el estado completo del recurso y
    ``activo`` se toma del payload (si no se envía, queda activo). Para cambiar
    sólo el ciclo de vida o un único dato conviene ``PATCH``.
    """


class DoctorUpdate(BaseModel):
    """Payload de actualización parcial de un doctor (``PATCH``).

    Todos los campos son opcionales: sólo se aplican los enviados, de modo que
    ``{"activo": false}`` da de baja al profesional y ``{"activo": true}`` lo
    reactiva sin tocar el resto de sus datos.
    """

    nombre: Optional[str] = Field(None, min_length=1, max_length=120)
    especialidad: Optional[str] = Field(None, min_length=1, max_length=120)
    activo: Optional[bool] = None


class DoctorRead(DoctorBase):
    """Representación de salida de un doctor."""

    model_config = ConfigDict(from_attributes=True)

    id: int

