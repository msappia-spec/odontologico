"""Enumeraciones del dominio (estados de turno y de notificación por WhatsApp)."""

import enum


class EstadoTurno(str, enum.Enum):
    """Estado lógico del turno."""

    PENDIENTE = "PENDIENTE"
    CONFIRMADO = "CONFIRMADO"
    CANCELADO = "CANCELADO"


class EstadoWhatsApp(str, enum.Enum):
    """Estado del flujo de notificación por WhatsApp asociado al turno."""

    PENDIENTE_ENVIO = "PENDIENTE_ENVIO"
    ENVIADO = "ENVIADO"
    FALLIDO = "FALLIDO"
    RESPONDIDO = "RESPONDIDO"


class TipoOpcionMenu(str, enum.Enum):
    """Comportamiento de una opción del menú del FAQ Bot de WhatsApp.

    * ``TEXTO``: responde el texto libre que cargó la secretaría en ``respuesta``.
    * ``PRECIO``: arma la tarifa con la prestación de consulta vigente.
    * ``OBRAS_SOCIALES``: lista las coberturas con convenio activas.
    * ``UBICACION``: dirección y horarios configurados en el ``.env``.
    * ``SECRETARIA``: no responde; deja el log «Atención Manual Requerida».
    """

    TEXTO = "TEXTO"
    PRECIO = "PRECIO"
    OBRAS_SOCIALES = "OBRAS_SOCIALES"
    UBICACION = "UBICACION"
    SECRETARIA = "SECRETARIA"


class TipoPagoDoctor(str, enum.Enum):
    """Modalidad con la que se le paga al odontólogo su producción (Módulo 3).

    * ``DUENO``: es dueño del consultorio y se queda con (casi) toda su
      producción; ``valor_pago`` es el porcentaje que retiene (si no se informa,
      se asume el 100 %).
    * ``COMISION``: cobra un porcentaje fijo de lo que produjo (``valor_pago``
      entre 0 y 100, obligatorio).
    * ``SUELDO``: cobra un monto fijo por mes (``valor_pago``), sin importar
      cuánto produjo; la producción se sigue midiendo para el reporte.
    """

    DUENO = "DUENO"
    COMISION = "COMISION"
    SUELDO = "SUELDO"


class EstadoPagoMovimiento(str, enum.Enum):
    """Situación de cobro de un movimiento de caja (Módulo 3).

    El ciclo natural es: al cerrar la visita el movimiento nace ``PENDIENTE``
    (la obra social todavía no pagó); al generar la presentación del período
    pasa a ``EN_PROCESO_LIQUIDACION``; y al acreditarse el depósito bancario se
    cierra como ``COBRADO`` (la OS pagó) o ``DEBITADO_OS`` (la OS rechazó o
    descontó el importe).
    """

    PENDIENTE = "PENDIENTE"
    EN_PROCESO_LIQUIDACION = "EN_PROCESO_LIQUIDACION"
    COBRADO = "COBRADO"
    DEBITADO_OS = "DEBITADO_OS"


class TipoDisponibilidadEspecifica(str, enum.Enum):
    """Efecto de un tramo horario cargado para **una fecha concreta**.

    Se usa en ``DisponibilidadEspecifica`` (excepciones de agenda): un rango
    ``HABILITADO`` suma atención ese día y uno ``BLOQUEADO`` la resta, sin
    tocar la agenda semanal del odontólogo (``DisponibilidadDoctor``).
    """

    HABILITADO = "HABILITADO"
    BLOQUEADO = "BLOQUEADO"
