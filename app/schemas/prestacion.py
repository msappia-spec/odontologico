"""Esquemas Pydantic de ``Prestacion`` (catálogo de prácticas del Módulo 3)."""

from typing import List, Literal, Optional

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


# --------------------------------------------------------------------------- #
# Importación del PDF de aranceles del Colegio
# --------------------------------------------------------------------------- #
# ``NUEVO`` y ``ACTUALIZADO`` son las prácticas que entran o cambian con el PDF;
# ``SIN_CAMBIOS`` las que ya coinciden y ``ERROR`` las que no se pudieron
# procesar (por ejemplo, un código repetido dentro del propio PDF).
EstadoImportacionPrestacion = Literal["NUEVO", "ACTUALIZADO", "SIN_CAMBIOS", "ERROR"]


class ImportarPrestacionesPdfRequest(BaseModel):
    """Payload del importador del PDF de aranceles.

    El panel lee el archivo con ``FileReader`` y lo manda codificado en
    **base64** (el PDF oficial pesa ~2 MB). ``simular`` permite ver el reporte
    sin escribir en el catálogo.
    """

    nombre_archivo: Optional[str] = Field(
        None,
        max_length=200,
        description="Nombre del PDF subido (se devuelve en el reporte).",
    )
    contenido_base64: str = Field(
        ...,
        min_length=1,
        # 10 MB de PDF pesan ~13,3 MB en base64. El límite se valida de nuevo
        # (sobre los bytes) al abrir el archivo, en ``leer_aranceles_pdf``.
        max_length=14_000_000,
        description="PDF codificado en base64 (también acepta un data URL).",
    )
    simular: bool = Field(
        False,
        description=(
            "Con ``true`` devuelve el reporte sin crear ni actualizar prácticas."
        ),
    )


class ImportarPrestacionesPdfFilaRead(BaseModel):
    """Resultado de una práctica leída del PDF."""

    pagina: int
    linea: int
    codigo: str
    descripcion: str
    valor_base: Optional[float] = None
    prestacion_id: Optional[int] = None
    estado: EstadoImportacionPrestacion
    sin_precio: bool = Field(
        False,
        description=(
            "La práctica viene sin honorario en el PDF (queda con valor 0 si es "
            "nueva, o conserva el valor que ya tenía)."
        ),
    )
    mensaje: Optional[str] = None


class ImportarPrestacionesPdfRead(BaseModel):
    """Reporte de la importación del PDF de aranceles."""

    simulado: bool
    nombre_archivo: Optional[str] = None
    filas_leidas: int
    creados: int
    actualizados: int
    sin_cambios: int
    sin_precio: int
    errores: int
    detalle: List[ImportarPrestacionesPdfFilaRead] = Field(default_factory=list)
