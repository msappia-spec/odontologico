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

La respuesta pública nunca expone el ``token`` ni datos de otros turnos.
"""

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.anamnesis_token import (
    DatosTokenAnamnesis,
    TokenAnamnesisInvalidoError,
    validar_token_anamnesis,
)
from app.core.datetime_utils import utcnow
from app.core.exceptions import RecursoNoEncontradoError
from app.models.antecedentes_salud import AntecedentesSalud
from app.models.historia_clinica import HistoriaClinica
from app.models.turno import Turno
from app.schemas.antecedentes_salud import (
    AnamnesisGuardadaRead,
    AnamnesisPublicaRead,
    AntecedentesSaludRead,
    DeclaracionSaludCreate,
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


def _resolver_turno(db: Session, datos: DatosTokenAnamnesis) -> Turno:
    """Carga el turno del token y comprueba que el paciente siga siendo el mismo.

    El token firmado ya garantiza que nadie pudo cambiar los ids, pero se
    compara igual contra la base: si un turno se reasignara (no ocurre en el
    flujo actual) el enlace viejo deja de servir en lugar de mostrar la ficha de
    otra persona.
    """
    turno = db.execute(
        select(Turno)
        .options(selectinload(Turno.paciente), selectinload(Turno.doctor))
        .where(Turno.id == datos.turno_id)
    ).scalars().first()
    if turno is None:
        raise RecursoNoEncontradoError(
            "No encontramos el turno asociado a este enlace. Pedí uno nuevo en "
            "el consultorio."
        )
    if turno.paciente_id != datos.paciente_id:
        # No se detalla el motivo para no filtrar información de otros turnos.
        raise TokenAnamnesisInvalidoError()
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


def guardar_declaracion_publica(
    db: Session, token: Optional[str], data: DeclaracionSaludCreate
) -> AnamnesisGuardadaRead:
    """Guarda la ficha enviada por el paciente y marca la declaración completa.

    Se permite **corregir** la declaración con el mismo enlace (el paciente
    puede haber marcado algo por error): la fila es única por historia clínica y
    ``fecha_completado`` refleja el último envío.

    La creación de la historia clínica es idempotente y tolera dos envíos
    simultáneos del mismo enlace: el índice único sobre ``historia_clinica_id``
    frena al segundo y se reintenta sobre la fila ya creada.
    """
    datos = validar_token_anamnesis(token)
    turno = _resolver_turno(db, datos)

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
    respuesta = _respuesta_publica(turno, antecedentes)
    return AnamnesisGuardadaRead(**respuesta.model_dump(), mensaje=MENSAJE_GUARDADO)


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

