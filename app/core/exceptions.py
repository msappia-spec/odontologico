"""Excepciones de dominio compartidas y su mapeo a códigos HTTP."""

from typing import Any, Optional


class AppError(Exception):
    """Error base de la aplicación.

    ``status_code`` permite que el manejador global lo traduzca a una respuesta
    HTTP coherente sin llenar los endpoints de ``try/except``.
    """

    status_code: int = 400
    default_detail: str = "Error en la solicitud."

    def __init__(self, detail: Optional[str] = None, *, extra: Optional[dict[str, Any]] = None):
        self.detail = detail or self.default_detail
        self.extra = extra or {}
        super().__init__(self.detail)


class ValidacionError(AppError):
    """Datos de entrada inválidos a nivel de regla de negocio."""

    status_code = 422
    default_detail = "Datos inválidos."


class RecursoNoEncontradoError(AppError):
    """El recurso solicitado no existe."""

    status_code = 404
    default_detail = "Recurso no encontrado."


class ConflictoError(AppError):
    """Conflicto con el estado actual (p. ej. solapamiento de turnos)."""

    status_code = 409
    default_detail = "Conflicto con el estado actual del recurso."


class TurnoSolapadoError(ConflictoError):
    """Un doctor ya posee un turno que se solapa en el rango solicitado."""

    default_detail = "El doctor ya tiene un turno en ese rango horario."


class PacienteConTurnoSolapadoError(AppError):
    """El paciente ya tiene otro turno vigente que se solapa con el rango pedido.

    Un paciente no puede estar en dos agendas a la vez (es "ubicuo"): si ya
    está citado a esa hora con **otro** odontólogo, la solicitud se rechaza con
    HTTP 400 para que la secretaría corrija el horario o cancele el turno
    existente. Se responde 400 (y no 409) porque el origen del conflicto es el
    dato ingresado en la solicitud.
    """

    status_code = 400
    default_detail = (
        "El paciente ya tiene un turno asignado con otro odontólogo en este horario"
    )


class TurnoNoEncontradoError(RecursoNoEncontradoError):
    """No existe un turno que corresponda al criterio dado."""

    default_detail = "Turno no encontrado."


class RespuestaWebhookInvalidaError(AppError):
    """El payload recibido por el webhook no tiene el formato esperado."""

    status_code = 400
    default_detail = "Payload de webhook inválido."


class WhatsAppEnvioError(AppError):
    """Falla al intentar enviar un mensaje por WhatsApp."""

    status_code = 502
    default_detail = "No se pudo enviar el mensaje de WhatsApp."


class RegistroInmutableError(AppError):
    """Se intentó modificar o borrar un registro clínico inmutable.

    Los registros de la historia clínica (las **evoluciones médicas**) tienen
    valor legal: sólo se pueden crear y leer. La API no expone ``PUT``/``PATCH``
    /``DELETE`` para ellos; esta excepción es la red de seguridad de la capa ORM
    (``before_update``/``before_delete`` de ``EvolucionMedica``) por si alguna
    otra vía —un script, una consola de base de datos, un test— intenta tocar una
    fila ya guardada. Se responde **403** (la operación está prohibida por
    normativa, no por un conflicto de datos).
    """

    status_code = 403
    default_detail = "Los registros clínicos son inmutables: no se pueden modificar ni eliminar."
