"""Esquemas Pydantic de ``Doctor``.

Incluye la **modalidad de pago** (Módulo 3) que usa la liquidación de
profesionales:

* ``tipo_pago``: ``DUENO`` (por defecto), ``COMISION`` o ``SUELDO``.
* ``valor_pago``: el porcentaje (0-100) para ``DUENO``/``COMISION`` o el importe
  mensual para ``SUELDO``. ``DUENO`` sin valor se interpreta como el 100 %.

La coherencia entre ambos campos se valida acá (regla de datos) y se revalida en
:mod:`app.services.doctor_service` contra el estado guardado, para que un
``PATCH`` que sólo cambie el tipo no deje al profesional sin forma de pago.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.pagos import validar_modalidad_pago
from app.models.enums import TipoPagoDoctor


class DoctorBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120)
    especialidad: str = Field(..., min_length=1, max_length=120)
    activo: bool = True
    tipo_pago: TipoPagoDoctor = Field(
        TipoPagoDoctor.DUENO,
        description=(
            "DUENO (se queda con su producción), COMISION (porcentaje de lo que "
            "produce) o SUELDO (fijo mensual); se usa en la liquidación del mes."
        ),
    )
    valor_pago: Optional[float] = Field(
        None,
        ge=0,
        description=(
            "Porcentaje 0-100 para DUENO/COMISION o importe mensual para SUELDO. "
            "Omitirlo en un DUENO significa el 100 %."
        ),
    )

    @model_validator(mode="after")
    def _coherencia_de_pago(self) -> "DoctorBase":
        validar_modalidad_pago(self.tipo_pago, self.valor_pago)
        return self


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
    reactiva sin tocar el resto de sus datos. La coherencia entre ``tipo_pago`` y
    ``valor_pago`` se valida con los valores vigentes del profesional.
    """

    nombre: Optional[str] = Field(None, min_length=1, max_length=120)
    especialidad: Optional[str] = Field(None, min_length=1, max_length=120)
    activo: Optional[bool] = None
    tipo_pago: Optional[TipoPagoDoctor] = None
    valor_pago: Optional[float] = Field(None, ge=0)


class DoctorRead(DoctorBase):
    """Representación de salida de un doctor."""

    model_config = ConfigDict(from_attributes=True)

    id: int


