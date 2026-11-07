"""Esquemas Pydantic del Módulo 6: proveedores, gastos y cuentas por pagar.

Contratos del circuito de egresos:

* **Alta de proveedor** (``ProveedorCreate`` / ``ProveedorRead``): quién es, de
  qué rubro y cuánto se le debe hoy (``saldo_pendiente``), con el detalle de
  facturas abiertas y deuda vencida.
* **Alta de factura recibida** (``GastoFacturaCreate`` / ``GastoFacturaRead``):
  el gasto con su concepto, importes, emisión y vencimiento. ``paciente_id``
  sólo se acepta para proveedores ``MECANICO_DENTAL`` (lo valida el servicio,
  que es quien conoce el rubro).
* **Pago** (``PagoEgresoCreate`` / ``PagoEgresoRegistradoRead``): el pago
  aplicado a una factura y los saldos que quedan, tanto de la factura como del
  proveedor.

Convención de importes
----------------------

Igual que el Módulo 5: ``Decimal`` en todo el cálculo (``Numeric(10, 2)`` en la
base) y publicación como **número JSON** con ``field_serializer(..., when_used=
"json")``, para que el panel sume importes sin recibir cadenas. El cliente nunca
puede enviar más de dos decimales ni un importe fuera del rango de la columna:
Pydantic responde ``422`` antes de tocar la base.

Convención de fechas
--------------------

Las fechas viajan y se guardan en **UTC naive** (``to_naive_utc``), igual que el
resto del sistema. ``fecha_vencimiento`` es obligatoria en la base, pero si la
secretaría no la informa se calcula con el plazo por defecto
(``DIAS_VENCIMIENTO_POR_DEFECTO``) para no bloquear la carga.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, List, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.core.datetime_utils import to_naive_utc
from app.models.enums import EstadoPagoFactura, MetodoPagoEgreso, TipoProveedor

#: Importe monetario: hasta 10 dígitos con 2 decimales (``Numeric(10, 2)``).
Monto = Annotated[Decimal, Field(max_digits=10, decimal_places=2)]

#: Importe de una factura o de un pago: siempre mayor que cero.
MontoPositivo = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]

#: Plazo que se asume cuando la factura llega sin fecha de vencimiento.
DIAS_VENCIMIENTO_POR_DEFECTO = 30

#: Largo del concepto (la columna es ``String(255)``).
MAX_CONCEPTO = 255

CERO = Decimal("0.00")


class _EsquemaEgreso(BaseModel):
    """Base de los esquemas de salida: se construyen desde el ORM."""

    model_config = ConfigDict(from_attributes=True)


class _SolicitudConFechas(BaseModel):
    """Normaliza las fechas recibidas a UTC naive (convención del proyecto)."""

    @model_validator(mode="after")
    def _fechas_utc_naive(self):
        for campo in ("fecha_emision", "fecha_vencimiento", "fecha_pago"):
            valor = getattr(self, campo, None)
            if valor is not None:
                setattr(self, campo, to_naive_utc(valor))
        return self


class ProveedorCreate(BaseModel):
    """Alta de un proveedor (``POST /egresos/proveedores``)."""

    nombre: str = Field(
        ..., min_length=1, max_length=150, description="Razón social o nombre."
    )
    telefono_contacto: Optional[str] = Field(
        None, max_length=50, description="Teléfono o WhatsApp de contacto."
    )
    tipo_proveedor: TipoProveedor = Field(
        ...,
        description=(
            "``MECANICO_DENTAL`` (laboratorio de piezas), ``INSUMOS`` "
            "(droguería/descartables) o ``SERVICIOS_IMPUESTOS``."
        ),
    )

    @field_validator("nombre", mode="before")
    @classmethod
    def _nombre_obligatorio(cls, valor):
        """Recorta espacios y rechaza un nombre vacío."""
        texto = str(valor or "").strip()
        if not texto:
            raise ValueError("El nombre del proveedor es obligatorio.")
        return texto

    @field_validator("telefono_contacto", mode="before")
    @classmethod
    def _telefono_opcional(cls, valor):
        """Un teléfono vacío se guarda como ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

class ProveedorRead(_EsquemaEgreso):
    """Proveedor con su saldo actualizado y el estado de su cartera."""

    id: UUID
    nombre: str
    telefono_contacto: Optional[str] = None
    tipo_proveedor: TipoProveedor
    saldo_pendiente: Monto = Field(
        CERO, description="Cuánto le debe hoy el consultorio (facturas abiertas)."
    )
    facturas_abiertas: int = Field(
        0, description="Facturas con saldo (``PENDIENTE`` o ``PARCIAL``)."
    )
    deuda_vencida: Monto = Field(
        CERO, description="Parte del saldo cuyas facturas ya pasaron el vencimiento."
    )

    @field_serializer("saldo_pendiente", "deuda_vencida", when_used="json")
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class PagoEgresoRead(_EsquemaEgreso):
    """Un pago aplicado a una factura recibida."""

    id: UUID
    gasto_factura_id: UUID
    monto_pagado: Monto
    metodo_pago: MetodoPagoEgreso = Field(
        ..., description="``EFECTIVO``, ``TRANSFERENCIA`` o ``TARJETA``."
    )
    fecha_pago: datetime

    @field_serializer("monto_pagado", when_used="json")
    def _monto_numerico(self, valor: Decimal) -> float:
        return float(valor)


