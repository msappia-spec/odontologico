"""Servicio del menú configurable del FAQ Bot de WhatsApp.

Centraliza el CRUD que consume el panel (*Bot WhatsApp*) y las funciones que usa
``whatsapp_bot_service`` para resolver qué opción contestar:

* ``opciones_por_defecto`` / ``sembrar_menu_por_defecto``: las cuatro opciones
  históricas (precio, obras sociales, ubicación y secretaría). Se siembran una
  sola vez en ``init_db``; si la tabla está vacía (pruebas o base nueva) el bot
  las usa como respaldo para no quedarse sin menú.
* ``obtener_menu_vigente``: las filas activas ordenadas por ``orden`` y número.
* CRUD con **baja lógica** (``activa = false``) y reinicio al menú por defecto.

El número es la tecla que responde el paciente y no se repite entre filas.
"""

from typing import Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    ValidacionError,
)
from app.models.enums import TipoOpcionMenu
from app.models.opcion_menu_whatsapp import OpcionMenuWhatsApp
from app.schemas.opcion_menu_whatsapp import OpcionMenuCreate, OpcionMenuUpdate

#: Menú histórico: se usa como respaldo y como punto de partida del panel.
OPCIONES_POR_DEFECTO: tuple[dict, ...] = (
    {
        "numero": "1",
        "titulo": "Precio de la consulta",
        "tipo": TipoOpcionMenu.PRECIO,
        "respuesta": None,
        "activa": True,
        "orden": 1,
    },
    {
        "numero": "2",
        "titulo": "Obras sociales con convenio",
        "tipo": TipoOpcionMenu.OBRAS_SOCIALES,
        "respuesta": None,
        "activa": True,
        "orden": 2,
    },
    {
        "numero": "3",
        "titulo": "Ubicación y horarios",
        "tipo": TipoOpcionMenu.UBICACION,
        "respuesta": None,
        "activa": True,
        "orden": 3,
    },
    {
        "numero": "4",
        "titulo": "Hablar con una secretaria",
        "tipo": TipoOpcionMenu.SECRETARIA,
        "respuesta": None,
        "activa": True,
        "orden": 4,
    },
)


def opciones_por_defecto() -> list[OpcionMenuWhatsApp]:
    """Devuelve las opciones históricas como objetos **transitorios** (sin sesión).

    Sirven para que el bot arme el mismo menú cuando la tabla todavía está vacía,
    sin escribir nada en la base. Al no estar agregados a una ``Session``, no se
    persisten por accidente.
    """
    return [OpcionMenuWhatsApp(**datos) for datos in OPCIONES_POR_DEFECTO]


def contar_opciones(db: Session) -> int:
    """Cantidad de filas del menú (activas e inactivas)."""
    return int(
        db.scalar(select(func.count()).select_from(OpcionMenuWhatsApp)) or 0
    )


def listar_opciones(
    db: Session, *, activas: Optional[bool] = None
) -> Sequence[OpcionMenuWhatsApp]:
    """Lista el menú ordenado; ``activas`` filtra por estado (``None`` = todas)."""
    stmt = select(OpcionMenuWhatsApp).order_by(
        OpcionMenuWhatsApp.orden, OpcionMenuWhatsApp.numero
    )
    if activas is True:
        stmt = stmt.where(OpcionMenuWhatsApp.activa.is_(True))
    elif activas is False:
        stmt = stmt.where(OpcionMenuWhatsApp.activa.is_(False))
    return db.execute(stmt).scalars().all()


def listar_opciones_activas(db: Session) -> Sequence[OpcionMenuWhatsApp]:
    """Sólo las opciones que el bot muestra y atiende."""
    return listar_opciones(db, activas=True)


def obtener_menu_vigente(db: Session) -> list[OpcionMenuWhatsApp]:
    """Menú que debe usar el bot en este momento.

    Si la tabla está vacía se devuelven las cuatro opciones por defecto (así una
    base recién creada, o los tests, siguen teniendo el menú histórico). Si hay
    filas pero todas están dadas de baja, se devuelve una lista vacía: la
    secretaría desactivó el menú a propósito y el bot deriva a atención manual.
    """
    if contar_opciones(db) == 0:
        return opciones_por_defecto()
    return list(listar_opciones_activas(db))


def obtener_opcion(db: Session, opcion_id: int) -> OpcionMenuWhatsApp:
    """Busca una opción por id o lanza ``404``."""
    opcion = db.get(OpcionMenuWhatsApp, opcion_id)
    if opcion is None:
        raise RecursoNoEncontradoError(
            f"No se encontró una opción de menú con id={opcion_id}."
        )
    return opcion


