"""Clase base declarativa de SQLAlchemy (estilo 2.0)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa común a todos los modelos ORM del proyecto."""
