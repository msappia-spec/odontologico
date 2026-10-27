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
5. La secretaría también puede **mandar el enlace a mano**: el panel y la CLI
   (``python -m scripts.enviar_ficha``) piden el enlace firmado con el mensaje
   ya redactado y el Click-to-Chat (``wa.me``). Ese envío **no** completa la
   ficha ni marca el recordatorio: la ficha se completa recién cuando el
   paciente envía el formulario, y el recordatorio sigue pendiente hasta que
   salga de verdad.

La respuesta pública nunca expone el ``token`` ni datos de otros turnos.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Union

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.anamnesis_token import (
    DatosTokenAnamnesis,
    TokenAnamnesisInvalidoError,
    construir_enlace_anamnesis,
    validar_token_anamnesis,
)
from app.core.config import settings
from app.core.datetime_utils import rango_del_dia, to_naive_utc, utcnow
from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    ValidacionError,
)
from app.core.whatsapp_utils import (
    construir_enlace_click_to_chat,
    formatear_fecha_recordatorio,
    normalizar_telefono_whatsapp,
    profesional_con_articulo,
)
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
    FichaMedicaUpdate,
    FichaSaludEnlaceRead,
    FichaSaludEnvioRead,
    FichaSaludPanelRead,
)
from app.schemas.paciente import PacienteDatosFiliatorios
from app.services import paciente_service
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

#: Texto con el que el panel (o la CLI) le manda el enlace al paciente.
#:
#: El enlace va **solo en su propia línea**, separado por una línea en blanco y
#: sin puntuación pegada: WhatsApp sólo lo muestra en azul (y lo abre al tocarlo)
#: cuando la URL arranca una línea. Al final de una frase queda como texto gris y
#: el paciente hace clic sin que pase nada.
PLANTILLA_ENVIO_FICHA = (
    "Hola {paciente}, antes de tu consulta del {fecha} a las {hora} con "
    "{profesional}, completá tu ficha de salud desde este enlace:\n"
    "\n"
    "{enlace}"
)

