"""Autenticación del panel: login, logout, sesión, módulos y passkeys.

Rutas (prefijo ``/api/v1``):

* ``POST /auth/login`` — valida credenciales, verifica ``activo``, actualiza
  ``ultimo_acceso`` y devuelve el JWT junto con el usuario (con
  ``es_superadmin`` y ``modulos_permitidos``). La cookie de sesión es
  ``HttpOnly`` + ``SameSite=Lax``, así el JS del panel no puede leer el token y
  el navegador no lo manda en peticiones cruzadas.
* ``POST /auth/logout`` — borra la cookie de sesión.
* ``GET  /auth/me`` — usuario de la sesión vigente (lo usa el panel para decidir
  si muestra el login y qué ítems del menú ocultar).
* ``GET  /auth/modulos`` — catálogo de permisos configurables.
* ``POST /auth/webauthn/…`` — huella digital / Face ID (WebAuthn):
  ``registro/opciones`` y ``registro/verificar`` exigen sesión;
  ``login/opciones`` y ``login/verificar`` son públicos y devuelven exactamente
  la misma sesión que ``/login``. La contraseña sigue siendo el respaldo.

El login está limitado a :data:`settings.login_max_intentos` fallos por minuto por
IP **y por cuenta** (HTTP 429): el contador por IP frena a un atacante desde una
máquina y el de cuenta evita que rotar la IP permita seguir probando la clave de
un mismo usuario. Los contadores son por proceso; ver la nota en
:mod:`app.core.security`. El ingreso con huella usa contadores propios para que
un lector que falla no bloquee la contraseña.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core import webauthn as webauthn_core
from app.core.config import settings
from app.core.security import (
    MODULOS,
    NOMBRE_COOKIE_SESION,
    clave_de_cuenta,
    crear_token_acceso,
    get_current_user,
    ip_del_cliente,
    limitador_biometria,
    limitador_biometria_cuenta,
    limitador_cuenta,
    limitador_login,
    registrar_biometria_fallida,
    registrar_login_fallido,
    segundos_bloqueo_biometria,
    verificar_password,
)
from app.db.session import get_db
from app.models.usuario import Usuario
from app.schemas.usuario import (
    LoginRequest,
    MensajeRead,
    ModuloRead,
    SesionRead,
    UsuarioRead,
)
from app.schemas.webauthn import (
    CredencialBiometricaRead,
    CredencialRegistradaRead,
    LoginBiometricoRequest,
    OpcionesWebAuthnRead,
    RegistroBiometricoRequest,
    WebAuthnOpcionesLoginRequest,
)
from app.services import credencial_service, usuario_service

logger = logging.getLogger("app.api.auth")

router = APIRouter(prefix="/auth", tags=["Autenticación"])

#: Hash bcrypt de una contraseña ficticia. Se compara siempre que el email no
#: exista para que el tiempo de respuesta no revele si la cuenta está registrada
#: (defensa contra enumeración de usuarios).
HASH_FICTICIO = (
    "$2b$12$h1c7Kt5lKdyA8rRUx6jDMO6MS1hTVqB1smKk6Gz2Qe7AwhILwSLg."
)


def _fijar_cookie_sesion(response: Response, token: str, minutos: int) -> None:
    """Setea la cookie de sesión con los atributos de seguridad del proyecto."""
    response.set_cookie(
        key=NOMBRE_COOKIE_SESION,
        value=token,
        max_age=minutos * 60,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_sesion_segura,
        path="/",
    )


@router.post(
    "/login",
    response_model=SesionRead,
    summary="Iniciar sesión",
    description=(
        "Valida el email y la contraseña, comprueba que la cuenta esté activa, "
        "registra el último acceso y devuelve el token JWT (8 horas) junto con "
        "el usuario y sus permisos. Además deja la cookie HttpOnly de sesión. "
        "Tras 5 intentos fallidos en un minuto desde la misma IP responde 429."
    ),
)
def iniciar_sesion(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SesionRead:
    ip = ip_del_cliente(request)
    cuenta = clave_de_cuenta(str(payload.email))
    espera = max(
        limitador_login.segundos_bloqueado(ip),
        limitador_cuenta.segundos_bloqueado(cuenta),
    )
    if espera:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Demasiados intentos fallidos. Esperá unos segundos antes de "
                "volver a probar."
            ),
            headers={"Retry-After": str(espera)},
        )

    usuario = usuario_service.obtener_por_email(db, str(payload.email))
    if usuario is None:
        # Se compara contra un hash ficticio para no delatar la existencia del
        # email por el tiempo de respuesta.
        verificar_password(payload.password, HASH_FICTICIO)
        registrar_login_fallido(ip, str(payload.email), cuenta)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verificar_password(payload.password, usuario.hashed_password):
        registrar_login_fallido(ip, usuario.email, cuenta)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está deshabilitada. Hablá con un administrador.",
        )

    limitador_login.registrar_exito(ip)
    limitador_cuenta.registrar_exito(cuenta)
    usuario_service.registrar_acceso(db, usuario)
    token, minutos = crear_token_acceso(usuario)
    _fijar_cookie_sesion(response, token, minutos)
    logger.info("Login correcto de %s desde %s.", usuario.email, ip)
    return SesionRead(
        access_token=token,
        expira_en_minutos=minutos,
        usuario=usuario_service.leer_usuario(usuario),
    )


@router.post(
    "/logout",
    response_model=MensajeRead,
    summary="Cerrar sesión",
    description="Borra la cookie de sesión del navegador.",
)
def cerrar_sesion(response: Response) -> MensajeRead:
    response.delete_cookie(
        key=NOMBRE_COOKIE_SESION,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_sesion_segura,
    )
    return MensajeRead(detail="Sesión cerrada.")


@router.get(
    "/me",
    response_model=UsuarioRead,
    summary="Usuario de la sesión",
    description=(
        "Devuelve la cuenta autenticada con sus módulos. Es la primera llamada "
        "del panel: si responde 401 se muestra la pantalla de login."
    ),
)
def usuario_actual(usuario: Usuario = Depends(get_current_user)) -> UsuarioRead:
    return usuario_service.leer_usuario(usuario)


@router.get(
    "/modulos",
    response_model=list[ModuloRead],
    summary="Catálogo de módulos y permisos",
    description=(
        "Lista los códigos que se pueden marcar en «Usuarios y permisos». El "
        "frontend la usa para dibujar los checkboxes con su nombre visible."
    ),
)
def listar_modulos(_: Usuario = Depends(get_current_user)) -> list[ModuloRead]:
    return [
        ModuloRead(codigo=codigo, nombre=nombre, descripcion=detalle)
        for codigo, nombre, detalle in MODULOS
    ]


# --------------------------------------------------------------------------- #
# WebAuthn / passkeys: registro del dispositivo (requiere sesión)
# --------------------------------------------------------------------------- #
def _limitar_biometria(ip: str, cuenta: str) -> None:
    """Responde 429 si la IP o la cuenta agotaron los intentos biométricos."""
    espera = segundos_bloqueo_biometria(ip, cuenta)
    if espera:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Demasiados intentos con huella. Esperá unos segundos o "
                "ingresá con tu contraseña."
            ),
            headers={"Retry-After": str(espera)},
        )


def _credential_id_recibido(credencial: dict) -> Optional[str]:
    """Extrae el ``credential_id`` canónico del JSON del navegador.

    Se acepta ``rawId`` (el nombre del estándar) o ``id`` (el mismo valor en
    base64url). Un valor ausente o ilegible se trata como credencial
    desconocida, sin distinguir el motivo.
    """
    valor = credencial.get("rawId") or credencial.get("id") or ""
    if not isinstance(valor, str):
        return None
    return webauthn_core.credential_id_canonico(valor)


@router.post(
    "/webauthn/registro/opciones",
    response_model=OpcionesWebAuthnRead,
    summary="Opciones para registrar huella / Face ID",
    description=(
        "Genera el desafío para que el usuario ya autenticado registre la "
        "biometría de este celular o notebook. Las credenciales existentes se "
        "excluyen para no duplicar la misma llave."
    ),
)
def opciones_registro_biometrico(
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpcionesWebAuthnRead:
    credenciales = credencial_service.listar_credenciales(db, usuario.id)
    opciones, desafio = webauthn_core.opciones_de_registro(
        usuario_id=usuario.id,
        email=usuario.email,
        nombre_completo=usuario.nombre_completo,
        credenciales_ids=[credencial.credential_id for credencial in credenciales],
    )
    return OpcionesWebAuthnRead(
        challenge_token=webauthn_core.crear_token_desafio(
            desafio, usuario.id, webauthn_core.TIPO_DESAFIO_REGISTRO
        ),
        publicKey=opciones,
    )


@router.post(
    "/webauthn/registro/verificar",
    response_model=CredencialRegistradaRead,
    summary="Guardar la huella de este dispositivo",
    description=(
        "Verifica la firma del alta y guarda la clave pública. El desafío debe "
        "haber sido emitido para este mismo usuario; un desafío de login no "
        "sirve para registrar."
    ),
)
def verificar_registro_biometrico(
    payload: RegistroBiometricoRequest,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredencialRegistradaRead:
    usuario_id, desafio = webauthn_core.leer_token_desafio(
        payload.challenge_token, webauthn_core.TIPO_DESAFIO_REGISTRO
    )
    if usuario_id != usuario.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El desafío biométrico no corresponde a tu usuario.",
        )

    verificacion = webauthn_core.verificar_registro(payload.credential, desafio)
    credencial = credencial_service.guardar_credencial(
        db,
        usuario_id=usuario.id,
        credential_id=webauthn_core.b64url(verificacion.credential_id),
        public_key=verificacion.credential_public_key,
        sign_count=verificacion.sign_count,
        nombre_dispositivo=payload.nombre_dispositivo,
    )
    logger.info(
        "Dispositivo biométrico registrado para %s (%s).",
        usuario.email,
        credencial.nombre_dispositivo,
    )
    return CredencialRegistradaRead(
        detail=(
            f"«{credencial.nombre_dispositivo}» ya puede entrar con huella o "
            "Face ID."
        ),
        credencial=CredencialBiometricaRead.model_validate(credencial),
    )


@router.get(
    "/webauthn/credenciales",
    response_model=list[CredencialBiometricaRead],
    summary="Dispositivos con huella registrada",
    description="Lista los dispositivos del usuario de la sesión (sin claves).",
)
def listar_credenciales_biometricas(
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CredencialBiometricaRead]:
    return [
        CredencialBiometricaRead.model_validate(credencial)
        for credencial in credencial_service.listar_credenciales(db, usuario.id)
    ]


@router.delete(
    "/webauthn/credenciales/{credencial_id}",
    response_model=MensajeRead,
    summary="Eliminar un dispositivo",
    description=(
        "Borra la llave de un dispositivo perdido o vendido. Sólo se pueden "
        "eliminar las credenciales propias; la contraseña sigue funcionando."
    ),
)
def eliminar_credencial_biometrica(
    credencial_id: uuid.UUID,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MensajeRead:
    credencial_service.eliminar_credencial(db, usuario.id, credencial_id)
    logger.info("El usuario %s eliminó una credencial biométrica.", usuario.email)
    return MensajeRead(
        detail="Dispositivo eliminado. Podés seguir entrando con tu contraseña."
    )


# --------------------------------------------------------------------------- #
# WebAuthn / passkeys: ingreso con huella (público)
# --------------------------------------------------------------------------- #
@router.post(
    "/webauthn/login/opciones",
    response_model=OpcionesWebAuthnRead,
    summary="Desafío para entrar con huella / Face ID",
    description=(
        "Recibe el email y devuelve el desafío WebAuthn con las credenciales "
        "registradas por esa cuenta. La respuesta es la misma exista o no el "
        "email (no se puede enumerar usuarios); si la cuenta no tiene huellas, "
        "el navegador no muestra ningún diálogo y se usa la contraseña."
    ),
)
def opciones_login_biometrico(
    payload: WebAuthnOpcionesLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> OpcionesWebAuthnRead:
    ip = ip_del_cliente(request)
    cuenta = clave_de_cuenta(str(payload.email))
    _limitar_biometria(ip, cuenta)

    usuario = usuario_service.obtener_por_email(db, str(payload.email))
    credenciales = (
        credencial_service.listar_credenciales(db, usuario.id)
        if usuario is not None and usuario.activo
        else []
    )
    opciones, desafio = webauthn_core.opciones_de_login(
        credenciales_ids=[credencial.credential_id for credencial in credenciales]
    )
    # Cuenta inexistente o desactivada: se emite un desafío señuelo atado a un
    # UUID aleatorio. La forma de la respuesta es idéntica a la de una cuenta
    # real y la verificación posterior falla con el 401 genérico.
    dueno = usuario.id if usuario is not None else uuid.uuid4()
    return OpcionesWebAuthnRead(
        challenge_token=webauthn_core.crear_token_desafio(
            desafio, dueno, webauthn_core.TIPO_DESAFIO_LOGIN
        ),
        publicKey=opciones,
    )


@router.post(
    "/webauthn/login/verificar",
    response_model=SesionRead,
    summary="Ingresar con huella / Face ID",
    description=(
        "Valida la firma biométrica contra la clave pública guardada, actualiza "
        "el contador anti-clonación y el último acceso, y emite el mismo JWT + "
        "cookie HttpOnly + módulos que el login con contraseña."
    ),
)
def iniciar_sesion_biometrica(
    payload: LoginBiometricoRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> SesionRead:
    ip = ip_del_cliente(request)
    usuario_id, desafio = webauthn_core.leer_token_desafio(
        payload.challenge_token, webauthn_core.TIPO_DESAFIO_LOGIN
    )
    usuario = db.get(Usuario, usuario_id)
    email = usuario.email if usuario is not None else "desconocido"
    cuenta = clave_de_cuenta(email)
    _limitar_biometria(ip, cuenta)

    credencial_id = _credential_id_recibido(payload.credential)
    credencial = (
        credencial_service.buscar_por_credential_id(db, credencial_id)
        if credencial_id
        else None
    )
    # Mismo 401 para usuario inexistente, cuenta desactivada, credencial
    # desconocida o credencial de otra persona: no se filtra cuál falló.
    if (
        usuario is None
        or not usuario.activo
        or credencial is None
        or credencial.usuario_id != usuario.id
    ):
        registrar_biometria_fallida(ip, email, cuenta)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "No pudimos validar tu huella. Probá de nuevo o ingresá con "
                "tu contraseña."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        verificacion = webauthn_core.verificar_login(
            payload.credential,
            desafio,
            public_key=credencial.public_key,
            sign_count=credencial.sign_count,
        )
    except HTTPException:
        registrar_biometria_fallida(ip, email, cuenta)
        raise

    nuevo_contador = max(0, int(verificacion.new_sign_count or 0))
    # Protección contra clonación: un contador que retrocede implica que dos
    # copias de la misma llave se usaron. Si el autenticador no implementa el
    # contador (0) o repite el valor, se acepta y queda el aviso en el log.
    if (
        credencial.sign_count > 0
        and nuevo_contador > 0
        and nuevo_contador < credencial.sign_count
    ):
        registrar_biometria_fallida(ip, email, cuenta)
        logger.error(
            "Posible clonación de la credencial biométrica de %s "
            "(contador %s < %s).",
            usuario.email,
            nuevo_contador,
            credencial.sign_count,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Esta huella no se pudo validar por seguridad. Ingresá con tu "
                "contraseña y volvé a registrar el dispositivo."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    credencial_service.actualizar_contador(db, credencial, nuevo_contador)
    limitador_biometria.registrar_exito(ip)
    limitador_biometria_cuenta.registrar_exito(cuenta)
    usuario_service.registrar_acceso(db, usuario)
    token, minutos = crear_token_acceso(usuario)
    _fijar_cookie_sesion(response, token, minutos)
    logger.info(
        "Login biométrico correcto de %s desde %s (dispositivo: %s).",
        usuario.email,
        ip,
        credencial.nombre_dispositivo,
    )
    return SesionRead(
        access_token=token,
        expira_en_minutos=minutos,
        usuario=usuario_service.leer_usuario(usuario),
    )
