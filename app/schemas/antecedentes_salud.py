"""Esquemas Pydantic del Módulo 4: ficha pública de salud (anamnesis).

Hay dos grupos:

* **Entrada pública** (``DeclaracionSaludCreate``): lo único que el paciente
  puede enviar. No incluye ids, paciente, historia clínica, ``completado`` ni
  fechas: todo eso lo decide el servidor a partir del token firmado.
* **Salida**: ``AntecedentesSaludRead`` (datos + alerta) y
  ``AnamnesisPublicaRead`` (contexto del turno para mostrar en el enlace).

Los campos de riesgo se calculan con las propiedades del modelo para que la
tarjeta del odontólogo y la respuesta de la API nunca queden desincronizadas.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, computed_field, field_validator

from app.models.antecedentes_salud import MAX_TEXTO_ANTECEDENTES
from app.models.enums import EstadoTurno


class DeclaracionSaludCreate(BaseModel):
    """Cuestionario que completa el paciente desde el enlace público.

    Los textos son opcionales; ``"\"\"`` o sólo espacios se guardan como
    ``NULL``. Los booleanos ausentes equivalen a «no» (``False``).
    """

    alergias_medicamentos: Optional[str] = Field(
        None,
        max_length=MAX_TEXTO_ANTECEDENTES,
        description="Alergias o reacciones a medicamentos (opcional).",
    )
    presion_alta: bool = Field(
        default=False, description="El paciente declara presión arterial alta."
    )
    problemas_cardiacos: bool = Field(
        default=False, description="El paciente declara problemas cardíacos."
    )
    medicacion_actual: Optional[str] = Field(
        None,
        max_length=MAX_TEXTO_ANTECEDENTES,
        description="Medicación que toma actualmente (opcional).",
    )
    embarazo: bool = Field(
        default=False, description="El paciente declara estar embarazada."
    )

    @field_validator(
        "alergias_medicamentos", "medicacion_actual", mode="before"
    )
    @classmethod
    def _textos_opcionales(cls, valor, info: ValidationInfo):
        """Limpia espacios y normaliza los textos vacíos a ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        if len(texto) > MAX_TEXTO_ANTECEDENTES:
            raise ValueError(
                f"{info.field_name} no puede superar los "
                f"{MAX_TEXTO_ANTECEDENTES} caracteres."
            )
        return texto or None


class AntecedentesSaludRead(BaseModel):
    """Declaración de salud tal como la lee el odontólogo.

    ``motivos_alerta`` y ``alerta_medica`` son **campos calculados** a partir
    de los datos declarados: no se guardan en la base para que no puedan quedar
    desactualizados si se corrige la ficha.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    historia_clinica_id: int
    alergias_medicamentos: Optional[str] = None
    presion_alta: bool = False
    problemas_cardiacos: bool = False
    medicacion_actual: Optional[str] = None
    embarazo: bool = False
    completado: bool = False
    fecha_completado: Optional[datetime] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def motivos_alerta(self) -> List[str]:
        """Etiquetas legibles de los antecedentes que exigen atención."""
        motivos: List[str] = []
        if (self.alergias_medicamentos or "").strip():
            motivos.append("Alergias a medicamentos")
        if self.presion_alta:
            motivos.append("Presión arterial alta")
        if self.problemas_cardiacos:
            motivos.append("Problemas cardíacos")
        if (self.medicacion_actual or "").strip():
            motivos.append("Medicación actual")
        if self.embarazo:
            motivos.append("Embarazo")
        return motivos

    @computed_field  # type: ignore[prop-decorator]
    @property
    def alerta_medica(self) -> bool:
        """``True`` si hay al menos un antecedente de riesgo declarado."""
        return bool(self.motivos_alerta)


class AnamnesisPublicaRead(BaseModel):
    """Contexto que ve el paciente al abrir su enlace y estado de la ficha."""

    turno_id: int
    paciente_id: int
    paciente_nombre: str
    doctor_nombre: Optional[str] = None
    doctor_especialidad: Optional[str] = None
    fecha_hora_inicio: datetime
    estado_turno: EstadoTurno
    antecedentes_salud: Optional[AntecedentesSaludRead] = Field(
        None,
        description="``null`` mientras el paciente no haya enviado la ficha.",
    )
    completado: bool = Field(
        default=False, description="``True`` si la declaración ya fue enviada."
    )


class AnamnesisGuardadaRead(AnamnesisPublicaRead):
    """Respuesta del envío: el estado actualizado y un mensaje de confirmación."""

    mensaje: str = "¡Gracias! Recibimos tu ficha de salud."
