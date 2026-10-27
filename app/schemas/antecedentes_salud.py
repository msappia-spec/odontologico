"""Esquemas Pydantic del Módulo 4: ficha médica (anamnesis).

El cuestionario reproduce la **Historia Clínica General** del consultorio y se
divide en dos grupos de campos:

* **Paciente** (``_CamposPaciente``): datos filiatorios del encabezado, antecedentes
  familiares, personales e historia odontológica declarada. Es lo único que
  acepta el enlace público (``DeclaracionSaludCreate``): un paciente no puede
  firmar el examen bucal ni el diagnóstico.
* **Profesional** (``_CamposProfesional``): examen bucal, diagnóstico
  presuntivo y plan de tratamiento. Sólo los acepta el ``PUT`` interno
  (``FichaMedicaUpdate``), que usa la secretaría o el odontólogo.

Los **datos filiatorios** (``_CamposFiliatorios``) no se guardan en la tabla de
la ficha: se sincronizan en el registro del ``Paciente`` al guardar, para que la
secretaría no los tipee dos veces. Al leer, el servicio los completa desde el
paciente; las respuestas pública y del panel también exponen
``datos_filiatorios`` para precargar el formulario aunque la ficha esté
pendiente.

``AntecedentesSaludRead`` expone **todos** los campos (paciente + profesional)
para el panel y la vista móvil, con ``motivos_alerta`` y ``alerta_medica``
calculados sobre la misma función que el ORM, de modo que la tarjeta del
odontólogo y la respuesta de la API nunca queden desincronizadas.

Los textos libres se limpian (``strip``) y los vacíos se guardan como ``NULL``.
Los booleanos ausentes equivalen a «no» (``False``).
"""

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
)

from app.models.antecedentes_salud import (
    MAX_TEXTO_ANTECEDENTES,
    calcular_motivos_alerta,
)
from app.models.enums import EstadoTurno
from app.schemas.paciente import PacienteDatosFiliatorios


#: Opciones cerradas del cuestionario (se normalizan a MAYÚSCULAS).
_OPCIONES: dict[str, tuple[str, ...]] = {
    "hepatitis_tipo": ("A", "B", "C"),
    "dolor_intensidad": ("SUAVE", "MODERADO", "INTENSO"),
    "dolor_duracion": ("TEMPORARIO", "INTERMITENTE", "CONTINUO"),
    "dolor_desencadenante": ("ESPONTANEO", "PROVOCADO"),
    "dolor_estimulo": ("FRIO", "CALOR"),
    "dolor_localizacion": ("LOCALIZADO", "IRRADIADO"),
    "higiene_bucal": ("MUY_BUENO", "BUENO", "DEFICIENTE", "MALO"),
}

#: Campos de texto libre del cuestionario (validados en conjunto).
_CAMPOS_TEXTO: tuple[str, ...] = (
    "padre_patologias",
    "madre_patologias",
    "hermanos_detalle",
    "enfermedad_actual_detalle",
    "tratamiento_medico_detalle",
    "medicacion_actual",
    "medicamentos_ultimos_5_anios",
    "alergias_medicamentos",
    "alergia_otros",
    "fiebre_reumatica_proteccion",
    "diabetes_tratamiento",
    "problemas_cardiacos_detalle",
    "anticoagulantes_frecuencia",
    "chagas_tratamiento",
    "problemas_hepaticos_detalle",
    "epilepsia_medicacion",
    "enfermedad_infectocontagiosa_detalle",
    "operaciones_detalle",
    "operaciones_cuando",
    "problemas_respiratorios_detalle",
    "otra_enfermedad_detalle",
    "tratamientos_alternativos",
    "medico_clinico",
    "centro_derivacion",
    "medicacion_previa_detalle",
    "medicacion_previa_desde",
    "dolor_zona",
    "dolor_calmante",
    "traumatismo_cuando",
    "traumatismo_como",
    "fractura_cual",
    "fractura_tratamiento",
    "examen_bucal_detalle",
    "lesiones_otros",
    "sangrado_encias_cuando",
    "pus_boca_donde",
    "cara_hinchada_otros",
    "indice_placa",
    "diagnostico_presuntivo",
    "plan_tratamiento",
    "observaciones",
)


