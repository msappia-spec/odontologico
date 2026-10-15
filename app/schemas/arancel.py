"""Esquemas Pydantic de ``ArancelObraSocial`` (cobertura y coseguro por práctica).

Este es el acuerdo económico con cada cobertura: para un par
(obra social, prestación) se declara cuánto paga la OS y cuánto queda como
coseguro del paciente. El alta se hace siempre *dentro* de una obra social
(``POST /obras-sociales/{id}/aranceles``), por eso el payload **no** lleva
``obra_social_id``: sale de la URL y así el cliente no puede dirigir el arancel
a otra cobertura.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.core.money import redondear_monto, sumar_montos


class ArancelObraSocialBase(BaseModel):
    """Montos acordados con la cobertura para una práctica."""

    prestacion_id: int = Field(..., gt=0, description="Práctica del catálogo.")
    monto_cubre_os: float = Field(
        0, ge=0, description="Cuánto paga la obra social por la práctica."
    )
    monto_coseguro_paciente: float = Field(
        0,
        ge=0,
        description=(
            "Cuánto le queda al paciente como coseguro (lo que se cobra en el "
            "mostrador al cerrar la visita)."
        ),
    )

    @field_validator("monto_cubre_os", "monto_coseguro_paciente", mode="after")
    @classmethod
    def _redondear_montos(cls, valor: float) -> float:
        return redondear_monto(valor)


class ArancelObraSocialCreate(ArancelObraSocialBase):
    """Payload de creación de un arancel (``POST`` anidado en la obra social)."""


class ArancelObraSocialUpdate(BaseModel):
    """Payload de actualización parcial de un arancel (``PATCH``/``PUT``).

    ``prestacion_id`` no se puede cambiar: el arancel identifica el par
    (cobertura, práctica); para otro par hay que cargar otro arancel.
    """

    monto_cubre_os: Optional[float] = Field(None, ge=0)
    monto_coseguro_paciente: Optional[float] = Field(None, ge=0)

    @field_validator("monto_cubre_os", "monto_coseguro_paciente", mode="after")
    @classmethod
    def _redondear_montos(cls, valor: Optional[float]) -> Optional[float]:
        return None if valor is None else redondear_monto(valor)


class ArancelObraSocialRead(BaseModel):
    """Representación de salida de un arancel, con los nombres ya resueltos.

    Expone la cobertura y la práctica en campos planos (mismo criterio que
    ``EvolucionMedicaRead`` con su ``doctor``) para que la tabla del panel se
    dibuje sin resolver relaciones en el navegador.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    obra_social_id: int
    obra_social_nombre: Optional[str] = Field(None, validation_alias="obra_social")
    prestacion_id: int
    prestacion_codigo: Optional[str] = Field(None, validation_alias="prestacion")
    prestacion_descripcion: Optional[str] = Field(None, validation_alias="prestacion")
    monto_cubre_os: float
    monto_coseguro_paciente: float
    monto_total: float = Field(
        0, description="Suma de lo que paga la OS más el coseguro del paciente."
    )

    @field_validator("obra_social_nombre", mode="before")
    @classmethod
    def _nombre_obra_social(cls, valor):
        """Convierte la relación ORM ``ObraSocial`` en el nombre plano."""
        if valor is None or isinstance(valor, str):
            return valor
        return getattr(valor, "nombre", None)

    @field_validator("prestacion_codigo", "prestacion_descripcion", mode="before")
    @classmethod
    def _datos_prestacion(cls, valor, info: ValidationInfo):
        """Convierte la relación ORM ``Prestacion`` en el dato plano pedido.

        El código de la práctica vive en ``codigo_nomenclador``, así que el
        nombre del campo expuesto no coincide con el atributo del modelo.
        """
        if valor is None or isinstance(valor, str):
            return valor
        atributo = (
            "codigo_nomenclador"
            if info.field_name == "prestacion_codigo"
            else "descripcion"
        )
        return getattr(valor, atributo, None)

    @model_validator(mode="after")
    def _calcular_total(self) -> "ArancelObraSocialRead":
        """Completa ``monto_total`` (lo que vale la práctica acordada)."""
        self.monto_total = sumar_montos(
            [self.monto_cubre_os, self.monto_coseguro_paciente]
        )
        return self


# --------------------------------------------------------------------------- #
# Convenios: la grilla completa de precios de una cobertura
# --------------------------------------------------------------------------- #
# Estado de cada fila del reporte de importación. ``IGNORADA`` es una fila con
# código pero sin precio (la planilla descargada trae vacías las prácticas que
# todavía no tienen convenio) y ``SIN_CAMBIOS`` una fila cuyo precio es el mismo
# que ya estaba cargado.
EstadoImportacionArancel = Literal[
    "NUEVO", "ACTUALIZADO", "SIN_CAMBIOS", "IGNORADA", "ERROR"
]


