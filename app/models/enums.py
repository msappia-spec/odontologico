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


class TipoMovimientoCC(str, enum.Enum):
    """Naturaleza de un movimiento de la cuenta corriente de un paciente.

    El libro mayor (``TransaccionCC``) es un registro **append-only** con dos
    únicas naturalezas, igual que un mayor contable:

    * ``DEBITO``: aumenta la deuda del paciente (cargo por un tratamiento, un
      presupuesto aceptado, un coseguro impago…).
    * ``CREDITO``: la reduce (entrega inicial, pago de una cuota, pago a
      cuenta, saldo a favor).

    El ``saldo_actual`` de la ``CuentaCorriente`` es siempre
    ``suma(DEBITO) - suma(CREDITO)``: un saldo positivo es deuda del paciente y
    uno negativo es saldo a favor.
    """

    DEBITO = "DEBITO"
    CREDITO = "CREDITO"


class EstadoPlanCuotas(str, enum.Enum):
    """Situación de un plan de financiación (Módulo 5).

    * ``ACTIVO``: el plan está en curso y al paciente todavía le quedan cuotas
      (o saldo) por pagar.
    * ``FINALIZADO``: la cuenta quedó saldada; lo calcula el servicio al
      registrar el último pago (nunca se marca a mano).
    * ``RECOBRABLE``: el paciente dejó de pagar y la secretaría derivó el plan a
      **gestión de cobranza**. Sigue siendo deuda: si el paciente paga, el
      saldo baja, y recién pasa a ``FINALIZADO`` cuando la cuenta se cancela.
    """

    ACTIVO = "ACTIVO"
    FINALIZADO = "FINALIZADO"
    RECOBRABLE = "RECOBRABLE"


class TipoDisponibilidadEspecifica(str, enum.Enum):
    """Efecto de un tramo horario cargado para **una fecha concreta**.

    Se usa en ``DisponibilidadEspecifica`` (excepciones de agenda): un rango
    ``HABILITADO`` suma atención ese día y uno ``BLOQUEADO`` la resta, sin
    tocar la agenda semanal del odontólogo (``DisponibilidadDoctor``).
    """

    HABILITADO = "HABILITADO"
    BLOQUEADO = "BLOQUEADO"