class TextosOpcionalesMixin(BaseModel):
    """Normaliza los textos libres y las opciones cerradas del cuestionario."""

    @field_validator(*_CAMPOS_TEXTO, mode="before", check_fields=False)
    @classmethod
    def _textos_opcionales(cls, valor, info: ValidationInfo):
        """Limpia espacios y convierte los textos vacíos en ``NULL``."""
        if valor is None:
            return None
        texto = str(valor).strip()
        if len(texto) > MAX_TEXTO_ANTECEDENTES:
            raise ValueError(
                f"{info.field_name} no puede superar los "
                f"{MAX_TEXTO_ANTECEDENTES} caracteres."
            )
        return texto or None

    @field_validator(*_OPCIONES, mode="before", check_fields=False)
    @classmethod
    def _opciones_cerradas(cls, valor, info: ValidationInfo):
        """Pasa la opción a MAYÚSCULAS y rechaza valores fuera de la lista."""
        if valor is None:
            return None
        texto = str(valor).strip().upper()
        if not texto:
            return None
        permitidas = _OPCIONES[info.field_name]
        if texto not in permitidas:
            raise ValueError(
                f"{info.field_name} debe ser uno de: {', '.join(permitidas)}."
            )
        return texto


class _CamposFiliatorios(PacienteDatosFiliatorios):
    """Filiatorios del encabezado de la Historia Clínica General.

    Se declaran acá (y no en la tabla de la ficha) porque su destino es el
    paciente: al guardar, ``anamnesis_service`` los copia a ``Paciente`` y al
    leer los completa desde ahí. La regla de sincronización es **no borrar**:
    un campo vacío no pisa un dato ya cargado por la secretaría.
    """


class _CamposPaciente(_CamposFiliatorios, TextosOpcionalesMixin):
    """Antecedentes que declara el paciente desde el enlace público."""

    # Antecedentes familiares
    padre_vive: bool = False
    padre_patologias: Optional[str] = None
    madre_vive: bool = False
    madre_patologias: Optional[str] = None
    hermanos: bool = False
    hermanos_sanos: bool = False
    hermanos_detalle: Optional[str] = None

    # Antecedentes personales
    enfermedad_actual: bool = False
    enfermedad_actual_detalle: Optional[str] = None
    tratamiento_medico: bool = False
    tratamiento_medico_detalle: Optional[str] = None
    medicacion_actual: Optional[str] = None
    medicamentos_ultimos_5_anios: Optional[str] = None
    deporte: bool = False
    deporte_malestar: bool = False
    alergias_medicamentos: Optional[str] = None
    alergia_anestesia: bool = False
    alergia_penicilina: bool = False
    alergia_otros: Optional[str] = None
    cicatriza_bien: bool = False
    sangra_mucho: bool = False
    colageno: bool = False
    fiebre_reumatica: bool = False
    fiebre_reumatica_proteccion: Optional[str] = None
    diabetes: bool = False
    diabetes_controlada: bool = False
    diabetes_tratamiento: Optional[str] = None
    problemas_cardiacos: bool = False
    problemas_cardiacos_detalle: Optional[str] = None
    anticoagulantes: bool = False
    anticoagulantes_frecuencia: Optional[str] = None
    presion_alta: bool = False
    chagas: bool = False
    chagas_tratamiento: Optional[str] = None
    problemas_renales: bool = False
    ulcera_gastrica: bool = False
    hepatitis: bool = False
    hepatitis_tipo: Optional[Literal["A", "B", "C"]] = None
    problemas_hepaticos: bool = False
    problemas_hepaticos_detalle: Optional[str] = None
    convulsiones: bool = False
    epilepsia: bool = False
    epilepsia_medicacion: Optional[str] = None
    sifilis_gonorrea: bool = False
    enfermedad_infectocontagiosa: bool = False
    enfermedad_infectocontagiosa_detalle: Optional[str] = None
    transfusiones: bool = False
    operaciones: bool = False
    operaciones_detalle: Optional[str] = None
    operaciones_cuando: Optional[str] = None
    problemas_respiratorios: bool = False
    problemas_respiratorios_detalle: Optional[str] = None
    fuma: bool = False
    embarazo: bool = False
    embarazo_meses: Optional[int] = Field(None, ge=0, le=12)
    otra_enfermedad: bool = False
    otra_enfermedad_detalle: Optional[str] = None
    tratamientos_alternativos: Optional[str] = None
    medico_clinico: Optional[str] = None
    centro_derivacion: Optional[str] = None

    # Historia clínica odontológica
    consulta_previa: bool = False
    medicacion_previa: bool = False
    medicacion_previa_detalle: Optional[str] = None
    medicacion_previa_desde: Optional[str] = None
    medicacion_previa_resultados: bool = False
    dolor: bool = False
    dolor_intensidad: Optional[Literal["SUAVE", "MODERADO", "INTENSO"]] = None
    dolor_duracion: Optional[
        Literal["TEMPORARIO", "INTERMITENTE", "CONTINUO"]
    ] = None
    dolor_desencadenante: Optional[Literal["ESPONTANEO", "PROVOCADO"]] = None
    dolor_estimulo: Optional[Literal["FRIO", "CALOR"]] = None
    dolor_localizacion: Optional[Literal["LOCALIZADO", "IRRADIADO"]] = None
    dolor_zona: Optional[str] = None
    dolor_calmante: Optional[str] = None
    traumatismo_dental: bool = False
    traumatismo_cuando: Optional[str] = None
    traumatismo_como: Optional[str] = None
    fractura_dental: bool = False
    fractura_cual: Optional[str] = None
    fractura_tratamiento: Optional[str] = None
    dificultad_hablar: bool = False
    dificultad_masticar: bool = False
    dificultad_abrir_boca: bool = False
    dificultad_tragar: bool = False


