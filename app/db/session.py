"""Configuración del engine de SQLAlchemy y de la sesión de base de datos."""

from typing import Generator

from sqlalchemy import Engine, create_engine
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
