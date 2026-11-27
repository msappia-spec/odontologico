"""Pruebas del módulo de autenticación, usuarios y permisos.

Cubren el contrato de seguridad completo:

* la API exige sesión (401) y el login devuelve JWT + cookie HttpOnly;
* la contraseña es fuerte y se guarda hasheada (bcrypt), nunca en claro;
* el limitador frena la fuerza bruta (429 tras 5 fallos por IP);
* ``require_module`` deja pasar sólo a los módulos asignados (403 al resto) y
  el superadmin entra a todo;
* ``BORRAR_MOVIMIENTOS`` es un permiso aparte de la caja;
* el panel trae el login, el menú dinámico y la vista «Usuarios y Permisos»;
* el webhook y la ficha pública siguen abiertos (no usan sesión de usuario).

Los tests que necesitan una sesión válida usan ``client_sin_sesion`` + un
usuario real creado con ``crear_usuario``. Los demás usan ``client``, que
reemplaza ``get_current_user`` por un superadmin (así la suite histórica no
tuvo que cambiar).
"""

import os
import re

import pytest

from app.core.exceptions import ConflictoError
from app.core.security import (
    MODULOS,
    hash_password,
    normalizar_modulos,
    verificar_password,
)
from app.services import usuario_service
from scripts.crear_admin import (
    _url_sin_clave,
    asegurar_secret_key,
    cargar_entorno,
)

#: Contraseña de los usuarios de prueba (cumple la regla del panel).
CLAVE = "ClaveSegura1"


# --------------------------------------------------------------------------- #
# Contraseñas
# --------------------------------------------------------------------------- #
def test_la_contrasena_se_guarda_como_hash_bcrypt_con_sal():
    """El hash nunca es la contraseña y dos hashes de la misma clave difieren."""
    primero = hash_password(CLAVE)
    segundo = hash_password(CLAVE)

    assert primero != CLAVE
    assert primero.startswith("$2")
    assert primero != segundo  # sal aleatoria
    assert verificar_password(CLAVE, primero)
    assert verificar_password(CLAVE, segundo)
    assert not verificar_password("OtraClave1", primero)


def test_los_modulos_se_normalizan_y_rechazan_codigos_inventados():
    assert normalizar_modulos(["registros", "AGENDA_TURNOS", "REGISTROS"]) == [
        "AGENDA_TURNOS",
        "REGISTROS",
    ]
    try:
        normalizar_modulos(["PANEL_PRINCIPAL", "MODULO_FALSO"])
    except ValueError as exc:
        assert "MODULO_FALSO" in str(exc)
    else:  # pragma: no cover - el catálogo debe validar
        raise AssertionError("Un módulo desconocido debe rechazarse")
    # El catálogo tiene exactamente las diez secciones del menú.
    assert [codigo for codigo, _, _ in MODULOS] == [
        "PANEL_PRINCIPAL",
        "AGENDA_TURNOS",
        "FICHAS_SALUD",
        "CAJA_PRESUPUESTOS",
        "CUENTA_CORRIENTE",
        "PROVEEDORES_EGRESOS",
        "BORRAR_MOVIMIENTOS",
        "REGISTROS",
        "CONFIGURACION",
        "ADMIN_USUARIOS",
    ]


# --------------------------------------------------------------------------- #
# Sesión
# --------------------------------------------------------------------------- #
def test_la_api_sin_sesion_responde_401(client_sin_sesion):
    respuesta = client_sin_sesion.get("/api/v1/pacientes")

    assert respuesta.status_code == 401, respuesta.text
    assert "iniciar sesión" in respuesta.json()["detail"].lower()
    assert respuesta.headers.get("www-authenticate") == "Bearer"


def test_el_login_valido_devuelve_token_usuario_y_cookie_httponly(
    client_sin_sesion, crear_usuario, db_session
):
    usuario = crear_usuario(
        email="Admin@Consultorio.com",
        es_superadmin=True,
        modulos=["PANEL_PRINCIPAL"],
    )

    respuesta = client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "ADMIN@consultorio.com", "password": CLAVE},
    )

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["access_token"]
    assert datos["token_type"] == "bearer"
    assert datos["expira_en_minutos"] == 8 * 60
    assert datos["usuario"]["email"] == "admin@consultorio.com"
    assert datos["usuario"]["es_superadmin"] is True
    assert datos["usuario"]["modulos_permitidos"] == ["PANEL_PRINCIPAL"]
    # Nunca se expone el hash.
    assert "hashed_password" not in datos["usuario"]

    cookie = respuesta.headers.get("set-cookie", "").lower()
    assert "odonto_sesion=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie

    db_session.refresh(usuario)
    assert usuario.ultimo_acceso is not None

    # La cookie deja entrar sin volver a mandar el token.
    sesion = client_sin_sesion.get("/api/v1/auth/me")
    assert sesion.status_code == 200, sesion.text
    assert sesion.json()["id"] == str(usuario.id)