class _CamposProfesional(TextosOpcionalesMixin):
    """Examen bucal y diagnóstico que completa el profesional."""

    labios_anormal: bool = False
    lengua_anormal: bool = False
    paladar_anormal: bool = False
    piso_boca_anormal: bool = False
    carrillos_anormal: bool = False
    rebordes_anormal: bool = False
    trigono_anormal: bool = False
    retromolar_anormal: bool = False
    examen_bucal_detalle: Optional[str] = None
    lesiones_manchas: bool = False
    lesiones_abultamiento: bool = False
    lesiones_ulceraciones: bool = False
    lesiones_ampollas: bool = False
    lesiones_otros: Optional[str] = None
    sangrado_encias: bool = False
    sangrado_encias_cuando: Optional[str] = None
    pus_boca: bool = False
    pus_boca_donde: Optional[str] = None
    movilidad_dental: bool = False
    dientes_altos: bool = False
    cara_hinchada: bool = False
    cara_hinchada_hielo: bool = False
    cara_hinchada_calor: bool = False
    cara_hinchada_otros: Optional[str] = None
    momentos_azucar: Optional[int] = Field(None, ge=0, le=50)
    indice_placa: Optional[str] = None
    higiene_bucal: Optional[
        Literal["MUY_BUENO", "BUENO", "DEFICIENTE", "MALO"]
    ] = None
    sarro: bool = False
    enfermedad_periodontal: bool = False
    diagnostico_presuntivo: Optional[str] = None
    plan_tratamiento: Optional[str] = None
    fecha_plan: Optional[date] = None
    observaciones: Optional[str] = None


class DeclaracionSaludCreate(_CamposPaciente):
    """Cuestionario que completa el paciente desde el enlace público.

    No incluye el examen bucal ni el diagnóstico: esos bloques los completa el
    profesional desde el panel interno. Un enlace público que los envíe los ve
    ignorados (Pydantic descarta los campos desconocidos).
    """


class FichaMedicaUpdate(_CamposPaciente, _CamposProfesional):
    """Ficha completa que carga la secretaría o el profesional desde el panel.

    Es el payload del ``PUT /anamnesis/panel/{turno_id}``: acepta tanto los
    antecedentes del paciente como el examen bucal y el plan de tratamiento.
    """


