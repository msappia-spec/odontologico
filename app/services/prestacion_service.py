"""Servicio del catálogo de prestaciones (prácticas facturables, Módulo 3).

Reglas de negocio, en línea con ``obra_social_service``:

* El **código de nomenclador es único**. Se defiende en dos capas: una
  verificación previa sin distinguir mayúsculas (mensaje claro, ``409``) y el
  índice único ``uq_prestacion_codigo`` de la base de datos (condición de
  carrera), traducido también a ``ConflictoError``.
* La baja es **lógica**: ``desactivar_prestacion`` marca ``activa = False`` en
  lugar de borrar la fila, porque los aranceles cargados y las facturas ya
  emitidas (``detalles_movimiento_caja``) la referencian. La práctica deja de
  ofrecerse al facturar y se puede reactivar desde el panel.
"""

import base64
import binascii
import logging
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.aranceles_pdf import ArancelPdf, leer_aranceles_pdf
from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    ValidacionError,
)
from app.core.money import redondear_monto
from app.core.planilla_aranceles import canonizar_codigo
from app.models.prestacion import Prestacion
from app.schemas.prestacion import (
    ImportarPrestacionesPdfFilaRead,
    ImportarPrestacionesPdfRead,
    ImportarPrestacionesPdfRequest,
    PrestacionCreate,
    PrestacionUpdate,
)

logger = logging.getLogger("app.services.prestaciones")


def obtener_prestacion(db: Session, prestacion_id: int) -> Prestacion:
    """Devuelve la práctica indicada o lanza ``RecursoNoEncontradoError``."""
    prestacion = db.get(Prestacion, prestacion_id)
    if prestacion is None:
        raise RecursoNoEncontradoError(
            f"No existe una prestación con id={prestacion_id}."
        )
    return prestacion


def obtener_prestacion_activa(db: Session, prestacion_id: int) -> Prestacion:
    """Devuelve la práctica sólo si está vigente (se usa al facturar).

    Una práctica dada de baja se comporta como inexistente para la facturación:
    responde ``404`` con un mensaje que explica que está inactiva, para que la
    secretaría la reactive en lugar de cobrar una práctica retirada.
    """
    prestacion = obtener_prestacion(db, prestacion_id)
    if not prestacion.activa:
        raise RecursoNoEncontradoError(
            f"La prestación id={prestacion_id} ({prestacion.codigo_nomenclador}) "
            "está dada de baja y no se puede facturar."
        )
    return prestacion


def _validar_codigo_libre(
    db: Session, codigo: str, excluir_id: Optional[int] = None
) -> None:
    """Comprueba que no exista otra práctica con el mismo código."""
    stmt = select(Prestacion.id).where(
        func.lower(Prestacion.codigo_nomenclador) == codigo.lower()
    )
    if excluir_id is not None:
        stmt = stmt.where(Prestacion.id != excluir_id)
    existente = db.execute(stmt).scalars().first()
    if existente is not None:
        raise ConflictoError(
            f"Ya existe una prestación con el código {codigo} (id={existente})."
        )


def listar_prestaciones(
    db: Session,
    activas: Optional[bool] = None,
    buscar: Optional[str] = None,
) -> Sequence[Prestacion]:
    """Devuelve el catálogo ordenado por código, con filtros opcionales.

    :param activas: ``True`` sólo vigentes, ``False`` sólo dadas de baja,
        ``None`` (por defecto) todas.
    :param buscar: búsqueda parcial por código o descripción (sin distinguir
        mayúsculas).
    """
    stmt = select(Prestacion).order_by(Prestacion.codigo_nomenclador)
    if activas is not None:
        stmt = stmt.where(Prestacion.activa == activas)
    if buscar:
        texto = f"%{buscar.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Prestacion.codigo_nomenclador).like(texto)
            | func.lower(Prestacion.descripcion).like(texto)
        )
    return db.execute(stmt).scalars().all()


def crear_prestacion(db: Session, data: PrestacionCreate) -> Prestacion:
    """Crea una práctica validando que el código no esté repetido."""
    _validar_codigo_libre(db, data.codigo_nomenclador)

    prestacion = Prestacion(**data.model_dump())
    db.add(prestacion)
    try:
        db.commit()
    except IntegrityError as exc:  # índice único uq_prestacion_codigo
        db.rollback()
        logger.warning("IntegrityError al crear prestación: %s", exc.orig)
        raise ConflictoError(
            f"Ya existe una prestación con el código {data.codigo_nomenclador}."
        ) from exc
    db.refresh(prestacion)
    logger.info(
        "Prestación creada id=%s codigo=%s",
        prestacion.id,
        prestacion.codigo_nomenclador,
    )
    return prestacion