def test_el_login_rechaza_clave_incorrecta_y_email_inexistente_igual(
    client_sin_sesion, crear_usuario
):
    crear_usuario(email="secretaria@consultorio.com")

    con_clave_mala = client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "secretaria@consultorio.com", "password": "Incorrecta1"},
    )
    inexistente = client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "nadie@consultorio.com", "password": CLAVE},
    )

    assert con_clave_mala.status_code == 401
    assert inexistente.status_code == 401
    # Mismo mensaje: no se revela si el email existe.
    assert con_clave_mala.json()["detail"] == inexistente.json()["detail"]


def test_el_login_bloquea_una_cuenta_desactivada(client_sin_sesion, crear_usuario):
    crear_usuario(email="bloqueada@consultorio.com", activo=False)

    respuesta = client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "bloqueada@consultorio.com", "password": CLAVE},
    )

    assert respuesta.status_code == 403
    assert "deshabilitada" in respuesta.json()["detail"].lower()


def test_el_login_limita_cinco_intentos_fallidos_por_minuto(
    client_sin_sesion, crear_usuario
):
    crear_usuario(email="objetivo@consultorio.com")

    codigos = [
        client_sin_sesion.post(
            "/api/v1/auth/login",
            json={"email": "objetivo@consultorio.com", "password": "MalaClave1"},
        ).status_code
        for _ in range(6)
    ]

    assert codigos[:5] == [401] * 5
    assert codigos[5] == 429
    bloqueada = client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "objetivo@consultorio.com", "password": CLAVE},
    )
    assert bloqueada.status_code == 429
    assert bloqueada.headers.get("retry-after")


def test_el_limite_tambien_se_aplica_por_cuenta_aunque_cambie_la_ip(
    client_sin_sesion, crear_usuario
):
    """Rotar la IP (o falsificar X-Forwarded-For) no permite seguir probando."""
    crear_usuario(email="cuenta@consultorio.com")

    for indice in range(5):
        intento = client_sin_sesion.post(
            "/api/v1/auth/login",
            headers={"x-forwarded-for": f"10.0.0.{indice}"},
            json={"email": "cuenta@consultorio.com", "password": "MalaClave1"},
        )
        assert intento.status_code == 401

    bloqueado = client_sin_sesion.post(
        "/api/v1/auth/login",
        headers={"x-forwarded-for": "10.0.0.99"},
        json={"email": "cuenta@consultorio.com", "password": "MalaClave1"},
    )
    assert bloqueado.status_code == 429


def test_el_logout_borra_la_cookie(client_sin_sesion, crear_usuario):
    crear_usuario(email="salida@consultorio.com")

    assert (
        client_sin_sesion.post(
            "/api/v1/auth/login",
            json={"email": "salida@consultorio.com", "password": CLAVE},
        ).status_code
        == 200
    )
    salida = client_sin_sesion.post("/api/v1/auth/logout")
    assert salida.status_code == 200

    # TestClient guarda la cookie borrada: la sesión ya no vale.
    assert client_sin_sesion.get("/api/v1/auth/me").status_code == 401


# --------------------------------------------------------------------------- #
# Permisos por módulo
# --------------------------------------------------------------------------- #
def test_un_modulo_habilita_su_router_y_el_resto_responde_403(
    client_sin_sesion, crear_usuario
):
    crear_usuario(email="registros@consultorio.com", modulos=["REGISTROS"])
    assert (
        client_sin_sesion.post(
            "/api/v1/auth/login",
            json={"email": "registros@consultorio.com", "password": CLAVE},
        ).status_code
        == 200
    )

    assert client_sin_sesion.get("/api/v1/pacientes").status_code == 200
    # Secciones no asignadas: 403 con el módulo que falta en el mensaje.
    for ruta, seccion in (
        ("/api/v1/prestaciones", "Caja"),
        ("/api/v1/egresos/proveedores", "egresos"),
        ("/api/v1/turnos/sello", "Agenda"),
        ("/api/v1/usuarios", "Usuarios y permisos"),
    ):
        respuesta = client_sin_sesion.get(ruta)
        assert respuesta.status_code == 403, ruta
        assert seccion.lower() in respuesta.json()["detail"].lower(), ruta


