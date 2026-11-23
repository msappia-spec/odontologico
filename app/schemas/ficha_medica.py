"""Esquemas Pydantic de la ficha médica del paciente (Módulo 4).

La ficha médica reproduce las dos caras de la hoja de papel del consultorio:

* **cabecera**: vencimiento, grupo familiar, parentesco y antecedentes de
  enfermedad;
* **estado bucal general**: presencia de sarro y enfermedad periodontal;
* **trabajo realizado**: fecha, código, pieza dentaria, práctica e importes
  Debe/Haber, con el saldo calculado renglón por renglón.

Los datos filiatorios viajan **dentro del paciente** (:class:`PacienteRead`),
que es su única fuente de verdad: la ficha no los duplica ni los vuelve a
validar.

Reglas de normalización:

* Los textos se recortan y el vacío se guarda como ``NULL`` (igual que la ficha
  de salud y los datos administrativos del paciente).
* ``debe`` y ``haber`` son importes en pesos, nunca negativos. El **saldo no se
  recibe**: lo calcula el servicio sumando Debe y restando Haber en el orden del
  libro.
* ``presencia_sarro`` y ``enfermedad_periodontal`` admiten ``null`` («sin
  responder»), ``true`` (SI) y ``false`` (NO).
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.models.ficha_medica import MAX_RENGLONES_TRABAJO, MAX_TEXTO_TRABAJO
from app.schemas.paciente import PacienteRead

#: Textos libres de la cabecera y su longitud máxima (la misma de la tabla).
_LARGOS_CABECERA: dict[str, int] = {
    "grupo_familiar": 120,
    "parentesco": 60,
    "antecedentes_enfermedad": 1000,
}

#: Tope de cada importe del libro (evita errores de tipeo enormes).
_MAX_IMPORTE = 99_999_999.0


def _limpiar_texto(valor, info: ValidationInfo):
    """Recorta el texto, rechaza el exceso y convierte el vacío en ``NULL``."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    largo = _LARGOS_CABECERA[info.field_name]
    if len(texto) > largo:
        raise ValueError(f"{info.field_name} no puede superar los {largo} caracteres.")
    return texto


class FichaMedicaTrabajoCreate(BaseModel):
    """Un renglón del libro «Trabajo realizado»."""

    fecha: Optional[date] = Field(
        None, description="Fecha de la práctica (AAAA-MM-DD)."
    )
    codigo: Optional[str] = Field(
        None,
        max_length=30,
        description=(
            "Código del nomenclador tomado del catálogo de prestaciones; "
            "se guarda como texto para conservar el histórico."
        ),
    )
    pieza: Optional[str] = Field(
        None,
        max_length=30,
        description="Pieza dentaria; admite varias, p. ej. «18, 17».",
    )
    trabajo: Optional[str] = Field(
        None, max_length=MAX_TEXTO_TRABAJO, description="Práctica realizada."
    )
    debe: Optional[float] = Field(
        None, ge=0, le=_MAX_IMPORTE, description="Importe debitado (pesos)."
    )
    haber: Optional[float] = Field(
        None, ge=0, le=_MAX_IMPORTE, description="Importe acreditado (pesos)."
    )

    @field_validator("fecha", mode="before")
    @classmethod
    def _fecha_opcional(cls, valor):
        """Acepta el ``""`` de un formulario web y las fechas ISO."""
        if valor is None:
            return None
        if isinstance(valor, str) and not valor.strip():
            return None
        return valor

    @field_validator("codigo", "pieza", "trabajo", mode="before")
    @classmethod
    def _textos(cls, valor, info: ValidationInfo):
        if valor is None:
            return None
        texto = str(valor).strip()
        if not texto:
            return None
        largo = MAX_TEXTO_TRABAJO if info.field_name == "trabajo" else 30
        if len(texto) > largo:
            raise ValueError(
                f"{info.field_name} no puede superar los {largo} caracteres."
            )
        return texto

    def tiene_datos(self) -> bool:
        """``True`` si el renglón no está completamente en blanco."""
        return any(
            (
                self.fecha is not None,
                self.codigo is not None,
                self.pieza is not None,
                self.trabajo is not None,
                self.debe is not None,
                self.haber is not None,
            )
        )


class FichaMedicaUpdate(BaseModel):
    """Payload del ``PUT``: reemplaza la ficha médica completa del paciente."""

    vencimiento: Optional[date] = Field(
        None, description="Vencimiento de la credencial («Vto» del papel)."
    )
    grupo_familiar: Optional[str] = Field(
        None, max_length=120, description="Grupo familiar del titular."
    )
    parentesco: Optional[str] = Field(
        None, max_length=60, description="Parentesco con el titular."
    )
    antecedentes_enfermedad: Optional[str] = Field(
        None,
        max_length=1000,
        description="Antecedentes de enfermedad anotados por el profesional.",
    )
    presencia_sarro: Optional[bool] = Field(
        None,
        description="Presencia de sarro: ``true`` (SI), ``false`` (NO) o ``null``.",
    )
    enfermedad_periodontal: Optional[bool] = Field(
        None,
        description="Enfermedad periodontal: ``true`` (SI), ``false`` (NO) o ``null``.",
    )
    trabajos: List[FichaMedicaTrabajoCreate] = Field(
        default_factory=list,
        description=(
            "Renglones de «Trabajo realizado». El conjunto reemplaza al "
            "anterior; los renglones en blanco se descartan."
        ),
    )

    @field_validator("vencimiento", mode="before")
    @classmethod
    def _vencimiento_opcional(cls, valor):
        """Acepta el campo vacío del formulario (``""``) como ``None``."""
        if valor is None:
            return None
        if isinstance(valor, str) and not valor.strip():
            return None
        return valor

    @field_validator(*_LARGOS_CABECERA, mode="before")
    @classmethod
    def _textos_cabecera(cls, valor, info: ValidationInfo):
        return _limpiar_texto(valor, info)

    @model_validator(mode="after")
    def _limite_de_renglones(self) -> "FichaMedicaUpdate":
        """El libro no puede traer una cantidad absurda de renglones."""
        if len(self.trabajos) > MAX_RENGLONES_TRABAJO:
            raise ValueError(
                f"El libro de trabajos admite hasta {MAX_RENGLONES_TRABAJO} "
                "renglones."
            )
        return self


class FichaMedicaTrabajoRead(BaseModel):
    """Renglón guardado, con el saldo acumulado que calcula el servicio."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    orden: int
    fecha: Optional[date] = None
    codigo: Optional[str] = None
    pieza: Optional[str] = None
    trabajo: Optional[str] = None
    debe: Optional[float] = None
    haber: Optional[float] = None
    saldo: float = Field(
        default=0.0, description="Saldo acumulado (Debe - Haber) hasta este renglón."
    )


class FichaMedicaRead(BaseModel):
    """Ficha médica de un paciente (``id = null`` si todavía no se cargó)."""

    id: Optional[int] = None
    historia_clinica_id: Optional[int] = None
    numero_ficha: Optional[str] = Field(
        None, description="Número visible de la historia clínica (HC-000123)."
    )
    vencimiento: Optional[date] = None
    grupo_familiar: Optional[str] = None
    parentesco: Optional[str] = None
    antecedentes_enfermedad: Optional[str] = None
    presencia_sarro: Optional[bool] = None
    enfermedad_periodontal: Optional[bool] = None
    actualizado_en: Optional[datetime] = None
    trabajos: List[FichaMedicaTrabajoRead] = Field(default_factory=list)
    total_debe: float = 0.0
    total_haber: float = 0.0
    saldo: float = 0.0
    paciente: PacienteRead
