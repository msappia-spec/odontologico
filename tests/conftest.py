"""Fixtures compartidas para las pruebas.

Se usa una base SQLite **en memoria** (``StaticPool``) totalmente aislada del
entorno real, y se sobreescriben dos dependencias de FastAPI:

* ``get_db``: la sesión apunta a la base de prueba;
* ``get_current_user``: un superadmin en memoria, para que los tests históricos
  sigan pegándole a la API sin loguearse. Las pruebas de autenticación usan
  ``client_sin_sesion``, que **no** instala ese segundo override.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import get_current_user, hash_password
from app.db.base import Base  # registra todos los modelos en los metadatos
from app.db.session import get_db
from app.main import app
from app.models.doctor import Doctor
from app.models.paciente import Paciente
from app.models.usuario import Usuario
from app.services.categoria_paciente_service import sembrar_categorias_por_defecto


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


@pytest.fixture(autouse=True)
def reiniciar_limite_login():
    """Aísla el limitador de intentos entre pruebas.

    El contador vive en memoria y es global; sin este reinicio, una prueba que
    fuerza 5 logins fallidos dejaría a las siguientes con HTTP 429.
    """
    from app.core.security import reiniciar_limite_login as _reiniciar

    _reiniciar()
    yield


@pytest.fixture()
def usuario_autenticado() -> Usuario:
    """Superadmin ficticio que reemplaza a la sesión en las pruebas existentes.

    No se persiste: las dependencias ``get_current_user`` y ``require_module``
    sólo leen ``es_superadmin`` y ``modulos_permitidos``. Así los 1000+ tests
    históricos siguen pegándole a la API sin tener que loguearse en cada uno.
    """
    return Usuario(
        id=uuid4(),
        email="admin@prueba.local",
        nombre_completo="Administrador de prueba",
        hashed_password="no-se-usa-en-pruebas",
        es_superadmin=True,
        activo=True,
        modulos_permitidos=[],
    )


@pytest.fixture()
def crear_usuario(db_session):
    """Fábrica de usuarios reales para las pruebas de login y permisos."""

    def _crear(
        email: str = "secretaria@prueba.local",
        password: str = "ClaveSegura1",
        *,
        nombre: str = "Secretaria de prueba",
        es_superadmin: bool = False,
        activo: bool = True,
        modulos=None,
    ) -> Usuario:
        usuario = Usuario(
            email=email.strip().lower(),
            nombre_completo=nombre,
            hashed_password=hash_password(password),
            es_superadmin=es_superadmin,
            activo=activo,
            modulos_permitidos=list(modulos or []),
        )
        db_session.add(usuario)
        db_session.commit()
        db_session.refresh(usuario)
        return usuario

    return _crear


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
    # El catálogo de categorías es la fuente de verdad de ``pacientes.categoria``
    # (el endpoint de pacientes valida contra él). En producción lo siembra
    # ``init_db``; las pruebas crean el esquema directamente, así que se siembra
    # acá las tres categorías históricas.
    with sessionmaker(bind=test_engine)() as db:
        sembrar_categorias_por_defecto(db)
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
def client(engine, db_session, usuario_autenticado) -> TestClient:
    """``TestClient`` de FastAPI con ``get_db`` y la sesión sobreescritos.

    Se reemplaza ``get_current_user`` por un superadmin en memoria: es el
    *override* de autenticación que mantiene verdes los tests históricos, que
    asumen que la API responde sin login. Las pruebas de autenticación usan
    ``client_sin_sesion`` (sin este override).

    Nota: se evita el ``context manager`` para no ejecutar el ``lifespan`` (que
    crearía el esquema en la base de datos real ``DATABASE_URL``).
    """

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: usuario_autenticado
    test_client = TestClient(app)
    try:
        yield ClienteConCron(test_client)
    finally:
        # Se retiran sólo las claves de esta fixture: así dos clientes distintos
        # pueden convivir en un mismo test sin pisarse los overrides.
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def client_sin_sesion(engine, db_session) -> TestClient:
    """Cliente **sin** el override de autenticación (para probar login/permisos).

    La base sigue siendo la de prueba (``get_db`` sobreescrito), pero las
    dependencias de seguridad se ejecutan de verdad: sin cookie ni token, la API
    responde 401.
    """

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    try:
        yield ClienteConCron(test_client)
    finally:
        app.dependency_overrides.pop(get_db, None)


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