def test_el_superadmin_entra_a_todas_las_secciones(client_sin_sesion, crear_usuario):
    crear_usuario(email="duenio@consultorio.com", es_superadmin=True)
    client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "duenio@consultorio.com", "password": CLAVE},
    )

    for ruta in (
        "/api/v1/pacientes",
        "/api/v1/prestaciones",
        "/api/v1/egresos/proveedores",
        "/api/v1/turnos/sello",
        "/api/v1/usuarios",
        "/api/v1/auth/modulos",
    ):
        assert client_sin_sesion.get(ruta).status_code == 200, ruta

    modulos = client_sin_sesion.get("/api/v1/auth/modulos").json()
    assert [m["codigo"] for m in modulos] == [c for c, _, _ in MODULOS]


def test_borrar_movimientos_es_un_permiso_aparte_de_la_caja(
    client_sin_sesion, crear_usuario
):
    crear_usuario(email="caja@consultorio.com", modulos=["CAJA_PRESUPUESTOS"])
    client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "caja@consultorio.com", "password": CLAVE},
    )

    # Con la caja habilitada pero sin BORRAR_MOVIMIENTOS, el DELETE se rechaza
    # antes de buscar el movimiento (403, no 404).
    respuesta = client_sin_sesion.delete("/api/v1/movimientos-caja/999")
    assert respuesta.status_code == 403
    assert "Borrar movimientos" in respuesta.json()["detail"]


def test_borrar_movimientos_habilitado_deja_pasar_hasta_el_404(
    client_sin_sesion, crear_usuario
):
    crear_usuario(
        email="depurador@consultorio.com",
        modulos=["CAJA_PRESUPUESTOS", "BORRAR_MOVIMIENTOS"],
    )
    client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "depurador@consultorio.com", "password": CLAVE},
    )

    # El permiso de borrado pasa; el 404 es del movimiento inexistente.
    assert client_sin_sesion.delete("/api/v1/movimientos-caja/999").status_code == 404
    assert (
        client_sin_sesion.delete(
            "/api/v1/finanzas/cuenta-corriente/transacciones/"
            "00000000-0000-0000-0000-000000000000"
        ).status_code
        == 404
    )


def test_desactivar_la_cuenta_corta_la_sesion_en_el_acto(
    client_sin_sesion, crear_usuario, db_session
):
    """El JWT sigue firmado, pero ``get_current_user`` revalida ``activo``."""
    usuario = crear_usuario(email="corte@consultorio.com", modulos=["REGISTROS"])
    assert (
        client_sin_sesion.post(
            "/api/v1/auth/login",
            json={"email": "corte@consultorio.com", "password": CLAVE},
        ).status_code
        == 200
    )
    assert client_sin_sesion.get("/api/v1/pacientes").status_code == 200

    usuario.activo = False
    db_session.commit()

    respuesta = client_sin_sesion.get("/api/v1/pacientes")
    assert respuesta.status_code == 403
    assert "deshabilitada" in respuesta.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# Administración de usuarios
# --------------------------------------------------------------------------- #
def test_el_admin_crea_el_usuario_con_modulos_y_vinculo(client, doctor):
    respuesta = client.post(
        "/api/v1/usuarios",
        json={
            "email": "Nueva.Secretaria@Consultorio.com",
            "nombre_completo": "  Nueva   Secretaria  ",
            "password": CLAVE,
            "modulos_permitidos": ["REGISTROS", "AGENDA_TURNOS"],
            "doctor_id": doctor.id,
        },
    )

    assert respuesta.status_code == 201, respuesta.text
    datos = respuesta.json()
    assert datos["email"] == "nueva.secretaria@consultorio.com"
    assert datos["nombre_completo"] == "Nueva Secretaria"
    assert datos["modulos_permitidos"] == ["AGENDA_TURNOS", "REGISTROS"]
    assert datos["es_superadmin"] is False
    assert datos["activo"] is True
    assert datos["doctor_id"] == doctor.id
    assert datos["doctor_nombre"] == doctor.nombre
    assert "hashed_password" not in datos

    # El email es único.
    repetido = client.post(
        "/api/v1/usuarios",
        json={
            "email": "nueva.secretaria@consultorio.com",
            "nombre_completo": "Otra Persona",
            "password": CLAVE,
            "modulos_permitidos": ["REGISTROS"],
        },
    )
    assert repetido.status_code == 409


