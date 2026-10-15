"""Utilidades de importes del Módulo 3 (facturación y liquidaciones).

Los importes se guardan como ``Float`` (decisión del módulo), pero **no** se
suman ni se comparan en binario: cada operación pasa por :class:`~decimal.Decimal`
y se vuelve a ``float`` redondeado a dos decimales. Así ``0.1 + 0.2`` nunca deja
un ``0.30000000000000004`` en una factura y el total del mostrador coincide con
el que calcula la obra social.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

# Cantidad de decimales con la que se trabaja (pesos argentinos).
DECIMALES = 2
_CENTAVOS = Decimal("0.01")


def redondear_monto(valor: float | int | str | None) -> float:
    """Redondea un importe a dos decimales (medio hacia arriba).

    ``None`` se interpreta como ``0``: el precio de una práctica sin configurar
    no puede dejar la factura en un valor indeterminado.
    """
    if valor is None:
        return 0.0
    return float(Decimal(str(valor)).quantize(_CENTAVOS, rounding=ROUND_HALF_UP))


def sumar_montos(valores: Iterable[float | int | None]) -> float:
    """Suma importes con precisión decimal y devuelve el total redondeado."""
    total = sum((Decimal(str(v or 0)) for v in valores), Decimal("0"))
    return float(total.quantize(_CENTAVOS, rounding=ROUND_HALF_UP))


def multiplicar_monto(monto: float | int | None, cantidad: int) -> float:
    """Multiplica un importe por una cantidad (entera) en precisión decimal."""
    total = Decimal(str(monto or 0)) * Decimal(int(cantidad))
    return float(total.quantize(_CENTAVOS, rounding=ROUND_HALF_UP))


def porcentaje_de(monto: float | int | None, porcentaje: float | int | None) -> float:
    """Devuelve ``monto`` * ``porcentaje`` / 100 con precisión decimal."""
    if monto is None or porcentaje is None:
        return 0.0
    valor = Decimal(str(monto)) * Decimal(str(porcentaje)) / Decimal("100")
    return float(valor.quantize(_CENTAVOS, rounding=ROUND_HALF_UP))
