"""Esquemas Pydantic del Módulo 3: facturación, caja y liquidaciones.

Agrupa los contratos de la facturación mixta:

* **Cierre de visita** (``FacturarTurnoRequest``): las prácticas realizadas y el
  resultado del cálculo (``MovimientoCajaRead``), con el importe a cobrar en el
  mostrador ya separado del que se presenta a la obra social.
* **Ayuda de carga** (``PrestacionDisponibleRead``): cada práctica con el arancel
  que le corresponde según la cobertura del paciente, para que el formulario
  muestre el coseguro antes de guardar.
* **Liquidaciones** (``PresentacionObraSocialRead`` y
  ``LiquidacionProfesionalesRead``): los dos cierres de mes, el de las obras
  sociales y el de los profesionales.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.datetime_utils import to_naive_utc
from app.core.money import sumar_montos
from app.models.enums import EstadoPagoMovimiento, TipoPagoDoctor

# Texto libre para el motivo del débito o la nota del cobro.
_MAX_OBSERVACIONES = 2000


# ---------------------------------------------------------------------------
# Cierre de la visita (facturación de un turno)
# ---------------------------------------------------------------------------


class PrestacionFacturadaCreate(BaseModel):
    """Práctica realizada en la visita, con su cantidad."""

    prestacion_id: int = Field(..., gt=0)
    cantidad: int = Field(1, ge=1, le=99, description="Repeticiones de la práctica.")


class FacturarTurnoRequest(BaseModel):
    """Payload del cierre de una visita: qué se hizo y cuándo se cobró."""

    prestaciones: List[PrestacionFacturadaCreate] = Field(
        ...,
        min_length=1,
        description="Prácticas realizadas (al menos una).",
    )
    fecha: Optional[datetime] = Field(
        None,
        description=(
            "Momento del cierre; si se omite se usa el del alta. Sirve para "
            "cargar una atención de días anteriores."
        ),
    )
    observaciones: Optional[str] = Field(None, max_length=_MAX_OBSERVACIONES)
    permitir_particular: bool = Field(
        False,
        description=(
            "Si el paciente tiene cobertura pero la práctica **no** tiene arancel "
            "cargado, con ``true`` se cobra a valor de referencia como particular "
            "en lugar de rechazar la facturación (**422**)."
        ),
    )

    @field_validator("observaciones", mode="before")
    @classmethod
    def _observaciones_opcionales(cls, valor):
        """Las observaciones vacías se guardan como ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

    @model_validator(mode="after")
    def _normalizar_fecha(self) -> "FacturarTurnoRequest":
        """Deja la fecha en UTC naive (convención del proyecto)."""
        if self.fecha is not None:
            self.fecha = to_naive_utc(self.fecha)
        return self


class PresupuestoTurnoRequest(BaseModel):
    """Payload de la **simulación** de cobro (no escribe nada en la base).

    Se usa para mostrar el cartel del coseguro mientras la secretaría tilda
    prácticas en el formulario, sin crear todavía el movimiento de caja.
    """

    prestaciones: List[PrestacionFacturadaCreate] = Field(..., min_length=1)
    permitir_particular: bool = False



class PrestacionFacturadaCalculadaRead(BaseModel):
    """Práctica ya resuelta contra el arancel de la cobertura (vista previa)."""

    prestacion_id: int
    codigo_nomenclador: str
    descripcion: str
    cantidad: int
    monto_cubre_os: float
    monto_coseguro_paciente: float
    monto_total: float
    origen: str = Field(
        "ARANCEL",
        description="``ARANCEL``, ``VALOR_BASE`` (particular) o ``SIN_ARANCEL``.",
    )


class PresupuestoCobroRead(BaseModel):
    """Cartel del mostrador: cuánto se cobra en efectivo y cuánto va a la OS.

    Lo devuelve la vista previa del formulario de cobro
    (``POST /turnos/{id}/presupuesto``) para que el importe que ve la secretaría
    lo calcule el **mismo motor** que después guarda el movimiento de caja.
    """

    turno_id: int
    paciente_nombre: str
    obra_social_nombre: Optional[str] = None
    permitir_particular: bool
    cantidad_prestaciones: int
    monto_efectivo: float
    monto_pendiente_os: float
    monto_total: float
    advertencias: List[str] = Field(
        default_factory=list,
        description="Prácticas sin arancel cargado (se cobran particular si se permite).",
    )
    prestaciones: List[PrestacionFacturadaCalculadaRead] = Field(default_factory=list)