def actualizar_prestacion(
    db: Session, prestacion_id: int, data: PrestacionUpdate
) -> Prestacion:
    """Actualiza parcialmente una práctica (``PUT``/``PATCH``).

    Sólo se aplican los campos enviados; si llega un ``codigo_nomenclador``
    distinto se revalida su unicidad contra el resto del catálogo. ``activa``
    forma parte del payload, así que el mismo ``PATCH`` sirve para dar de baja
    (``{"activa": false}``) y para reactivar.
    """
    prestacion = obtener_prestacion(db, prestacion_id)
    cambios = data.model_dump(exclude_unset=True)

    if cambios.get("codigo_nomenclador"):
        _validar_codigo_libre(
            db, cambios["codigo_nomenclador"], excluir_id=prestacion.id
        )

    for campo, valor in cambios.items():
        setattr(prestacion, campo, valor)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.warning(
            "IntegrityError al actualizar prestación %s: %s", prestacion_id, exc.orig
        )
        raise ConflictoError(
            "Ya existe una prestación con el código "
            f"{cambios.get('codigo_nomenclador')}."
        ) from exc
    db.refresh(prestacion)
    logger.info("Prestación actualizada id=%s", prestacion.id)
    return prestacion


def desactivar_prestacion(db: Session, prestacion_id: int) -> Prestacion:
    """Da de baja la práctica (**borrado lógico**: ``activa = False``).

    La fila se conserva para no romper los aranceles ni las facturas que la
    referencian, pero la práctica deja de ofrecerse al facturar. Es idempotente.
    """
    prestacion = obtener_prestacion(db, prestacion_id)

    if prestacion.activa:
        prestacion.activa = False
        db.commit()
        db.refresh(prestacion)
        logger.info("Prestación desactivada id=%s", prestacion.id)
    else:
        logger.info("Prestación id=%s ya estaba inactiva", prestacion.id)
    return prestacion