#: Canales aceptados al mandar la ficha (ver ``FichaSaludEnvioCreate``).
CANALES_ENVIO_FICHA = ("manual", "api")


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
    """Arma la respuesta de lectura con la declaración (si existe).

    Los filiatorios se envían siempre (aunque la ficha esté pendiente) para que
    el formulario público se abra con los datos que el paciente ya tiene
    cargados y sólo tenga que confirmarlos.
    """
    declaracion = (
        _leer_ficha(antecedentes, turno.paciente)
        if antecedentes is not None
        else None
    )
    return AnamnesisPublicaRead(
        **_datos_contexto(turno),
        datos_filiatorios=PacienteDatosFiliatorios(
            **paciente_service.datos_filiatorios(turno.paciente)
        ),
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


def _leer_ficha(
    antecedentes: AntecedentesSalud, paciente: Optional[Paciente]
) -> AntecedentesSaludRead:
    """Ficha tal como la leen el panel y el enlace público.

    Los antecedentes salen de su tabla y los **filiatorios del paciente**, que
    son la única fuente de verdad: así una corrección hecha en el formulario de
    pacientes aparece también en la ficha médica.
    """
    ficha = AntecedentesSaludRead.model_validate(antecedentes)
    return ficha.model_copy(update=paciente_service.datos_filiatorios(paciente))


def _sincronizar_paciente(
    paciente: Optional[Paciente], filiatorios: dict[str, Any]
) -> None:
    """Copia al paciente los filiatorios informados en la ficha.

    Regla de oro: **un campo vacío no borra** un dato ya cargado. El enlace
    público puede reenviarse con la mitad del formulario en blanco y no debe
    dejar al paciente sin domicilio ni fecha de nacimiento. Para limpiar un dato
    se usa el ``PATCH /pacientes/{id}`` (que sí distingue «no enviado» de
    ``null``).
    """
    if paciente is None:
        return
    for campo, valor in filiatorios.items():
        if isinstance(valor, str):
            valor = valor.strip()
            if not valor:
                continue
        if valor is None:
            continue
        setattr(paciente, campo, valor)


def _aplicar_declaracion(
    antecedentes: AntecedentesSalud,
    data: Union[DeclaracionSaludCreate, FichaMedicaUpdate],
    paciente: Optional[Paciente] = None,
) -> None:
    """Copia los campos del payload al modelo ORM y sincroniza el paciente.

    Se recorre ``model_dump()`` en lugar de asignar campo por campo: el enlace
    público sólo puede escribir los campos de ``DeclaracionSaludCreate``, y el
    ``PUT`` interno guarda además el examen bucal y el diagnóstico que trae
    ``FichaMedicaUpdate``. Así agregar una pregunta nueva no exige tocar este
    servicio.

    Los **datos filiatorios** se separan antes: no son columnas de la ficha,
    viajan al registro del paciente.
    """
    datos = data.model_dump()
    filiatorios = {
        campo: datos.pop(campo) for campo in paciente_service.CAMPOS_FILIATORIOS
    }
    for campo, valor in datos.items():
        setattr(antecedentes, campo, valor)
    _sincronizar_paciente(paciente, filiatorios)
    antecedentes.completado = True
    antecedentes.fecha_completado = utcnow()


def _guardar_declaracion(
    db: Session,
    turno: Turno,
    data: Union[DeclaracionSaludCreate, FichaMedicaUpdate],
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

        _aplicar_declaracion(antecedentes, data, turno.paciente)
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
    db: Session, turno_id: int, data: FichaMedicaUpdate
) -> AntecedentesSaludRead:
    """Carga o corrige la ficha desde el panel interno de la secretaría.

    Es el mismo cuestionario y la misma validación que el enlace público, pero
    sin token: la secretaría lo completa con las respuestas del paciente que
    está en el mostrador o que llamó por teléfono. Se devuelve la declaración
    para que la fila del panel pueda mostrar de inmediato la alerta médica.
    """
    turno = _resolver_turno_por_id(db, turno_id)
    antecedentes = _guardar_declaracion(db, turno, data)
    return _leer_ficha(antecedentes, turno.paciente)


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
            _leer_ficha(antecedentes, paciente) if antecedentes is not None else None
        )
        completado = bool(declaracion is not None and declaracion.completado)
        if solo_pendientes and completado:
            continue
        # El enlace se firma en el servidor (el navegador no tiene la clave) y el
        # mensaje se arma una sola vez para que el panel pueda copiarlo tal cual.
        enlace = construir_enlace_anamnesis(turno.id, turno.paciente_id)
        mensaje = construir_mensaje_ficha(
            paciente_nombre=paciente.nombre,
            fecha_hora_inicio=turno.fecha_hora_inicio,
            profesional=doctor.nombre,
            enlace=enlace,
        )
        fichas.append(
            FichaSaludPanelRead(
                turno_id=turno.id,
                paciente_id=turno.paciente_id,
                paciente_nombre=paciente.nombre,
                paciente_dni=paciente.dni,
                paciente_telefono=paciente.telefono,
                telefono_whatsapp=normalizar_telefono_whatsapp(paciente.telefono),
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
                datos_filiatorios=PacienteDatosFiliatorios(
                    **paciente_service.datos_filiatorios(paciente)
                ),
                antecedentes_salud=declaracion,
                enlace=enlace,
                mensaje=mensaje,
                enlace_whatsapp=construir_enlace_click_to_chat(
                    paciente.telefono, mensaje
                ),
            )
        )
    return fichas


def construir_mensaje_ficha(
    *,
    paciente_nombre: str,
    fecha_hora_inicio: datetime,
    profesional: Optional[str],
    enlace: str,
) -> str:
    """Redacta el mensaje con el que se le manda el enlace al paciente.

    Reutiliza los formateadores del recordatorio (fecha "en letras" y artículo
    del profesional) para que los dos mensajes suenen igual. El enlace cierra el
    mensaje en un párrafo propio (ver :data:`PLANTILLA_ENVIO_FICHA`)::

        Hola Ana Pérez, antes de tu consulta del viernes 16 de octubre de 2026
        a las 10:30 con la Dra. Gómez, completá tu ficha de salud desde este
        enlace:

        https://mi-consultorio.com/anamnesis/<token>
    """
    nombre = (paciente_nombre or "").strip() or "paciente"
    return PLANTILLA_ENVIO_FICHA.format(
        paciente=nombre,
        fecha=formatear_fecha_recordatorio(fecha_hora_inicio),
        hora=fecha_hora_inicio.strftime("%H:%M"),
        profesional=profesional_con_articulo(profesional) or "tu profesional",
        enlace=enlace,
    )


def _declaracion_del_paciente(
    db: Session, paciente_id: int
) -> Optional[AntecedentesSalud]:
    """Declaración vigente del paciente (``None`` si nunca envió la ficha)."""
    return _antecedentes_de_historia(db, obtener_historia(db, paciente_id))