class DetalleMovimientoCajaRead(BaseModel):
    """Práctica facturada, con los importes congelados al momento del cierre."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    prestacion_id: int
    codigo_nomenclador: str
    descripcion: str
    cantidad: int
    monto_cubre_os: float
    monto_coseguro_paciente: float
    monto_total: float = Field(0, description="cubre OS + coseguro (por cantidad).")

    @model_validator(mode="after")
    def _calcular_total(self) -> "DetalleMovimientoCajaRead":
        self.monto_total = sumar_montos(
            [self.monto_cubre_os, self.monto_coseguro_paciente]
        )
        return self


class MovimientoCajaRead(BaseModel):
    """Movimiento de caja (cierre de una visita) con los datos ya resueltos.

    Expone el paciente y su cobertura en campos planos (para que la tabla de caja
    y las presentaciones a la obra social se dibujen sin consultar al navegador)
    y el detalle de prácticas facturadas.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    turno_id: int
    doctor_id: int
    fecha: datetime
    monto_efectivo: float
    monto_pendiente_os: float
    monto_cobrado_os: Optional[float] = None
    monto_total: float = Field(
        0, description="Efectivo del mostrador + saldo presentado a la OS."
    )
    estado_pago: EstadoPagoMovimiento
    observaciones: Optional[str] = None

    doctor_nombre: Optional[str] = Field(None, validation_alias="doctor")
    doctor_especialidad: Optional[str] = Field(None, validation_alias="doctor")

    turno_fecha_hora_inicio: Optional[datetime] = Field(None, validation_alias="turno")
    paciente_id: Optional[int] = Field(None, validation_alias="turno")
    paciente_nombre: Optional[str] = Field(None, validation_alias="turno")
    paciente_dni: Optional[str] = Field(None, validation_alias="turno")
    obra_social_id: Optional[int] = Field(None, validation_alias="turno")
    obra_social_nombre: Optional[str] = Field(None, validation_alias="turno")

    prestaciones: List[DetalleMovimientoCajaRead] = Field(
        default_factory=list, validation_alias="detalles"
    )

    @field_validator("doctor_nombre", "doctor_especialidad", mode="before")
    @classmethod
    def _datos_del_doctor(cls, valor, info: ValidationInfo):
        """Convierte la relación ORM ``Doctor`` en el campo plano homónimo."""
        if valor is None or isinstance(valor, str):
            return valor
        return getattr(valor, info.field_name.replace("doctor_", ""), None)

    @field_validator("turno_fecha_hora_inicio", mode="before")
    @classmethod
    def _fecha_del_turno(cls, valor):
        if valor is None or isinstance(valor, datetime):
            return valor
        return getattr(valor, "fecha_hora_inicio", None)

    @field_validator("paciente_id", "paciente_nombre", "paciente_dni", mode="before")
    @classmethod
    def _datos_del_paciente(cls, valor, info: ValidationInfo):
        """Navega ``movimiento.turno.paciente`` para exponer el dato plano."""
        if valor is None or isinstance(valor, (str, int)):
            return valor
        paciente = getattr(valor, "paciente", None)
        return getattr(paciente, info.field_name.replace("paciente_", ""), None)

    @field_validator("obra_social_id", "obra_social_nombre", mode="before")
    @classmethod
    def _datos_de_la_cobertura(cls, valor, info: ValidationInfo):
        """Navega ``turno.paciente.obra_social`` para exponer la cobertura."""
        if valor is None or isinstance(valor, (str, int)):
            return valor
        paciente = getattr(valor, "paciente", None)
        if info.field_name == "obra_social_id":
            return getattr(paciente, "obra_social_id", None)
        return getattr(getattr(paciente, "obra_social", None), "nombre", None)

    @model_validator(mode="after")
    def _calcular_total(self) -> "MovimientoCajaRead":
        """Completa el total facturado del movimiento."""
        self.monto_total = sumar_montos([self.monto_efectivo, self.monto_pendiente_os])
        return self


# ---------------------------------------------------------------------------
# Ayuda de carga del formulario de cobro
# ---------------------------------------------------------------------------


class PrestacionDisponibleRead(BaseModel):
    """Práctica con el arancel que le corresponde al paciente del turno.

    ``origen`` explica de dónde salió el importe:

    * ``ARANCEL``: la cobertura tiene arancel cargado para esa práctica.
    * ``VALOR_BASE``: el paciente no tiene cobertura (se cobra particular).
    * ``SIN_ARANCEL``: el paciente tiene cobertura pero falta cargar el arancel;
      para facturar esa práctica hay que configurarla o usar
      ``permitir_particular``.
    """

    prestacion_id: int
    codigo_nomenclador: str
    descripcion: str
    valor_base: float
    cubierta_por_os: bool
    monto_cubre_os: float
    monto_coseguro_paciente: float
    origen: str


class PrestacionesDisponiblesRead(BaseModel):
    """Respuesta del formulario de cobro: paciente, cobertura y prácticas."""

    turno_id: int
    paciente_id: int
    paciente_nombre: str
    paciente_dni: Optional[str] = None
    obra_social_id: Optional[int] = None
    obra_social_nombre: Optional[str] = None
    numero_afiliado: Optional[str] = None
    doctor_nombre: str
    turno_fecha_hora_inicio: datetime
    facturado: bool = Field(
        False, description="``true`` si el turno ya tiene un movimiento de caja."
    )
    prestaciones: List[PrestacionDisponibleRead] = Field(default_factory=list)


class FacturacionTurnoRead(BaseModel):
    """Estado de facturación de un turno (para saber si ya se cobró)."""

    turno_id: int
    facturado: bool
    movimiento: Optional[MovimientoCajaRead] = None


# ---------------------------------------------------------------------------
# Caja: cobros y débitos individuales
# ---------------------------------------------------------------------------