def test_el_alta_rechaza_contrasenas_debiles_y_modulos_inventados(client):
    base = {
        "email": "debil@consultorio.com",
        "nombre_completo": "Clave Débil",
        "modulos_permitidos": ["REGISTROS"],
    }
    for indice, clave in enumerate(
        ("corta1A", "sinmayuscula1", "SINMINUSCULA1", "SinNumeros")
    ):
        respuesta = client.post(
            "/api/v1/usuarios",
            json=dict(base, email=f"debil{indice}@consultorio.com", password=clave),
        )
        assert respuesta.status_code == 422, clave
        assert "password" in respuesta.text.lower()

    inventado = client.post(
        "/api/v1/usuarios",
        json=dict(base, password=CLAVE, modulos_permitidos=["SECCION_INVENTADA"]),
    )
    assert inventado.status_code == 422
    assert "SECCION_INVENTADA" in inventado.text


def test_el_admin_actualiza_permisos_bloquea_y_blanquea_la_clave(
    client, db_session
):
    creado = client.post(
        "/api/v1/usuarios",
        json={
            "email": "empleada@consultorio.com",
            "nombre_completo": "Empleada Uno",
            "password": CLAVE,
            "modulos_permitidos": ["AGENDA_TURNOS"],
        },
    ).json()

    permisos = client.patch(
        f"/api/v1/usuarios/{creado['id']}/permisos",
        json={
            "modulos_permitidos": ["CUENTA_CORRIENTE", "CAJA_PRESUPUESTOS"],
            "activo": False,
        },
    )
    assert permisos.status_code == 200, permisos.text
    assert permisos.json()["modulos_permitidos"] == [
        "CAJA_PRESUPUESTOS",
        "CUENTA_CORRIENTE",
    ]
    assert permisos.json()["activo"] is False

    reset = client.patch(
        f"/api/v1/usuarios/{creado['id']}/reset-password",
        json={"password": "NuevaClave9"},
    )
    assert reset.status_code == 200, reset.text
    assert "Empleada Uno" in reset.json()["detail"]

    guardado = usuario_service.obtener_por_email(db_session, "empleada@consultorio.com")
    assert verificar_password("NuevaClave9", guardado.hashed_password)
    assert not verificar_password(CLAVE, guardado.hashed_password)

    listado = client.get("/api/v1/usuarios")
    assert listado.status_code == 200
    assert [u["email"] for u in listado.json()] == ["empleada@consultorio.com"]


def test_un_admin_comun_no_puede_crear_superadmins(client_sin_sesion, crear_usuario):
    crear_usuario(email="jefa@consultorio.com", modulos=["ADMIN_USUARIOS"])
    client_sin_sesion.post(
        "/api/v1/auth/login",
        json={"email": "jefa@consultorio.com", "password": CLAVE},
    )

    respuesta = client_sin_sesion.post(
        "/api/v1/usuarios",
        json={
            "email": "nuevo.duenio@consultorio.com",
            "nombre_completo": "Nuevo Dueño",
            "password": CLAVE,
            "es_superadmin": True,
            "modulos_permitidos": ["ADMIN_USUARIOS"],
        },
    )
    assert respuesta.status_code == 403
    assert "superadmin" in respuesta.json()["detail"].lower()


def test_no_se_puede_dejar_el_sistema_sin_superadmins(crear_usuario, db_session):
    unico = crear_usuario(email="unico@consultorio.com", es_superadmin=True)

    with pytest.raises(ConflictoError) as desactivar:
        usuario_service.actualizar_permisos(db_session, unico, activo=False)
    assert "sin ningún superadmin" in desactivar.value.detail

    with pytest.raises(ConflictoError) as degradar:
        usuario_service.actualizar_permisos(db_session, unico, es_superadmin=False)
    assert "sin ningún superadmin" in degradar.value.detail


# --------------------------------------------------------------------------- #
# Rutas públicas y script de arranque
# --------------------------------------------------------------------------- #
def test_el_webhook_la_ficha_publica_y_el_health_siguen_abiertos(client_sin_sesion):
    """Lo que no es del panel no puede quedar detrás del login."""
    assert client_sin_sesion.get("/health").status_code == 200

    webhook = client_sin_sesion.post("/api/v1/whatsapp/webhook", json={"event": "message"})
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["tipo"] == "IGNORADO"

    # Un token inválido es 400 (lo valida el HMAC), no 401 de sesión.
    ficha = client_sin_sesion.get("/api/v1/anamnesis/token-invalido")
    assert ficha.status_code == 400, ficha.text

    # La API administrativa sí exige sesión.
    assert client_sin_sesion.get("/api/v1/usuarios").status_code == 401


