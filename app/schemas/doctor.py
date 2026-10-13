"""Esquemas Pydantic de ``Doctor``."""

from pydantic import BaseModel, ConfigDict, Field


class DoctorBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120)
    especialidad: str = Field(..., min_length=1, max_length=120)
    activo: bool = True


class DoctorCreate(DoctorBase):
    """Payload de creación de un doctor."""


class DoctorRead(DoctorBase):
    """Representación de salida de un doctor."""

    model_config = ConfigDict(from_attributes=True)

    id: int