class AntecedentesSaludRead(_CamposPaciente, _CamposProfesional):
    """Ficha médica tal como la lee el odontólogo.

    ``motivos_alerta`` y ``alerta_medica`` son **campos calculados** a partir
    de los datos declarados: no se guardan en la base para que no puedan quedar
    desactualizados si se corrige la ficha.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    historia_clinica_id: int
    completado: bool = False
    fecha_completado: Optional[datetime] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def motivos_alerta(self) -> List[str]:
        """Etiquetas legibles de los antecedentes que exigen atención."""
        return calcular_motivos_alerta(self)

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
    datos_filiatorios: PacienteDatosFiliatorios = Field(
        default_factory=PacienteDatosFiliatorios,
        description=(
            "Filiatorios que ya están cargados en el paciente (fecha de "
            "nacimiento, domicilio…). Se envían aunque la ficha esté pendiente "
            "para que el formulario se abra precargado."
        ),
    )
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


class FichaSaludPanelRead(BaseModel):
    """Fila del panel interno «Fichas de salud».

    A diferencia de :class:`AnamnesisPublicaRead`, esta vista es para la
    secretaría y el odontólogo: incluye el nombre y el teléfono del paciente y
    el **enlace firmado** que se puede reenviar por WhatsApp. El enlace se arma
    en el servidor porque el token HMAC no se puede firmar desde el navegador.

    ``mensaje`` y ``enlace_whatsapp`` llegan listos para usar: el primero es el
    texto sugerido (con el enlace adentro) y el segundo el Click-to-Chat
    (``wa.me``) con ese mismo texto ya codificado. Así el panel no duplica la
    redacción ni la normalización del teléfono.
    """

    turno_id: int
    paciente_id: int
    paciente_nombre: str
    paciente_dni: Optional[str] = Field(
        None, description="Documento del paciente (para buscarlo en el panel)."
    )
    paciente_telefono: str = ""
    telefono_whatsapp: str = Field(
        "",
        description=(
            "Teléfono normalizado para WhatsApp (sólo dígitos, con código de "
            "país). Vacío si el paciente no tiene un número utilizable."
        ),
    )
    doctor_nombre: Optional[str] = None
    fecha_hora_inicio: datetime
    fecha_hora_fin: datetime
    estado_turno: EstadoTurno
    completado: bool = False
    alerta_medica: bool = False
    motivos_alerta: List[str] = Field(default_factory=list)
    fecha_completado: Optional[datetime] = None
    datos_filiatorios: PacienteDatosFiliatorios = Field(
        default_factory=PacienteDatosFiliatorios,
        description=(
            "Filiatorios vigentes del paciente, para abrir el modal interno "
            "precargado aunque la ficha todavía esté pendiente."
        ),
    )
    antecedentes_salud: Optional[AntecedentesSaludRead] = Field(
        None,
        description=(
            "Declaración completa para precargar el formulario interno de la "
            "secretaría. ``null`` si el paciente todavía no cargó la ficha."
        ),
    )
    enlace: str = Field(
        ..., description="URL pública firmada para abrir o reenviar la ficha."
    )
    mensaje: str = Field(
        "",
        description="Texto sugerido para enviarle al paciente por WhatsApp.",
    )
    enlace_whatsapp: str = Field(
        "",
        description=(
            "Enlace Click-to-Chat (``wa.me``) con el mensaje cargado. Vacío si "
            "el teléfono del paciente no sirve para WhatsApp."
        ),
    )


class FichaSaludEnlaceRead(BaseModel):
    """Ficha de un turno lista para enviar (panel y CLI de secretaría).

    Es la respuesta de ``GET /anamnesis/panel/{turno_id}/enlace``: el enlace
    firmado, el texto sugerido y el Click-to-Chat. Se puede pedir para un turno
    que esté fuera de la ventana del panel, porque la secretaría busca por
    paciente y no por fecha.
    """

    turno_id: int
    paciente_id: int
    paciente_nombre: str
    paciente_dni: Optional[str] = None
    paciente_telefono: str = ""
    telefono_whatsapp: str = Field(
        "",
        description="Destino normalizado (puede ser el teléfono alternativo pedido).",
    )
    doctor_nombre: Optional[str] = None
    fecha_hora_inicio: datetime
    fecha_hora_fin: datetime
    estado_turno: EstadoTurno
    completado: bool = False
    fecha_completado: Optional[datetime] = None
    enlace: str
    mensaje: str = ""
    enlace_whatsapp: str = ""

    @property
    def token(self) -> str:
        """Credencial que viaja al final del enlace público (sin el resto de la URL)."""
        return self.enlace.rsplit("/", 1)[-1]


class FichaSaludEnvioCreate(BaseModel):
    """Cómo se manda la ficha desde el panel o desde la CLI.

    * ``manual`` (por defecto): no envía nada; devuelve el enlace y el texto
      para que la secretaría los copie en WhatsApp Web o en el celular.
    * ``api``: envía el mensaje por la WhatsApp Cloud API. Si el servidor no
      tiene credenciales configuradas, el envío queda **simulado** (igual que
      los recordatorios) y se informa con ``simulado = true``.
    """

    canal: Literal["manual", "api"] = Field(
        default="manual",
        description="``manual`` sólo prepara el enlace; ``api`` envía por WhatsApp.",
    )
    telefono: Optional[str] = Field(
        None,
        max_length=40,
        description=(
            "Destino alternativo (por ejemplo el celular desde el que llamó el "
            "paciente). Si se omite se usa el teléfono cargado en la ficha."
        ),
    )

    @field_validator("telefono", mode="before")
    @classmethod
    def _telefono_opcional(cls, valor):
        """Convierte el campo vacío en ``None`` para no perder el teléfono del paciente."""
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None


class FichaSaludEnvioRead(FichaSaludEnlaceRead):
    """Resultado del envío: qué canal se usó y si el mensaje salió de verdad."""

    canal: Literal["manual", "api"] = "manual"
    enviado: bool = False
    simulado: bool = Field(
        False,
        description=(
            "``True`` si el canal fue ``api`` pero el servidor no tiene "
            "credenciales de WhatsApp: no salió ningún mensaje real."
        ),
    )
    whatsapp_mensaje_id: Optional[str] = Field(
        None, description="``wamid`` devuelto por Meta (o el simulado)."
    )
