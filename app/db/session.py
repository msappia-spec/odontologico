"""Configuración del engine de SQLAlchemy y de la sesión de base de datos."""

from typing import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _crear_engine() -> Engine:
    """Crea el engine, aplicando los argumentos específicos de cada dialecto."""
    url = settings.database_url

    connect_args: dict = {}
    if url.startswith("sqlite"):
        # Necesario para poder compartir la conexión entre hilos (TestClient,
        # tareas programadas en background, etc.).
        connect_args = {"check_same_thread": False}

    return create_engine(
        url,
        echo=settings.debug,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


engine: Engine = _crear_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def _fijar_zona_utc(_session: Session, transaction, connection) -> None:
    """Fija la zona de la transacción en UTC (sólo PostgreSQL).

    La agenda guarda la **hora de pared del consultorio** como datetime naive
    (``2026-11-30T09:00``). PostgreSQL interpreta un naive según la zona de la
    sesión al escribirlo en una columna ``TIMESTAMPTZ``; si el servidor local
    está en ``America/Buenos_Aires``, un turno de las 09:00 se guarda como 12:00
    UTC y el ORM lo devuelve como 12:00. En Vercel y Neon la sesión suele ser
    UTC, pero no conviene depender de la configuración del proveedor.

    Se usa ``SET LOCAL`` al comenzar **cada transacción** en lugar de una opción
    de conexión: funciona igual con el pooler de Neon y no queda pegado a una
    conexión física que PgBouncer puede reasignar entre transacciones.
    """
    if transaction.nested:
        return
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")


event.listen(SessionLocal, "after_begin", _fijar_zona_utc)


def get_db() -> Generator[Session, None, None]:
    """Dependencia de FastAPI que provee una sesión por request.

    Garantiza el ``rollback`` ante errores y el cierre de la sesión al finalizar.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
