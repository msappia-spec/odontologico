"""Servicio del catálogo de categorías de pacientes.

Concentra las reglas que comparten el panel (modal *Categorías de pacientes*) y
el alta/edición de pacientes:

* ``CATEGORIAS_POR_DEFECTO``: las tres categorías históricas
  (``SIN_CATEGORIZAR``, ``EXCELENTE`` y ``MALO``). Se siembran una sola vez en
  ``init_db``; si la tabla está vacía (pruebas o base nueva) se siembran al
  primer uso para que el desplegable nunca quede vacío.
* CRUD con **baja lógica** (``activa = false``): un paciente que ya tenía la
  categoría no pierde el dato, simplemente deja de ofrecerse.
* ``validar_codigo_asignable``: el paciente sólo puede guardar una categoría
  existente y activa (``422`` si no).
* ``SIN_CATEGORIZAR`` es la categoría por defecto del esquema: no se puede
  desactivar ni cambiarle el código.
* Al **cambiar el código** de una categoría se actualizan en bloque los
  pacientes que la tenían, para no dejar códigos huérfanos.
"""

from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    ValidacionError,
)
from app.models.categoria_paciente import CategoriaPaciente
from app.models.paciente import Paciente
from app.schemas.categoria_paciente import (
    CategoriaPacienteCreate,
    CategoriaPacienteUpdate,
    normalizar_codigo_categoria,
)

#: Categoría que usa el esquema cuando el paciente no informa una.
CODIGO_POR_DEFECTO = "SIN_CATEGORIZAR"

#: Catálogo histórico: es el punto de partida del panel y el respaldo cuando la
#: tabla todavía está vacía. ``MALO`` conserva el aviso previo al turno.
CATEGORIAS_POR_DEFECTO: tuple[dict, ...] = (
    {
        "codigo": "SIN_CATEGORIZAR",
        "nombre": "Sin categorizar",
        "alerta": False,
        "activa": True,
        "orden": 1,
    },
    {
        "codigo": "EXCELENTE",
        "nombre": "Excelente",
        "alerta": False,
        "activa": True,
        "orden": 2,
    },
    {
        "codigo": "MALO",
        "nombre": "Malo",
        "alerta": True,
        "activa": True,
        "orden": 3,
    },
)


def categorias_por_defecto() -> list[CategoriaPaciente]:
    """Devuelve las categorías históricas como objetos **transitorios**.

    Al no estar agregados a una ``Session`` no se persisten por accidente: se
    usan como respaldo del panel y de las validaciones cuando la tabla está
    vacía y el sembrado no se pudo ejecutar.
    """
    return [CategoriaPaciente(**datos) for datos in CATEGORIAS_POR_DEFECTO]


def contar_categorias(db: Session) -> int:
    """Cantidad de filas del catálogo (activas e inactivas)."""
    return int(db.scalar(select(func.count()).select_from(CategoriaPaciente)) or 0)


def sembrar_categorias_por_defecto(db: Session) -> int:
    """Carga las tres categorías históricas si el catálogo está vacío.

    Devuelve cuántas filas creó (0 si ya había configuración). Es idempotente y
    la llama ``init_db`` al arrancar la aplicación. Si dos procesos arrancan a
    la vez sobre una tabla vacía, el índice único del código hace fallar al
    segundo: se revierte y se informa 0 porque el catálogo ya quedó sembrado.
    """
    if contar_categorias(db) > 0:
        return 0
    categorias = categorias_por_defecto()
    db.add_all(categorias)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return 0
    return len(categorias)


def _asegurar_categorias(db: Session) -> None:
    """Siembra el catálogo histórico si alguien lo dejó vacío.

    Ocurre en bases creadas con ``create_all`` sin pasar por ``init_db`` (por
    ejemplo las pruebas). El sembrado es idempotente, así que no pisa la
    configuración de la secretaría cuando ya hay filas.
    """
    if contar_categorias(db) == 0:
        sembrar_categorias_por_defecto(db)


def listar_categorias(
    db: Session, *, activas: Optional[bool] = None
) -> Sequence[CategoriaPaciente]:
    """Lista el catálogo ordenado; ``activas`` filtra por estado (``None`` = todas)."""
    _asegurar_categorias(db)
    stmt = select(CategoriaPaciente).order_by(
        CategoriaPaciente.orden, CategoriaPaciente.nombre
    )
    if activas is True:
        stmt = stmt.where(CategoriaPaciente.activa.is_(True))
    elif activas is False:
        stmt = stmt.where(CategoriaPaciente.activa.is_(False))
    return db.execute(stmt).scalars().all()


def mapa_por_codigo(db: Session) -> dict[str, CategoriaPaciente]:
    """Catálogo indexado por código (para resolver nombres sin N+1)."""
    return {categoria.codigo: categoria for categoria in listar_categorias(db)}


def obtener_categoria(db: Session, categoria_id: int) -> CategoriaPaciente:
    """Busca una categoría por id o lanza ``404``."""
    categoria = db.get(CategoriaPaciente, categoria_id)
    if categoria is None:
        raise RecursoNoEncontradoError(
            f"No se encontró una categoría de paciente con id={categoria_id}."
        )
    return categoria


