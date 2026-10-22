"""Servicio de la ficha pública de salud (Módulo 4: anamnesis).

Flujo completo:

1. El recordatorio de WhatsApp incluye ``/anamnesis/<token>``. El token lo
   genera :mod:`app.core.anamnesis_token` y **no** revela credenciales: sólo
   identifica el turno y el paciente mediante una firma HMAC.
2. ``GET`` del formulario resuelve el turno, verifica que el paciente del token
   sea el del turno y devuelve el contexto + la declaración existente (si la
   hay). Es **sólo lectura**: no crea la historia clínica.
3. ``POST`` guarda la declaración, crea la historia clínica si el paciente
   todavía no tenía una (igual que la primera evolución del Módulo 2) y marca la
   ficha como ``completado``. Desde ese momento el odontólogo ve la alerta en el
   panel y en la vista móvil.
4. El panel interno permite que la secretaría cargue o corrija la misma ficha
   con ``PUT /anamnesis/panel/{turno_id}`` cuando el paciente está en el
   mostrador o responde por teléfono. Es el mismo cuestionario y la misma
   validación, sin necesidad de abrir el enlace público.

La respuesta pública nunca expone el ``token`` ni datos de otros turnos.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.anamnesis_token import (
    DatosTokenAnamnesis,
    TokenAnamnesisInvalidoError,
    construir_enlace_anamnesis,
    validar_token_anamnesis,
)
from app.core.datetime_utils import rango_del_dia, to_naive_utc, utcnow
from app.core.exceptions import ConflictoError, RecursoNoEncontradoError
from app.models.antecedentes_salud import AntecedentesSalud
from app.models.doctor import Doctor
from app.models.enums import EstadoTurno
from app.models.historia_clinica import HistoriaClinica
from app.models.paciente import Paciente
from app.models.turno import Turno
from app.schemas.antecedentes_salud import (
    AnamnesisGuardadaRead,
    AnamnesisPublicaRead,
    AntecedentesSaludRead,
    DeclaracionSaludCreate,
    FichaSaludPanelRead,
)
from app.services.historia_clinica_service import (
    obtener_historia,
    obtener_o_crear_historia,
)

logger = logging.getLogger("app.services.anamnesis")

#: Mensaje de confirmación que ve el paciente al enviar el formulario.
MENSAJE_GUARDADO = (
    "¡Gracias! Recibimos tu ficha de salud. El profesional la verá antes de "
    "tu consulta."
)


def _cargar_turno(db: Session, turno_id: int) -> Optional[Turno]:
    """Turno con paciente y profesional cargados (``None`` si no existe)."""
    return db.execute(
        select(Turno)
        .options(selectinload(Turno.paciente), selectinload(Turno.doctor))
        .where(Turno.id == turno_id)
    ).scalars().first()


def _resolver_turno(db: Session, datos: DatosTokenAnamnesis) -> Turno:
    """Carga el turno del token y comprueba que el paciente siga siendo el mismo.

    El token firmado ya garantiza que nadie pudo cambiar los ids, pero se
    compara igual contra la base: si un turno se reasignara (no ocurre en el
    flujo actual) el enlace viejo deja de servir en lugar de mostrar la ficha de
    otra persona.
    """
    turno = _cargar_turno(db, datos.turno_id)
    if turno is None:
        raise RecursoNoEncontradoError(
            "No encontramos el turno asociado a este enlace. Pedí uno nuevo en "
            "el consultorio."
        )
    if turno.paciente_id != datos.paciente_id:
        # No se detalla el motivo para no filtrar información de otros turnos.
        raise TokenAnamnesisInvalidoError()
    return turno


def _resolver_turno_por_id(db: Session, turno_id: int) -> Turno:
    """Turno que abre la secretaría desde el panel interno.

    A diferencia del enlace público no hay token: el id llega del panel. Se
    rechazan los turnos cancelados porque la ficha sólo tiene sentido para una
    atención vigente (el panel tampoco los lista).
    """
    turno = _cargar_turno(db, turno_id)
    if turno is None:
        raise RecursoNoEncontradoError(
            "No encontramos el turno indicado. Actualizá el panel e intentá de nuevo."
        )
    if turno.estado_turno == EstadoTurno.CANCELADO:
        raise ConflictoError(
            "El turno está cancelado: no se puede cargar la ficha de salud."
        )
    return turno


def _antecedentes_de_historia(
    db: Session, historia: Optional[HistoriaClinica]
) -> Optional[AntecedentesSalud]:
    """Declaración de salud de una historia (``None`` si no existe)."""
    if historia is None:
        return None
    return db.execute(
        select(AntecedentesSalud).where(
            AntecedentesSalud.historia_clinica_id == historia.id
        )
    ).scalars().first()


def _datos_contexto(turno: Turno) -> dict[str, Any]:
    """Campos comunes a la vista pública (sin datos sensibles de otros turnos)."""
    paciente = turno.paciente
    doctor = turno.doctor
    return {
        "turno_id": turno.id,
        "paciente_id": turno.paciente_id,
        "paciente_nombre": (
            paciente.nombre if paciente is not None else f"Paciente #{turno.paciente_id}"
        ),
        "doctor_nombre": doctor.nombre if doctor is not None else None,
        "doctor_especialidad": doctor.especialidad if doctor is not None else None,
        "fecha_hora_inicio": turno.fecha_hora_inicio,
        "estado_turno": turno.estado_turno,
    }


def _respuesta_publica(
    turno: Turno, antecedentes: Optional[AntecedentesSalud]
) -> AnamnesisPublicaRead:
    """Arma la respuesta de lectura con la declaración (si existe)."""
    declaracion = (
        AntecedentesSaludRead.model_validate(antecedentes)
        if antecedentes is not None
        else None
    )
    return AnamnesisPublicaRead(
        **_datos_contexto(turno),
        antecedentes_salud=declaracion,
        completado=bool(antecedentes is not None and antecedentes.completado),
    )


def obtener_formulario_publico(
    db: Session, token: Optional[str]
) -> AnamnesisPublicaRead:
    """Datos que muestra el enlace público, sin escribir en la base.

    :raises TokenAnamnesisInvalidoError: token ausente o alterado (HTTP 400).
    :raises RecursoNoEncontradoError: el turno ya no existe (HTTP 404).
    """
    datos = validar_token_anamnesis(token)
    turno = _resolver_turno(db, datos)
    historia = obtener_historia(db, turno.paciente_id)
    return _respuesta_publica(turno, _antecedentes_de_historia(db, historia))


def _aplicar_declaracion(
    antecedentes: AntecedentesSalud, data: DeclaracionSaludCreate
) -> None:
    """Copia los campos permitidos del payload público al modelo ORM."""
    antecedentes.alergias_medicamentos = data.alergias_medicamentos
    antecedentes.presion_alta = data.presion_alta
    antecedentes.problemas_cardiacos = data.problemas_cardiacos
    antecedentes.medicacion_actual = data.medicacion_actual
    antecedentes.embarazo = data.embarazo
    antecedentes.completado = True
    antecedentes.fecha_completado = utcnow()


def _guardar_declaracion(
    db: Session, turno: Turno, data: DeclaracionSaludCreate
) -> AntecedentesSalud:
    """Crea o corrige la declaración del paciente del turno.

    La fila es única por historia clínica. Si dos peticiones simultáneas (por
    ejemplo, el paciente desde el enlace y la secretaría desde el panel) intentan
    crearla, el índice único frena a la segunda y se reintenta sobre la fila ya
    creada.
    """
    antecedentes: Optional[AntecedentesSalud] = None
    for intento in range(2):
        historia = obtener_o_crear_historia(db, turno.paciente_id)
        antecedentes = _antecedentes_de_historia(db, historia)
        if antecedentes is None:
            antecedentes = AntecedentesSalud(historia_clinica_id=historia.id)
            db.add(antecedentes)
            historia.antecedentes_salud = antecedentes

        _aplicar_declaracion(antecedentes, data)
        try:
            db.commit()
        except IntegrityError:
            # Dos envíos a la vez: la otra transacción ya creó la declaración.
            db.rollback()
            if intento == 1:  # pragma: no cover - carrera persistente/error real
                raise
            logger.warning(
                "Declaración de salud concurrente para el paciente %s; "
                "se reintenta sobre la fila existente.",
                turno.paciente_id,
            )
            continue
        else:
            db.refresh(antecedentes)
            break

    logger.info(
        "Ficha de salud completada (turno=%s paciente=%s alerta=%s).",
        turno.id,
        turno.paciente_id,
        antecedentes.alerta_medica if antecedentes is not None else False,
    )
    # El bucle siempre asigna ``antecedentes``; el ``assert`` sólo ayuda a los
    # analizadores de tipos (el ``raise`` del segundo intento ya corta antes).
    assert antecedentes is not None  # pragma: no cover
    return antecedentes


def guardar_declaracion_publica(
    db: Session, token: Optional[str], data: DeclaracionSaludCreate
) -> AnamnesisGuardadaRead:
    """Guarda la ficha enviada por el paciente y marca la declaración completa.

    Se permite **corregir** la declaración con el mismo enlace (el paciente
    puede haber marcado algo por error): la fila es única por historia clínica y
    ``fecha_completado`` refleja el último envío.
    """
    datos = validar_token_anamnesis(token)
    turno = _resolver_turno(db, datos)
    antecedentes = _guardar_declaracion(db, turno, data)
    respuesta = _respuesta_publica(turno, antecedentes)
    return AnamnesisGuardadaRead(**respuesta.model_dump(), mensaje=MENSAJE_GUARDADO)


def guardar_declaracion_turno(
    db: Session, turno_id: int, data: DeclaracionSaludCreate
) -> AntecedentesSaludRead:
    """Carga o corrige la ficha desde el panel interno de la secretaría.

    Es el mismo cuestionario y la misma validación que el enlace público, pero
    sin token: la secretaría lo completa con las respuestas del paciente que
    está en el mostrador o que llamó por teléfono. Se devuelve la declaración
    para que la fila del panel pueda mostrar de inmediato la alerta médica.
    """
    turno = _resolver_turno_por_id(db, turno_id)
    antecedentes = _guardar_declaracion(db, turno, data)
    return AntecedentesSaludRead.model_validate(antecedentes)


def listar_fichas_panel(
    db: Session,
    dias: int = 30,
    solo_pendientes: bool = False,
    ahora: Optional[datetime] = None,
) -> list[FichaSaludPanelRead]:
    """Turnos del panel «Fichas de salud», ordenados por fecha de atención.

    La ventana incluye **todo el día de hoy** (aunque la hora ya haya pasado) y
    los ``dias`` siguientes, para que la secretaría vea también las fichas de
    los turnos de la mañana. Los turnos cancelados no se listan.

    :param solo_pendientes: si es ``True``, oculta las fichas ya completadas.
    :param ahora: inyectable para pruebas (por defecto ``utcnow()``).
    """
    referencia = utcnow() if ahora is None else to_naive_utc(ahora)
    dias = max(0, int(dias))
    inicio, _ = rango_del_dia(referencia.date())
    _, fin_ventana = rango_del_dia((referencia + timedelta(days=dias)).date())

    filas = db.execute(
        select(Turno, Paciente, Doctor, AntecedentesSalud)
        .join(Paciente, Turno.paciente_id == Paciente.id)
        .join(Doctor, Turno.doctor_id == Doctor.id)
        .outerjoin(HistoriaClinica, HistoriaClinica.paciente_id == Paciente.id)
        .outerjoin(
            AntecedentesSalud,
            AntecedentesSalud.historia_clinica_id == HistoriaClinica.id,
        )
        .where(
            Turno.fecha_hora_inicio >= inicio,
            Turno.fecha_hora_inicio <= fin_ventana,
            Turno.estado_turno != EstadoTurno.CANCELADO,
        )
        .order_by(Turno.fecha_hora_inicio)
        .limit(500)
        # La sesión de pruebas (y cualquier sesión larga) puede tener cargada
        # una historia clínica previa al POST del paciente: se refrescan las
        # entidades para que la fila muestre el estado real de la base.
        .execution_options(populate_existing=True)
    ).all()

    fichas: list[FichaSaludPanelRead] = []
    for turno, paciente, doctor, antecedentes in filas:
        declaracion = (
            AntecedentesSaludRead.model_validate(antecedentes)
            if antecedentes is not None
            else None
        )
        completado = bool(declaracion is not None and declaracion.completado)
        if solo_pendientes and completado:
            continue
        fichas.append(
            FichaSaludPanelRead(
                turno_id=turno.id,
                paciente_id=turno.paciente_id,
                paciente_nombre=paciente.nombre,
                paciente_telefono=paciente.telefono,
                doctor_nombre=doctor.nombre,
                fecha_hora_inicio=turno.fecha_hora_inicio,
                fecha_hora_fin=turno.fecha_hora_fin,
                estado_turno=turno.estado_turno,
                completado=completado,
                alerta_medica=bool(
                    completado and declaracion is not None and declaracion.alerta_medica
                ),
                motivos_alerta=(
                    list(declaracion.motivos_alerta)
                    if completado and declaracion is not None
                    else []
                ),
                fecha_completado=(
                    declaracion.fecha_completado
                    if completado and declaracion is not None
                    else None
                ),
                antecedentes_salud=declaracion,
                enlace=construir_enlace_anamnesis(turno.id, turno.paciente_id),
            )
        )
    return fichas


def necesita_completar_anamnesis(turno: Turno) -> bool:
    """``True`` si el recordatorio debe incluir el enlace de la ficha de salud.

    Se usa desde :mod:`app.services.whatsapp_service`. Una ficha ya completada
    no se vuelve a pedir; si el paciente todavía no tiene historia clínica o
    nunca envió la declaración, el enlace se agrega al recordatorio.
    """
    if turno.paciente is None:
        return False
    historia = turno.paciente.historia_clinica
    if historia is None:
        return True
    antecedentes = historia.antecedentes_salud
    return not (antecedentes is not None and antecedentes.completado)

