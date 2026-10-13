"""Carga datos de ejemplo para probar el sistema de forma manual.

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.seed

El script es **idempotente**: identifica cada registro por una clave natural
(odontóloga por nombre, paciente por DNI y turno por odontóloga + fecha/hora de
inicio), de modo que puede ejecutarse varias veces sin duplicar datos.

Las odontólogas de ``ODONTOLOGAS`` son las que alimentan los botones de filtro
del calendario (*Todos los odontólogos*, Cecilia, Fabiana y Florencia). Los
turnos de ejemplo se agendan en los próximos días para que la grilla muestre
contenido desde el primer arranque; las fechas se guardan en UTC naive
(convención del proyecto), por lo que las horas elegidas caen en horario de
consultorio para husos UTC-3/-4 (Argentina).

Además carga la **agenda semanal** de cada odontóloga en
``disponibilidad_doctores`` (``DISPONIBILIDAD``): lunes a viernes con dos tramos
(09:00–13:00 y 15:00–19:00) y sábado por la mañana. Es la configuración que el
calendario usa para ofrecer bloques de turnos, y sólo se crea cuando la
profesional todavía no tiene rangos, así el script nunca pisa una agenda editada
desde la pestaña *Disponibilidad* del panel.
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.datetime_utils import utcnow
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.doctor import Doctor
from app.models.enums import EstadoTurno, EstadoWhatsApp
from app.models.paciente import Paciente
from app.models.turno import Turno


# (nombre, especialidad) de las odontólogas del consultorio.
ODONTOLOGAS = (
    ("Dra. Cecilia Márquez", "Ortodoncia"),
    ("Dra. Fabiana Ferreyra", "Odontopediatría"),
    ("Dra. Florencia Bustos", "Endodoncia"),
)

# Agenda semanal por defecto de cada odontóloga (clave = día de la semana,
# 0 = domingo … 6 = sábado). Cada día lista sus tramos de atención como pares
# ("HH:MM desde", "HH:MM hasta"): el lunes tiene dos rangos (mañana y tarde),
# que es el caso que habilita el modelo ``DisponibilidadDoctor``.
DISPONIBILIDAD = {
    0: (),                                                # domingo: cerrado
    1: (("09:00", "13:00"), ("15:00", "19:00")),          # lunes
    2: (("09:00", "13:00"), ("15:00", "19:00")),          # martes
    3: (("09:00", "13:00"), ("15:00", "19:00")),          # miércoles
    4: (("09:00", "13:00"), ("15:00", "19:00")),          # jueves
    5: (("09:00", "13:00"), ("15:00", "19:00")),          # viernes
    6: (("09:00", "13:00"),),                             # sábado
}

# (nombre, telefono, email, dni, obra_social, numero_afiliado)
PACIENTES = (
    ("Juan Pérez", "+5491122334455", "juan.perez@example.com", "30123456", "OSDE", "610012345678"),
    ("María López", "+5491133445566", "maria.lopez@example.com", "27890123", "Swiss Medical", "SM-998877"),
    ("Pedro Ramírez", "+5491144556677", None, "34567890", "IOMA", "IO-445566"),
    ("Lucía Fernández", "+5491155667788", "lucia.fernandez@example.com", "32456789", "Particular", None),
)

# (índice de odontóloga, índice de paciente, días desde hoy, hora UTC, minutos,
#  estado del turno). El estado de WhatsApp se deriva del estado del turno.
# Las horas caen dentro de ``DISPONIBILIDAD`` (09:00-13:00 / 15:00-19:00) para
# que los turnos de ejemplo aparezcan como bloques ocupados del calendario.
TURNOS = (
    (0, 0, 0, 10, 30, EstadoTurno.PENDIENTE),
    (1, 1, 0, 16, 30, EstadoTurno.CONFIRMADO),
    (2, 2, 0, 17, 30, EstadoTurno.CONFIRMADO),
    (0, 3, 1, 10, 30, EstadoTurno.PENDIENTE),
    (1, 0, 2, 16, 30, EstadoTurno.PENDIENTE),
    (2, 1, 3, 11, 30, EstadoTurno.CANCELADO),
)


def _inicio(dias: int, hora: int, minuto: int) -> datetime:
    """Inicio del turno de ejemplo: ``dias`` adelante, a las ``hora``:``minuto``."""
    return (utcnow() + timedelta(days=dias)).replace(
        hour=hora, minute=minuto, second=0, microsecond=0
    )


def _estado_whatsapp(estado: EstadoTurno) -> EstadoWhatsApp:
    """Un turno confirmado o cancelado de ejemplo ya tiene respuesta del paciente."""
    if estado in (EstadoTurno.CONFIRMADO, EstadoTurno.CANCELADO):
        return EstadoWhatsApp.RESPONDIDO
    return EstadoWhatsApp.PENDIENTE_ENVIO


def run() -> None:
    init_db()

    with SessionLocal() as db:
        creados = {"doctores": 0, "pacientes": 0, "turnos": 0}
        omitidos = {"doctores": 0, "pacientes": 0, "turnos": 0}

        # --- Odontólogas (generan los botones de filtro del calendario) ------
        doctores = []
        for nombre, especialidad in ODONTOLOGAS:
            doctor = db.execute(
                select(Doctor).where(Doctor.nombre == nombre)
            ).scalars().first()
            if doctor is None:
                doctor = Doctor(nombre=nombre, especialidad=especialidad, activo=True)
                db.add(doctor)
                creados["doctores"] += 1
            else:
                omitidos["doctores"] += 1
            doctores.append(doctor)

        # --- Pacientes (con DNI y cobertura médica) --------------------------
        pacientes = []
        for nombre, telefono, email, dni, obra_social, numero_afiliado in PACIENTES:
            paciente = db.execute(
                select(Paciente).where(Paciente.dni == dni)
            ).scalars().first()
            if paciente is None:
                paciente = Paciente(
                    nombre=nombre,
                    telefono=telefono,
                    email=email,
                    dni=dni,
                    obra_social=obra_social,
                    numero_afiliado=numero_afiliado,
                )
                db.add(paciente)
                creados["pacientes"] += 1
            else:
                omitidos["pacientes"] += 1
            pacientes.append(paciente)

        db.commit()  # asigna los ids de doctores y pacientes
        for registro in (*doctores, *pacientes):
            db.refresh(registro)

        # --- Disponibilidad semanal por defecto ------------------------------
        # Sólo se crea si la odontóloga todavía no tiene rangos configurados, así
        # el script no pisa una agenda editada desde la pestaña Disponibilidad.
        rangos_creados = 0
        for doctor in doctores:
            ya_tiene = db.execute(
                select(DisponibilidadDoctor.id).where(
                    DisponibilidadDoctor.doctor_id == doctor.id
                )
            ).first()
            if ya_tiene is not None:
                continue
            for dia, tramos in DISPONIBILIDAD.items():
                for desde, hasta in tramos:
                    db.add(
                        DisponibilidadDoctor(
                            doctor_id=doctor.id,
                            dia_semana=dia,
                            hora_inicio=datetime.strptime(desde, "%H:%M").time(),
                            hora_fin=datetime.strptime(hasta, "%H:%M").time(),
                        )
                    )
                    rangos_creados += 1
        db.commit()

        # --- Turnos de ejemplo -----------------------------------------------
        for i_doctor, i_paciente, dias, hora, duracion, estado in TURNOS:
            doctor = doctores[i_doctor]
            paciente = pacientes[i_paciente]
            inicio = _inicio(dias, hora, 0)
            existente = db.execute(
                select(Turno).where(
                    Turno.doctor_id == doctor.id,
                    Turno.fecha_hora_inicio == inicio,
                )
            ).scalars().first()
            if existente is not None:
                omitidos["turnos"] += 1
                continue
            db.add(
                Turno(
                    paciente_id=paciente.id,
                    doctor_id=doctor.id,
                    fecha_hora_inicio=inicio,
                    fecha_hora_fin=inicio + timedelta(minutes=duracion),
                    estado_turno=estado,
                    estado_whatsapp=_estado_whatsapp(estado),
                )
            )
            creados["turnos"] += 1
        db.commit()

        print("Datos de ejemplo listos:")
        print(
            f"  Doctores   -> {len(doctores)} "
            f"({creados['doctores']} nuevos, {omitidos['doctores']} ya existían)"
        )
        for doctor in doctores:
            print(
                f"                 id={doctor.id} {doctor.nombre} "
                f"({doctor.especialidad})"
            )
        print(
            f"  Agenda     -> {rangos_creados} rangos nuevos "
            "(lun-vie 09:00-13:00 y 15:00-19:00, sáb 09:00-13:00)"
        )
        print(
            f"  Pacientes  -> {len(pacientes)} "
            f"({creados['pacientes']} nuevos, {omitidos['pacientes']} ya existían)"
        )
        for paciente in pacientes:
            print(
                f"                 id={paciente.id} {paciente.nombre} "
                f"({paciente.telefono}) DNI={paciente.dni or '-'} "
                f"OS={paciente.obra_social or '-'} Nro={paciente.numero_afiliado or '-'}"
            )
        print(
            f"  Turnos     -> {len(TURNOS)} "
            f"({creados['turnos']} nuevos, {omitidos['turnos']} ya existían)"
        )
        print("Interfaz web: http://127.0.0.1:8000/")


if __name__ == "__main__":
    run()
