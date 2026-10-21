"""Esquemas Pydantic del Módulo 2: historia clínica, evoluciones y adjuntos.

Sólo hay **entrada de alta** (``EvolucionMedicaCreate``) y **salida de lectura**:
no existen esquemas de actualización porque las evoluciones médicas son registros
inmutables (ver :mod:`app.models.evolucion_medica`).

Notas de diseño:

* ``historia_clinica_id`` **no** se acepta en el alta: la historia surge del
  paciente de la URL (``POST /pacientes/{id}/evoluciones``). Así el cliente no
  puede dirigir una evolución a la ficha de otro paciente.
* Los textos se limpian (``strip``) y los obligatorios no admiten quedar vacíos:
  una evolución sin diagnóstico o sin tratamiento no tiene valor clínico.
* ``observaciones`` vacío (``""``) se guarda como ``NULL``, igual que los campos
  administrativos opcionales del paciente.
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
from app.models.archivo_adjunto import TIPO_ARCHIVO_POR_DEFECTO
from app.schemas.antecedentes_salud import AntecedentesSaludRead

# Límite de los textos clínicos (el dictado de una atención sobra de largo).
_MAX_TEXTO = 4000


class ArchivoAdjuntoCreate(BaseModel):
    """Archivo (radiografía, foto, estudio) que acompaña a una evolución."""

    url_archivo: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="URL del archivo ya subido (radiografía, foto o estudio).",
    )
    tipo_archivo: str = Field(
        default=TIPO_ARCHIVO_POR_DEFECTO,
        max_length=30,
        description=(
            "Tipo de archivo, en mayúsculas (RADIOGRAFIA por defecto; también "
            "FOTO, ESTUDIO, OTRO…)."
        ),
    )

    @field_validator("url_archivo", mode="before")
    @classmethod
    def _limpiar_url(cls, valor):
        """Recorta la URL; una vacía es un error de carga (no un archivo)."""
        texto = str(valor).strip() if valor is not None else ""
        if not texto:
            raise ValueError("La URL del archivo no puede estar vacía.")
        return texto

    @field_validator("tipo_archivo", mode="before")
    @classmethod
    def _normalizar_tipo(cls, valor):
        """Normaliza el tipo a MAYÚSCULAS sin espacios (``foto`` -> ``FOTO``)."""
        texto = str(valor).strip().upper() if valor is not None else ""
        return texto or TIPO_ARCHIVO_POR_DEFECTO


class EvolucionMedicaCreate(BaseModel):
    """Payload del alta de una evolución médica (``POST``, único método de escritura)."""

    doctor_id: int = Field(..., gt=0, description="Odontólogo que realizó la atención.")
    fecha_atencion: Optional[datetime] = Field(
        None,
        description="Fecha/hora de la atención. Si se omite se usa el momento del alta.",
    )
    motivo_consulta: str = Field(..., max_length=_MAX_TEXTO)
    diagnostico: str = Field(..., max_length=_MAX_TEXTO)
    tratamiento_efectuado: str = Field(..., max_length=_MAX_TEXTO)
    observaciones: Optional[str] = Field(None, max_length=_MAX_TEXTO)
    archivos: List[ArchivoAdjuntoCreate] = Field(
        default_factory=list,
        description="Radiografías o estudios de la atención (opcional).",
    )

    @field_validator(
        "motivo_consulta", "diagnostico", "tratamiento_efectuado", mode="before"
    )
    @classmethod
    def _exigir_texto_clinico(cls, valor, info: ValidationInfo):
        """Limpia el texto y rechaza los campos clínicos obligatorios vacíos."""
        texto = str(valor).strip() if valor is not None else ""
        if not texto:
            raise ValueError(f"{info.field_name} no puede estar vacío.")
        return texto

    @field_validator("observaciones", mode="before")
    @classmethod
    def _observaciones_opcionales(cls, valor):
        """Las observaciones son opcionales: ``""`` se guarda como ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

    @model_validator(mode="after")
    def _normalizar_fecha(self) -> "EvolucionMedicaCreate":
        """Deja ``fecha_atencion`` en UTC naive (convención del proyecto)."""
        if self.fecha_atencion is not None:
            self.fecha_atencion = to_naive_utc(self.fecha_atencion)
        return self


class ArchivoAdjuntoRead(BaseModel):
    """Representación de salida de un archivo adjunto."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    url_archivo: str
    tipo_archivo: str
    fecha_subida: datetime


class EvolucionMedicaRead(BaseModel):
    """Representación de salida de una evolución médica.

    Expone el nombre y la especialidad de quien atendió, planos, para que la
    línea de tiempo se dibuje sin resolver relaciones en el navegador (mismo
    criterio que ``PacienteRead`` con el nombre de la obra social).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    historia_clinica_id: int
    doctor_id: int
    # ``validation_alias="doctor"``: al validar desde el ORM se lee la relación
    # ``doctor`` y el validador la convierte en el texto que muestra la UI.
    doctor_nombre: Optional[str] = Field(None, validation_alias="doctor")
    doctor_especialidad: Optional[str] = Field(None, validation_alias="doctor")
    fecha_atencion: datetime
    motivo_consulta: str
    diagnostico: str
    tratamiento_efectuado: str
    observaciones: Optional[str] = None
    archivos: List[ArchivoAdjuntoRead] = Field(default_factory=list)

    @field_validator("doctor_nombre", "doctor_especialidad", mode="before")
    @classmethod
    def _datos_del_doctor(cls, valor, info: ValidationInfo):
        """Convierte la relación ORM ``Doctor`` en el campo plano homónimo."""
        if valor is None or isinstance(valor, str):
            return valor
        return getattr(valor, info.field_name.replace("doctor_", ""), None)


class HistoriaClinicaDetalle(BaseModel):
    """Historia clínica de un paciente con su línea de tiempo (sólo lectura).

    ``id`` viaja en ``null`` mientras el paciente no tenga **evoluciones ni
    ficha de salud**: la historia se crea con la primera evolución o cuando el
    paciente envía su anamnesis. Una consulta de sólo lectura nunca escribe en
    la base.

    ``alerta_medica`` y ``motivos_alerta`` se repiten en el nivel superior para
    que el odontólogo pueda mostrar el aviso rojo sin recorrer el objeto de
    antecedentes (la sección móvil usa exactamente esos dos campos).
    """

    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = Field(
        None,
        description=(
            "Id de la ficha; ``null`` si el paciente aún no tiene evoluciones "
            "ni ficha de salud."
        ),
    )
    paciente_id: int
    paciente_nombre: str
    paciente_dni: Optional[str] = None
    numero_ficha: str
    total_evoluciones: int
    evoluciones: List[EvolucionMedicaRead] = Field(default_factory=list)
    antecedentes_salud: Optional[AntecedentesSaludRead] = Field(
        None,
        description="Declaración de salud enviada por el paciente (Módulo 4).",
    )
    alerta_medica: bool = Field(
        default=False,
        description="``True`` si la ficha de salud tiene antecedentes de riesgo.",
    )
    motivos_alerta: List[str] = Field(
        default_factory=list,
        description="Antecedentes de riesgo, con etiquetas listas para mostrar.",
    )


