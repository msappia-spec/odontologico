"""Carga datos de ejemplo para probar el sistema de forma manual.

Uso (desde la raíz del proyecto, con el venv activo)::

    python -m scripts.seed

El script es **idempotente**: identifica cada registro por una clave natural
(odontóloga por nombre, paciente por DNI, obra social por nombre y turno por
odontóloga + fecha/hora de inicio), de modo que puede ejecutarse varias veces sin
duplicar datos.

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

También carga el catálogo de **obras sociales** (``OBRAS_SOCIALES``) que alimenta
el desplegable del formulario de pacientes y vincula a cada paciente de ejemplo
con su cobertura (``Paciente.obra_social_id``).

Para el **Módulo 3** (facturación y caja) carga:

* la modalidad de pago de cada odontóloga (``ODONTOLOGAS``: dueña, comisión del
  45 % y sueldo fijo), que es lo que usa la liquidación de profesionales;
* el catálogo de **prestaciones** con su código de nomenclador y valor base
  (``PRESTACIONES``), que es lo que se tilda al cerrar una visita;
* los **aranceles** por cobertura (``ARANCELES``): cuánto cubre cada obra social
  y cuánto queda como coseguro del paciente.

Los importes son de ejemplo (pesos argentinos) para poder probar el circuito
completo: cobrar un coseguro, presentar el período y liquidar el mes.
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.datetime_utils import utcnow
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.arancel_obra_social import ArancelObraSocial
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.doctor import Doctor
from app.models.enums import EstadoTurno, EstadoWhatsApp, TipoPagoDoctor
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.models.prestacion import Prestacion
from app.models.turno import Turno


# (nombre, especialidad, tipo de pago, valor de pago) de las odontólogas. El
# tipo de pago es lo que liquida el Módulo 3: la dueña se queda con el 100 % de
# su producción, la comisionada con el 45 % y la tercera cobra un sueldo fijo,
# sin importar cuánto produzca.
ODONTOLOGAS = (
    ("Dra. Cecilia Márquez", "Ortodoncia", TipoPagoDoctor.DUENO, None),
    ("Dra. Fabiana Ferreyra", "Odontopediatría", TipoPagoDoctor.COMISION, 45.0),
    ("Dra. Florencia Bustos", "Endodoncia", TipoPagoDoctor.SUELDO, 850000.0),
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

# (nombre, codigo) de las coberturas del catálogo de obras sociales. Es el
# desplegable que usa el formulario de pacientes; las cuatro primeras son las que
# tienen asignadas los pacientes de ejemplo.
OBRAS_SOCIALES = (
    ("Particular", None),
    ("OSDE", "OSDE-01"),
    ("Swiss Medical", "SM-100"),
    ("IOMA", "IOMA-200"),
    ("Medicus", "MED-300"),
    ("Galeno", "GAL-400"),
)

# (nombre, telefono, email, dni, obra_social, numero_afiliado)
PACIENTES = (
    ("Juan Pérez", "+5491122334455", "juan.perez@example.com", "30123456", "OSDE", "610012345678"),
    ("María López", "+5491133445566", "maria.lopez@example.com", "27890123", "Swiss Medical", "SM-998877"),
    ("Pedro Ramírez", "+5491144556677", None, "34567890", "IOMA", "IO-445566"),
    ("Lucía Fernández", "+5491155667788", "lucia.fernandez@example.com", "32456789", "Particular", None),
)

# (código de nomenclador, descripción, valor base) del catálogo de prestaciones:
# lo que la secretaría tilda al cerrar la visita. El valor base es el precio de
# referencia para un paciente particular.
PRESTACIONES = (
    ("01.01", "Consulta y diagnóstico", 18000.0),
    ("01.02", "Consulta de urgencia", 25000.0),
    ("02.01", "Obturación de composite (una cara)", 42000.0),
    ("02.05", "Endodoncia unirradicular", 95000.0),
    ("03.01", "Limpieza y destartraje", 38000.0),
    ("04.01", "Extracción simple", 45000.0),
    ("05.01", "Radiografía periapical", 12000.0),
)

# (obra social, código de prestación, cubre la OS, coseguro del paciente) de los
# aranceles de ejemplo. La diferencia con el valor base es el descuento que la
# cobertura le consigue al paciente.
ARANCELES = (
    ("OSDE", "01.01", 12000.0, 6000.0),
    ("OSDE", "02.01", 30000.0, 12000.0),
    ("OSDE", "03.01", 28000.0, 10000.0),
    ("OSDE", "05.01", 9000.0, 3000.0),
    ("Swiss Medical", "01.01", 11000.0, 7000.0),
    ("Swiss Medical", "02.01", 28000.0, 14000.0),
    ("Swiss Medical", "02.05", 70000.0, 25000.0),
    ("IOMA", "01.01", 14000.0, 4000.0),
    ("IOMA", "03.01", 30000.0, 8000.0),
    ("IOMA", "04.01", 35000.0, 10000.0),
    ("Medicus", "01.01", 10000.0, 8000.0),
    ("Medicus", "05.01", 8000.0, 4000.0),
    ("Galeno", "01.01", 10000.0, 8000.0),
    ("Galeno", "02.01", 26000.0, 16000.0),
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
        creados = {
            "doctores": 0,
            "pacientes": 0,
            "turnos": 0,
            "obras": 0,
            "prestaciones": 0,
            "aranceles": 0,
        }
        omitidos = {
            "doctores": 0,
            "pacientes": 0,
            "turnos": 0,
            "obras": 0,
            "prestaciones": 0,
            "aranceles": 0,
        }

        # --- Odontólogas (generan los botones de filtro del calendario) ------
        doctores = []
        for nombre, especialidad, tipo_pago, valor_pago in ODONTOLOGAS:
            doctor = db.execute(
                select(Doctor).where(Doctor.nombre == nombre)
            ).scalars().first()
            if doctor is None:
                doctor = Doctor(
                    nombre=nombre,
                    especialidad=especialidad,
                    activo=True,
                    tipo_pago=tipo_pago,
                    valor_pago=valor_pago,
                )
                db.add(doctor)
                creados["doctores"] += 1
            else:
                omitidos["doctores"] += 1
            doctores.append(doctor)

        # --- Obras sociales (catálogo que alimenta el desplegable) -----------
        obras_sociales: dict[str, ObraSocial] = {}
        for nombre, codigo in OBRAS_SOCIALES:
            obra_social = db.execute(
                select(ObraSocial).where(ObraSocial.nombre == nombre)
            ).scalars().first()
            if obra_social is None:
                obra_social = ObraSocial(nombre=nombre, codigo=codigo, activa=True)
                db.add(obra_social)
                creados["obras"] += 1
            else:
                omitidos["obras"] += 1
            obras_sociales[nombre.lower()] = obra_social
        db.commit()

        # --- Pacientes (con DNI y cobertura médica) --------------------------
        pacientes = []
        for nombre, telefono, email, dni, obra_social, numero_afiliado in PACIENTES:
            paciente = db.execute(
                select(Paciente).where(Paciente.dni == dni)
            ).scalars().first()
            if paciente is None:
                cobertura = obras_sociales.get((obra_social or "").lower())
                paciente = Paciente(
                    nombre=nombre,
                    telefono=telefono,
                    email=email,
                    dni=dni,
                    obra_social_id=cobertura.id if cobertura else None,
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

        # --- Prestaciones y aranceles (Módulo 3: facturación mixta) ----------
        # El código de nomenclador identifica la práctica; el arancel es el
        # acuerdo económico con cada cobertura (cuánto cubre y cuánto queda de
        # coseguro para el mostrador).
        prestaciones: dict[str, Prestacion] = {}
        for codigo, descripcion, valor_base in PRESTACIONES:
            prestacion = db.execute(
                select(Prestacion).where(Prestacion.codigo_nomenclador == codigo)
            ).scalars().first()
            if prestacion is None:
                prestacion = Prestacion(
                    codigo_nomenclador=codigo,
                    descripcion=descripcion,
                    valor_base=valor_base,
                    activa=True,
                )
                db.add(prestacion)
                creados["prestaciones"] += 1
            else:
                omitidos["prestaciones"] += 1
            prestaciones[codigo] = prestacion
        db.commit()

        for nombre_obra, codigo, cubre, coseguro in ARANCELES:
            obra_social = obras_sociales.get(nombre_obra.lower())
            prestacion = prestaciones.get(codigo)
            if obra_social is None or prestacion is None:
                continue  # catálogo incompleto: se saltea esa fila
            existente = db.execute(
                select(ArancelObraSocial).where(
                    ArancelObraSocial.obra_social_id == obra_social.id,
                    ArancelObraSocial.prestacion_id == prestacion.id,
                )
            ).scalars().first()
            if existente is None:
                db.add(
                    ArancelObraSocial(
                        obra_social_id=obra_social.id,
                        prestacion_id=prestacion.id,
                        monto_cubre_os=cubre,
                        monto_coseguro_paciente=coseguro,
                    )
                )
                creados["aranceles"] += 1
            else:
                omitidos["aranceles"] += 1
        db.commit()

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
                f"({doctor.especialidad}) pago={doctor.tipo_pago.value}"
                f"{'' if doctor.valor_pago is None else f' valor={doctor.valor_pago:g}'}"
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
                f"OS={paciente.obra_social.nombre if paciente.obra_social else '-'} "
                f"Nro={paciente.numero_afiliado or '-'}"
            )
        print(
            f"  Obras soc. -> {len(OBRAS_SOCIALES)} "
            f"({creados['obras']} nuevas, {omitidos['obras']} ya existían): "
            + ", ".join(obra_social for obra_social, _ in OBRAS_SOCIALES)
        )
        print(
            f"  Turnos     -> {len(TURNOS)} "
            f"({creados['turnos']} nuevos, {omitidos['turnos']} ya existían)"
        )
        print(
            f"  Prácticas  -> {len(PRESTACIONES)} "
            f"({creados['prestaciones']} nuevas, {omitidos['prestaciones']} ya existían): "
            + ", ".join(codigo for codigo, _, _ in PRESTACIONES)
        )
        print(
            f"  Aranceles  -> {len(ARANCELES)} "
            f"({creados['aranceles']} nuevos, {omitidos['aranceles']} ya existían) "
            "por cobertura (cubre la OS + coseguro del paciente)"
        )
        print("Interfaz web: http://127.0.0.1:8000/")


if __name__ == "__main__":
    run()