def obtener_enlace_ficha(
    db: Session, turno_id: int, telefono: Optional[str] = None
) -> FichaSaludEnlaceRead:
    """Enlace firmado y mensaje listo para mandar (panel y CLI de secretaría).

    A diferencia de :func:`listar_fichas_panel` no filtra por fecha: la
    secretaría busca al paciente y necesita el enlace aunque el turno esté
    fuera de la ventana del panel. Sí rechaza los turnos cancelados, igual que
    el resto de las operaciones del panel.

    :param telefono: destino alternativo (por ejemplo el celular desde el que
        llamó el paciente). Si se omite se usa el teléfono de la ficha.
    :raises RecursoNoEncontradoError: el turno no existe.
    :raises ConflictoError: el turno está cancelado.
    """
    turno = _resolver_turno_por_id(db, turno_id)
    paciente = turno.paciente
    declaracion = _declaracion_del_paciente(db, turno.paciente_id)
    completado = bool(declaracion is not None and declaracion.completado)
    nombre = (
        paciente.nombre if paciente is not None else f"Paciente #{turno.paciente_id}"
    )
    enlace = construir_enlace_anamnesis(turno.id, turno.paciente_id)
    doctor = turno.doctor
    mensaje = construir_mensaje_ficha(
        paciente_nombre=nombre,
        fecha_hora_inicio=turno.fecha_hora_inicio,
        profesional=doctor.nombre if doctor is not None else None,
        enlace=enlace,
    )
    telefono_paciente = paciente.telefono if paciente is not None else ""
    destino = telefono_paciente if telefono is None else telefono
    return FichaSaludEnlaceRead(
        turno_id=turno.id,
        paciente_id=turno.paciente_id,
        paciente_nombre=nombre,
        paciente_dni=paciente.dni if paciente is not None else None,
        paciente_telefono=telefono_paciente or "",
        telefono_whatsapp=normalizar_telefono_whatsapp(destino),
        doctor_nombre=doctor.nombre if doctor is not None else None,
        fecha_hora_inicio=turno.fecha_hora_inicio,
        fecha_hora_fin=turno.fecha_hora_fin,
        estado_turno=turno.estado_turno,
        completado=completado,
        fecha_completado=(
            declaracion.fecha_completado if completado and declaracion else None
        ),
        enlace=enlace,
        mensaje=mensaje,
        enlace_whatsapp=construir_enlace_click_to_chat(destino, mensaje),
    )


def enviar_ficha(
    db: Session,
    turno_id: int,
    canal: str = "manual",
    telefono: Optional[str] = None,
) -> FichaSaludEnvioRead:
    """Prepara (o manda por la Cloud API) la ficha de salud de un turno.

    * ``manual``: no envía nada. Devuelve el enlace, el texto sugerido y el
      Click-to-Chat para que la secretaría lo mande desde su propio WhatsApp.
    * ``api``: usa el mismo transporte que los recordatorios. Si el servidor no
      tiene credenciales, el envío queda **simulado** (``simulado=True``), igual
      que en ``POST /whatsapp/recordatorios``.

    En ningún caso se marca el turno como «recordatorio enviado»: ese campo
    (``turno.estado_whatsapp``) describe el recordatorio del turno, y darlo por
    enviado sacaría al paciente de la lista de pendientes sin que el
    recordatorio haya salido. La ficha se completa recién cuando el paciente
    envía el formulario.

    :raises ValidacionError: ``canal`` desconocido.
    :raises RecursoNoEncontradoError: el turno no existe.
    :raises ConflictoError: turno cancelado o sin teléfono válido para ``api``.
    """
    if canal not in CANALES_ENVIO_FICHA:
        raise ValidacionError(
            f"Canal de envío desconocido: {canal!r}. Usá 'manual' o 'api'."
        )
    datos = obtener_enlace_ficha(db, turno_id, telefono=telefono)
    if canal == "manual":
        return FichaSaludEnvioRead(**datos.model_dump(), canal="manual")

    if not datos.telefono_whatsapp:
        raise ConflictoError(
            "El paciente no tiene un teléfono válido para WhatsApp. Corregí el "
            "número en su ficha o copiá el enlace para mandarlo a mano."
        )

    # Import local a propósito: ``whatsapp_service`` importa este módulo a nivel
    # de módulo (para saber si la ficha sigue pendiente) e importarlo arriba
    # crearía un ciclo entre los dos servicios.
    from app.services import whatsapp_service

    mensaje_id = whatsapp_service.enviar_mensaje_texto(
        datos.telefono_whatsapp,
        datos.mensaje,
        preview_url=True,
    )
    return FichaSaludEnvioRead(
        **datos.model_dump(),
        canal="api",
        enviado=True,
        simulado=not settings.whatsapp_envio_real,
        whatsapp_mensaje_id=mensaje_id,
    )


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