# --------------------------------------------------------------------------- #
# Importador del PDF de aranceles del Colegio
# --------------------------------------------------------------------------- #
def _bytes_de_pdf(data: ImportarPrestacionesPdfRequest) -> bytes:
    """Decodifica el PDF que llega en base64 (o como data URL)."""
    texto = data.contenido_base64.strip()
    if texto.startswith("data:") and "," in texto:
        # Data URL (``data:application/pdf;base64,AAAA``): el panel manda sólo
        # el payload, pero aceptarlo hace tolerante el endpoint.
        texto = texto.split(",", 1)[1]
    # Algunos lectores agregan saltos de línea al base64: se descartan antes de
    # validar, para aceptarlos sin dejar pasar caracteres ajenos al alfabeto.
    texto = "".join(texto.split())
    try:
        return base64.b64decode(texto, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidacionError(
            "El PDF llegó mal codificado: volvé a subir el archivo."
        ) from exc


def _catalogo_por_codigo(db: Session) -> dict[str, Prestacion]:
    """Indexa el catálogo por el código *canonizado* (sin puntos ni mayúsculas).

    El PDF del Colegio usa ``101``, ``40101`` y ``11.02``, mientras que el
    catálogo puede tener el mismo código con otro formato. ``canonizar_codigo``
    (la misma función del importador de planillas) evita duplicados por esa
    diferencia.
    """
    catalogo: dict[str, Prestacion] = {}
    for prestacion in db.execute(select(Prestacion)).scalars():
        catalogo.setdefault(
            canonizar_codigo(prestacion.codigo_nomenclador), prestacion
        )
    return catalogo


def _fila_reporte_pdf(
    fila: ArancelPdf,
    estado: str,
    prestacion: Optional[Prestacion],
    mensaje: Optional[str],
) -> ImportarPrestacionesPdfFilaRead:
    """Arma el renglón del reporte para una práctica leída del PDF."""
    return ImportarPrestacionesPdfFilaRead(
        pagina=fila.pagina,
        linea=fila.linea,
        codigo=fila.codigo,
        descripcion=fila.descripcion,
        valor_base=fila.valor,
        prestacion_id=prestacion.id if prestacion is not None else None,
        estado=estado,  # type: ignore[arg-type]
        sin_precio=fila.valor is None,
        mensaje=mensaje,
    )


def importar_prestaciones_pdf(
    db: Session, data: ImportarPrestacionesPdfRequest
) -> ImportarPrestacionesPdfRead:
    """Crea o actualiza el catálogo con el PDF de aranceles del Colegio.

    Cada código se busca en el catálogo **sin distinguir puntos ni mayúsculas**
    (``11.02`` y ``1102`` son la misma práctica). Si no existe se crea; si
    existe se actualizan la descripción y el honorario. Una práctica que en el
    PDF no trae precio conserva el ``valor_base`` que ya tenía, y al crearla
    queda en ``0`` para cargarlo a mano.

    Con ``simular=True`` se devuelve el mismo reporte sin escribir nada. Las
    filas con problemas no cortan la importación: vuelven con estado ``ERROR``.
    """
    filas = leer_aranceles_pdf(_bytes_de_pdf(data))
    catalogo = _catalogo_por_codigo(db)
    detalle: List[Tuple[ArancelPdf, str, Optional[Prestacion], Optional[str]]] = []
    contadores = {"NUEVO": 0, "ACTUALIZADO": 0, "SIN_CAMBIOS": 0, "ERROR": 0}
    vistos: dict[str, ArancelPdf] = {}
    hubo_cambios = False

    for fila in filas:
        clave = canonizar_codigo(fila.codigo)
        anterior = vistos.get(clave)
        if anterior is not None:
            contadores["ERROR"] += 1
            detalle.append(
                (
                    fila,
                    "ERROR",
                    None,
                    (
                        f"El código {fila.codigo} ya apareció en la página "
                        f"{anterior.pagina}, línea {anterior.linea}: se conserva "
                        "la primera aparición."
                    ),
                )
            )
            continue
        vistos[clave] = fila

        prestacion = catalogo.get(clave)
        if prestacion is None:
            if not data.simular:
                prestacion = Prestacion(
                    codigo_nomenclador=fila.codigo.upper(),
                    descripcion=fila.descripcion,
                    valor_base=redondear_monto(fila.valor or 0),
                    activa=True,
                )
                db.add(prestacion)
                catalogo[clave] = prestacion
                hubo_cambios = True
            contadores["NUEVO"] += 1
            detalle.append((fila, "NUEVO", prestacion, None))
            continue

        cambia_descripcion = prestacion.descripcion.strip() != fila.descripcion
        cambia_valor = (
            fila.valor is not None
            and redondear_monto(prestacion.valor_base)
            != redondear_monto(fila.valor)
        )
        if not cambia_descripcion and not cambia_valor:
            contadores["SIN_CAMBIOS"] += 1
            detalle.append((fila, "SIN_CAMBIOS", prestacion, None))
            continue

        if not data.simular:
            if cambia_descripcion:
                prestacion.descripcion = fila.descripcion
            if cambia_valor and fila.valor is not None:
                prestacion.valor_base = redondear_monto(fila.valor)
            hubo_cambios = True
        contadores["ACTUALIZADO"] += 1
        detalle.append((fila, "ACTUALIZADO", prestacion, None))

    if hubo_cambios:
        try:
            db.flush()  # asigna los id de las prácticas nuevas antes de reportar
            db.commit()
        except IntegrityError as exc:  # índice único uq_prestacion_codigo
            db.rollback()
            logger.warning(
                "IntegrityError al importar el PDF de aranceles: %s", exc.orig
            )
            raise ConflictoError(
                "Alguna de esas prácticas se cargó al mismo tiempo desde otro "
                "lado: volvé a intentar la importación."
            ) from exc
        logger.info(
            "Prestaciones importadas desde PDF: filas=%s creados=%s "
            "actualizados=%s errores=%s",
            len(filas),
            contadores["NUEVO"],
            contadores["ACTUALIZADO"],
            contadores["ERROR"],
        )

    return ImportarPrestacionesPdfRead(
        simulado=data.simular,
        nombre_archivo=data.nombre_archivo,
        filas_leidas=len(filas),
        creados=contadores["NUEVO"],
        actualizados=contadores["ACTUALIZADO"],
        sin_cambios=contadores["SIN_CAMBIOS"],
        sin_precio=sum(fila.valor is None for fila in filas),
        errores=contadores["ERROR"],
        detalle=[
            _fila_reporte_pdf(fila, estado, prestacion, mensaje)
            for fila, estado, prestacion, mensaje in detalle
        ],
    )
