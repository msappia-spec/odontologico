"""Esquemas Pydantic de ``Paciente``."""

import re
from datetime import date
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
)

# Documento del paciente (DNI, CUIT o pasaporte): entre 5 y 20 caracteres
# alfanuméricos, ya sin los separadores habituales (puntos, guiones, espacios).
_RE_DOCUMENTO = re.compile(r"^[0-9A-Za-z]{5,20}$")

#: Textos filiatorios del paciente y su longitud máxima. Es la misma lista que
#: usa la ficha médica, así que la validación no cambia según la pantalla.
_LARGOS_FILIATORIOS: dict[str, int] = {
    "estado_civil": 30,
    "nacionalidad": 60,
    "domicilio": 200,
    "profesion": 120,
    "titular": 120,
    "lugar_trabajo": 120,
    "jerarquia": 60,
}


class PacienteDatosFiliatorios(BaseModel):
    """Datos filiatorios de la Historia Clínica General del consultorio.

    Estos campos viven en la tabla ``pacientes`` y **también** viajan con la
    ficha médica (:class:`app.schemas.antecedentes_salud.DeclaracionSaludCreate`
    y ``FichaMedicaUpdate``): el paciente los completa desde el enlace público y
    la secretaría desde el panel, y al guardar la ficha se sincronizan en el
    registro del paciente (ver :mod:`app.services.anamnesis_service`). Hay una
    sola fuente de verdad y los datos administrativos no se tipean dos veces.

    Reglas de normalización:

    * Los textos vacíos o con sólo espacios se guardan como ``NULL``.
    * ``fecha_nacimiento`` se recibe como ``AAAA-MM-DD`` (el valor de un
      ``<input type="date">``) y se rechaza si es futura. La **edad no se
      persiste**: se calcula en :class:`PacienteRead` para que no quede
      desactualizada con el paso del tiempo.
    """

    fecha_nacimiento: Optional[date] = Field(
        None, description="Fecha de nacimiento (AAAA-MM-DD). La edad se calcula."
    )
    estado_civil: Optional[str] = Field(
        None, max_length=30, description="Estado civil declarado."
    )
    nacionalidad: Optional[str] = Field(
        None, max_length=60, description="Nacionalidad del paciente."
    )
    domicilio: Optional[str] = Field(
        None, max_length=200, description="Calle, número, barrio y localidad."
    )
    profesion: Optional[str] = Field(
        None, max_length=120, description="Profesión o actividad."
    )
    titular: Optional[str] = Field(
        None,
        max_length=120,
        description="Titular de la obra social (puede no ser el paciente).",
    )
    lugar_trabajo: Optional[str] = Field(
        None, max_length=120, description="Lugar de trabajo."
    )
    jerarquia: Optional[str] = Field(
        None, max_length=60, description="Jerarquía o categoría laboral."
    )

    @field_validator(*_LARGOS_FILIATORIOS, mode="before")
    @classmethod
    def _textos_filiatorios(cls, valor, info: ValidationInfo):
        """Limpia espacios y convierte los textos vacíos en ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        if not texto:
            return None
        largo = _LARGOS_FILIATORIOS[info.field_name]
        if len(texto) > largo:
            raise ValueError(
                f"{info.field_name} no puede superar los {largo} caracteres."
            )
        return texto

    @field_validator("fecha_nacimiento", mode="before")
    @classmethod
    def _fecha_de_nacimiento(cls, valor):
        """Acepta el campo vacío y rechaza fechas futuras o mal escritas."""
        if valor is None:
            return None
        if isinstance(valor, date):
            fecha = valor
        else:
            texto = str(valor).strip()
            if not texto:
                return None
            try:
                fecha = date.fromisoformat(texto[:10])
            except ValueError as exc:
                raise ValueError(
                    "La fecha de nacimiento debe tener el formato AAAA-MM-DD."
                ) from exc
        if fecha > date.today():
            raise ValueError("La fecha de nacimiento no puede ser futura.")
        return fecha


class PacienteDatosAdministrativos(PacienteDatosFiliatorios):
    """Datos administrativos opcionales del paciente (alta, edición y lectura).

    Los campos son opcionales pero **normalizados**:

    * Las cadenas vacías o con sólo espacios se guardan como ``NULL`` (así el
      formulario web puede enviar los campos sin completar sin ensuciar la base).
    * El ``dni`` se limpia de puntos, guiones y espacios y se valida su formato,
      de modo que ``"30.123.456"`` y ``"30123456"`` sean el mismo documento
      (clave para el índice único ``uq_paciente_dni``).

    La cobertura se expresa de dos maneras:

    * ``obra_social_id``: **forma recomendada**; es la clave foránea al catálogo
      ``obras_sociales`` (el desplegable del formulario de pacientes la usa).
    * ``obra_social``: texto libre *heredado* (compatibilidad con clientes
      anteriores y con el ``curl`` de la documentación). El servicio lo resuelve
      contra el catálogo: si ya existe una cobertura con ese nombre la vincula y,
      si no, la crea. Enviarlo con ``null`` (o ``""``) desvincula la cobertura.
    """

    dni: Optional[str] = Field(
        None,
        max_length=20,
        description="Documento del paciente (único). Se guarda sin puntos ni guiones.",
    )
    obra_social_id: Optional[int] = Field(
        None,
        gt=0,
        description="Id de la obra social del paciente (catálogo /obras-sociales).",
    )
    obra_social: Optional[str] = Field(
        None,
        max_length=120,
        description=(
            "Nombre de la obra social (texto heredado). Se resuelve contra el "
            "catálogo: se vincula la existente o se crea una nueva."
        ),
    )
    numero_afiliado: Optional[str] = Field(
        None,
        max_length=60,
        description="Número de afiliado dentro de la obra social.",
    )

    @field_validator("dni", "obra_social", "numero_afiliado", mode="before")
    @classmethod
    def _normalizar_opcionales(cls, valor, info: ValidationInfo):
        """Convierte cadenas vacías en ``None`` y normaliza el documento."""
        if valor is None:
            return None
        texto = str(valor).strip()
        if not texto:
            return None
        if info.field_name == "dni":
            texto = re.sub(r"[\s.\-]", "", texto)
            if not _RE_DOCUMENTO.match(texto):
                raise ValueError(
                    "El DNI debe tener entre 5 y 20 caracteres alfanuméricos "
                    "(se ignoran puntos, guiones y espacios)."
                )
        return texto

    @field_validator("obra_social_id", mode="before")
    @classmethod
    def _normalizar_obra_social_id(cls, valor):
        """Convierte el ``""`` del ``<select>`` en ``None`` (sin cobertura)."""
        if valor is None:
            return None
        if isinstance(valor, str):
            texto = valor.strip()
            return int(texto) if texto else None
        return valor


class PacienteBase(PacienteDatosAdministrativos):
    nombre: str = Field(..., min_length=1, max_length=120)
    telefono: str = Field(
        ...,
        min_length=6,
        max_length=30,
        description="Teléfono en formato internacional, p. ej. +5491122334455",
    )
    email: Optional[EmailStr] = None


class PacienteCreate(PacienteBase):
    """Payload de creación de un paciente."""


class PacienteUpdate(PacienteDatosAdministrativos):
    """Payload de actualización parcial de un paciente (``PATCH``).

    Todos los campos son opcionales: sólo se aplican los enviados. Enviar un
    campo con ``null`` lo limpia (p. ej. ``{"dni": null}`` borra el documento y
    ``{"obra_social_id": null}`` quita la cobertura).
    """

    nombre: Optional[str] = Field(None, min_length=1, max_length=120)
    telefono: Optional[str] = Field(None, min_length=6, max_length=30)
    email: Optional[EmailStr] = None


class PacienteRead(PacienteBase):
    """Representación de salida de un paciente.

    Expone la cobertura de las dos formas: ``obra_social_id`` (la clave foránea)
    y ``obra_social`` como **nombre** de la cobertura asociada, para que el panel
    muestre la tabla sin resolver la relación en el navegador.

    ``edad`` es un campo **calculado** a partir de ``fecha_nacimiento``: no se
    guarda en la base para que no quede desactualizado con los cumpleaños.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int

    @field_validator("obra_social", mode="before")
    @classmethod
    def _nombre_de_la_obra_social(cls, valor):
        """Convierte la relación ORM ``ObraSocial`` en su ``nombre``."""
        if valor is None or isinstance(valor, str):
            return valor
        return getattr(valor, "nombre", None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def edad(self) -> Optional[int]:
        """Edad en años cumplidos (``None`` si no hay fecha de nacimiento)."""
        nacimiento = self.fecha_nacimiento
        if nacimiento is None:
            return None
        hoy = date.today()
        cumplio = (hoy.month, hoy.day) >= (nacimiento.month, nacimiento.day)
        return hoy.year - nacimiento.year - (0 if cumplio else 1)


