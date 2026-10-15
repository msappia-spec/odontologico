"""Pruebas del seed del nomenclador odontológico de Córdoba (Módulo 3).

Verifican la lógica de ``scripts/seed_nomenclador_cba`` sobre la base SQLite en
memoria del ``conftest`` (nunca tocan ``odontologico.db``):

1. **Carga completa**: inserta los 9 códigos de la grilla con su descripción por
   capítulo y su valor base.
2. **Idempotencia**: una segunda corrida no inserta ni duplica nada.
3. **Normalización**: un código ya cargado con espacios o mayúsculas raras se
   reconoce como existente (el índice ``uq_prestacion_codigo`` no se viola).
4. **``--actualizar``**: refresca descripción/precio de los ya cargados; sin el
   flag, un precio ajustado a mano desde el panel se respeta.
5. **``--dry-run``**: informa sin escribir (y no llama a ``init_db``).
6. **Contrato de los datos**: códigos únicos y descripciones con su capítulo.
"""

import pytest

from app.models.prestacion import Prestacion
from scripts import seed_nomenclador_cba as seed


class _SesionCompartida:
    """Context manager mínimo: entrega la sesión de prueba sin cerrarla.

    ``main`` usa ``with SessionLocal() as db:``; en las pruebas se reemplaza
    ``SessionLocal`` por esto para que la sesión siga viva al salir del bloque y
    las aserciones puedan consultar la misma base.
    """

    def __init__(self, sesion):
        self._sesion = sesion

    def __enter__(self):
        return self._sesion

    def __exit__(self, *exc_info):
        return False


@pytest.fixture()
def seed_aislado(monkeypatch, db_session):
    """Apunta el seed a la sesión de prueba y neutraliza ``init_db``."""
    monkeypatch.setattr(seed, "SessionLocal", lambda: _SesionCompartida(db_session))
    monkeypatch.setattr(seed, "init_db", lambda: None)
    return db_session


def _contar(db_session) -> int:
    return db_session.query(Prestacion).count()


# --------------------------------------------------------------------------- #
# 1 y 2. Carga completa e idempotencia
# --------------------------------------------------------------------------- #


def test_carga_los_nueve_codigos(db_session):
    contadores = seed.cargar_nomenclador(db_session, detalle=False)

    assert contadores["insertadas"] == len(seed.NOMENCLADOR_CORDOBA) == 9
    assert contadores["existentes"] == 0
    assert contadores["errores"] == 0
    assert contadores["total"] == 9

    filas = {p.codigo_nomenclador: p for p in db_session.query(Prestacion).all()}
    for codigo, descripcion, valor_base in seed.NOMENCLADOR_CORDOBA:
        practica = filas[codigo]
        assert practica.descripcion == descripcion
        assert practica.valor_base == valor_base
        assert practica.activa is True


def test_segunda_corrida_no_duplica(db_session):
    seed.cargar_nomenclador(db_session, detalle=False)
    contadores = seed.cargar_nomenclador(db_session, detalle=False)

    assert contadores["insertadas"] == 0
    assert contadores["existentes"] == 9
    assert contadores["total"] == 9
    assert _contar(db_session) == 9


def test_codigos_ya_existentes_con_espacios_se_reconocen(db_session):
    """La comparación normaliza espacios y mayúsculas (idempotencia real)."""
    db_session.add(
        Prestacion(
            codigo_nomenclador=" 10100 ",
            descripcion="Cargada a mano",
            valor_base=99999.0,
            activa=True,
        )
    )
    db_session.commit()

    contadores = seed.cargar_nomenclador(db_session, detalle=False)

    assert contadores["insertadas"] == 8
    assert contadores["existentes"] == 1
    assert _contar(db_session) == 9
    # Sin ``--actualizar`` no se pisa el precio cargado por la secretaría.
    cargada = (
        db_session.query(Prestacion)
        .filter(Prestacion.codigo_nomenclador == " 10100 ")
        .one()
    )
    assert cargada.valor_base == 99999.0


# --------------------------------------------------------------------------- #
# 3 y 4. --actualizar y --dry-run
# --------------------------------------------------------------------------- #


