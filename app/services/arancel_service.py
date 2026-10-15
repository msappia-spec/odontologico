"""Servicio de aranceles por obra social (Módulo 3).

Un arancel es el acuerdo económico con una cobertura para **una** práctica:
``monto_cubre_os`` (lo que paga la obra social) y ``monto_coseguro_paciente``
(lo que queda para cobrar en el mostrador). El servicio concentra:

* El CRUD del acuerdo (``POST /obras-sociales/{id}/aranceles`` y
  ``PATCH/DELETE /aranceles/{id}``), validando que la cobertura y la práctica
  existan y que no haya dos aranceles para el mismo par (``409``,
  ``uq_arancel_os_prestacion``).
* :func:`resolver_arancel`, que es la consulta que usa la facturación para
  traducir una práctica realizada en los dos importes de la visita.
* La pantalla **Convenios**: :func:`convenio_de_obra_social` arma la grilla
  completa (cada práctica del nomenclador con lo que cubre la cobertura y el
  **coseguro sugerido**, ``valor_base - monto_cubre_os``) y
  :func:`guardar_convenio` la persiste en lote.
* El **importador masivo** de precios (:func:`importar_aranceles`) y la
  **plantilla** de dos columnas (:func:`plantilla_aranceles`) que la secretaría
  completa desde Excel.
"""

import base64
import binascii
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictoError,
    RecursoNoEncontradoError,
    ValidacionError,
)
from app.core.money import redondear_monto, sumar_montos
from app.core.planilla_aranceles import (
    canonizar_codigo,
    generar_csv,
    generar_xlsx,
    leer_planilla,
    nombre_archivo_seguro,
)
from app.models.arancel_obra_social import ArancelObraSocial
from app.models.prestacion import Prestacion
from app.schemas.arancel import (
    ArancelObraSocialCreate,
    ArancelObraSocialUpdate,
    ConvenioFilaRead,
    ConvenioObraSocialRead,
    GuardarConvenioRequest,
    ImportarArancelesFilaRead,
    ImportarArancelesRead,
    ImportarArancelesRequest,
)
from app.services import obra_social_service, prestacion_service

logger = logging.getLogger("app.services.aranceles")


def obtener_arancel(db: Session, arancel_id: int) -> ArancelObraSocial:
    """Devuelve el arancel indicado o lanza ``RecursoNoEncontradoError``."""
    arancel = db.get(ArancelObraSocial, arancel_id)
    if arancel is None:
        raise RecursoNoEncontradoError(f"No existe un arancel con id={arancel_id}.")
    return arancel


def listar_aranceles(
    db: Session,
    obra_social_id: Optional[int] = None,
    prestacion_id: Optional[int] = None,
) -> Sequence[ArancelObraSocial]:
    """Lista los aranceles configurados, ordenados por código de práctica.

    Los filtros son acumulativos y opcionales: por cobertura (``obra_social_id``)
    y/o por práctica (``prestacion_id``).
    """
    stmt = (
        select(ArancelObraSocial)
        .join(ArancelObraSocial.prestacion)
        .order_by(ArancelObraSocial.obra_social_id, Prestacion.codigo_nomenclador)
    )
    if obra_social_id is not None:
        stmt = stmt.where(ArancelObraSocial.obra_social_id == obra_social_id)
    if prestacion_id is not None:
        stmt = stmt.where(ArancelObraSocial.prestacion_id == prestacion_id)
    return db.execute(stmt).scalars().all()


def _validar_par_libre(
    db: Session,
    obra_social_id: int,
    prestacion_id: int,
    excluir_id: Optional[int] = None,
) -> None:
    """Comprueba que la cobertura no tenga ya un arancel para esa práctica."""
    stmt = select(ArancelObraSocial.id).where(
        ArancelObraSocial.obra_social_id == obra_social_id,
        ArancelObraSocial.prestacion_id == prestacion_id,
    )
    if excluir_id is not None:
        stmt = stmt.where(ArancelObraSocial.id != excluir_id)
    existente = db.execute(stmt).scalars().first()
    if existente is not None:
        raise ConflictoError(
            "Esa obra social ya tiene un arancel cargado para la prestación "
            f"(arancel id={existente}): editalo en lugar de crear otro."
        )