def obtener_por_codigo(
    db: Session, codigo: Optional[str]
) -> Optional[CategoriaPaciente]:
    """Busca una categoría por código (``None`` si no existe)."""
    if not codigo:
        return None
    normalizado = normalizar_codigo_categoria(codigo)
    stmt = select(CategoriaPaciente).where(CategoriaPaciente.codigo == normalizado)
    return db.execute(stmt).scalars().first()


def validar_codigo_asignable(db: Session, codigo: str) -> CategoriaPaciente:
    """Devuelve la categoría si existe y está activa; si no, responde ``422``."""
    _asegurar_categorias(db)
    categoria = obtener_por_codigo(db, codigo)
    if categoria is None:
        raise ValidacionError(
            f"La categoría «{normalizar_codigo_categoria(codigo)}» no existe. "
            "Agregala desde «Agregar o editar categorías» en el formulario de "
            "pacientes."
        )
    if not categoria.activa:
        raise ValidacionError(
            f"La categoría «{categoria.nombre}» está dada de baja. Reactivala "
            "o elegí otra."
        )
    return categoria


def categoria_avisa(db: Session, codigo: Optional[str]) -> bool:
    """Indica si la categoría indicada dispara el aviso previo al turno."""
    categoria = obtener_por_codigo(db, codigo)
    return bool(categoria and categoria.alerta)


def _validar_codigo_unico(
    db: Session, codigo: str, *, excluir_id: Optional[int] = None
) -> None:
    """El código no puede repetirse (el índice único es la garantía final)."""
    stmt = select(CategoriaPaciente.id).where(CategoriaPaciente.codigo == codigo)
    if excluir_id is not None:
        stmt = stmt.where(CategoriaPaciente.id != excluir_id)
    if db.execute(stmt).scalars().first() is not None:
        raise ConflictoError(f"Ya existe una categoría con el código {codigo}.")


def _siguiente_orden(db: Session) -> int:
    """Orden para una categoría nueva: al final de la lista."""
    maximo = db.scalar(select(func.max(CategoriaPaciente.orden))) or 0
    return int(maximo) + 1


def _commit_codigo_unico(db: Session, codigo: str) -> None:
    """Confirma el cambio y traduce la carrera del índice único a un ``409``."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictoError(
            f"Ya existe una categoría con el código {codigo}."
        ) from exc


def crear_categoria(
    db: Session, payload: CategoriaPacienteCreate
) -> CategoriaPaciente:
    """Crea una categoría (``409`` si el código ya existe)."""
    codigo = normalizar_codigo_categoria(payload.codigo)
    _validar_codigo_unico(db, codigo)
    datos = payload.model_dump()
    datos["codigo"] = codigo
    if not datos.get("orden"):
        datos["orden"] = _siguiente_orden(db)
    categoria = CategoriaPaciente(**datos)
    db.add(categoria)
    _commit_codigo_unico(db, codigo)
    db.refresh(categoria)
    return categoria


def actualizar_categoria(
    db: Session, categoria_id: int, payload: CategoriaPacienteUpdate
) -> CategoriaPaciente:
    """Actualiza sólo los campos enviados y valida el estado final.

    Reglas especiales de la categoría por defecto (``SIN_CATEGORIZAR``): no se
    le puede cambiar el código ni darla de baja, porque el esquema la usa como
    valor de los pacientes que no informan categoría.
    """
    categoria = obtener_categoria(db, categoria_id)
    datos = payload.model_dump(exclude_unset=True)

    if "codigo" in datos:
        datos["codigo"] = normalizar_codigo_categoria(datos["codigo"])
        if (
            categoria.codigo == CODIGO_POR_DEFECTO
            and datos["codigo"] != CODIGO_POR_DEFECTO
        ):
            raise ValidacionError(
                "La categoría por defecto «Sin categorizar» no puede cambiar de "
                "código."
            )
        if datos["codigo"] != categoria.codigo:
            _validar_codigo_unico(db, datos["codigo"], excluir_id=categoria.id)

    if datos.get("activa") is False and categoria.codigo == CODIGO_POR_DEFECTO:
        raise ValidacionError(
            "La categoría por defecto «Sin categorizar» no puede darse de baja."
        )

    codigo_anterior = categoria.codigo
    for campo, valor in datos.items():
        setattr(categoria, campo, valor)

    # Si se renombró el código, los pacientes que la tenían se actualizan en
    # bloque: ``pacientes.categoria`` guarda el código, no una clave foránea.
    if categoria.codigo != codigo_anterior:
        db.execute(
            update(Paciente)
            .where(Paciente.categoria == codigo_anterior)
            .values(categoria=categoria.codigo)
        )

    _commit_codigo_unico(db, categoria.codigo)
    db.refresh(categoria)
    return categoria


def desactivar_categoria(db: Session, categoria_id: int) -> CategoriaPaciente:
    """**Baja lógica**: deja de ofrecerse, sin borrar la fila ni el dato."""
    categoria = obtener_categoria(db, categoria_id)
    if categoria.codigo == CODIGO_POR_DEFECTO:
        raise ValidacionError(
            "La categoría por defecto «Sin categorizar» no puede darse de baja."
        )
    categoria.activa = False
    db.commit()
    db.refresh(categoria)
    return categoria

