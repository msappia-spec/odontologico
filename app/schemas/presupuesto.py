"""Esquemas Pydantic del módulo de presupuestos.

El documento que se imprime está formado por:

* una **cabecera** con el paciente, la fecha, la dirección y la forma de pago;
* una lista ordenada de **trabajos**, cada uno con su detalle, precio y una
  entrega opcional.

El envío por WhatsApp usa dos esquemas propios: ``PresupuestoEnvioWhatsApp``
(número revisado en el panel y opción de guardarlo en la ficha) y
``PresupuestoEnviadoWhatsApp`` (resultado del envío del PDF).

Reglas de normalización (las mismas del resto del sistema):

* los textos se recortan y el vacío se guarda como ``NULL``;
* la descripción conserva los saltos de línea para poder agregar varios
  detalles en un mismo trabajo;
* los importes nunca son negativos y se redondean a dos decimales;
* una fecha vacía usa la fecha del día;
* el detalle y el precio son obligatorios para poder imprimir el renglón; el
  código de prestación es opcional.
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)

from app.core.money import redondear_monto, sumar_montos
from app.models.presupuesto import (
    FORMA_PAGO_POR_DEFECTO,
    MAX_CODIGO_PRESUPUESTO,
    MAX_DETALLE_PRESUPUESTO,
    MAX_IMPORTE_PRESUPUESTO,
    MAX_ITEMS_PRESUPUESTO,
)
from app.schemas.paciente import PacienteRead


class PresupuestoItemCreate(BaseModel):
    """Un renglón del detalle de los trabajos."""

    codigo: Optional[str] = Field(
        None,
        max_length=MAX_CODIGO_PRESUPUESTO,
        description=(
            "Código de nomenclador de la prestación (por ejemplo ``11.02``). "
            "Es opcional y se imprime antes de la descripción."
        ),
    )
    detalle: str = Field(
        ...,
        min_length=1,
        max_length=MAX_DETALLE_PRESUPUESTO,
        description=(
            "Texto del trabajo que se imprime en el presupuesto. Admite saltos "
            "de línea para agregar otros detalles."
        ),
    )
    precio: float = Field(
        ...,
        ge=0,
        le=MAX_IMPORTE_PRESUPUESTO,
        description="Precio del trabajo en pesos.",
    )
    entrega: Optional[float] = Field(
        None,
        ge=0,
        le=MAX_IMPORTE_PRESUPUESTO,
        description=(
            "Anticipo que se abona al aceptar el presupuesto. Si se omite, el "
            "renglón no muestra la línea «Forma de Pago : Entrega»."
        ),
    )

    @field_validator("codigo", mode="before")
    @classmethod
    def _normalizar_codigo(cls, valor):
        """Recorta el código y convierte el vacío en ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip().upper()
        if not texto:
            return None
        if len(texto) > MAX_CODIGO_PRESUPUESTO:
            raise ValueError(
                "El código de la prestación no puede superar los "
                f"{MAX_CODIGO_PRESUPUESTO} caracteres."
            )
        return texto

    @field_validator("detalle", mode="before")
    @classmethod
    def _normalizar_detalle(cls, valor):
        """Recorta el texto, conserva los saltos de línea y rechaza el vacío."""
        if valor is None:
            return valor
        lineas = [
            " ".join(linea.split())
            for linea in (
                str(valor).replace("\r\n", "\n").replace("\r", "\n").split("\n")
            )
        ]
        texto = "\n".join(linea for linea in lineas if linea)
        if not texto:
            raise ValueError("La descripción del trabajo no puede estar vacía.")
        if len(texto) > MAX_DETALLE_PRESUPUESTO:
            raise ValueError(
                "La descripción del trabajo no puede superar los "
                f"{MAX_DETALLE_PRESUPUESTO} caracteres."
            )
        return texto

    @field_validator("entrega", mode="before")
    @classmethod
    def _entrega_vacia(cls, valor):
        """El ``""`` del formulario web significa «sin entrega»."""
        if valor is None:
            return None
        if isinstance(valor, str) and not valor.strip():
            return None
        return valor

    @field_validator("precio", "entrega", mode="after")
    @classmethod
    def _redondear_importes(cls, valor: Optional[float]) -> Optional[float]:
        """Redondea a dos decimales (convención monetaria del sistema)."""
        return None if valor is None else redondear_monto(valor)