def crear_arancel(
    db: Session, obra_social_id: int, data: ArancelObraSocialCreate
) -> ArancelObraSocial:
    """Carga el arancel de una práctica para una cobertura.

    Valida que la obra social y la práctica existan (``404``) y que el par no
    esté repetido (``409``).
    """
    obra_social_service.obtener_obra_social(db, obra_social_id)
    prestacion_service.obtener_prestacion(db, data.prestacion_id)
    _validar_par_libre(db, obra_social_id, data.prestacion_id)

    arancel = ArancelObraSocial(obra_social_id=obra_social_id, **data.model_dump())
    db.add(arancel)
    try:
        db.commit()
    except IntegrityError as exc:  # índice único uq_arancel_os_prestacion
        db.rollback()
        logger.warning("IntegrityError al crear arancel: %s", exc.orig)
        raise ConflictoError(
            "Esa obra social ya tiene un arancel cargado para la prestación."
        ) from exc
    db.refresh(arancel)
    logger.info(
        "Arancel creado id=%s obra_social_id=%s prestacion_id=%s",
        arancel.id,
        obra_social_id,
        arancel.prestacion_id,
    )
    return arancel


def actualizar_arancel(
    db: Session, arancel_id: int, data: ArancelObraSocialUpdate
) -> ArancelObraSocial:
    """Actualiza los montos de un arancel (``PUT``/``PATCH``).

    Sólo se aplican los campos enviados; el par (cobertura, práctica) no cambia.
    """
    arancel = obtener_arancel(db, arancel_id)
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(arancel, campo, valor)
    db.commit()
    db.refresh(arancel)
    logger.info("Arancel actualizado id=%s", arancel.id)
    return arancel


def reemplazar_arancel(
    db: Session, arancel_id: int, data: ArancelObraSocialCreate
) -> ArancelObraSocial:
    """Reemplaza los montos de un arancel (``PUT`` clásico).

    El par (cobertura, práctica) **no** se puede cambiar: el arancel identifica
    ese par. Enviar otra ``prestacion_id`` responde **422** (para el nuevo par se
    carga otro arancel).
    """
    arancel = obtener_arancel(db, arancel_id)
    if data.prestacion_id != arancel.prestacion_id:
        raise ValidacionError(
            "El arancel ya está asociado a otra prestación: para cambiarla, "
            "eliminá este arancel y cargá uno nuevo."
        )
    arancel.monto_cubre_os = data.monto_cubre_os
    arancel.monto_coseguro_paciente = data.monto_coseguro_paciente
    db.commit()
    db.refresh(arancel)
    logger.info("Arancel reemplazado id=%s", arancel.id)
    return arancel


def eliminar_arancel(db: Session, arancel_id: int) -> None:
    """Borra un arancel: es **configuración**, no un comprobante.

    A diferencia de las prácticas y las coberturas (que tienen baja lógica), el
    arancel es una tabla de precios: se puede borrar y volver a cargar. Las
    facturas ya emitidas conservan sus importes congelados en el detalle, así
    que borrar el arancel no altera ninguna liquidación pasada.
    """
    arancel = obtener_arancel(db, arancel_id)
    db.delete(arancel)
    db.commit()
    logger.info(
        "Arancel eliminado id=%s obra_social_id=%s prestacion_id=%s",
        arancel_id,
        arancel.obra_social_id,
        arancel.prestacion_id,
    )


def resolver_arancel(
    db: Session, obra_social_id: Optional[int], prestacion_id: int
) -> Optional[ArancelObraSocial]:
    """Devuelve el arancel del par (cobertura, práctica), si existe.

    Un paciente **sin** cobertura (``obra_social_id is None``) no tiene arancel:
    se factura a valor de referencia.
    """
    if obra_social_id is None:
        return None
    stmt = select(ArancelObraSocial).where(
        ArancelObraSocial.obra_social_id == obra_social_id,
        ArancelObraSocial.prestacion_id == prestacion_id,
    )
    return db.execute(stmt).scalars().first()