class GastoFacturaCreate(_SolicitudConFechas):
    """Alta de una factura o gasto recibido (``POST /egresos/facturas``)."""

    proveedor_id: UUID = Field(..., description="Proveedor que emitió la factura.")
    paciente_id: Optional[int] = Field(
        None,
        gt=0,
        description=(
            "Paciente al que corresponde la pieza. **Sólo** se admite si el "
            "proveedor es ``MECANICO_DENTAL``; en otro rubro responde 422."
        ),
    )
    concepto: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CONCEPTO,
        description="Detalle del gasto («Corona de porcelana molar 36», «Luz Octubre»).",
    )
    monto_total: MontoPositivo = Field(
        ..., description="Importe total de la factura (lo que se le debe)."
    )
    fecha_emision: Optional[datetime] = Field(
        None, description="Fecha de emisión; si se omite se usa el momento del alta."
    )
    fecha_vencimiento: Optional[datetime] = Field(
        None,
        description=(
            "Fecha de vencimiento; si se omite se toma la emisión más "
            f"{DIAS_VENCIMIENTO_POR_DEFECTO} días."
        ),
    )

    @field_validator("concepto", mode="before")
    @classmethod
    def _concepto_obligatorio(cls, valor):
        texto = str(valor or "").strip()
        if not texto:
            raise ValueError("El concepto del gasto es obligatorio.")
        return texto

    @model_validator(mode="after")
    def _vencimiento_no_anterior_a_emision(self):
        """Un vencimiento anterior a la emisión es un dato imposible."""
        emision = self.fecha_emision
        vencimiento = self.fecha_vencimiento
        if emision is not None and vencimiento is not None and vencimiento < emision:
            raise ValueError(
                "fecha_vencimiento no puede ser anterior a fecha_emision."
            )
        return self


class GastoFacturaRead(_EsquemaEgreso):
    """Factura recibida con sus importes, su estado y sus pagos."""

    id: UUID
    proveedor_id: UUID
    proveedor_nombre: str = Field(..., description="Nombre del proveedor (comodidad).")
    proveedor_tipo: TipoProveedor
    paciente_id: Optional[int] = Field(
        None, description="Paciente de la pieza (sólo ``MECANICO_DENTAL``)."
    )
    paciente_nombre: Optional[str] = None
    concepto: str
    monto_total: Monto
    monto_pagado: Monto = CERO
    saldo_pendiente: Monto = CERO
    estado_pago: EstadoPagoFactura
    fecha_emision: datetime
    fecha_vencimiento: datetime
    vencida: bool = Field(False, description="Tiene saldo y ya pasó el vencimiento.")
    pagos: List[PagoEgresoRead] = Field(default_factory=list)

    @field_serializer(
        "monto_total", "monto_pagado", "saldo_pendiente", when_used="json"
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class PagoEgresoCreate(_SolicitudConFechas):
    """Pago de una factura recibida (``POST /egresos/pagar``)."""

    gasto_factura_id: UUID = Field(..., description="Factura que se cancela.")
    monto_pagado: MontoPositivo = Field(
        ..., description="Importe pagado; nunca puede superar el saldo de la factura."
    )
    metodo_pago: MetodoPagoEgreso = Field(
        MetodoPagoEgreso.EFECTIVO,
        description=(
            "``EFECTIVO`` (por defecto), ``TRANSFERENCIA`` o ``TARJETA``. Es el "
            "medio con el que **sale** la plata del consultorio."
        ),
    )
    fecha_pago: Optional[datetime] = Field(
        None, description="Momento del pago; si se omite se usa el del alta."
    )


class PagoEgresoRegistradoRead(_EsquemaEgreso):
    """Resultado de registrar un pago: el pago y los saldos que quedan.

    Se devuelven los tres importes que la secretaría necesita para no volver a
    consultar: lo pagado, lo que falta de esa factura y lo que se le debe al
    proveedor después de la operación (la suma de todas sus facturas abiertas).
    """

    pago: PagoEgresoRead
    factura_estado_pago: EstadoPagoFactura
    factura_saldo_pendiente: Monto = CERO
    proveedor_saldo_pendiente: Monto = CERO

    @field_serializer(
        "factura_saldo_pendiente", "proveedor_saldo_pendiente", when_used="json"
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)


class ProveedorDetalleRead(ProveedorRead):
    """Proveedor con todas sus facturas (del gasto más nuevo al más viejo)."""

    facturas: List[GastoFacturaRead] = Field(default_factory=list)


class DeudaPorTipoRead(BaseModel):
    """Deuda agrupada por rubro del proveedor."""

    tipo_proveedor: TipoProveedor
    cantidad_facturas: int = 0
    deuda: Monto = CERO

    @field_serializer("deuda", when_used="json")
    def _monto_numerico(self, valor: Decimal) -> float:
        return float(valor)


class ResumenEgresosRead(BaseModel):
    """Tablero de cuentas por pagar del consultorio.

    ``deuda_total`` es la suma de los saldos de todas las facturas abiertas
    —coincide con la suma de ``saldo_pendiente`` de los proveedores— y
    ``deuda_vencida`` la parte que ya pasó su fecha de vencimiento, que es la
    que la secretaría tiene que pagar primero.
    """

    generado: datetime
    cantidad_proveedores: int = 0
    cantidad_facturas_abiertas: int = 0
    total_facturado: Monto = CERO
    total_pagado: Monto = CERO
    deuda_total: Monto = CERO
    deuda_vencida: Monto = CERO
    por_tipo: List[DeudaPorTipoRead] = Field(default_factory=list)

    @field_serializer(
        "total_facturado",
        "total_pagado",
        "deuda_total",
        "deuda_vencida",
        when_used="json",
    )
    def _montos_numericos(self, valor: Decimal) -> float:
        return float(valor)

