"""Reglas de la modalidad de pago de los odontólogos (Módulo 3).

Vive en ``app.core`` porque la usan **dos** capas: los esquemas Pydantic (para
rechazar un payload incoherente con ``422``) y el servicio de doctores (para
revalidar contra el estado guardado cuando se edita el profesional). Levanta
``ValueError`` a propósito: Pydantic lo convierte en error de validación y el
servicio lo traduce a :class:`~app.core.exceptions.ValidacionError`.
"""

from typing import Optional

from app.models.enums import TipoPagoDoctor


def validar_modalidad_pago(
    tipo_pago: TipoPagoDoctor, valor_pago: Optional[float]
) -> None:
    """Comprueba que ``valor_pago`` sea coherente con ``tipo_pago``.

    * ``COMISION`` necesita el porcentaje dentro del rango 0-100.
    * ``SUELDO`` necesita el importe mensual, mayor que 0.
    * ``DUENO`` puede omitir el valor (se asume el 100 %) y, si lo informa, debe
      ser un porcentaje válido.
    """
    if tipo_pago in (TipoPagoDoctor.DUENO, TipoPagoDoctor.COMISION):
        if valor_pago is not None and not 0 <= valor_pago <= 100:
            raise ValueError(
                "valor_pago debe ser un porcentaje entre 0 y 100 para "
                f"tipo_pago={tipo_pago.value}."
            )
        if tipo_pago == TipoPagoDoctor.COMISION and valor_pago is None:
            raise ValueError(
                "Una comisión necesita valor_pago (porcentaje entre 0 y 100)."
            )
    if tipo_pago == TipoPagoDoctor.SUELDO and (
        valor_pago is None or valor_pago <= 0
    ):
        raise ValueError(
            "Un sueldo necesita valor_pago (importe mensual mayor que 0)."
        )