def mapa_aranceles(
    db: Session, obra_social_id: Optional[int]
) -> Dict[int, ArancelObraSocial]:
    """Índice ``prestacion_id -> arancel`` de una cobertura (una sola consulta).

    Lo usa :mod:`app.services.facturacion_service` para resolver todas las
    prácticas del formulario de cobro sin N consultas.
    """
    if obra_social_id is None:
        return {}
    return {
        arancel.prestacion_id: arancel
        for arancel in listar_aranceles(db, obra_social_id=obra_social_id)
    }


# --------------------------------------------------------------------------- #
# Convenios: la grilla completa de precios de una cobertura
# --------------------------------------------------------------------------- #
def coseguro_sugerido(valor_base: float, monto_cubre_os: float) -> float:
    """Coseguro que le queda al paciente: arancel base **menos** lo que cubre la OS.

    Es la regla que el panel muestra en vivo (``Coseguro sugerido``) y la que
    aplica el importador a cada código de la planilla. Nunca es negativo: si la
    cobertura paga más que el arancel de referencia, el mostrador no cobra nada.
    """
    diferencia = Decimal(str(valor_base or 0)) - Decimal(str(monto_cubre_os or 0))
    if diferencia <= 0:
        return 0.0
    return redondear_monto(diferencia)


def _catalogo_por_codigo(db: Session) -> Dict[str, Prestacion]:
    """Índice ``código canonizado -> práctica activa`` del nomenclador.

    Se resuelve con una sola consulta: el importador traduce así toda la planilla
    (cientos de códigos) sin encadenar una consulta por fila.
    """
    return {
        canonizar_codigo(prestacion.codigo_nomenclador): prestacion
        for prestacion in prestacion_service.listar_prestaciones(db, activas=True)
    }


def _fila_convenio(
    prestacion: Prestacion, arancel: Optional[ArancelObraSocial]
) -> ConvenioFilaRead:
    """Arma la fila de la grilla de convenios de una práctica."""
    cubre = arancel.monto_cubre_os if arancel is not None else 0.0
    coseguro = arancel.monto_coseguro_paciente if arancel is not None else 0.0
    return ConvenioFilaRead(
        prestacion_id=prestacion.id,
        codigo_nomenclador=prestacion.codigo_nomenclador,
        descripcion=prestacion.descripcion,
        valor_base=redondear_monto(prestacion.valor_base),
        arancel_id=arancel.id if arancel is not None else None,
        monto_cubre_os=redondear_monto(cubre),
        monto_coseguro_paciente=redondear_monto(coseguro),
        coseguro_sugerido=coseguro_sugerido(prestacion.valor_base, cubre),
        monto_total=sumar_montos([cubre, coseguro]),
        cargado=arancel is not None,
    )


def convenio_de_obra_social(db: Session, obra_social_id: int) -> ConvenioObraSocialRead:
    """Devuelve la grilla de precios de una cobertura (todas las prácticas activas).

    Es la pantalla *Convenios*: para cada práctica del nomenclador viajan el
    arancel base (referencia del Colegio), lo que la cobertura tiene cargado, el
    coseguro guardado y el **coseguro sugerido** (``valor_base - monto que cubre
    la OS``) que el panel recalcula en vivo. Los totales suman el arancel base, lo
    que cubre la OS y el coseguro sugerido de toda la grilla. Responde **404** si
    la cobertura no existe.
    """
    obra_social = obra_social_service.obtener_obra_social(db, obra_social_id)
    aranceles = mapa_aranceles(db, obra_social_id)
    filas = [
        _fila_convenio(prestacion, aranceles.get(prestacion.id))
        for prestacion in prestacion_service.listar_prestaciones(db, activas=True)
    ]
    return ConvenioObraSocialRead(
        obra_social_id=obra_social.id,
        obra_social_nombre=obra_social.nombre,
        total_prestaciones=len(filas),
        total_cargadas=sum(1 for fila in filas if fila.cargado),
        total_valor_base=sumar_montos(fila.valor_base for fila in filas),
        total_cubre_os=sumar_montos(fila.monto_cubre_os for fila in filas),
        total_coseguro=sumar_montos(fila.coseguro_sugerido for fila in filas),
        filas=filas,
    )


