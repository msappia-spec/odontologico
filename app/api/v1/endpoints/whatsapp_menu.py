"""Endpoints del menú configurable del FAQ Bot de WhatsApp.

La pantalla *Bot WhatsApp* del panel administra con estos endpoints las opciones
que el bot le muestra al paciente cuando escribe «hola»:

* **Listar** (``GET``): todas las opciones o sólo las activas (``?activas=``).
* **Crear** (``POST``): el número (tecla) es único → ``409`` si se repite.
* **Reemplazar / modificar** (``PUT``/``PATCH``): ``PUT`` manda todos los campos
  (lo usa el modal del panel) y ``PATCH`` sólo los enviados (por ejemplo
  ``{activa: true}`` para reactivar).
* **Dar de baja** (``DELETE``): **borrado lógico** (``activa = false``).
* **Vista previa** (``GET /vista-previa``): el texto del menú activo (o el aviso
  de menú vacío), armado con las mismas funciones que usa el bot.
* **Restaurar** (``POST /restaurar``): vuelve a las cuatro opciones históricas.

Las rutas fijas (``/vista-previa`` y ``/restaurar``) se declaran antes de
``/{opcion_id}`` para que FastAPI no intente interpretarlas como un id.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.opcion_menu_whatsapp import OpcionMenuWhatsApp
from app.schemas.opcion_menu_whatsapp import (
    MenuWhatsAppVistaPrevia,
    OpcionMenuCreate,
    OpcionMenuRead,
    OpcionMenuUpdate,
)
from app.services import whatsapp_bot_service, whatsapp_menu_service

router = APIRouter(prefix="/whatsapp/menu", tags=["WhatsApp"])


@router.get(
    "",
    response_model=List[OpcionMenuRead],
    summary="Listar las opciones del menú del bot",
)
def listar_opciones(
    activas: Optional[bool] = Query(
        default=None,
        description=(
            "``true`` sólo las opciones que muestra el bot, ``false`` sólo las "
            "dadas de baja. Si se omite, todas."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[OpcionMenuWhatsApp]:
    return list(whatsapp_menu_service.listar_opciones(db, activas=activas))


@router.post(
    "",
    response_model=OpcionMenuRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una opción del menú",
    description=(
        "Agrega un renglón al menú del bot. El número es un único dígito y no "
        "puede repetirse (**409**). Una opción de tipo ``TEXTO`` exige "
        "``respuesta`` (**422**); los otros tipos arman el mensaje con datos del "
        "sistema."
    ),
)
def crear_opcion(
    payload: OpcionMenuCreate, db: Session = Depends(get_db)
) -> OpcionMenuWhatsApp:
    return whatsapp_menu_service.crear_opcion(db, payload)


@router.get(
    "/vista-previa",
    response_model=MenuWhatsAppVistaPrevia,
    summary="Vista previa del menú que recibe el paciente",
    description=(
        "Devuelve el texto del menú con las opciones activas, usando las mismas "
        "funciones de redacción que el webhook (y los datos vigentes de precio, "
        "obras sociales y ubicación). Si no hay opciones activas devuelve el "
        "aviso de menú vacío; en ese caso el bot no envía nada y deriva a "
        "atención manual."
    ),
)
def vista_previa_menu(db: Session = Depends(get_db)) -> MenuWhatsAppVistaPrevia:
    opciones = whatsapp_menu_service.obtener_menu_vigente(db)
    return MenuWhatsAppVistaPrevia(
        texto=whatsapp_bot_service.mensaje_menu(opciones),
        opciones=len(opciones),
    )


@router.post(
    "/restaurar",
    response_model=List[OpcionMenuRead],
    summary="Restaurar el menú histórico",
    description=(
        "Borra la configuración actual y vuelve a las cuatro opciones por "
        "defecto (precio, obras sociales, ubicación y secretaría)."
    ),
)
def restaurar_menu(db: Session = Depends(get_db)) -> List[OpcionMenuWhatsApp]:
    return list(whatsapp_menu_service.restaurar_menu_por_defecto(db))


@router.get(
    "/{opcion_id}",
    response_model=OpcionMenuRead,
    summary="Obtener una opción del menú",
)
def obtener_opcion(
    opcion_id: int, db: Session = Depends(get_db)
) -> OpcionMenuWhatsApp:
    return whatsapp_menu_service.obtener_opcion(db, opcion_id)


@router.put(
    "/{opcion_id}",
    response_model=OpcionMenuRead,
    summary="Reemplazar una opción del menú",
    description="Reemplaza todos los campos de la opción (misma validación que el alta).",
)
def reemplazar_opcion(
    opcion_id: int, payload: OpcionMenuCreate, db: Session = Depends(get_db)
) -> OpcionMenuWhatsApp:
    return whatsapp_menu_service.actualizar_opcion(
        db, opcion_id, OpcionMenuUpdate(**payload.model_dump())
    )


@router.patch(
    "/{opcion_id}",
    response_model=OpcionMenuRead,
    summary="Modificar una opción del menú",
    description="Cambia sólo los campos enviados (por ejemplo ``activa`` o ``titulo``).",
)
def modificar_opcion(
    opcion_id: int, payload: OpcionMenuUpdate, db: Session = Depends(get_db)
) -> OpcionMenuWhatsApp:
    return whatsapp_menu_service.actualizar_opcion(db, opcion_id, payload)


@router.delete(
    "/{opcion_id}",
    response_model=OpcionMenuRead,
    summary="Dar de baja una opción del menú",
    description=(
        "**Baja lógica**: la opción deja de mostrarse y de atenderse, pero se "
        "conserva para poder reactivarla con ``PATCH {activa: true}``."
    ),
)
def desactivar_opcion(
    opcion_id: int, db: Session = Depends(get_db)
) -> OpcionMenuWhatsApp:
    return whatsapp_menu_service.desactivar_opcion(db, opcion_id)