def test_el_script_del_admin_genera_una_secret_key_estable(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'APP_NAME="Franciosi"\nSECRET_KEY=""\nWAHA_URL="http://localhost:3000"\n',
        encoding="utf-8",
    )

    clave, creada = asegurar_secret_key(env)

    assert creada is True
    assert len(clave) >= 32
    texto = env.read_text(encoding="utf-8")
    assert texto.count("SECRET_KEY=") == 1
    assert f'SECRET_KEY="{clave}"' in texto
    assert 'WAHA_URL="http://localhost:3000"' in texto  # no pisa el resto

    # Es idempotente: la segunda ejecución respeta la clave existente.
    otra_vez, creada_otra_vez = asegurar_secret_key(env)
    assert otra_vez == clave
    assert creada_otra_vez is False

    # Un .env sin la variable también queda con la clave agregada.
    nuevo = tmp_path / ".env.nuevo"
    nuevo.write_text('APP_NAME="Otro"\n', encoding="utf-8")
    clave_nueva, creada_nueva = asegurar_secret_key(nuevo)
    assert creada_nueva is True and len(clave_nueva) >= 32


def test_crear_admin_usa_la_base_del_archivo_indicado(tmp_path, monkeypatch):
    """``--env-file`` debe mover también el ``DATABASE_URL`` del proceso.

    Sin esto, ``python -m scripts.crear_admin --env-file .env.vercel.local``
    creaba el administrador en la base del ``.env`` local (o en la SQLite por
    defecto) en lugar de la de producción.
    """
    archivo = tmp_path / ".env.vercel.local"
    archivo.write_text(
        'DATABASE_URL="postgresql://usuario:clave@neon.example/db?sslmode=require"\n'
        'SECRET_KEY="clave-de-produccion"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./local.db")
    monkeypatch.setenv("SECRET_KEY", "clave-local")

    cargar_entorno(archivo)

    assert os.environ["DATABASE_URL"].endswith("@neon.example/db?sslmode=require")
    assert os.environ["SECRET_KEY"] == "clave-de-produccion"


def test_crear_admin_falla_si_el_archivo_de_entorno_no_existe(tmp_path):
    """Un ``--env-file`` con una ruta equivocada no debe caer en la base local."""
    with pytest.raises(SystemExit, match="No existe el archivo de entorno"):
        cargar_entorno(tmp_path / "no-existe.local")


def test_los_mensajes_del_admin_ocultan_la_contrasena_de_la_base():
    """La URL que se imprime al crear el admin no puede filtrar la credencial."""
    visible = _url_sin_clave(
        "postgresql+psycopg://usuario:secreta@host:5432/base?sslmode=require"
    )
    assert "secreta" not in visible
    assert visible == (
        "postgresql+psycopg://usuario:***@host:5432/base?sslmode=require"
    )
    assert _url_sin_clave("sqlite:///./odontologico.db") == "sqlite:///./odontologico.db"


# --------------------------------------------------------------------------- #
# Interfaz: login, menú dinámico y vista de usuarios
# --------------------------------------------------------------------------- #
def test_el_panel_trae_la_pantalla_de_login_y_la_sesion_en_el_menu(client):
    html = client.get("/").text

    for control in (
        "pantalla-login",
        "form-login",
        "login-email",
        "login-password",
        "login-submit",
        "login-error",
        "sesion-avatar",
        "sesion-nombre",
        "sesion-rol",
        "btn-logout",
    ):
        assert f'id="{control}"' in html, control
    assert "Ingresar al panel" in html
    assert "/static/usuarios.js" in html


def test_cada_seccion_del_menu_declara_su_modulo_para_ocultarse(client):
    html = client.get("/").text

    pares = dict(re.findall(r'data-view="([^"]+)" data-modulo="([^"]+)"', html))
    assert pares == {
        "dashboard": "PANEL_PRINCIPAL",
        "calendario": "AGENDA_TURNOS",
        "disponibilidad": "AGENDA_TURNOS",
        "recordatorios": "AGENDA_TURNOS",
        "fichas-salud": "FICHAS_SALUD",
        "facturacion": "CAJA_PRESUPUESTOS",
        "presupuestos": "CAJA_PRESUPUESTOS",
        "cuenta-corriente": "CUENTA_CORRIENTE",
        "egresos": "PROVEEDORES_EGRESOS",
        "borrar-movimientos": "BORRAR_MOVIMIENTOS",
        "doctores": "REGISTROS",
        "pacientes": "REGISTROS",
        "obras-sociales": "CONFIGURACION",
        "bot-whatsapp": "CONFIGURACION",
        "usuarios": "ADMIN_USUARIOS",
    }
    # El ítem de usuarios vive en Configuración y sólo lo ve quien administra.
    assert 'data-view="usuarios" data-modulo="ADMIN_USUARIOS"' in html
    assert "Usuarios y Permisos" in html
    # Los botones del encabezado siguen el permiso de la agenda.
    assert html.count('data-modulo-requerido="AGENDA_TURNOS"') == 2


def test_la_vista_de_usuarios_tiene_tabla_tarjetas_y_checkboxes(client):
    html = client.get("/").text

    for control in (
        "view-usuarios",
        "usuarios-subtitulo",
        "count-usuarios",
        "btn-nuevo-usuario",
        "btn-refrescar-usuarios",
        "lista-usuarios",
        "modal-usuario",
        "form-usuario",
        "usuario-id",
        "usuario-nombre",
        "usuario-email",
        "usuario-password",
        "usuario-doctor",
        "usuario-superadmin",
        "usuario-modulos",
        "usuario-marcar-todos",
        "usuario-error",
        "modal-reset-password",
        "form-reset-password",
        "usuario-reset-id",
        "usuario-reset-password",
        "usuario-reset-error",
    ):
        assert f'id="{control}"' in html, control
    # Checkboxes de permisos y aviso de que las cuentas se desactivan.
    assert 'class="permiso-grid mt-2"' in html
    assert "se <span class=\"font-semibold\">desactivan</span>" in html


def test_el_javascript_de_usuarios_usa_los_endpoints_del_modulo(client):
    js = client.get("/static/usuarios.js").text

    assert '"/auth/me"' in js
    assert '"/auth/login"' in js
    assert '"/auth/logout"' in js
    assert '"/auth/modulos"' in js
    assert '"/usuarios"' in js
    assert '"/permisos"' in js and '"/reset-password"' in js

    assert "async function iniciarPanel(" in js
    assert "async function iniciarSesion(" in js
    assert "function sesionExpirada(" in js
    assert "async function cargarUsuarios(" in js
    assert "function renderUsuarios(" in js
    assert "function abrirModalUsuario(" in js
    assert "async function guardarUsuario(" in js
    assert "async function alternarCuenta(" in js
    assert "window.SesionApp" in js
    # La cookie viaja en cada petición del mismo origen.
    assert 'credentials: "same-origin"' in js


def test_el_panel_aplica_los_permisos_al_menu_y_al_encabezado(client):
    js = client.get("/static/app.js").text

    assert "const VISTA_MODULO = {" in js
    assert "function puede(codigo)" in js
    assert "function puedeVista(vista)" in js
    assert "function aplicarPermisos()" in js
    assert 'a.classList.toggle("hide", !puedeVista(a.dataset.view))' in js
    assert "[data-modulo-requerido]" in js
    assert "Usuarios y Permisos" in js
    # La carga inicial no pide secciones sin permiso ni tumba todo por un 403.
    assert "Promise.allSettled(pedidos)" in js
    assert 'puede("AGENDA_TURNOS")' in js
    # Una sesión vencida vuelve al login.
    assert "window.SesionApp.sesionExpirada" in js


def test_la_hoja_de_estilos_trae_el_login_y_los_permisos(client):
    estilos = client.get("/static/styles.css").text

    assert ".login-screen" in estilos
    assert ".login-card" in estilos
    assert ".login-error" in estilos
    assert ".permiso-grid" in estilos
    assert ".permiso-opcion" in estilos
    assert ".usuario-chip" in estilos


def test_la_agenda_movil_tambien_pide_sesion(client):
    html = client.get("/medico").text
    assert 'id="pantalla-login-movil"' in html
    assert 'id="form-login-movil"' in html
    assert 'id="movil-email"' in html
    assert 'id="movil-password"' in html
    assert 'id="movil-login-error"' in html

    js = client.get("/static/medico.js").text
    assert "async function verificarSesion(" in js
    assert "async function iniciarSesionMovil(" in js
    assert "function mostrarLoginMovil(" in js
    assert '"/auth/me"' in js and '"/auth/login"' in js
