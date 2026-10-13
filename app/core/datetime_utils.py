"""Utilidades de fecha/hora.

Convención del proyecto: se trabaja con *datetime* ``naive`` en UTC. Esto evita
inconsistencias de comparación entre SQLite (que no almacena zona horaria) y
PostgreSQL, manteniendo un único criterio temporal.
"""

from datetime import date, datetime, time, timezone


def utcnow() -> datetime:
    """Devuelve la fecha/hora actual en UTC como ``datetime`` naive (sin tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Normaliza un ``datetime`` (con o sin zona) a UTC naive."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def rango_del_dia(fecha: date) -> tuple[datetime, datetime]:
    """Devuelve el par ``(00:00:00.000000, 23:59:59.999999)`` de ``fecha``."""
    inicio = datetime.combine(fecha, time.min)
    fin = datetime.combine(fecha, time.max)
    return inicio, fin