def _validar_numero_unico(
    db: Session, numero: str, *, excluir_id: Optional[int] = None
) -> None:
    """El dígito no puede repetirse (dos opciones se dispararían igual)."""
    stmt = select(OpcionMenuWhatsApp).where(OpcionMenuWhatsApp.numero == numero)
    if excluir_id is not None:
        stmt = stmt.where(OpcionMenuWhatsApp.id != excluir_id)
    if db.execute(stmt).scalars().first() is not None:
        raise ConflictoError(f"Ya existe una opción con el número {numero}.")


def _validar_respuesta(tipo: TipoOpcionMenu, respuesta: Optional[str]) -> None:
    """Una opción ``TEXTO`` sin mensaje no tendría nada que enviar."""
    if tipo is TipoOpcionMenu.TEXTO and not (respuesta or "").strip():
        raise ValidacionError(
            "Una opción de tipo TEXTO necesita el texto de la respuesta."
        )


def _commit_numero_unico(db: Session, numero: str) -> None:
    """Confirma el cambio y traduce la carrera del índice único a un ``409``.

    ``_validar_numero_unico`` cubre el caso normal, pero dos peticiones
    simultáneas podrían pasar la validación antes de que alguna confirme. El
    índice único de la tabla es la garantía final; acá se devuelve el mismo
    error de conflicto en lugar de un ``500``.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictoError(
            f"Ya existe una opción con el número {numero}."
        ) from exc


def crear_opcion(db: Session, payload: OpcionMenuCreate) -> OpcionMenuWhatsApp:
    """Crea una opción del menú (``409`` si el número ya existe)."""
    _validar_numero_unico(db, payload.numero)
    _validar_respuesta(payload.tipo, payload.respuesta)
    opcion = OpcionMenuWhatsApp(**payload.model_dump())
    db.add(opcion)
    _commit_numero_unico(db, payload.numero)
    db.refresh(opcion)
    return opcion


def actualizar_opcion(
    db: Session, opcion_id: int, payload: OpcionMenuUpdate
) -> OpcionMenuWhatsApp:
    """Actualiza sólo los campos enviados y valida el estado final."""
    opcion = obtener_opcion(db, opcion_id)
    datos = payload.model_dump(exclude_unset=True)

    if "numero" in datos and datos["numero"] != opcion.numero:
        _validar_numero_unico(db, datos["numero"], excluir_id=opcion.id)

    tipo_final = datos.get("tipo", opcion.tipo)
    respuesta_final = datos.get("respuesta", opcion.respuesta)
    _validar_respuesta(tipo_final, respuesta_final)

    for campo, valor in datos.items():
        setattr(opcion, campo, valor)

    _commit_numero_unico(db, opcion.numero)
    db.refresh(opcion)
    return opcion


def desactivar_opcion(db: Session, opcion_id: int) -> OpcionMenuWhatsApp:
    """**Baja lógica**: deja de mostrarse y de atenderse, sin borrar la fila."""
    opcion = obtener_opcion(db, opcion_id)
    opcion.activa = False
    db.commit()
    db.refresh(opcion)
    return opcion


def sembrar_menu_por_defecto(db: Session) -> int:
    """Carga las cuatro opciones históricas si la tabla está vacía.

    Devuelve cuántas filas creó (0 si ya había configuración). Es idempotente y
    la llama ``init_db`` al arrancar la aplicación. Si dos procesos arrancan a la
    vez sobre una base vacía, el índice único hace fallar al segundo: se revierte
    y se informa 0 porque el menú ya quedó sembrado por el otro.
    """
    if contar_opciones(db) > 0:
        return 0
    opciones = opciones_por_defecto()
    db.add_all(opciones)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return 0
    return len(opciones)


def restaurar_menu_por_defecto(db: Session) -> list[OpcionMenuWhatsApp]:
    """Borra la configuración actual y vuelve a las cuatro opciones históricas.

    Si otra petición escribe el menú entre el borrado y el ``commit`` (por
    ejemplo, dos personas usan el panel a la vez), el índice único puede rechazar
    las filas históricas. En ese caso se revierte todo —el menú anterior queda
    intacto— y se responde ``409`` para que la secretaría reintente.
    """
    db.execute(delete(OpcionMenuWhatsApp))
    db.add_all(opciones_por_defecto())
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictoError(
            "No se pudo restaurar el menú porque otra persona lo está "
            "modificando. Volvé a intentarlo."
        ) from exc
    return list(listar_opciones(db))

