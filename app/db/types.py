"""Tipos de columna propios.

Convención del proyecto: los *datetime* viajan **naive en UTC** (ver
:mod:`app.core.datetime_utils`). SQLite guarda las fechas como texto sin zona y
las devuelve naive, pero PostgreSQL conserva la zona de las columnas
``TIMESTAMP WITH TIME ZONE`` y las devuelve **aware**; al mezclarlas con las
fechas naive que arma el código aparece el error que se vio en producción
(``GET /api/v1/doctores/{id}/horarios-disponibles``)::

    TypeError: can't compare offset-naive and offset-aware datetimes

:class:`UTCDateTime` normaliza el valor en las dos direcciones —al guardar y al
leer—, de modo que el ORM entrega siempre UTC naive y el mismo código funciona
igual con SQLite y con PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.datetime_utils import to_naive_utc


class UTCDateTime(TypeDecorator):
    """``DateTime`` con zona en la base, pero **UTC naive** en Python.

    La columna sigue siendo ``TIMESTAMP WITH TIME ZONE`` (el esquema no cambia,
    así que las bases existentes no necesitan migración): lo que cambia es que el
    valor se convierte a UTC naive al leerlo, que es el formato que espera todo
    el código (comparaciones, ``rango_del_dia``, serialización de la API).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: Optional[datetime], dialect: Dialect
    ) -> Optional[datetime]:
        """Normaliza a UTC naive antes de escribir (nunca guarda hora local)."""
        return None if value is None else to_naive_utc(value)

    def process_result_value(
        self, value: Optional[datetime], dialect: Dialect
    ) -> Optional[datetime]:
        """Devuelve UTC naive aunque el motor entregue el valor con zona."""
        return None if value is None else to_naive_utc(value)
