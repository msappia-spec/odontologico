"""Fixtures compartidas para las pruebas.

Se usa una base SQLite **en memoria** (``StaticPool``) totalmente aislada del
entorno real, y se sobreescribe la dependencia ``get_db`` de FastAPI.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base  # registra todos los modelos en los metadatos
from app.db.session import get_db
from app.main import app
from app.models.doctor import Doctor
from app.models.paciente import Paciente


@pytest.fixture(autouse=True)
def whatsapp_sin_credenciales(monkeypatch):
    """Aísla del entorno: en las pruebas los envíos de WhatsApp se **simulan**.

    ``settings`` se instancia una sola vez al importar la app, así que si la
    máquina (o el ``.env``) tiene cargadas las credenciales reales de WAHA, los
    tests que esperan un envío simulado harían un ``POST`` de verdad. Acá se
    vacían para todos los tests; el que necesite el camino real las vuelve a
    poner con su propio ``monkeypatch``.
    """
    monkeypatch.setattr(settings, "waha_url", None)
    monkeypatch.setattr(settings, "waha_api_key", None)
    monkeypatch.setattr(settings, "waha_session", "default")
    monkeypatch.setattr(settings, "cron_secret", "cron-secret-de-prueba")


class ClienteConCron:
    """Agrega el ``Bearer`` del cron a los POST de la tarea de recordatorios."""

    def __init__(self, client: TestClient):
        self._client = client

    def __getattr__(self, nombre):
        return getattr(self._client, nombre)

    def post(self, url, *args, **kwargs):
        if url == "/api/v1/tareas/enviar-recordatorios":
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Authorization", "Bearer cron-secret-de-prueba")
            return self._client.post(url, *args, headers=headers, **kwargs)
        return self._client.post(url, *args, **kwargs)


@pytest.fixture()
def engine():
    """Engine SQLite en memoria compartido durante toda la prueba."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


@pytest.fixture()
def db_session(engine) -> Session:
    """Sesión de base de datos ligada al engine de prueba."""
    testing_session = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = testing_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine, db_session) -> TestClient:
    """``TestClient`` de FastAPI con ``get_db`` sobreescrito.

    Nota: se evita el ``context manager`` para no ejecutar el ``lifespan`` (que
    crearía el esquema en la base de datos real ``DATABASE_URL``).
    """

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    try:
        yield ClienteConCron(test_client)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def doctor(db_session) -> Doctor:
    doctor = Doctor(nombre="Dr. House", especialidad="Diagnóstico", activo=True)
    db_session.add(doctor)
    db_session.commit()
    db_session.refresh(doctor)
    return doctor


@pytest.fixture()
def paciente(db_session) -> Paciente:
    paciente = Paciente(
        nombre="Paciente Demo",
        telefono="+5491122334455",
        email="paciente.demo@example.com",
    )
    db_session.add(paciente)
    db_session.commit()
    db_session.refresh(paciente)
    return paciente