def test_actualizar_refresca_descripcion_y_precio(db_session):
    seed.cargar_nomenclador(db_session, detalle=False)
    practica = (
        db_session.query(Prestacion)
        .filter(Prestacion.codigo_nomenclador == "20100")
        .one()
    )
    practica.descripcion = "MAL"
    practica.valor_base = 1.0
    practica.activa = False
    db_session.commit()

    contadores = seed.cargar_nomenclador(db_session, actualizar=True, detalle=False)

    assert contadores["actualizadas"] == 1
    assert contadores["insertadas"] == 0
    db_session.refresh(practica)
    assert practica.descripcion.startswith("Capítulo II: Obturación con amalgama")
    assert practica.valor_base == 32000.0
    assert practica.activa is True


def test_actualizar_no_cambia_lo_que_ya_coincide(db_session):
    seed.cargar_nomenclador(db_session, detalle=False)
    contadores = seed.cargar_nomenclador(db_session, actualizar=True, detalle=False)

    assert contadores["actualizadas"] == 0
    assert contadores["existentes"] == 9


def test_dry_run_no_escribe_en_la_base(seed_aislado):
    assert seed.main(["--dry-run", "--sin-detalle"]) == 0
    assert _contar(seed_aislado) == 0


def test_main_carga_de_verdad(seed_aislado):
    assert seed.main(["--sin-detalle"]) == 0
    assert _contar(seed_aislado) == 9
    # Y una segunda corrida por la interfaz de consola sigue siendo idempotente.
    assert seed.main(["--sin-detalle"]) == 0
    assert _contar(seed_aislado) == 9


def test_main_devuelve_error_si_falla_la_base(monkeypatch, capsys):
    from sqlalchemy.exc import OperationalError

    class _Explota:
        def __enter__(self):
            raise OperationalError("select 1", {}, Exception("sin conexión"))

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(seed, "SessionLocal", _Explota)
    monkeypatch.setattr(seed, "init_db", lambda: None)

    assert seed.main(["--sin-detalle"]) == 1
    assert "No se pudo conectar" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 5. Contrato de los datos de la grilla
# --------------------------------------------------------------------------- #


def test_contrato_del_nomenclador():
    codigos = [codigo for codigo, _, _ in seed.NOMENCLADOR_CORDOBA]

    assert len(codigos) == len(set(codigos)), "un código repetido rompería el único"
    assert set(codigos) == {
        "10100",
        "10400",
        "20100",
        "21600",
        "30100",
        "30200",
        "50100",
        "10010",
        "10030",
    }
    for codigo, descripcion, valor_base in seed.NOMENCLADOR_CORDOBA:
        assert codigo.isdigit(), f"el código {codigo} debe ser numérico"
        assert descripcion.startswith("Capítulo "), descripcion
        assert len(descripcion) <= 200, "descripcion es String(200)"
        assert valor_base > 0, f"{codigo} sin valor base orientativo"


def test_no_choca_con_los_codigos_del_seed_de_ejemplo():
    """El seed de ejemplo usa ``01.01``; el nomenclador, 5 dígitos: no se pisan."""
    from scripts.seed import PRESTACIONES

    codigos_ejemplo = {codigo for codigo, _, _ in PRESTACIONES}
    codigos_nomenclador = {codigo for codigo, _, _ in seed.NOMENCLADOR_CORDOBA}

    assert codigos_ejemplo.isdisjoint(codigos_nomenclador)


# Descripciones oficiales de la grilla (contrato del pedido): si alguien edita la
# constante por accidente, esta prueba lo detecta.
DESCRIPCIONES_OFICIALES = {
    "10100": "Capítulo I: Consulta, examen, diagnóstico y fichado",
    "10400": "Capítulo I: Consulta de urgencia (diurna o nocturna)",
    "20100": "Capítulo II: Obturación con amalgama cavidad simple",
    "21600": "Capítulo II: Restauración con resinas compuestas (Composite estético)",
    "30100": "Capítulo III: Tratamiento unirradicular (Conducto en diente simple)",
    "30200": "Capítulo III: Tratamiento multirradicular (Conducto dos o más raíces)",
    "50100": "Capítulo V: Tartrectomía y cepillado mecánico "
    "(Limpieza y eliminación de sarro)",
    "10010": "Capítulo X: Extracción dentaria simple (Cirugía general)",
    "10030": "Capítulo X: Extracción de diente retenido (Cirugía compleja)",
}


def test_descripciones_oficiales_intactas():
    textos = {
        codigo: descripcion for codigo, descripcion, _ in seed.NOMENCLADOR_CORDOBA
    }

    assert textos == DESCRIPCIONES_OFICIALES