class CobrarMovimientoRequest(BaseModel):
    """Payload del cobro definitivo de una obra social (``POST .../cobrar``)."""

    monto_cobrado_os: Optional[float] = Field(
        None,
        ge=0,
        description=(
            "Importe realmente depositado por la OS. Si se omite se asume el "
            "saldo presentado (``monto_pendiente_os``); informarlo permite "
            "registrar diferencias de pago."
        ),
    )
    observaciones: Optional[str] = Field(None, max_length=_MAX_OBSERVACIONES)

    @field_validator("observaciones", mode="before")
    @classmethod
    def _observaciones_opcionales(cls, valor):
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class DebitarMovimientoRequest(BaseModel):
    """Payload del débito de la OS (``POST .../debitar``).

    El motivo es **obligatorio**: un débito es un importe que el consultorio deja
    de cobrar y la secretaría necesita saber por qué (práctica rechazada,
    afiliado sin cobertura al día, diferencia de arancel…).
    """

    observaciones: str = Field(
        ...,
        min_length=3,
        max_length=_MAX_OBSERVACIONES,
        description="Motivo del débito (queda registrado en el movimiento).",
    )
    monto_cobrado_os: Optional[float] = Field(
        None,
        ge=0,
        description=(
            "Si la OS pagó una parte, informar acá el importe depositado "
            "(débito parcial); si se omite, el débito se considera total (0)."
        ),
    )

    @field_validator("observaciones", mode="before")
    @classmethod
    def _exigir_motivo(cls, valor):
        texto = str(valor).strip() if valor is not None else ""
        if not texto:
            raise ValueError("El motivo del débito no puede estar vacío.")
        return texto


class ResumenCajaRead(BaseModel):
    """Totales de la caja de un rango de fechas (por defecto, el día)."""

    desde: datetime
    hasta: datetime
    cantidad_movimientos: int
    monto_efectivo: float
    monto_pendiente_os: float
    monto_cobrado_os: float
    monto_debitado_os: float


# ---------------------------------------------------------------------------
# Liquidación de obras sociales (presentación del período)
# ---------------------------------------------------------------------------


class LiquidacionObraSocialRequest(BaseModel):
    """Payload del cierre de mes de una cobertura (``POST``)."""

    obra_social_id: int = Field(..., gt=0, description="Cobertura a liquidar.")
    anio: int = Field(..., ge=2000, le=2100)
    mes: int = Field(..., ge=1, le=12, description="Mes calendario (1 = enero).")


class PresentacionObraSocialRead(BaseModel):
    """Presentación de un período para cobrarle a una obra social.

    ``generada`` distingue la **vista previa** (la secretaría consulta los
    movimientos que todavía están ``PENDIENTE``, sin cambiar nada) de la
    presentación ya emitida (los movimientos pasaron a
    ``EN_PROCESO_LIQUIDACION`` o ya se cerraron como ``COBRADO``/``DEBITADO_OS``).
    El ``numero_presentacion`` es determinístico (``PRES-OS3-202611``) para poder
    identificarla en el circuito de la cobertura sin una tabla extra.
    """

    numero_presentacion: str
    obra_social_id: int
    obra_social_nombre: str
    obra_social_codigo: Optional[str] = None
    anio: int
    mes: int
    periodo_desde: datetime
    periodo_hasta: datetime
    generada: bool
    cantidad_movimientos: int
    total_presentado: float = Field(
        0, description="Suma de los saldos presentados a la cobertura."
    )
    total_efectivo: float = Field(0, description="Coseguros ya cobrados en mostrador.")
    total_cobrado_os: float = 0
    total_debitado_os: float = 0
    total_pendiente_os: float = Field(
        0, description="Saldo que sigue sin acreditar (pendiente o en proceso)."
    )
    movimientos: List[MovimientoCajaRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Liquidación de profesionales (producción mensual por odontólogo)
# ---------------------------------------------------------------------------


class LiquidacionProfesionalDoctorRead(BaseModel):
    """Liquidación de un odontólogo: su producción del mes y sus honorarios."""

    doctor_id: int
    doctor_nombre: str
    doctor_especialidad: str
    activo: bool
    tipo_pago: TipoPagoDoctor
    valor_pago: Optional[float] = None
    cantidad_movimientos: int
    cantidad_turnos: int
    produccion_efectivo: float
    produccion_os: float
    produccion_total: float
    honorarios: float = Field(0, description="Lo que le corresponde cobrar del mes.")
    detalle_honorarios: str = Field(
        "", description="Cómo se calculó (porcentaje, sueldo fijo, etc.)."
    )
    movimientos: List[MovimientoCajaRead] = Field(default_factory=list)


class LiquidacionProfesionalesRead(BaseModel):
    """Reporte mensual del consultorio: producción y honorarios por profesional."""

    anio: int
    mes: int
    periodo_desde: datetime
    periodo_hasta: datetime
    total_efectivo: float
    total_os: float
    total_produccion: float
    total_honorarios: float
    doctores: List[LiquidacionProfesionalDoctorRead] = Field(default_factory=list)