class PresupuestoCreate(BaseModel):
    """Payload de alta o reemplazo completo de un presupuesto."""

    paciente_id: int = Field(
        ..., gt=0, description="Paciente al que se le entrega el presupuesto."
    )
    fecha: date = Field(
        default_factory=date.today,
        description="Fecha del presupuesto (AAAA-MM-DD).",
    )
    forma_pago: str = Field(
        FORMA_PAGO_POR_DEFECTO,
        min_length=1,
        max_length=120,
        description="Forma de pago general que se imprime en la cabecera.",
    )
    direccion: Optional[str] = Field(
        None,
        max_length=200,
        description=(
            "Dirección que se imprime. Si queda vacía se usa el domicilio del "
            "paciente al generar el PDF."
        ),
    )
    items: List[PresupuestoItemCreate] = Field(
        ...,
        min_length=1,
        max_length=MAX_ITEMS_PRESUPUESTO,
        description="Trabajos del presupuesto, en el orden en que se imprimen.",
    )

    @field_validator("fecha", mode="before")
    @classmethod
    def _fecha_opcional(cls, valor):
        """Un campo de fecha vacío del formulario usa la fecha del día."""
        if valor is None:
            return date.today()
        if isinstance(valor, str) and not valor.strip():
            return date.today()
        return valor

    @field_validator("forma_pago", mode="before")
    @classmethod
    def _normalizar_forma_pago(cls, valor):
        """Recorta la forma de pago y aplica el valor por defecto."""
        if valor is None:
            return FORMA_PAGO_POR_DEFECTO
        texto = " ".join(str(valor).split())
        return texto or FORMA_PAGO_POR_DEFECTO

    @field_validator("direccion", mode="before")
    @classmethod
    def _normalizar_direccion(cls, valor, info: ValidationInfo):
        """Recorta la dirección y convierte el vacío en ``NULL``."""
        if valor is None:
            return None
        texto = " ".join(str(valor).split())
        if not texto:
            return None
        if len(texto) > 200:
            raise ValueError("La dirección no puede superar los 200 caracteres.")
        return texto


class PresupuestoUpdate(PresupuestoCreate):
    """Payload del ``PUT``: reemplaza el presupuesto completo."""


class PresupuestoItemRead(BaseModel):
    """Renglón devuelto por la API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    orden: int
    codigo: Optional[str] = None
    detalle: str
    precio: float
    entrega: Optional[float] = None


class PresupuestoRead(BaseModel):
    """Presupuesto completo con totales calculados y datos del paciente."""

    id: int
    paciente_id: int
    fecha: date
    forma_pago: str
    direccion: Optional[str] = None
    creado_en: Optional[datetime] = None
    actualizado_en: Optional[datetime] = None
    items: List[PresupuestoItemRead] = Field(default_factory=list)
    total: float = 0.0
    total_entregas: float = 0.0
    saldo: float = 0.0
    paciente: PacienteRead

    @classmethod
    def con_totales(
        cls,
        *,
        presupuesto,
        items: List[PresupuestoItemRead],
    ) -> "PresupuestoRead":
        """Arma la respuesta sumando los importes con precisión decimal."""
        total = sumar_montos(item.precio for item in items)
        total_entregas = sumar_montos(item.entrega for item in items)
        return cls(
            id=presupuesto.id,
            paciente_id=presupuesto.paciente_id,
            fecha=presupuesto.fecha,
            forma_pago=presupuesto.forma_pago,
            # La dirección efectiva del documento: la propia o la del paciente.
            direccion=presupuesto.direccion or presupuesto.paciente.domicilio,
            creado_en=presupuesto.creado_en,
            actualizado_en=presupuesto.actualizado_en,
            items=items,
            total=total,
            total_entregas=total_entregas,
            saldo=redondear_monto(total - total_entregas),
            paciente=PacienteRead.model_validate(presupuesto.paciente),
        )


class PresupuestoEnvioWhatsApp(BaseModel):
    """Datos del envío del presupuesto en PDF por WhatsApp.

    El panel abre un modal con el teléfono de la ficha para que la secretaría
    pueda corregirlo **antes** de enviar (por ejemplo, un número viejo o un
    celular nuevo). Si ``telefono`` se omite se usa el del paciente.
    """

    telefono: Optional[str] = Field(
        None,
        max_length=30,
        description=(
            "Número al que se envía el PDF. Si se omite se usa el teléfono de "
            "la ficha del paciente. Admite el formato local (``351 643-2863``) "
            "o internacional (``+54 9 351 643-2863``)."
        ),
    )
    actualizar_telefono: bool = Field(
        False,
        description=(
            "Guarda el número corregido en la ficha del paciente para los "
            "próximos envíos. Sólo se aplica si el número normalizado difiere "
            "del que ya tenía cargado."
        ),
    )

    @field_validator("telefono", mode="before")
    @classmethod
    def _telefono_vacio(cls, valor):
        """El campo vacío del formulario significa «usar el de la ficha»."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class PresupuestoEnviadoWhatsApp(BaseModel):
    """Resultado del envío del presupuesto por WhatsApp."""

    presupuesto_id: int
    telefono: str = Field(
        ..., description="Número normalizado al que se envió (``549...``)."
    )
    nombre_archivo: str
    mensaje_id: str
    simulado: bool = Field(
        False,
        description=(
            "``True`` cuando faltan ``WAHA_URL``/``WAHA_API_KEY`` y el envío "
            "sólo se registró en el log."
        ),
    )
    actualizado_telefono: bool = Field(
        False, description="``True`` si el número quedó guardado en la ficha."
    )
