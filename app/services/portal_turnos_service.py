"""Servicio del portal público de turnos (``/reservar``).

Concentra las reglas que **no** pueden vivir en el endpoint porque tocan la
base y la agenda del Módulo 1:

* :func:`listar_inicial` y :func:`horarios_libres` son de sólo lectura y no
  exponen datos de pacientes.
* :func:`buscar_paciente` devuelve una vista mínima (nombre de pila, cobertura y
  teléfono enmascarado) para agilizar la reserva sin filtrar la ficha completa.
* :func:`reservar` crea o actualiza al paciente y el turno dentro de **una sola
  transacción**: bloquea la fila del odontólogo, vuelve a calcular los horarios
  libres justo antes de guardar y deja que el índice único parcial
  ``uq_turno_doctor_fecha_inicio`` sea la última barrera contra dos reservas
  simultáneas del mismo horario.
* :func:`cancelar` exige DNI + código corto (HMAC) y respeta la antelación
  mínima configurada; el turno queda ``CANCELADO`` y su bloque vuelve a estar
  libre para la agenda.
* :func:`construir_mensaje_confirmacion` arma el WhatsApp de confirmación con el
  código de autogestión. El envío real sólo se intenta si WAHA está configurado
  (``settings.whatsapp_envio_real``); un fallo de WhatsApp nunca revierte una
  reserva ya guardada.

Convención de fechas: la agenda guarda la **hora de pared del consultorio** como
``datetime`` naive (el panel envía ``2026-11-30T09:00`` sin zona). El portal
compara contra :func:`ahora_consultorio`, que aplica
``PORTAL_DESFASE_HORARIO_HORAS`` (por defecto ``-3``); usar UTC escondería los
turnos de la mañana en Argentina.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    TurnoSolapadoError,
    ValidacionError,
)
from app.core.whatsapp_utils import (
    formatear_fecha_recordatorio,
    normalizar_telefono_waha,
    profesional_con_articulo,
)
from app.models.doctor import Doctor
from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.models.turno import Turno
from app.schemas.portal_turnos import (
    PortalCancelacionCreate,
    PortalCancelacionRead,
    PortalDoctorRead,
    PortalInicialRead,
    PortalObraSocialRead,
    PortalPacienteRead,
    PortalReservaCreate,
    PortalReservaRead,
)
from app.services import turno_service, whatsapp_service

logger = logging.getLogger("app.services.portal_turnos")

#: Estados que cuentan como turno futuro activo para el tope antiabuso.
ESTADOS_ACTIVOS = (EstadoTurno.PENDIENTE, EstadoTurno.CONFIRMADO)


def ahora_consultorio() -> datetime:
    """Hora de pared del consultorio, naive (sin ``tzinfo``).

    La agenda del ERP guarda los horarios como los escribe el panel
    (``2026-11-30T09:00``, sin zona). Para saber si un bloque ya pasó hay que
    compararlo contra la misma convención: usar ``utcnow`` escondería los turnos
    de la mañana en Argentina (UTC-3). El desfase se configura con
    ``PORTAL_DESFASE_HORARIO_HORAS``.
    """
    zona = timezone(timedelta(hours=settings.portal_desfase_horario_horas))
    return datetime.now(zona).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Catálogos públicos
# --------------------------------------------------------------------------- #
def listar_inicial(db: Session) -> PortalInicialRead:
    """Profesionales activos y coberturas activas, sin ningún dato de pacientes."""
    doctores = db.execute(
        select(Doctor).where(Doctor.activo.is_(True)).order_by(Doctor.nombre)
    ).scalars().all()
    obras = db.execute(
        select(ObraSocial)
        .where(ObraSocial.activa.is_(True))
        .order_by(ObraSocial.nombre)
    ).scalars().all()
    return PortalInicialRead(
        doctores=[
            PortalDoctorRead(
                id=doctor.id,
                nombre=doctor.nombre,
                especialidad=doctor.especialidad,
            )
            for doctor in doctores
        ],
        obras_sociales=[
            PortalObraSocialRead(id=obra.id, nombre=obra.nombre) for obra in obras
        ],
    )


def horarios_libres(db: Session, doctor_id: int, fecha) -> List[str]:
    """Horas libres (``HH:MM``) del profesional en ``fecha``.

    Reutiliza el cálculo del Módulo 1 (agenda semanal o excepciones del día,
    menos los turnos vigentes) y descarta las horas que ya pasaron cuando la
    fecha es hoy. Nunca devuelve datos del turno que ocupa un bloque.
    """
    if fecha < ahora_consultorio().date():
        raise ValidacionError(
            "No se puede reservar una fecha pasada. Elegí un día de hoy en adelante."
        )

    _, bloques = turno_service.horarios_disponibles(
        db, doctor_id, fecha, settings.portal_duracion_turno_min
    )
    ahora = ahora_consultorio()
    libres: List[str] = []
    for hora_inicio, _ in bloques:
        if datetime.combine(fecha, hora_inicio) <= ahora:
            continue
        libres.append(hora_inicio.strftime("%H:%M"))
    return libres


# --------------------------------------------------------------------------- #
# Búsqueda mínima por DNI
# --------------------------------------------------------------------------- #
def _nombre_de_pila(nombre: str) -> str:
    """Primer token del nombre completo (la respuesta pública no da apellidos)."""
    partes = (nombre or "").strip().split()
    return partes[0] if partes else ""


def enmascarar_telefono(telefono: Optional[str]) -> str:
    """Deja ver sólo los últimos cuatro dígitos (``***4589``)."""
    digitos = "".join(c for c in str(telefono or "") if c.isdigit())
    if not digitos:
        return "***"
    return "***" + digitos[-4:]


def _consulta_paciente_por_dni(dni: str, *, bloquear: bool = False):
    """Arma la consulta del paciente por DNI (expuesta para pruebas de SQL).

    La relación ``Paciente.obra_social`` es ``lazy="joined"``: el ``SELECT``
    incluye un ``LEFT OUTER JOIN`` a ``obras_sociales``. PostgreSQL no permite
    un ``FOR UPDATE`` genérico sobre el lado nulable de un outer join
    (``FOR UPDATE cannot be applied to the nullable side of an outer join``),
    así que el bloqueo se limita explícitamente a la tabla ``pacientes``.
    SQLite ignora la cláusula y sigue funcionando igual en las pruebas.
    """
    stmt = select(Paciente).where(Paciente.dni == dni)
    if bloquear:
        stmt = stmt.with_for_update(of=Paciente)
    return stmt


def _buscar_por_dni(
    db: Session, dni: str, *, bloquear: bool = False
) -> Optional[Paciente]:
    """Paciente con ese documento (opcionalmente con ``SELECT … FOR UPDATE``)."""
    return (
        db.execute(_consulta_paciente_por_dni(dni, bloquear=bloquear))
        .scalars()
        .first()
    )


def buscar_paciente(db: Session, dni: str) -> PortalPacienteRead:
    """Indica si el DNI existe y devuelve sólo datos mínimos y enmascarados."""
    paciente = _buscar_por_dni(db, dni)
    if paciente is None:
        return PortalPacienteRead(nuevo=True)

    obra = paciente.obra_social
    return PortalPacienteRead(
        nuevo=False,
        nombre=_nombre_de_pila(paciente.nombre),
        obra_social_id=paciente.obra_social_id,
        obra_social=obra.nombre if obra else None,
        telefono_enmascarado=enmascarar_telefono(paciente.telefono),
    )


# --------------------------------------------------------------------------- #
# Reserva
# --------------------------------------------------------------------------- #
def _obtener_doctor_bloqueado(db: Session, doctor_id: int) -> Doctor:
    """Valida que el profesional exista/esté activo y bloquea su fila.

    El bloqueo es lo que evita que dos reservas simultáneas vean el mismo hueco
    libre: la segunda espera a que la primera confirme y recién entonces
    recalcula la disponibilidad. En SQLite (pruebas) la cláusula se ignora y el
    índice único parcial de ``turnos`` cubre el caso exacto.
    """
    stmt = select(Doctor).where(Doctor.id == doctor_id).with_for_update()
    doctor = db.execute(stmt).scalars().first()
    if doctor is None:
        raise RecursoNoEncontradoError(f"No existe un doctor con id={doctor_id}.")
    if not doctor.activo:
        raise RecursoNoEncontradoError(
            f"El doctor id={doctor_id} está dado de baja y no puede recibir turnos."
        )
    return doctor


def _obra_social_publica(
    db: Session,
    obra_social_id: Optional[int],
    paciente: Optional[Paciente] = None,
) -> Optional[ObraSocial]:
    """Valida la cobertura elegida.

    Una cobertura **activa** siempre se acepta. Una dada de baja sólo se admite
    si es la que el paciente ya tenía (así su ficha no queda bloqueada para
    reservar por una baja administrativa del catálogo); para un DNI nuevo o una
    cobertura distinta se rechaza.
    """
    if obra_social_id is None:
        return None
    obra = db.get(ObraSocial, obra_social_id)
    if obra is None:
        raise ValidacionError(
            "La obra social elegida no está disponible. Elegí otra del listado."
        )
    if not obra.activa and (paciente is None or paciente.obra_social_id != obra.id):
        raise ValidacionError(
            "La obra social elegida no está disponible. Elegí otra del listado."
        )
    return obra


def _horario_solicitado(
    db: Session, data: PortalReservaCreate
) -> Tuple[datetime, datetime]:
    """Valida el bloque pedido y lo vuelve a buscar en la agenda **ahora**.

    Es el re-chequeo que exige el control de concurrencia: se ejecuta dentro de
    la misma transacción y después de bloquear al profesional, de modo que un
    turno creado por otra reserva ya esté visible.
    """
    ahora = ahora_consultorio()
    inicio = data.fecha_hora
    if inicio.second or inicio.microsecond:
        raise ValidacionError("El horario debe estar alineado a bloques de 30 minutos.")
    if inicio <= ahora:
        raise ValidacionError("No se puede reservar un turno en el pasado.")

    fecha = inicio.date()
    duracion = settings.portal_duracion_turno_min
    fin = inicio + timedelta(minutes=duracion)
    if fin.date() != fecha:
        raise ValidacionError("El horario elegido no es válido.")

    _, bloques = turno_service.horarios_disponibles(
        db, data.doctor_id, fecha, duracion
    )
    pedido = (inicio.time(), fin.time())
    if pedido not in bloques:
        raise ConflictoError(
            "Ese horario acaba de ocuparse o no está disponible. Actualizá los "
            "horarios y elegí otro."
        )
    return inicio, fin


def _contar_turnos_activos(db: Session, paciente_id: int, ahora: datetime) -> int:
    """Turnos futuros vigentes del paciente (PENDIENTE o CONFIRMADO)."""
    return int(
        db.execute(
            select(func.count(Turno.id)).where(
                Turno.paciente_id == paciente_id,
                Turno.estado_turno.in_(ESTADOS_ACTIVOS),
                Turno.fecha_hora_inicio >= ahora,
            )
        ).scalar_one()
    )


def _telefono_para_guardar(telefono: str) -> str:
    """Normaliza al formato de WAHA (``549…``) conservando un fallback textual."""
    normalizado = normalizar_telefono_waha(telefono)
    return normalizado or telefono.strip()


def codigo_cancelacion(turno_id: int, dni: str) -> str:
    """Código corto y estable que el paciente usa para cancelar su turno.

    Se deriva con HMAC-SHA256 de la clave de sesión, el ``turno_id`` y el DNI:
    no se guarda en la base y no se puede adivinar sin conocer el DNI y el
    secreto del servidor. El DNI sigue siendo obligatorio al cancelar, así que
    el código no es una credencial por sí solo.
    """
    mensaje = f"cancelar-turno:{turno_id}:{dni}".encode("utf-8")
    digest = hmac.new(
        settings.jwt_secret_efectivo.encode("utf-8"), mensaje, hashlib.sha256
    ).hexdigest()
    return digest[:8].upper()


def construir_mensaje_confirmacion(
    turno: Turno,
    doctor: Doctor,
    obra: Optional[ObraSocial],
    codigo: str,
) -> str:
    """Texto del WhatsApp de confirmación con el código de autogestión."""
    nombre = (turno.paciente.nombre if turno.paciente else "").strip() or "paciente"
    profesional = profesional_con_articulo(doctor.nombre)
    lineas = [
        f"Hola {nombre}, tu turno quedó reservado.",
        "",
        f"Fecha: {formatear_fecha_recordatorio(turno.fecha_hora_inicio)}",
        f"Hora: {turno.fecha_hora_inicio:%H:%M} hs",
        f"Profesional: {profesional}",
        f"Especialidad: {doctor.especialidad}",
        "Lugar: Franciosi Odontología",
    ]
    if obra is not None:
        lineas.append(f"Obra social: {obra.nombre}")
    lineas.extend(
        [
            "",
            f"Código de cancelación: {codigo}",
            (
                "Para cancelar hasta "
                f"{settings.portal_cancelacion_horas} horas antes, ingresá en "
                f"{settings.url_publica}/reservar con tu DNI y ese código."
            ),
            "",
            "¡Te esperamos!",
        ]
    )
    return "\n".join(lineas)


def _notificar_confirmacion(
    db: Session,
    turno: Turno,
    paciente: Paciente,
    doctor: Doctor,
    obra: Optional[ObraSocial],
) -> bool:
    """Envía el WhatsApp de confirmación si WAHA está configurado.

    El resultado se guarda en el turno para que el panel sepa si el aviso salió.
    Un error de WhatsApp no revierte la reserva: se registra como ``FALLIDO``
    para que la secretaría lo vea y pueda reenviarlo desde el panel.
    """
    if not settings.whatsapp_envio_real:
        # Sin credenciales el mensaje se omite a propósito (antes que simular un
        # envío que no existe). El turno queda PENDIENTE_ENVIO para el cron.
        return False

    codigo = codigo_cancelacion(turno.id, paciente.dni or "")
    texto = construir_mensaje_confirmacion(turno, doctor, obra, codigo)
    try:
        mensaje_id = whatsapp_service.enviar_mensaje_texto(paciente.telefono, texto)
    except Exception:  # noqa: BLE001 - el aviso no puede tumbar la reserva
        logger.exception("Falló el WhatsApp de confirmación del turno id=%s", turno.id)
        turno.estado_whatsapp = EstadoWhatsApp.FALLIDO
        db.commit()
        return False

    turno.estado_whatsapp = EstadoWhatsApp.ENVIADO
    turno.whatsapp_mensaje_id = mensaje_id
    db.commit()
    logger.info(
        "Confirmación de WhatsApp enviada: turno id=%s mensaje_id=%s",
        turno.id,
        mensaje_id,
    )
    return True


def reservar(db: Session, data: PortalReservaCreate) -> PortalReservaRead:
    """Reserva un turno para un paciente nuevo o existente.

    Orden de la transacción:

    1. bloquea al odontólogo y vuelve a validar el bloque pedido;
    2. valida la cobertura y busca (y bloquea) al paciente por DNI;
    3. aplica el tope de ``PORTAL_MAX_TURNOS_ACTIVOS`` turnos futuros;
    4. crea el paciente si es nuevo o actualiza teléfono/cobertura si existe;
    5. inserta el turno y **recién ahí** confirma la transacción.

    Cualquier fallo hace ``rollback`` completo: no queda un paciente creado sin
    turno ni un turno sin paciente.
    """
    doctor = _obtener_doctor_bloqueado(db, data.doctor_id)
    inicio, fin = _horario_solicitado(db, data)

    ahora = ahora_consultorio()
    paciente = _buscar_por_dni(db, data.dni, bloquear=True)
    obra = _obra_social_publica(db, data.obra_social_id, paciente)
    telefono = _telefono_para_guardar(data.telefono_whatsapp)

    if paciente is not None:
        activos = _contar_turnos_activos(db, paciente.id, ahora)
        if activos >= settings.portal_max_turnos_activos:
            raise ConflictoError(
                "Ya tenés "
                f"{activos} turnos reservados. Para sacar otro, cancelá uno "
                "desde «Cancelar turno» o llamá al consultorio.",
                extra={"limite_turnos": settings.portal_max_turnos_activos},
            )
        if paciente.telefono != telefono:
            paciente.telefono = telefono
        if data.obra_social_id is not None:
            paciente.obra_social_id = data.obra_social_id
    else:
        if not data.apellido:
            raise ValidacionError(
                "Para registrarte por primera vez necesitamos tu nombre y apellido."
            )
        nombre_completo = f"{data.nombre} {data.apellido}".strip()
        paciente = Paciente(
            nombre=nombre_completo,
            dni=data.dni,
            telefono=telefono,
            obra_social_id=data.obra_social_id,
        )
        db.add(paciente)
        db.flush()

    turno = Turno(
        paciente_id=paciente.id,
        doctor_id=doctor.id,
        fecha_hora_inicio=inicio,
        fecha_hora_fin=fin,
        estado_turno=EstadoTurno.PENDIENTE,
        estado_whatsapp=EstadoWhatsApp.PENDIENTE_ENVIO,
        motivo_consulta=data.motivo_consulta,
    )
    db.add(turno)
    try:
        db.commit()
    except IntegrityError as exc:
        # Índice único parcial (doctor_id, fecha_hora_inicio) o DNI duplicado
        # por dos altas simultáneas: la transacción entera se descarta.
        db.rollback()
        logger.warning(
            "Reserva pública rechazada por integridad (doctor_id=%s): %s",
            data.doctor_id,
            exc.orig,
        )
        raise TurnoSolapadoError(
            "Ese horario acaba de ocuparse o el DNI se registró en otra "
            "solicitud. Volvé a intentar."
        ) from exc

    db.refresh(turno)
    db.refresh(paciente)
    enviado = _notificar_confirmacion(db, turno, paciente, doctor, obra)

    return PortalReservaRead(
        turno_id=turno.id,
        fecha_hora=turno.fecha_hora_inicio,
        fecha=turno.fecha_hora_inicio.date(),
        hora=turno.fecha_hora_inicio.strftime("%H:%M"),
        doctor=doctor.nombre,
        especialidad=doctor.especialidad,
        paciente=paciente.nombre,
        obra_social=obra.nombre if obra else None,
        motivo_consulta=turno.motivo_consulta,
        codigo_cancelacion=codigo_cancelacion(turno.id, paciente.dni or ""),
        whatsapp_enviado=enviado,
    )


# --------------------------------------------------------------------------- #
# Cancelación
# --------------------------------------------------------------------------- #
def _turno_por_codigo(
    turnos: Sequence[Turno], dni: str, codigo: str
) -> Optional[Turno]:
    """Busca el turno por número de id o por el código corto de autogestión."""
    for turno in turnos:
        if codigo == str(turno.id):
            return turno
        if codigo == codigo_cancelacion(turno.id, dni):
            return turno
    return None


def cancelar(db: Session, data: PortalCancelacionCreate) -> PortalCancelacionRead:
    """Cancela un turno propio si faltan al menos las horas configuradas.

    El DNI y el código se validan juntos: un código correcto con otro DNI no
    encuentra el turno (se responde 404 genérico para no confirmar que existe).
    Si el turno ya estaba cancelado la operación es idempotente y no falla.
    """
    paciente = _buscar_por_dni(db, data.dni)
    if paciente is None:
        raise RecursoNoEncontradoError(
            "No encontramos un turno con ese DNI y código de cancelación."
        )

    turnos = db.execute(
        select(Turno).where(Turno.paciente_id == paciente.id)
    ).scalars().all()
    turno = _turno_por_codigo(turnos, data.dni, data.codigo)
    if turno is None:
        raise RecursoNoEncontradoError(
            "No encontramos un turno con ese DNI y código de cancelación. "
            "Revisá el código del mensaje de confirmación."
        )

    if turno.estado_turno == EstadoTurno.CANCELADO:
        return PortalCancelacionRead(
            turno_id=turno.id,
            fecha_hora=turno.fecha_hora_inicio,
            estado_turno=turno.estado_turno.value,
            ya_cancelado=True,
            mensaje="Ese turno ya estaba cancelado.",
        )

    faltante = turno.fecha_hora_inicio - ahora_consultorio()
    if faltante < timedelta(hours=settings.portal_cancelacion_horas):
        raise ConflictoError(
            "Sólo se puede cancelar hasta "
            f"{settings.portal_cancelacion_horas} horas antes del turno. "
            "Comunicate con el consultorio para resolverlo."
        )

    turno.estado_turno = EstadoTurno.CANCELADO
    db.commit()
    db.refresh(turno)
    logger.info(
        "Turno cancelado desde el portal: id=%s paciente_id=%s (%s)",
        turno.id,
        paciente.id,
        turno.fecha_hora_inicio.isoformat(),
    )
    return PortalCancelacionRead(
        turno_id=turno.id,
        fecha_hora=turno.fecha_hora_inicio,
        estado_turno=turno.estado_turno.value,
        ya_cancelado=False,
        mensaje="Turno cancelado. El horario quedó libre y ya podés reservar otro.",
    )