def guardar_convenio(
    db: Session, obra_social_id: int, data: GuardarConvenioRequest
) -> ConvenioObraSocialRead:
    """Guarda en lote los precios del convenio (``PUT`` de la pantalla *Convenios*).

    Para cada fila crea el arancel que falte y actualiza los montos del existente.
    Cuando el payload no trae ``monto_coseguro_paciente`` se guarda el **coseguro
    sugerido** (:func:`coseguro_sugerido`), así la grilla del panel y el
    importador aplican exactamente la misma regla.

    La grilla se valida **antes** de escribir y se confirma en una sola
    transacción: o se guarda completa o no se guarda nada. Responde **404** si la
    cobertura o alguna práctica no existen (o la práctica está dada de baja) y
    **422** si los montos son negativos.
    """
    obra_social = obra_social_service.obtener_obra_social(db, obra_social_id)
    aranceles = mapa_aranceles(db, obra_social_id)

    # Primera pasada: resolver y validar toda la grilla sin tocar la base.
    planes: List[Tuple[Prestacion, float, float]] = []
    for fila in data.filas:
        prestacion = prestacion_service.obtener_prestacion_activa(
            db, fila.prestacion_id
        )
        coseguro = fila.monto_coseguro_paciente
        if coseguro is None:
            coseguro = coseguro_sugerido(prestacion.valor_base, fila.monto_cubre_os)
        planes.append((prestacion, fila.monto_cubre_os, coseguro))

    # Segunda pasada: aplicar los cambios (una sola transacción).
    creados = actualizados = sin_cambios = 0
    for prestacion, cubre, coseguro in planes:
        arancel = aranceles.get(prestacion.id)
        if arancel is None:
            arancel = ArancelObraSocial(
                obra_social_id=obra_social.id,
                prestacion_id=prestacion.id,
                monto_cubre_os=cubre,
                monto_coseguro_paciente=coseguro,
            )
            db.add(arancel)
            aranceles[prestacion.id] = arancel
            creados += 1
        elif (
            redondear_monto(arancel.monto_cubre_os) != cubre
            or redondear_monto(arancel.monto_coseguro_paciente) != coseguro
        ):
            arancel.monto_cubre_os = cubre
            arancel.monto_coseguro_paciente = coseguro
            actualizados += 1
        else:
            sin_cambios += 1

    try:
        db.commit()
    except IntegrityError as exc:  # índice único uq_arancel_os_prestacion
        db.rollback()
        logger.warning(
            "IntegrityError al guardar el convenio de %s: %s", obra_social_id, exc.orig
        )
        raise ConflictoError(
            "Se cargó otro precio para una de esas prácticas al mismo tiempo: "
            "actualizá la grilla y volvé a guardar."
        ) from exc

    logger.info(
        "Convenio guardado obra_social_id=%s creados=%s actualizados=%s sin_cambios=%s",
        obra_social_id,
        creados,
        actualizados,
        sin_cambios,
    )
    convenio = convenio_de_obra_social(db, obra_social_id)
    convenio.creados = creados
    convenio.actualizados = actualizados
    convenio.sin_cambios = sin_cambios
    return convenio


