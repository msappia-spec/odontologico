"""Autenticación y autorización del panel (Módulo de usuarios y permisos).

Este módulo es la **única fuente de verdad** de:

* el catálogo de módulos (códigos que el panel usa para ocultar secciones);
* el hash/verificación de contraseñas (bcrypt con sal aleatoria);
* la emisión y validación de los JWT (HS256, ``SECRET_KEY`` del entorno);
* las dependencias de FastAPI ``get_current_user`` y ``require_module``;
* el limitador de intentos fallidos de login por IP y por cuenta (con
  contadores separados para el ingreso con huella digital).

Decisiones de seguridad:

* La contraseña **nunca** se guarda ni se registra en texto plano: se compara
  con ``bcrypt.checkpw`` contra el hash.
* El token viaja en la cookie ``HttpOnly`` (el JS no lo puede leer) y también se
  devuelve en el cuerpo del login para clientes que usen ``Bearer``.
* ``require_module`` deja pasar a los superadmins y, para el resto, exige que el
  usuario tenga **al menos uno** de los códigos pedidos. Eso permite compartir un
  router entre secciones sin abrir de más (por ejemplo ``GET /doctores`` lo usan
  tanto *Registros* como la agenda del calendario).
* El limitador es en memoria y por proceso: frena la fuerza bruta contra una
  instancia, aunque en serverless cada instancia tenga su propio contador. Es una
  capa barata que complementa (no reemplaza) el control de credenciales.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta
from threading import Lock
from typing import Callable, Iterable, Optional, Sequence, Tuple

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.datetime_utils import utcnow
from app.db.session import get_db
from app.models.usuario import Usuario

logger = logging.getLogger("app.core.security")

#: Nombre de la cookie de sesión (HttpOnly, SameSite=Lax).
NOMBRE_COOKIE_SESION = "odonto_sesion"
#: Algoritmo de firma del token.
ALGORITMO_JWT = "HS256"

#: Catálogo de módulos configurables. El orden es el del menú lateral.
#:
#: Cada entrada es ``(código, nombre visible, descripción corta)``. El código es
#: el que se guarda en ``Usuario.modulos_permitidos`` y el que usan los routers;
#: no se debe renombrar sin migrar los permisos existentes.
MODULOS: Tuple[Tuple[str, str, str], ...] = (
    (
        "PANEL_PRINCIPAL",
        "Panel Principal",
        "Resumen del consultorio, indicadores y alertas médicas del día.",
    ),
    (
        "AGENDA_TURNOS",
        "Agenda y turnos",
        "Calendario, disponibilidad por odontólogo y recordatorios de WhatsApp.",
    ),
    (
        "FICHAS_SALUD",
        "Fichas de salud",
        "Declaración de salud, historia clínica, evolución y odontograma.",
    ),
    (
        "CAJA_PRESUPUESTOS",
        "Caja y presupuestos",
        "Facturación de visitas, caja del día, prestaciones, aranceles y presupuestos.",
    ),
    (
        "CUENTA_CORRIENTE",
        "Cuenta corriente",
        "Saldo deudor por paciente, libro mayor, cuotas y pagos.",
    ),
    (
        "PROVEEDORES_EGRESOS",
        "Proveedores y egresos",
        "Facturas de proveedores, cuentas por pagar y pagos de egresos.",
    ),
    (
        "BORRAR_MOVIMIENTOS",
        "Borrar movimientos",
        "Eliminar cierres de caja y movimientos de cuenta corriente (sensible).",
    ),
    (
        "REGISTROS",
        "Registros",
        "Alta y edición de doctores, pacientes y categorías de pacientes.",
    ),
    (
        "CONFIGURACION",
        "Configuración",
        "Obras sociales y menú del bot de WhatsApp.",
    ),
    (
        "ADMIN_USUARIOS",
        "Usuarios y permisos",
        "Crear usuarios, asignar módulos y restablecer contraseñas.",
    ),
)

#: Códigos válidos, en el orden del catálogo.
CODIGOS_MODULO: Tuple[str, ...] = tuple(codigo for codigo, _, _ in MODULOS)

#: Mapa ``código -> (nombre, descripción)`` para armar respuestas y la UI.
MODULOS_POR_CODIGO = {codigo: (nombre, detalle) for codigo, nombre, detalle in MODULOS}


def normalizar_modulos(codigos: Iterable[str]) -> list[str]:
    """Valida y ordena una lista de códigos de módulo.

    Devuelve los códigos **sin repetir** y en el orden del catálogo, para que la
    base guarde siempre la misma forma. Un código desconocido es un error de la
    solicitud (el frontend no puede inventar permisos).
    """
    pedidos = {str(codigo).strip().upper() for codigo in codigos if str(codigo).strip()}
    desconocidos = sorted(pedidos - set(CODIGOS_MODULO))
    if desconocidos:
        raise ValueError("Módulo(s) desconocido(s): " + ", ".join(desconocidos))
    return [codigo for codigo in CODIGOS_MODULO if codigo in pedidos]


# --------------------------------------------------------------------------- #
# Contraseñas (bcrypt)
# --------------------------------------------------------------------------- #
#: bcrypt no acepta entradas de más de 72 bytes (las truncaría en silencio).
MAXIMO_BYTES_PASSWORD = 72


def hash_password(password: str) -> str:
    """Devuelve el hash bcrypt (con sal aleatoria) de una contraseña.

    Se usa ``bcrypt`` directo (sin ``passlib``) para no arrastrar una capa extra
    ni su incompatibilidad con las versiones nuevas de bcrypt. El costo (12) es
    el recomendado por la librería y tarda ~0,2 s por hash, que es justamente lo
    que encarece un ataque de diccionario.
    """
    if not password:
        raise ValueError("La contraseña no puede estar vacía.")
    datos = password.encode("utf-8")
    if len(datos) > MAXIMO_BYTES_PASSWORD:
        raise ValueError("La contraseña no puede superar los 72 bytes en UTF-8.")
    return bcrypt.hashpw(datos, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verificar_password(password: str, hashed_password: str) -> bool:
    """Compara una contraseña en claro con su hash bcrypt.

    Cualquier error de formato del hash se trata como credencial inválida: nunca
    se propaga una excepción al cliente ni se filtra si el email existe.
    """
    if not password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def crear_token_acceso(usuario: Usuario) -> tuple[str, int]:
    """Firma un JWT de acceso para el usuario y devuelve ``(token, minutos)``.

    El ``sub`` es el UUID del usuario (no el email: el email puede cambiar y el
    identificador no). El token incluye ``exp`` (8 h por defecto) e ``iat``.
    """
    ahora = utcnow()
    horas = max(1, int(settings.access_token_expire_hours))
    expira = ahora + timedelta(hours=horas)
    payload = {
        "sub": str(usuario.id),
        "email": usuario.email,
        "sa": bool(usuario.es_superadmin),
        "iat": ahora,
        "exp": expira,
    }
    token = jwt.encode(payload, settings.jwt_secret_efectivo, algorithm=ALGORITMO_JWT)
    return token, horas * 60


def decodificar_token(token: str) -> dict:
    """Valida la firma y la expiración del JWT.

    Lanza ``HTTPException`` 401 con un mensaje uniforme: no distingue entre token
    vencido, alterado o de otro usuario.
    """
    try:
        return jwt.decode(
            token, settings.jwt_secret_efectivo, algorithms=[ALGORITMO_JWT]
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión expiró. Volvé a iniciar sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida. Volvé a iniciar sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _token_del_request(request: Request) -> Optional[str]:
    """Extrae el token de la cookie de sesión o del ``Authorization: Bearer``."""
    token = request.cookies.get(NOMBRE_COOKIE_SESION)
    if token:
        return token.strip()
    autorizacion = request.headers.get("Authorization", "")
    esquema, _, valor = autorizacion.partition(" ")
    if esquema.lower() == "bearer" and valor.strip():
        return valor.strip()
    return None


# --------------------------------------------------------------------------- #
# Dependencias de FastAPI
# --------------------------------------------------------------------------- #
def get_current_user(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """Dependencia: devuelve el usuario autenticado o responde 401.

    Se busca de nuevo en la base (no se confía sólo en el token) para que
    desactivar una cuenta tenga efecto **inmediato**, sin esperar a que expire el
    JWT.
    """
    token = _token_del_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Necesitás iniciar sesión para acceder al panel.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decodificar_token(token)
    try:
        usuario_id = uuid.UUID(str(payload.get("sub")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida. Volvé a iniciar sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La cuenta no existe. Volvé a iniciar sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está deshabilitada. Hablá con un administrador.",
        )
    return usuario


def require_module(*codigos_modulo: str) -> Callable[..., Usuario]:
    """Crea la dependencia que exige al menos uno de los módulos indicados.

    Uso en los routers::

        @router.get("", dependencies=[Depends(require_module("REGISTROS"))])

    Un superadmin pasa siempre. Para el resto, si ninguno de los códigos está en
    ``modulos_permitidos`` se responde **403 Forbidden** con un mensaje que indica
    qué permiso falta.
    """
    requeridos = tuple(normalizar_modulos(codigos_modulo))
    if not requeridos:
        raise ValueError("require_module necesita al menos un código de módulo.")

    def dependencia(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if usuario.es_superadmin:
            return usuario
        permitidos = set(usuario.modulos_permitidos or ())
        if permitidos.intersection(requeridos):
            return usuario
        nombres = " o ".join(MODULOS_POR_CODIGO[codigo][0] for codigo in requeridos)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tu usuario no tiene permiso para esta sección ({nombres}). "
                "Pedile a un administrador que te habilite el módulo."
            ),
        )

    return dependencia


# --------------------------------------------------------------------------- #
# Limitador de intentos de login (fuerza bruta)
# --------------------------------------------------------------------------- #
class LimitadorIntentos:
    """Cuenta intentos fallidos por clave (IP) dentro de una ventana móvil.

    Es thread-safe (``Lock``) porque FastAPI ejecuta los endpoints sincrónicos
    en un hilo del pool. La ventana se calcula con ``monotonic`` para que un
    cambio de hora del sistema no reinicie los contadores.
    """

    def __init__(self, maximo: int = 5, ventana_segundos: int = 60) -> None:
        self.maximo = max(1, int(maximo))
        self.ventana = max(1, int(ventana_segundos))
        self._fallos: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _vigentes(self, clave: str) -> deque[float]:
        """Descarta los fallos que ya salieron de la ventana."""
        intentos = self._fallos[clave]
        limite = time.monotonic() - self.ventana
        while intentos and intentos[0] < limite:
            intentos.popleft()
        return intentos

    def segundos_bloqueado(self, clave: str) -> int:
        """Devuelve 0 si la clave puede intentar, o los segundos que faltan."""
        with self._lock:
            intentos = self._vigentes(clave)
            if len(intentos) < self.maximo:
                return 0
            restante = self.ventana - (time.monotonic() - intentos[0])
            return max(1, int(restante) + 1)

    def registrar_fallo(self, clave: str) -> None:
        """Anota un intento fallido (con la hora monotónica actual)."""
        with self._lock:
            self._vigentes(clave).append(time.monotonic())

    def registrar_exito(self, clave: str) -> None:
        """Limpia los fallos al autenticarse correctamente."""
        with self._lock:
            self._fallos.pop(clave, None)

    def reiniciar(self) -> None:
        """Vacía todos los contadores (pruebas y reinicio de mantenimiento)."""
        with self._lock:
            self._fallos.clear()


#: Instancia única usada por ``POST /auth/login`` (por IP).
limitador_login = LimitadorIntentos(
    maximo=settings.login_max_intentos,
    ventana_segundos=settings.login_ventana_segundos,
)

#: Segundo contador, por **cuenta** (email normalizado). El de IP frena a un
#: atacante desde una máquina; éste evita que rotar la IP (o falsificar
#: ``X-Forwarded-For``) permita seguir probando la clave de un mismo usuario.
limitador_cuenta = LimitadorIntentos(
    maximo=settings.login_max_intentos,
    ventana_segundos=settings.login_ventana_segundos,
)


def clave_de_cuenta(email: str) -> str:
    """Clave del limitador por cuenta (email normalizado)."""
    return "cuenta:" + (email or "").strip().lower()


def ip_del_cliente(request: Request) -> str:
    """IP del cliente, respetando el proxy de Vercel (``X-Forwarded-For``)."""
    adelantado = request.headers.get("x-forwarded-for", "")
    if adelantado:
        # El primer valor es el cliente original; los siguientes son proxies.
        return adelantado.split(",")[0].strip() or "desconocida"
    return (request.client.host if request.client else "") or "desconocida"


def registrar_login_fallido(
    ip: str, email: str, clave_cuenta: Optional[str] = None
) -> None:
    """Anota el fallo por IP y por cuenta, y deja el rastro en el log.

    Nunca registra la contraseña (ni su longitud): sólo el email y la IP.
    """
    limitador_login.registrar_fallo(ip)
    limitador_cuenta.registrar_fallo(clave_cuenta or clave_de_cuenta(email))
    logger.warning("Login fallido para %s desde %s.", email, ip)


def reiniciar_limite_login() -> None:
    """Reinicia los limitadores (lo usan las pruebas para aislarse)."""
    limitador_login.reiniciar()
    limitador_cuenta.reiniciar()
    reiniciar_limite_biometria()


# --------------------------------------------------------------------------- #
# Limitador del login biométrico (WebAuthn / passkeys)
# --------------------------------------------------------------------------- #
#: Contadores propios del ingreso con huella. Se mantienen separados de los del
#: login con contraseña a propósito: un lector que no reconoce la huella no debe
#: dejar al usuario sin poder entrar con su clave. La ventana y el máximo son los
#: mismos (``LOGIN_MAX_INTENTOS`` / ``LOGIN_VENTANA_SEGUNDOS``).
limitador_biometria = LimitadorIntentos(
    maximo=settings.login_max_intentos,
    ventana_segundos=settings.login_ventana_segundos,
)
limitador_biometria_cuenta = LimitadorIntentos(
    maximo=settings.login_max_intentos,
    ventana_segundos=settings.login_ventana_segundos,
)


def reiniciar_limite_biometria() -> None:
    """Vacía los contadores del ingreso con huella."""
    limitador_biometria.reiniciar()
    limitador_biometria_cuenta.reiniciar()


def segundos_bloqueo_biometria(ip: str, cuenta: str) -> int:
    """Segundos de espera si la IP o la cuenta superaron los intentos."""
    return max(
        limitador_biometria.segundos_bloqueado(ip),
        limitador_biometria_cuenta.segundos_bloqueado(cuenta),
    )


def registrar_biometria_fallida(
    ip: str, email: str, clave_cuenta: Optional[str] = None
) -> None:
    """Anota un intento biométrico fallido (IP + cuenta) y lo deja en el log."""
    limitador_biometria.registrar_fallo(ip)
    limitador_biometria_cuenta.registrar_fallo(
        clave_cuenta or clave_de_cuenta(email)
    )
    logger.warning("Login biométrico fallido para %s desde %s.", email, ip)


def nombres_de_modulos(codigos: Sequence[str]) -> list[str]:
    """Traduce códigos a nombres visibles (para mensajes y reportes)."""
    return [
        MODULOS_POR_CODIGO[codigo][0] if codigo in MODULOS_POR_CODIGO else codigo
        for codigo in codigos
    ]
