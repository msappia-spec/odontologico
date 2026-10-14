"""Pruebas de ``scripts/migrar_sqlite_a_postgres.py``.

La migración se prueba de punta a punta **sin PostgreSQL**: el destino de las
pruebas es otra base SQLite (archivo temporal), lo que valida el esquema, el
orden de dependencias, la preservación de los ``id`` y de las claves foráneas,
la idempotencia y el modo simulacro. Lo único exclusivo de PostgreSQL (el
reajuste de secuencias) se verifica de forma aislada, sin servidor.
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, settings
from app.db.base import Base
from app.models.disponibilidad import DisponibilidadDoctor
from app.models.disponibilidad_especifica import DisponibilidadEspecifica
from app.models.doctor import Doctor
from app.models.enums import (
    EstadoTurno,
    EstadoWhatsApp,
    TipoDisponibilidadEspecifica,
)
from app.models.obra_social import ObraSocial
from app.models.paciente import Paciente
from app.models.turno import Turno
from scripts.migrar_sqlite_a_postgres import (
    MODOS,
    _normalizar_url,
    _reajustar_secuencias,
    main,
    migrar,
)

TABLAS = tuple(tabla.name for tabla in Base.metadata.sorted_tables)


def _cargar_datos(engine) -> None:
    """Carga un juego de datos con todas las tablas y sus relaciones."""
    sesion = sessionmaker(bind=engine, expire_on_commit=False)()
    obra = ObraSocial(nombre="OSDE", codigo="001", activa=True)
    doctor = Doctor(nombre="Dra. Test", especialidad="Ortodoncia", activo=True)
    paciente = Paciente(
        nombre="Paciente Test",
        dni="30123456",
        telefono="+5491100000000",
        obra_social=obra,
    )
    otro = Paciente(nombre="Sin obra", dni=None, telefono="+5491100000001")
    inicio = datetime(2026, 11, 3, 12, 0)
    turno = Turno(
        paciente=paciente,
        doctor=doctor,
        fecha_hora_inicio=inicio,
        fecha_hora_fin=inicio + timedelta(hours=1),
        estado_turno=EstadoTurno.PENDIENTE,
        estado_whatsapp=EstadoWhatsApp.PENDIENTE_ENVIO,
    )
    cancelado = Turno(
        paciente=otro,
        doctor=doctor,
        fecha_hora_inicio=inicio + timedelta(hours=2),
        fecha_hora_fin=inicio + timedelta(hours=3),
        estado_turno=EstadoTurno.CANCELADO,
        estado_whatsapp=EstadoWhatsApp.FALLIDO,
    )
    disponibilidad = DisponibilidadDoctor(
        doctor=doctor, dia_semana=1, hora_inicio=time(9, 0), hora_fin=time(13, 0)
    )
    excepcion = DisponibilidadEspecifica(
        doctor=doctor,
        fecha=date(2026, 11, 3),
        hora_inicio=time(8, 0),
        hora_fin=time(9, 0),
        tipo=TipoDisponibilidadEspecifica.BLOQUEADO,
    )
    sesion.add_all(
        [obra, doctor, paciente, otro, turno, cancelado, disponibilidad, excepcion]
    )
    sesion.commit()
    sesion.close()


@pytest.fixture()
def origen_url(tmp_path) -> str:
    """Base SQLite temporal con datos representativos de todas las tablas."""
    ruta = tmp_path / "origen.db"
    url = f"sqlite:///{ruta.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    _cargar_datos(engine)
    engine.dispose()
    return url


@pytest.fixture()
def destino_url(tmp_path) -> str:
    """Ruta de una base de destino que todavía no existe (la crea la migración)."""
    return f"sqlite:///{(tmp_path / 'destino.db').as_posix()}"




def _conteos(url: str) -> dict[str, int]:
    """Cuenta las filas de cada tabla en la base indicada."""
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            return {
                tabla: int(
                    conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{tabla}"').scalar()
                )
                for tabla in TABLAS
            }
    finally:
        engine.dispose()


def _leer(url: str, clase) -> list:
    """Instancias de ``clase`` en la base indicada, ordenadas por ``id``."""
    engine = create_engine(url, future=True)
    try:
        sesion = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            return list(
                sesion.execute(select(clase).order_by(clase.id)).scalars().all()
            )
        finally:
            sesion.close()
    finally:
        engine.dispose()


def test_simulacro_no_escribe(origen_url, destino_url):
    """Sin ``ejecutar=True`` sólo se informa: el destino queda vacío."""
    resultado = migrar(destino_url, origen_url)

    assert resultado.ejecutado is False
    assert resultado.total_insertadas == 0
    assert resultado.origen_filas["turnos"] == 2
    assert set(resultado.destino_final.values()) == {0}
    assert set(_conteos(destino_url).values()) == {0}


def test_copia_todo_preservando_ids_y_relaciones(origen_url, destino_url):
    """La copia conserva identificadores, estados, fechas y claves foráneas."""
    resultado = migrar(destino_url, origen_url, ejecutar=True)

    assert resultado.ejecutado is True
    assert resultado.salteadas == {}
    assert resultado.destino_final == resultado.origen_filas
    assert _conteos(destino_url) == _conteos(origen_url)

    doctores = _leer(destino_url, Doctor)
    assert [doctor.id for doctor in doctores] == [1]
    assert doctores[0].nombre == "Dra. Test"
    assert doctores[0].activo is True

    turnos = _leer(destino_url, Turno)
    assert [turno.id for turno in turnos] == [1, 2]
    assert turnos[0].estado_turno is EstadoTurno.PENDIENTE
    assert turnos[1].estado_turno is EstadoTurno.CANCELADO
    assert turnos[0].estado_whatsapp is EstadoWhatsApp.PENDIENTE_ENVIO
    assert turnos[0].fecha_hora_inicio == datetime(2026, 11, 3, 12, 0)
    assert {turno.doctor_id for turno in turnos} == {1}
    assert {turno.paciente_id for turno in turnos} == {1, 2}

    obras = _leer(destino_url, ObraSocial)
    pacientes = _leer(destino_url, Paciente)
    assert obras[0].nombre == "OSDE"
    assert pacientes[0].obra_social_id == obras[0].id
    assert pacientes[1].obra_social_id is None

    disponible = _leer(destino_url, DisponibilidadDoctor)
    assert (disponible[0].dia_semana, disponible[0].hora_inicio) == (1, time(9, 0))

    excepciones = _leer(destino_url, DisponibilidadEspecifica)
    assert excepciones[0].fecha == date(2026, 11, 3)
    assert excepciones[0].tipo is TipoDisponibilidadEspecifica.BLOQUEADO


def test_reemplazar_puede_correr_dos_veces(origen_url, destino_url):
    """Repetir el modo ``reemplazar`` no duplica ni pierde filas."""
    migrar(destino_url, origen_url, ejecutar=True)
    segunda = migrar(destino_url, origen_url, ejecutar=True)

    assert segunda.salteadas == {}
    assert segunda.destino_final == segunda.origen_filas
    assert _conteos(destino_url) == segunda.origen_filas


def test_reemplazar_deja_el_destino_igual_al_origen(origen_url, destino_url):
    """Una fila cargada sólo en el destino se elimina en el modo ``reemplazar``."""
    migrar(destino_url, origen_url, ejecutar=True)

    engine = create_engine(destino_url, future=True)
    sesion = sessionmaker(bind=engine, expire_on_commit=False)()
    sesion.add(ObraSocial(nombre="Sólo en la nube", activa=True))
    sesion.commit()
    sesion.close()
    engine.dispose()
    assert _conteos(destino_url)["obras_sociales"] == 2

    resultado = migrar(destino_url, origen_url, ejecutar=True)

    assert resultado.destino_final["obras_sociales"] == 1
    assert _conteos(destino_url) == _conteos(origen_url)


def test_agregar_no_duplica_lo_existente(origen_url, destino_url):
    """El modo ``agregar`` saltea las claves primarias ya presentes."""
    migrar(destino_url, origen_url, ejecutar=True)
    resultado = migrar(destino_url, origen_url, ejecutar=True, modo="agregar")

    assert resultado.total_insertadas == 0
    assert resultado.salteadas == resultado.origen_filas
    assert resultado.destino_final == resultado.origen_filas


def test_agregar_copia_solo_las_filas_nuevas(origen_url, destino_url):
    """Con una fila nueva en el origen, ``agregar`` copia únicamente esa."""
    migrar(destino_url, origen_url, ejecutar=True)

    engine = create_engine(origen_url, future=True)
    sesion = sessionmaker(bind=engine, expire_on_commit=False)()
    sesion.add(Doctor(nombre="Dr. Nuevo", especialidad="Endodoncia", activo=False))
    sesion.commit()
    sesion.close()
    engine.dispose()

    resultado = migrar(destino_url, origen_url, ejecutar=True, modo="agregar")

    assert resultado.insertadas["doctores"] == 1
    assert resultado.salteadas["doctores"] == 1
    assert resultado.insertadas["turnos"] == 0
    assert resultado.salteadas["turnos"] == 2
    assert _conteos(destino_url)["doctores"] == 2


def test_normalizar_url_usa_el_mismo_criterio_que_la_config():
    """Se puede pegar la cadena del proveedor tal cual (sin ``+psycopg``)."""
    for cadena in ("postgresql://u:p@host/db", "postgres://u:p@host/db"):
        esperado = "postgresql+psycopg://u:p@host/db"
        assert _normalizar_url(cadena) == esperado
        # La normalización del script y la de la app deben coincidir, o la
        # migración funcionaría y la app no (o al revés).
        assert Settings(_env_file=None, database_url=cadena).database_url == esperado

    for cadena in ("postgresql+psycopg://u:p@host/db", "sqlite:///./odontologico.db"):
        assert _normalizar_url(cadena) == cadena


def test_orden_de_tablas_respeta_las_dependencias():
    """Las tablas referenciadas se copian antes que las que las referencian."""
    orden = [tabla.name for tabla in Base.metadata.sorted_tables]

    assert set(orden) == set(TABLAS)
    assert orden.index("obras_sociales") < orden.index("pacientes")
    assert orden.index("doctores") < orden.index("turnos")
    assert orden.index("doctores") < orden.index("disponibilidad_doctores")
    assert orden.index("doctores") < orden.index("disponibilidad_especifica")


def test_no_se_puede_migrar_sobre_la_misma_base(origen_url):
    """Un destino igual al origen se rechaza (evita borrar los datos buenos)."""
    with pytest.raises(RuntimeError, match="misma base de datos"):
        migrar(origen_url, origen_url, ejecutar=True)


def test_modo_invalido(origen_url, destino_url):
    with pytest.raises(ValueError, match="Modo inválido"):
        migrar(destino_url, origen_url, modo="copiar")


def test_reajustar_secuencias_solo_aplica_en_postgresql(origen_url):
    """En SQLite no hay secuencias que reajustar (no falla ni escribe nada)."""
    engine = create_engine(origen_url, future=True)
    try:
        assert _reajustar_secuencias(engine, Base.metadata.sorted_tables) == []
    finally:
        engine.dispose()


def test_cli_migra_y_reporta(origen_url, destino_url, capsys):
    codigo = main(["--origen", origen_url, "--destino", destino_url, "--ejecutar"])
    salida = capsys.readouterr().out

    assert codigo == 0
    assert "Total de filas copiadas: 8" in salida
    assert "[SIMULACRO" not in salida
    assert set(_conteos(destino_url).values()) != {0}


def test_cli_sin_ejecutar_no_escribe(origen_url, destino_url, capsys):
    assert main(["--origen", origen_url, "--destino", destino_url]) == 0

    assert "SIMULACRO" in capsys.readouterr().out
    assert set(_conteos(destino_url).values()) == {0}


def test_cli_exige_destino(monkeypatch):
    """Sin ``--destino`` ni ``DATABASE_URL_DESTINO`` el CLI corta con error."""
    monkeypatch.delenv("DATABASE_URL_DESTINO", raising=False)

    with pytest.raises(SystemExit):
        main(["--origen", "sqlite:///no-existe.db"])


def test_modos_disponibles():
    """Los modos documentados en el encabezado del script son los reales."""
    assert MODOS == ("reemplazar", "agregar")


def test_migra_la_base_local_real_si_existe(tmp_path):
    """Copia la base local del proyecto (si está) a un destino temporal."""
    url_origen = settings.database_url
    if not url_origen.startswith("sqlite"):
        pytest.skip("DATABASE_URL no apunta a SQLite: no hay base local.")
    ruta = Path(url_origen.split("sqlite:///")[-1]).resolve()
    if not ruta.exists():
        pytest.skip(f"No existe la base local {ruta}.")

    destino = f"sqlite:///{(tmp_path / 'copia.db').as_posix()}"
    resultado = migrar(destino, url_origen, ejecutar=True)

    assert resultado.total_insertadas > 0
    assert resultado.destino_final == resultado.origen_filas
    assert _conteos(destino) == _conteos(url_origen)