class ConvenioFilaBase(BaseModel):
    """Precio acordado con la cobertura para una práctica de la grilla."""

    prestacion_id: int = Field(..., gt=0, description="Práctica del nomenclador.")
    monto_cubre_os: float = Field(
        0, ge=0, description="Monto que cubre la obra social por esa práctica."
    )
    monto_coseguro_paciente: Optional[float] = Field(
        None,
        ge=0,
        description=(
            "Coseguro que queda para el mostrador. Si se omite, el servidor "
            "sugiere la diferencia entre el arancel base y lo que cubre la OS."
        ),
    )

    @field_validator("monto_cubre_os", "monto_coseguro_paciente", mode="after")
    @classmethod
    def _redondear_montos(cls, valor: Optional[float]) -> Optional[float]:
        return None if valor is None else redondear_monto(valor)


class GuardarConvenioRequest(BaseModel):
    """Payload del guardado en lote de la grilla de convenios (``PUT``)."""

    filas: List[ConvenioFilaBase] = Field(
        ...,
        min_length=1,
        description="Filas del convenio a crear o actualizar (al menos una).",
    )

    @model_validator(mode="after")
    def _sin_prestaciones_repetidas(self) -> "GuardarConvenioRequest":
        """Una cobertura no puede tener dos precios de la misma práctica."""
        vistas = set()
        for fila in self.filas:
            if fila.prestacion_id in vistas:
                raise ValueError(
                    "La grilla repite la prestación id="
                    f"{fila.prestacion_id}: cada práctica va una sola vez."
                )
            vistas.add(fila.prestacion_id)
        return self


class ConvenioFilaRead(BaseModel):
    """Fila de la grilla de convenios: arancel base, cobertura y coseguro.

    ``coseguro_sugerido`` es la diferencia entre el ``valor_base`` (arancel de
    referencia del Colegio de Córdoba) y lo que cubre la OS: el número que el
    panel muestra en vivo mientras la secretaría escribe. ``cargado`` indica si la
    cobertura ya tiene un precio guardado para esa práctica.
    """

    model_config = ConfigDict(from_attributes=True)

    prestacion_id: int
    codigo_nomenclador: str
    descripcion: str
    valor_base: float
    arancel_id: Optional[int] = None
    monto_cubre_os: float = 0
    monto_coseguro_paciente: float = 0
    coseguro_sugerido: float = 0
    monto_total: float = 0
    cargado: bool = False


class ConvenioObraSocialRead(BaseModel):
    """Estado completo del convenio de una cobertura (lo que pinta el panel)."""

    obra_social_id: int
    obra_social_nombre: str
    total_prestaciones: int
    total_cargadas: int
    total_valor_base: float
    total_cubre_os: float
    total_coseguro: float
    filas: List[ConvenioFilaRead]
    # Resultado del guardado en lote (queda en 0 al consultar el convenio).
    creados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0


class ImportarArancelesRequest(BaseModel):
    """Payload del importador masivo de aranceles (planilla ``.csv``/``.xlsx``).

    El archivo puede venir como **texto** (``contenido``, útil para CSV) o en
    **base64** (``contenido_base64``, el camino del panel: sirve para CSV y para
    XLSX). ``simular`` permite previsualizar el resultado sin escribir nada.
    """

    nombre_archivo: Optional[str] = Field(
        None,
        max_length=200,
        description="Nombre del archivo subido (ayuda a elegir el lector).",
    )
    contenido: Optional[str] = Field(
        None,
        max_length=2_000_000,
        description="Planilla en texto plano (CSV).",
    )
    contenido_base64: Optional[str] = Field(
        None,
        max_length=6_000_000,
        description="Planilla codificada en base64 (sirve para CSV y XLSX).",
    )
    simular: bool = Field(
        False,
        description=(
            "Con ``true`` devuelve el reporte sin tocar la base (vista previa)."
        ),
    )

    @model_validator(mode="after")
    def _un_solo_archivo(self) -> "ImportarArancelesRequest":
        """Exige exactamente una de las dos formas de mandar el archivo."""
        if bool(self.contenido) == bool(self.contenido_base64):
            raise ValueError(
                "Enviá la planilla en «contenido_base64» (o en «contenido» como "
                "texto): no se puede omitir ni mandar las dos."
            )
        return self


class ImportarArancelesFilaRead(BaseModel):
    """Resultado de una fila de la planilla importada."""

    linea: int
    codigo: str
    prestacion_id: Optional[int] = None
    descripcion: Optional[str] = None
    monto_cubre_os: Optional[float] = None
    monto_coseguro_paciente: Optional[float] = None
    estado: EstadoImportacionArancel
    mensaje: Optional[str] = None


class ImportarArancelesRead(BaseModel):
    """Reporte de la importación: qué se creó, qué se actualizó y qué falló."""

    obra_social_id: int
    obra_social_nombre: str
    simulado: bool
    nombre_archivo: Optional[str] = None
    filas_leidas: int
    creados: int
    actualizados: int
    sin_cambios: int
    ignoradas: int
    errores: int
    detalle: List[ImportarArancelesFilaRead] = Field(default_factory=list)
