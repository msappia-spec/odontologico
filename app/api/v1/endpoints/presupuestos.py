"""Endpoints del módulo de presupuestos.

Rutas (prefijo ``/api/v1``):

* ``GET /presupuestos`` — lista con filtro por paciente y búsqueda.
* ``POST /presupuestos`` — crea un presupuesto con sus trabajos.
* ``GET /presupuestos/{id}`` — detalle con totales.
* ``PUT /presupuestos/{id}`` — reemplaza cabecera y trabajos.
* ``DELETE /presupuestos/{id}`` — elimina el presupuesto.
* ``GET /presupuestos/{id}/pdf`` — documento listo para imprimir o guardar.
* ``POST /presupuestos/{id}/whatsapp`` — envía el PDF por WhatsApp al número
  revisado en el panel (con opción de guardarlo en la ficha del paciente).

El PDF se devuelve con ``Content-Disposition: inline`` para que el navegador lo
abra en el visor y desde ahí se pueda imprimir, igual que la Historia Clínica
General.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.presupuesto import (
    PresupuestoCreate,
    PresupuestoEnviadoWhatsApp,
    PresupuestoEnvioWhatsApp,
    PresupuestoRead,
    PresupuestoUpdate,
)
from app.services import (
    presupuesto_pdf_service,
    presupuesto_service,
    presupuesto_whatsapp_service,
)

router = APIRouter(prefix="/presupuestos", tags=["Presupuestos"])


@router.post(
    "",
    response_model=PresupuestoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un presupuesto",
    description=(
        "Registra un presupuesto para un paciente con uno o más trabajos. Cada "
        "trabajo lleva un código de prestación opcional, una descripción que "
        "admite saltos de línea, el precio y, opcionalmente, la entrega que se "
        "imprime como «Forma de Pago : Entrega». La dirección vacía usa el "
        "domicilio del paciente. Un paciente inexistente responde **404** y los "
        "importes negativos, un detalle vacío o una lista de trabajos vacía, "
        "**422**."
    ),
)
def crear_presupuesto(
    payload: PresupuestoCreate, db: Session = Depends(get_db)
) -> PresupuestoRead:
    return presupuesto_service.crear_presupuesto(db, payload)


@router.get(
    "",
    response_model=List[PresupuestoRead],
    summary="Listar presupuestos",
    description=(
        "Devuelve los presupuestos del más nuevo al más viejo. ``paciente_id`` "
        "limita la lista a un paciente y ``buscar`` compara nombre, DNI, forma "
        "de pago, código de prestación o descripción de los trabajos."
    ),
)
def listar_presupuestos(
    paciente_id: Optional[int] = Query(
        default=None, gt=0, description="Filtra por paciente."
    ),
    buscar: Optional[str] = Query(
        default=None,
        max_length=120,
        description=(
            "Búsqueda por nombre/DNI del paciente, forma de pago, código o "
            "descripción de los trabajos."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[PresupuestoRead]:
    return presupuesto_service.listar_presupuestos(
        db, paciente_id=paciente_id, buscar=buscar
    )


@router.get(
    "/{presupuesto_id}/pdf",
    response_class=Response,
    summary="Imprimir un presupuesto en PDF",
    description=(
        "Genera el PDF con el diseño del documento del consultorio: logo, "
        "datos del paciente, fecha, forma de pago, código y detalle de los "
        "trabajos, y precios. Se abre en el visor del navegador para imprimir "
        "o guardar."
    ),
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Presupuesto listo para imprimir.",
        }
    },
)
def descargar_presupuesto_pdf(
    presupuesto_id: int, db: Session = Depends(get_db)
) -> Response:
    contenido, nombre = presupuesto_pdf_service.generar_pdf(
        db, presupuesto_id
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/{presupuesto_id}/whatsapp",
    response_model=PresupuestoEnviadoWhatsApp,
    summary="Enviar el presupuesto por WhatsApp",
    description=(
        "Genera el PDF del presupuesto y lo envía como adjunto por WhatsApp, "
        "con un resumen del total, las entregas, el saldo y la forma de pago. "
        "``telefono`` permite revisar/corregir el número antes de enviar (el "
        "panel lo precarga con el de la ficha); si se omite se usa el del "
        "paciente. Con ``actualizar_telefono`` el número corregido queda "
        "guardado en la ficha después de un envío exitoso. Un presupuesto "
        "inexistente responde **404**, un número inválido **422** y una falla "
        "de WAHA **502**."
    ),
    responses={
        502: {"description": "WAHA rechazó el envío o no respondió."},
    },
)
def enviar_presupuesto_whatsapp(
    presupuesto_id: int,
    payload: PresupuestoEnvioWhatsApp,
    db: Session = Depends(get_db),
) -> PresupuestoEnviadoWhatsApp:
    return presupuesto_whatsapp_service.enviar_presupuesto(
        db, presupuesto_id, payload
    )


@router.get(
    "/{presupuesto_id}",
    response_model=PresupuestoRead,
    summary="Obtener un presupuesto",
    description=(
        "Devuelve la cabecera, los trabajos y los totales calculados "
        "(precio total, entregas y saldo). Responde **404** si no existe."
    ),
)
def obtener_presupuesto(
    presupuesto_id: int, db: Session = Depends(get_db)
) -> PresupuestoRead:
    return presupuesto_service.obtener_presupuesto(db, presupuesto_id)


@router.put(
    "/{presupuesto_id}",
    response_model=PresupuestoRead,
    summary="Reemplazar un presupuesto",
    description=(
        "Reemplaza la cabecera y **todos** los trabajos del presupuesto. Es un "
        "``PUT`` clásico: los renglones omitidos se eliminan. Un presupuesto o "
        "un paciente inexistente responde **404**."
    ),
)
def actualizar_presupuesto(
    presupuesto_id: int,
    payload: PresupuestoUpdate,
    db: Session = Depends(get_db),
) -> PresupuestoRead:
    return presupuesto_service.actualizar_presupuesto(
        db, presupuesto_id, payload
    )


@router.delete(
    "/{presupuesto_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un presupuesto",
    description=(
        "Borra el presupuesto y sus renglones. Es un documento comercial, no "
        "un registro clínico inmutable. Responde **404** si no existe."
    ),
)
def eliminar_presupuesto(
    presupuesto_id: int, db: Session = Depends(get_db)
) -> Response:
    presupuesto_service.eliminar_presupuesto(db, presupuesto_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
