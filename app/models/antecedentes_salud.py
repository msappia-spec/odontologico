"""Modelo ORM ``AntecedentesSalud``: ficha médica del paciente (Módulo 4).

El paciente completa el cuestionario desde un enlace público
(``/anamnesis/{token}``) **antes** de la consulta y el odontólogo termina de
cargar el examen bucal y el diagnóstico desde el panel. Los datos cuelgan de su
historia clínica, de modo que el odontólogo los ve en la misma ficha que las
evoluciones (Panel Principal y vista móvil).

El cuestionario reproduce la **Historia Clínica General** de papel que usa el
consultorio (``Historia-Clinica-GeneralNUEVA.pdf``), agrupado en cinco bloques:

* ``familiares``: padre, madre y hermanos.
* ``personales``: enfermedades, medicación, alergias, hábitos y antecedentes
  sistémicos (diabetes, cardiopatía, Chagas, hepatitis, etc.).
* ``odontológicos``: consulta previa, dolor, traumatismos y dificultades.
* ``examen bucal``: hallazgos de tejidos blandos, higiene y periodonto.
* ``diagnóstico``: diagnóstico presuntivo, plan de tratamiento y observaciones.

El **encabezado filiatorio** del PDF (fecha de nacimiento, estado civil,
nacionalidad, domicilio, profesión, titular, lugar de trabajo y jerarquía) no se
guarda acá: vive en ``Paciente`` y el servicio de anamnesis lo sincroniza al
guardar la ficha (ver :mod:`app.services.anamnesis_service`).

El **odontograma** no vive acá: es un gráfico aparte que el profesional completa
después de revisar al paciente (ver :mod:`app.models.odontograma`).

Decisiones de diseño:

* **Una ficha por historia clínica** (``uq_antecedentes_historia_clinica``): el
  paciente puede corregir su declaración con el mismo enlace, pero nunca se
  duplica el cuestionario.
* Los textos son libres y opcionales. Los booleanos permiten que el paciente
  marque los antecedentes con un toque desde el celular; los valores clínicos
  «Sin alergias» / «Ninguna» se representan con texto vacío (``NULL``).
* ``completado`` distingue una ficha creada por una declaración enviada de una
  fila todavía en borrador. ``fecha_completado`` queda en **UTC naive**, igual
  que el resto de las fechas del proyecto.
* ``ondelete="CASCADE"``: si se elimina la historia clínica, la declaración la
  acompaña (no queda información clínica huérfana).
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.db.types import UTCDateTime

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado/evitar import circular
    from app.models.historia_clinica import HistoriaClinica

#: Longitud máxima de los textos libres de la declaración.
MAX_TEXTO_ANTECEDENTES = 1000


def _columna_booleana():
    """Columna booleana obligatoria con ``False`` por defecto en la base."""
    return mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


def _columna_texto():
    """Texto libre opcional (los vacíos se guardan como ``NULL``)."""
    return mapped_column(Text, nullable=True)


def _columna_opcion(largo: int = 20):
    """Valor corto de una opción (``SUAVE``, ``BUENO``, ``A``…)."""
    return mapped_column(String(largo), nullable=True)


def _columna_entero():
    """Número entero opcional (meses de embarazo, momentos de azúcar)."""
    return mapped_column(Integer, nullable=True)


def _texto_informado(registro: Any, campo: str) -> bool:
    """``True`` si el campo de texto tiene contenido real."""
    return bool((getattr(registro, campo, None) or "").strip())


def calcular_motivos_alerta(registro: Any) -> List[str]:
    """Antecedentes que el odontólogo debe ver antes de atender.

    Se consideran de riesgo los booleanos marcados y los textos informados
    (alergias, medicación, enfermedades declaradas). El orden es **estable** y
    los cinco motivos históricos van primero para que la alerta se lea siempre
    igual y las fichas viejas no cambien de texto.
    """
    motivos: List[str] = []

    def marcado(campo: str) -> bool:
        return bool(getattr(registro, campo, False))

    def informado(campo: str) -> bool:
        return _texto_informado(registro, campo)

    # Motivos históricos (Módulo 4 original).
    if informado("alergias_medicamentos"):
        motivos.append("Alergias a medicamentos")
    if marcado("presion_alta"):
        motivos.append("Presión arterial alta")
    if marcado("problemas_cardiacos"):
        motivos.append("Problemas cardíacos")
    if informado("medicacion_actual"):
        motivos.append("Medicación actual")
    if marcado("embarazo"):
        motivos.append("Embarazo")

    # Antecedentes que agrega la Historia Clínica General del consultorio.
    if marcado("alergia_anestesia"):
        motivos.append("Alergia a la anestesia")
    if marcado("alergia_penicilina"):
        motivos.append("Alergia a la penicilina")
    if marcado("anticoagulantes"):
        motivos.append("Aspirina o anticoagulantes")
    if marcado("diabetes"):
        motivos.append("Diabetes")
    if marcado("fiebre_reumatica"):
        motivos.append("Fiebre reumática")
    if marcado("chagas"):
        motivos.append("Chagas")
    if marcado("epilepsia") or marcado("convulsiones"):
        motivos.append("Epilepsia o convulsiones")
    if marcado("hepatitis"):
        motivos.append("Hepatitis")
    if marcado("problemas_hepaticos"):
        motivos.append("Problemas hepáticos")
    if marcado("problemas_renales"):
        motivos.append("Problemas renales")
    if marcado("problemas_respiratorios"):
        motivos.append("Problemas respiratorios")
    if marcado("ulcera_gastrica"):
        motivos.append("Úlcera gástrica")
    if marcado("sifilis_gonorrea"):
        motivos.append("Sífilis o gonorrea")
    if marcado("enfermedad_infectocontagiosa"):
        motivos.append("Enfermedad infecto-contagiosa")
    if marcado("transfusiones"):
        motivos.append("Transfusiones")
    if marcado("operaciones"):
        motivos.append("Cirugías previas")
    if marcado("colageno"):
        motivos.append("Problema de colágeno")
    if marcado("sangra_mucho"):
        motivos.append("Sangrado excesivo")
    if marcado("otra_enfermedad"):
        motivos.append("Otra enfermedad declarada")
    return motivos


class AntecedentesSalud(Base):
    """Ficha médica declarada por el paciente y completada por el profesional."""

    __tablename__ = "antecedentes_salud"
    __table_args__ = (
        Index(
            "uq_antecedentes_historia_clinica",
            "historia_clinica_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    historia_clinica_id: Mapped[int] = mapped_column(
        ForeignKey("historias_clinicas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ------------------------------------------------------------------
    # Antecedentes familiares
    # ------------------------------------------------------------------
    padre_vive: Mapped[bool] = _columna_booleana()
    padre_patologias: Mapped[Optional[str]] = _columna_texto()
    madre_vive: Mapped[bool] = _columna_booleana()
    madre_patologias: Mapped[Optional[str]] = _columna_texto()
    hermanos: Mapped[bool] = _columna_booleana()
    hermanos_sanos: Mapped[bool] = _columna_booleana()
    hermanos_detalle: Mapped[Optional[str]] = _columna_texto()

    # ------------------------------------------------------------------
    # Antecedentes personales
    # ------------------------------------------------------------------
    enfermedad_actual: Mapped[bool] = _columna_booleana()
    enfermedad_actual_detalle: Mapped[Optional[str]] = _columna_texto()
    tratamiento_medico: Mapped[bool] = _columna_booleana()
    tratamiento_medico_detalle: Mapped[Optional[str]] = _columna_texto()
    medicacion_actual: Mapped[Optional[str]] = _columna_texto()
    medicamentos_ultimos_5_anios: Mapped[Optional[str]] = _columna_texto()
    deporte: Mapped[bool] = _columna_booleana()
    deporte_malestar: Mapped[bool] = _columna_booleana()
    alergias_medicamentos: Mapped[Optional[str]] = _columna_texto()
    alergia_anestesia: Mapped[bool] = _columna_booleana()
    alergia_penicilina: Mapped[bool] = _columna_booleana()
    alergia_otros: Mapped[Optional[str]] = _columna_texto()
    cicatriza_bien: Mapped[bool] = _columna_booleana()
    sangra_mucho: Mapped[bool] = _columna_booleana()
    colageno: Mapped[bool] = _columna_booleana()
    fiebre_reumatica: Mapped[bool] = _columna_booleana()
    fiebre_reumatica_proteccion: Mapped[Optional[str]] = _columna_texto()
    diabetes: Mapped[bool] = _columna_booleana()
    diabetes_controlada: Mapped[bool] = _columna_booleana()
    diabetes_tratamiento: Mapped[Optional[str]] = _columna_texto()
    problemas_cardiacos: Mapped[bool] = _columna_booleana()
    problemas_cardiacos_detalle: Mapped[Optional[str]] = _columna_texto()
    anticoagulantes: Mapped[bool] = _columna_booleana()
    anticoagulantes_frecuencia: Mapped[Optional[str]] = _columna_texto()
    presion_alta: Mapped[bool] = _columna_booleana()
    chagas: Mapped[bool] = _columna_booleana()
    chagas_tratamiento: Mapped[Optional[str]] = _columna_texto()
    problemas_renales: Mapped[bool] = _columna_booleana()
    ulcera_gastrica: Mapped[bool] = _columna_booleana()
    hepatitis: Mapped[bool] = _columna_booleana()
    hepatitis_tipo: Mapped[Optional[str]] = _columna_opcion()
    problemas_hepaticos: Mapped[bool] = _columna_booleana()
    problemas_hepaticos_detalle: Mapped[Optional[str]] = _columna_texto()
    convulsiones: Mapped[bool] = _columna_booleana()
    epilepsia: Mapped[bool] = _columna_booleana()
    epilepsia_medicacion: Mapped[Optional[str]] = _columna_texto()
    sifilis_gonorrea: Mapped[bool] = _columna_booleana()
    enfermedad_infectocontagiosa: Mapped[bool] = _columna_booleana()
    enfermedad_infectocontagiosa_detalle: Mapped[Optional[str]] = _columna_texto()
    transfusiones: Mapped[bool] = _columna_booleana()
    operaciones: Mapped[bool] = _columna_booleana()
    operaciones_detalle: Mapped[Optional[str]] = _columna_texto()
    operaciones_cuando: Mapped[Optional[str]] = _columna_texto()
    problemas_respiratorios: Mapped[bool] = _columna_booleana()
    problemas_respiratorios_detalle: Mapped[Optional[str]] = _columna_texto()
    fuma: Mapped[bool] = _columna_booleana()
    embarazo: Mapped[bool] = _columna_booleana()
    embarazo_meses: Mapped[Optional[int]] = _columna_entero()
    otra_enfermedad: Mapped[bool] = _columna_booleana()
    otra_enfermedad_detalle: Mapped[Optional[str]] = _columna_texto()
    tratamientos_alternativos: Mapped[Optional[str]] = _columna_texto()
    medico_clinico: Mapped[Optional[str]] = _columna_texto()
    centro_derivacion: Mapped[Optional[str]] = _columna_texto()

    # ------------------------------------------------------------------
    # Historia clínica odontológica (la declara el paciente)
    # ------------------------------------------------------------------
    consulta_previa: Mapped[bool] = _columna_booleana()
    medicacion_previa: Mapped[bool] = _columna_booleana()
    medicacion_previa_detalle: Mapped[Optional[str]] = _columna_texto()
    medicacion_previa_desde: Mapped[Optional[str]] = _columna_texto()
    medicacion_previa_resultados: Mapped[bool] = _columna_booleana()
    dolor: Mapped[bool] = _columna_booleana()
    dolor_intensidad: Mapped[Optional[str]] = _columna_opcion()
    dolor_duracion: Mapped[Optional[str]] = _columna_opcion()
    dolor_desencadenante: Mapped[Optional[str]] = _columna_opcion()
    dolor_estimulo: Mapped[Optional[str]] = _columna_opcion()
    dolor_localizacion: Mapped[Optional[str]] = _columna_opcion()
    dolor_zona: Mapped[Optional[str]] = _columna_texto()
    dolor_calmante: Mapped[Optional[str]] = _columna_texto()
    traumatismo_dental: Mapped[bool] = _columna_booleana()
    traumatismo_cuando: Mapped[Optional[str]] = _columna_texto()
    traumatismo_como: Mapped[Optional[str]] = _columna_texto()
    fractura_dental: Mapped[bool] = _columna_booleana()
    fractura_cual: Mapped[Optional[str]] = _columna_texto()
    fractura_tratamiento: Mapped[Optional[str]] = _columna_texto()
    dificultad_hablar: Mapped[bool] = _columna_booleana()
    dificultad_masticar: Mapped[bool] = _columna_booleana()
    dificultad_abrir_boca: Mapped[bool] = _columna_booleana()
    dificultad_tragar: Mapped[bool] = _columna_booleana()

    # ------------------------------------------------------------------
    # Examen bucal (lo completa el profesional)
    # ------------------------------------------------------------------
    labios_anormal: Mapped[bool] = _columna_booleana()
    lengua_anormal: Mapped[bool] = _columna_booleana()
    paladar_anormal: Mapped[bool] = _columna_booleana()
    piso_boca_anormal: Mapped[bool] = _columna_booleana()
    carrillos_anormal: Mapped[bool] = _columna_booleana()
    rebordes_anormal: Mapped[bool] = _columna_booleana()
    trigono_anormal: Mapped[bool] = _columna_booleana()
    retromolar_anormal: Mapped[bool] = _columna_booleana()
    examen_bucal_detalle: Mapped[Optional[str]] = _columna_texto()
    lesiones_manchas: Mapped[bool] = _columna_booleana()
    lesiones_abultamiento: Mapped[bool] = _columna_booleana()
    lesiones_ulceraciones: Mapped[bool] = _columna_booleana()
    lesiones_ampollas: Mapped[bool] = _columna_booleana()
    lesiones_otros: Mapped[Optional[str]] = _columna_texto()
    sangrado_encias: Mapped[bool] = _columna_booleana()
    sangrado_encias_cuando: Mapped[Optional[str]] = _columna_texto()
    pus_boca: Mapped[bool] = _columna_booleana()
    pus_boca_donde: Mapped[Optional[str]] = _columna_texto()
    movilidad_dental: Mapped[bool] = _columna_booleana()
    dientes_altos: Mapped[bool] = _columna_booleana()
    cara_hinchada: Mapped[bool] = _columna_booleana()
    cara_hinchada_hielo: Mapped[bool] = _columna_booleana()
    cara_hinchada_calor: Mapped[bool] = _columna_booleana()
    cara_hinchada_otros: Mapped[Optional[str]] = _columna_texto()
    momentos_azucar: Mapped[Optional[int]] = _columna_entero()
    indice_placa: Mapped[Optional[str]] = _columna_texto()
    higiene_bucal: Mapped[Optional[str]] = _columna_opcion()
    sarro: Mapped[bool] = _columna_booleana()
    enfermedad_periodontal: Mapped[bool] = _columna_booleana()

    # ------------------------------------------------------------------
    # Diagnóstico y plan (los completa el profesional)
    # ------------------------------------------------------------------
    diagnostico_presuntivo: Mapped[Optional[str]] = _columna_texto()
    plan_tratamiento: Mapped[Optional[str]] = _columna_texto()
    fecha_plan: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    observaciones: Mapped[Optional[str]] = _columna_texto()

    # ------------------------------------------------------------------
    # Control de la declaración
    # ------------------------------------------------------------------
    completado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    fecha_completado: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )

    # Relaciones
    historia_clinica: Mapped["HistoriaClinica"] = relationship(
        back_populates="antecedentes_salud"
    )

    @property
    def motivos_alerta(self) -> List[str]:
        """Antecedentes de riesgo, con etiquetas listas para mostrar."""
        return calcular_motivos_alerta(self)

    @property
    def alerta_medica(self) -> bool:
        """``True`` cuando la ficha tiene algún antecedente de riesgo."""
        return bool(self.motivos_alerta)

    def __repr__(self) -> str:  # pragma: no cover - representación de depuración
        return (
            f"<AntecedentesSalud id={self.id} "
            f"historia_clinica_id={self.historia_clinica_id} "
            f"completado={self.completado}>"
        )
