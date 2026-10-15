"""Endpoints de aranceles por obra social (facturación mixta, Módulo 3).

El arancel es el acuerdo económico con cada cobertura: cuánto paga la obra social
y cuánto queda como coseguro del paciente para **cada** práctica.

Rutas (prefijo ``/api/v1``):

* ``GET/POST /obras-sociales/{id}/aranceles`` — listar y cargar los aranceles de
  una cobertura (el alta es anidada: la cobertura sale de la URL).
* ``GET/PUT/PATCH/DELETE /aranceles/{id}`` — detalle, reemplazo total, edición
  parcial y borrado de un arancel puntual.
* ``GET/PUT /obras-sociales/{id}/convenio`` — la pantalla *Convenios*: la grilla
  completa del nomenclador con lo que cubre la cobertura, el **coseguro sugerido**
  (``valor_base - monto que cubre``) y su guardado en lote.
* ``GET /obras-sociales/{id}/convenio/plantilla`` — descarga la planilla de
  ejemplo de dos columnas (``codigo`` y ``precio_cobertura``), en ``.csv`` o
  ``.xlsx``.
* ``POST /obras-sociales/{id}/importar-aranceles`` — **importador masivo**: sube
  esa planilla completada y actualiza los aranceles de la cobertura (admite
  ``simular`` para previsualizar sin escribir).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.arancel_obra_social import ArancelObraSocial
from app.schemas.arancel import (
    ArancelObraSocialCreate,
    ArancelObraSocialRead,
    ArancelObraSocialUpdate,
    ConvenioObraSocialRead,
    GuardarConvenioRequest,
    ImportarArancelesRead,
    ImportarArancelesRequest,
)
from app.services import arancel_service

router = APIRouter(tags=["Aranceles"])


@router.get(
    "/obras-sociales/{obra_social_id}/aranceles",
    response_model=List[ArancelObraSocialRead],
    summary="Listar los aranceles de una obra social",
    description=(
        "Devuelve la tabla de precios acordada con esa cobertura, ordenada por "
        "código de práctica, con lo que paga la OS y el coseguro del paciente. "
        "Se puede acotar a una práctica con ``?prestacion_id=``. Responde "
        "**404** si la obra social no existe."
    ),
)
def listar_aranceles(
    obra_social_id: int,
    prestacion_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
) -> List[ArancelObraSocial]:
    return list(
        arancel_service.listar_aranceles(
            db, obra_social_id=obra_social_id, prestacion_id=prestacion_id
        )
    )


@router.post(
    "/obras-sociales/{obra_social_id}/aranceles",
    response_model=ArancelObraSocialRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cargar el arancel de una práctica",
    description=(
        "Registra cuánto cubre la cobertura y cuánto paga el paciente para una "
        "práctica. Una cobertura no puede tener dos aranceles de la misma "
        "práctica (**409**); responde **404** si la obra social o la práctica no "
        "existen."
    ),
)
def crear_arancel(
    obra_social_id: int,
    payload: ArancelObraSocialCreate,
    db: Session = Depends(get_db),
) -> ArancelObraSocial:
    return arancel_service.crear_arancel(db, obra_social_id, payload)


@router.get(
    "/aranceles/{arancel_id}",
    response_model=ArancelObraSocialRead,
    summary="Obtener un arancel",
    description="Devuelve el detalle de un arancel existente (``404`` si no existe).",
)
def obtener_arancel(
    arancel_id: int, db: Session = Depends(get_db)
) -> ArancelObraSocial:
    return arancel_service.obtener_arancel(db, arancel_id)


@router.put(
    "/aranceles/{arancel_id}",
    response_model=ArancelObraSocialRead,
    summary="Reemplazar los montos de un arancel",
    description=(
        "Reemplaza los montos del arancel (cobertura y coseguro). El par "
        "(obra social, práctica) no se puede cambiar: enviar otra "
        "``prestacion_id`` responde **422**."
    ),
)
def reemplazar_arancel(
    arancel_id: int,
    payload: ArancelObraSocialCreate,
    db: Session = Depends(get_db),
) -> ArancelObraSocial:
    return arancel_service.reemplazar_arancel(db, arancel_id, payload)


@router.patch(
    "/aranceles/{arancel_id}",
    response_model=ArancelObraSocialRead,
    summary="Actualizar los montos de un arancel",
    description=(
        "Actualiza parcialmente el arancel: sólo se aplican los montos enviados "
        "(por ejemplo ``{\"monto_coseguro_paciente\": 5000}``)."
    ),
)
def actualizar_arancel(
    arancel_id: int,
    payload: ArancelObraSocialUpdate,
    db: Session = Depends(get_db),
) -> ArancelObraSocial:
    return arancel_service.actualizar_arancel(db, arancel_id, payload)


@router.delete(
    "/aranceles/{arancel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un arancel",
    description=(
        "Borra el arancel del par (cobertura, práctica). Es **configuración**, no "
        "un comprobante: las facturas ya emitidas conservan sus importes "
        "congelados, así que borrarlo no altera ninguna liquidación pasada. "
        "Responde **204** y **404** si no existe."
    ),
)
def eliminar_arancel(arancel_id: int, db: Session = Depends(get_db)) -> Response:
    arancel_service.eliminar_arancel(db, arancel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Convenios: la grilla completa de precios de una cobertura
# --------------------------------------------------------------------------- #
@router.get(
    "/obras-sociales/{obra_social_id}/convenio",
    response_model=ConvenioObraSocialRead,
    summary="Ver la grilla de convenios de una obra social",
    description=(
        "Devuelve **todas** las prácticas activas del nomenclador base de Córdoba "
        "con el arancel de referencia (``valor_base``), lo que esa cobertura tiene "
        "cargado, el coseguro guardado y el **coseguro sugerido** "
        "(``valor_base - monto que cubre la OS``), que es lo que el panel muestra "
        "en vivo. Incluye los totales de la grilla y responde **404** si la obra "
        "social no existe."
    ),
)
def ver_convenio(
    obra_social_id: int, db: Session = Depends(get_db)
) -> ConvenioObraSocialRead:
    return arancel_service.convenio_de_obra_social(db, obra_social_id)


@router.put(
    "/obras-sociales/{obra_social_id}/convenio",
    response_model=ConvenioObraSocialRead,
    summary="Guardar la grilla de convenios en lote",
    description=(
        "Crea o actualiza el arancel de cada fila enviada. Si una fila no trae "
        "``monto_coseguro_paciente`` se guarda el **coseguro sugerido** "
        "(``valor_base - monto que cubre la OS``). La grilla se valida antes de "
        "escribir y se confirma en una sola transacción; responde **404** si la "
        "obra social o alguna práctica no existen (o la práctica está dada de "
        "baja), **422** si los montos son negativos o la grilla repite una "
        "práctica y **409** si otra carga pisó el mismo par."
    ),
)
def guardar_convenio(
    obra_social_id: int,
    payload: GuardarConvenioRequest,
    db: Session = Depends(get_db),
) -> ConvenioObraSocialRead:
    return arancel_service.guardar_convenio(db, obra_social_id, payload)


@router.get(
    "/obras-sociales/{obra_social_id}/convenio/plantilla",
    summary="Descargar la plantilla de aranceles",
    response_class=Response,
    responses={
        200: {
            "content": {
                "text/csv": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            },
            "description": "Planilla de ejemplo con las columnas codigo/precio_cobertura.",
        }
    },
    description=(
        "Descarga la **plantilla de ejemplo** de esa cobertura: dos columnas fijas "
        "(``codigo`` y ``precio_cobertura``) con todos los códigos del nomenclador "
        "base, ya con el precio que la cobertura tiene cargado hoy (vacío si "
        "todavía no hay acuerdo). Se baja como adjunto (``Content-Disposition``) en "
        "``.csv`` (default, el que abre Excel es-AR) o ``.xlsx`` con "
        "``?formato=xlsx``; **404** si la obra social no existe."
    ),
)
def descargar_plantilla(
    obra_social_id: int,
    formato: str = Query("csv", description="``csv`` (default) o ``xlsx``."),
    db: Session = Depends(get_db),
) -> Response:
    contenido, nombre, media_type = arancel_service.plantilla_aranceles(
        db, obra_social_id, formato
    )
    return Response(
        content=contenido,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.post(
    "/obras-sociales/{obra_social_id}/importar-aranceles",
    response_model=ImportarArancelesRead,
    summary="Importar masivamente los aranceles de una planilla",
    description=(
        "Procesa la planilla completada (``codigo`` + ``precio_cobertura``) y "
        "actualiza los aranceles de esa cobertura buscando cada código en el "
        "nomenclador base de Córdoba. El archivo viaja en ``contenido_base64`` "
        "(sirve para ``.csv`` y ``.xlsx``) o en ``contenido`` como texto. "
        "Devuelve un **reporte** con lo creado, lo actualizado, lo que no cambió y "
        "las filas con error (que no cortan la importación); con ``simular=true`` "
        "no escribe nada y sirve de vista previa. Responde **404** si la obra "
        "social no existe y **422** si el archivo no se puede leer."
    ),
)
def importar_aranceles(
    obra_social_id: int,
    payload: ImportarArancelesRequest,
    db: Session = Depends(get_db),
) -> ImportarArancelesRead:
    return arancel_service.importar_aranceles(db, obra_social_id, payload)
