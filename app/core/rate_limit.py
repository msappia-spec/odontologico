"""Limitador de peticiones por IP para el portal público de turnos.

El portal ``/reservar`` es la única superficie de la API abierta a personas sin
cuenta de empleado. Para que no se convierta en un blanco de bots (enumeración
de DNI, creación masiva de turnos o *scraping* de la agenda) cada endpoint
declara una dependencia de :func:`limitar_portal` o
:func:`limitar_operacion_portal`, que aplica una **ventana deslizante** por IP.

Decisiones:

* Se usa ``monotonic`` (no la hora del sistema) para que un ajuste de reloj no
  reinicie los contadores.
* ``Lock`` protege las estructuras porque FastAPI ejecuta los endpoints
  sincrónicos en hilos del pool.
* Igual que el limitador de login, es **por proceso**: en Vercel cada instancia
  tiene su propio contador. Es una capa barata que complementa el control de
  concurrencia de la base y el índice único de turnos, no un reemplazo.
* Cuando se supera el límite se responde ``429`` con ``Retry-After`` (segundos),
  el mismo contrato que ya usa ``POST /auth/login``.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.security import ip_del_cliente


class LimitadorPeticiones:
    """Cuenta peticiones por clave dentro de una ventana móvil de segundos."""

    def __init__(self, maximo: int, ventana_segundos: int) -> None:
        self.maximo = max(1, int(maximo))
        self.ventana = max(1, int(ventana_segundos))
        self._peticiones: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _vigentes(self, clave: str) -> deque[float]:
        """Descarta las peticiones que ya salieron de la ventana."""
        marcas = self._peticiones[clave]
        limite = time.monotonic() - self.ventana
        while marcas and marcas[0] < limite:
            marcas.popleft()
        return marcas

    def segundos_bloqueado(self, clave: str) -> int:
        """Devuelve 0 si la clave puede pasar, o los segundos que faltan."""
        with self._lock:
            marcas = self._vigentes(clave)
            if len(marcas) < self.maximo:
                return 0
            restante = self.ventana - (time.monotonic() - marcas[0])
            return max(1, int(restante) + 1)

    def registrar(self, clave: str) -> None:
        """Anota una petición aceptada (con la hora monotónica actual)."""
        with self._lock:
            self._vigentes(clave).append(time.monotonic())

    def reiniciar(self) -> None:
        """Vacía todos los contadores (pruebas y mantenimiento)."""
        with self._lock:
            self._peticiones.clear()


#: Lecturas públicas: doctores/obras sociales, disponibilidad y búsqueda por DNI.
limitador_portal = LimitadorPeticiones(
    maximo=settings.portal_max_peticiones,
    ventana_segundos=settings.portal_ventana_segundos,
)

#: Operaciones que escriben o revelan datos de una persona (reservar/cancelar).
#: Tope más bajo para frenar la creación masiva de turnos y la fuerza bruta
#: contra el código de cancelación.
limitador_operaciones_portal = LimitadorPeticiones(
    maximo=settings.portal_max_operaciones,
    ventana_segundos=settings.portal_ventana_segundos,
)


def _dependencia(limitador: LimitadorPeticiones):
    """Arma la dependencia de FastAPI que aplica un limitador concreto."""

    def dependencia(request: Request) -> None:
        ip = ip_del_cliente(request)
        espera = limitador.segundos_bloqueado(ip)
        if espera:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Demasiadas solicitudes desde esta conexión. Esperá unos "
                    "segundos y volvé a intentar."
                ),
                headers={"Retry-After": str(espera)},
            )
        limitador.registrar(ip)

    return dependencia


#: Dependencia para los GET del portal.
limitar_portal = _dependencia(limitador_portal)
#: Dependencia para reservar y cancelar.
limitar_operacion_portal = _dependencia(limitador_operaciones_portal)


def reiniciar_limites_portal() -> None:
    """Reinicia los contadores del portal (lo usan las pruebas)."""
    limitador_portal.reiniciar()
    limitador_operaciones_portal.reiniciar()