# --------------------------------------------------------------------------- #
# Importador masivo de precios (planilla estándar)
# --------------------------------------------------------------------------- #
def _bytes_de_payload(data: ImportarArancelesRequest) -> bytes:
    """Devuelve los bytes de la planilla subida (base64 o texto plano)."""
    if data.contenido_base64:
        texto = data.contenido_base64.strip()
        if texto.startswith("data:") and "," in texto:
            # Data URL (``data:...;base64,AAAA``): el panel manda sólo el payload,
            # pero aceptarlo hace tolerante el endpoint ante otros clientes.
            texto = texto.split(",", 1)[1]
        try:
            return base64.b64decode(texto)
        except (binascii.Error, ValueError) as exc:
            raise ValidacionError(
                "El archivo llegó mal codificado: volvé a subir la planilla."
            ) from exc
    return (data.contenido or "").encode("utf-8")


def _fila_reporte(
    linea: int,
    codigo: str,
    prestacion: Optional[Prestacion],
    monto_cubre_os: Optional[float],
    monto_coseguro: Optional[float],
    estado: str,
    mensaje: Optional[str],
) -> ImportarArancelesFilaRead:
    """Arma el renglón del reporte de importación de una fila de la planilla."""
    return ImportarArancelesFilaRead(
        linea=linea,
        codigo=codigo,
        prestacion_id=prestacion.id if prestacion is not None else None,
        descripcion=prestacion.descripcion if prestacion is not None else None,
        monto_cubre_os=monto_cubre_os,
        monto_coseguro_paciente=monto_coseguro,
        estado=estado,  # type: ignore[arg-type]
        mensaje=mensaje,
    )


def importar_aranceles(
    db: Session, obra_social_id: int, data: ImportarArancelesRequest
) -> ImportarArancelesRead:
    """Importa los precios de una planilla (``codigo`` + ``precio_cobertura``).

    Traduce cada código al **nomenclador base de Córdoba** (``Prestacion``, sin
    distinguir puntos ni mayúsculas) y crea o actualiza el arancel de la cobertura
    con el **coseguro sugerido** (``valor_base - precio_cobertura``), la misma
    regla que usa la pantalla *Convenios*.

    Una fila con problemas **no** cancela el resto: vuelve en el reporte con su
    número de línea (``ERROR``) o marcada como ``IGNORADA`` si no trae precio. Con
    ``simular=True`` se devuelve el mismo reporte sin escribir nada (vista previa
    del panel). Responde **404** si la cobertura no existe y **422** si el archivo
    no se puede leer (vacío, demasiado grande, formato desconocido o sin filas).
    """
    obra_social = obra_social_service.obtener_obra_social(db, obra_social_id)
    filas_planilla = leer_planilla(_bytes_de_payload(data), data.nombre_archivo)
    catalogo = _catalogo_por_codigo(db)
    aranceles = mapa_aranceles(db, obra_social_id)

    detalle: List[ImportarArancelesFilaRead] = []
    contadores = {
        "NUEVO": 0,
        "ACTUALIZADO": 0,
        "SIN_CAMBIOS": 0,
        "IGNORADA": 0,
        "ERROR": 0,
    }
    hubo_cambios = False

    for fila in filas_planilla:
        if fila.error is not None:
            contadores["ERROR"] += 1
            detalle.append(
                _fila_reporte(
                    fila.linea, fila.codigo, None, None, None, "ERROR", fila.error
                )
            )
            continue

        prestacion = catalogo.get(canonizar_codigo(fila.codigo))
        if prestacion is None:
            contadores["ERROR"] += 1
            detalle.append(
                _fila_reporte(
                    fila.linea,
                    fila.codigo,
                    None,
                    None,
                    None,
                    "ERROR",
                    "el código no está en el nomenclador de Córdoba: "
                    "revisá la planilla.",
                )
            )
            continue

        if fila.monto is None:
            contadores["IGNORADA"] += 1
            detalle.append(
                _fila_reporte(
                    fila.linea,
                    prestacion.codigo_nomenclador,
                    prestacion,
                    None,
                    None,
                    "IGNORADA",
                    "sin precio en la planilla: la fila se omite.",
                )
            )
            continue

        coseguro = coseguro_sugerido(prestacion.valor_base, fila.monto)
        arancel = aranceles.get(prestacion.id)
        if arancel is None:
            estado = "NUEVO"
            if not data.simular:
                arancel = ArancelObraSocial(
                    obra_social_id=obra_social.id,
                    prestacion_id=prestacion.id,
                    monto_cubre_os=fila.monto,
                    monto_coseguro_paciente=coseguro,
                )
                db.add(arancel)
                aranceles[prestacion.id] = arancel
                hubo_cambios = True
        elif redondear_monto(arancel.monto_cubre_os) == fila.monto:
            # Mismo precio que el cargado: no se reescribe (y no se pisa sin
            # necesidad un coseguro que la secretaría haya ajustado a mano).
            estado = "SIN_CAMBIOS"
        else:
            estado = "ACTUALIZADO"
            if not data.simular:
                arancel.monto_cubre_os = fila.monto
                arancel.monto_coseguro_paciente = coseguro
                hubo_cambios = True

        contadores[estado] += 1
        detalle.append(
            _fila_reporte(
                fila.linea,
                prestacion.codigo_nomenclador,
                prestacion,
                fila.monto,
                coseguro,
                estado,
                None,
            )
        )

    if hubo_cambios:
        try:
            db.commit()
        except IntegrityError as exc:  # índice único uq_arancel_os_prestacion
            db.rollback()
            logger.warning(
                "IntegrityError al importar los aranceles de %s: %s",
                obra_social_id,
                exc.orig,
            )
            raise ConflictoError(
                "Alguno de esos aranceles se cargó al mismo tiempo desde otro "
                "lado: volvé a intentar la importación."
            ) from exc
        logger.info(
            "Aranceles importados obra_social_id=%s filas=%s creados=%s "
            "actualizados=%s errores=%s",
            obra_social_id,
            len(filas_planilla),
            contadores["NUEVO"],
            contadores["ACTUALIZADO"],
            contadores["ERROR"],
        )

    return ImportarArancelesRead(
        obra_social_id=obra_social.id,
        obra_social_nombre=obra_social.nombre,
        simulado=data.simular,
        nombre_archivo=data.nombre_archivo,
        filas_leidas=len(filas_planilla),
        creados=contadores["NUEVO"],
        actualizados=contadores["ACTUALIZADO"],
        sin_cambios=contadores["SIN_CAMBIOS"],
        ignoradas=contadores["IGNORADA"],
        errores=contadores["ERROR"],
        detalle=detalle,
    )


def plantilla_aranceles(
    db: Session, obra_social_id: int, formato: str = "csv"
) -> Tuple[bytes, str, str]:
    """Genera la planilla de ejemplo de una cobertura: ``(bytes, nombre, tipo)``.

    Trae **sólo dos columnas** (``codigo`` y ``precio_cobertura``) con los códigos
    del nomenclador base ya presentes; el precio viene con lo que esa cobertura
    tiene cargado hoy, así la secretaría completa las filas vacías o corrige las
    existentes y vuelve a subir el archivo. Responde **404** si la cobertura no
    existe y **422** si el formato pedido no es ``csv`` ni ``xlsx``.
    """
    obra_social = obra_social_service.obtener_obra_social(db, obra_social_id)
    aranceles = mapa_aranceles(db, obra_social_id)
    filas = [
        (
            prestacion.codigo_nomenclador,
            redondear_monto(aranceles[prestacion.id].monto_cubre_os)
            if prestacion.id in aranceles
            else None,
        )
        for prestacion in prestacion_service.listar_prestaciones(db, activas=True)
    ]

    base = f"plantilla-aranceles-{nombre_archivo_seguro(obra_social.nombre)}"
    if formato == "xlsx":
        return (
            generar_xlsx(filas),
            f"{base}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if formato == "csv":
        return generar_csv(filas), f"{base}.csv", "text/csv; charset=utf-8"
    raise ValidacionError(
        "El formato de la plantilla tiene que ser «csv» (Excel es-AR) o «xlsx»."
    )
