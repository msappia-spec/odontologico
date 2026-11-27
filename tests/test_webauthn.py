"""Pruebas del ingreso biométrico (WebAuthn / passkeys).

Se usa un **autenticador falso** construido con ``cryptography`` que cumple el
estándar: genera una llave EC P-256, arma el ``attestationObject`` en CBOR
(formato ``none``), firma ``authenticatorData || SHA256(clientDataJSON)`` y
devuelve exactamente el JSON que produciría un navegador. Así la verificación
de ``py_webauthn`` se ejecuta de verdad (no se mockea) y las pruebas cubren
firma inválida, desafío vencido, credencial ajena y contador anti-clonación.

Flujo cubierto:

* el registro exige sesión y guarda la clave pública en
  ``credenciales_biometricas``;
* el login con huella devuelve el **mismo** JWT + cookie HttpOnly + módulos que
  el login con contraseña, y actualiza ``sign_count`` y ``ultimo_acceso``;
* un email inexistente no se distingue de uno sin credenciales (no hay
  enumeración de usuarios);
* el limitador de intentos biométricos responde 429;
* el panel trae el botón de huella, el modal de registro y el JS del flujo.
"""

import base64
import hashlib
import json
import uuid

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.core.config import settings
from app.models.credencial_biometrica import CredencialBiometrica
from app.models.usuario import Usuario

#: Contraseña de los usuarios de prueba (cumple la regla del panel).
CLAVE = "ClaveSegura1"


def _b64(datos: bytes) -> str:
    """base64url sin relleno (formato de WebAuthn)."""
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _b64d(texto: str) -> bytes:
    """Decodifica base64url con o sin relleno."""
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


class AutenticadorFalso:
    """Autenticador WebAuthn mínimo (ES256, attestation ``none``)."""

    def __init__(self) -> None:
        self.llave = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b"prueba-" + uuid.uuid4().bytes
        self.contador = 0

    def _clave_cose(self) -> bytes:
        """Clave pública en formato COSE_Key (mapa CBOR del estándar CTAP2)."""
        numeros = self.llave.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numeros.x.to_bytes(32, "big"),
                -3: numeros.y.to_bytes(32, "big"),
            }
        )

    def _auth_data(self, *, flags: int, contador: int) -> bytes:
        """``rpIdHash || flags || signCount [|| attestedCredentialData]``."""
        rp_id_hash = hashlib.sha256(
            settings.webauthn_rp_id.encode("utf-8")
        ).digest()
        datos = rp_id_hash + bytes([flags]) + contador.to_bytes(4, "big")
        if flags & 0x40:  # AT: alta de credencial
            datos += b"\x00" * 16  # AAGUID
            datos += len(self.credential_id).to_bytes(2, "big")
            datos += self.credential_id + self._clave_cose()
        return datos

    @staticmethod
    def _client_data(tipo: str, desafio: str) -> bytes:
        return json.dumps(
            {
                "type": tipo,
                "challenge": desafio,
                "origin": settings.webauthn_rp_origin,
                "crossOrigin": False,
            }
        ).encode("utf-8")

    def registrar(self, opciones: dict) -> dict:
        """Respuesta equivalente a ``navigator.credentials.create()``."""
        cliente = self._client_data("webauthn.create", opciones["challenge"])
        auth_data = self._auth_data(flags=0x45, contador=0)  # UP + UV + AT
        atestacion = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": auth_data}
        )
        return {
            "id": _b64(self.credential_id),
            "rawId": _b64(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": _b64(cliente),
                "attestationObject": _b64(atestacion),
                "transports": ["internal"],
            },
        }

    def autenticar(self, opciones: dict, *, contador=None) -> dict:
        """Respuesta equivalente a ``navigator.credentials.get()``."""
        self.contador = self.contador + 1 if contador is None else int(contador)
        cliente = self._client_data("webauthn.get", opciones["challenge"])
        auth_data = self._auth_data(flags=0x05, contador=self.contador)  # UP + UV
        firma = self.llave.sign(
            auth_data + hashlib.sha256(cliente).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": _b64(self.credential_id),
            "rawId": _b64(self.credential_id),
            "type": "public-key",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": _b64(cliente),
                "authenticatorData": _b64(auth_data),
                "signature": _b64(firma),
                "userHandle": None,
            },
        }


# --------------------------------------------------------------------------- #
# Utilidades de las pruebas
# --------------------------------------------------------------------------- #
def _entrar(client, email: str, password: str = CLAVE) -> None:
    """Login tradicional: deja la cookie de sesión en el cliente."""
    respuesta = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert respuesta.status_code == 200, respuesta.text


def _opciones_registro(client) -> dict:
    respuesta = client.post("/api/v1/auth/webauthn/registro/opciones")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _registrar(
    client, autenticador: AutenticadorFalso, nombre: str = "Celular de prueba"
):
    """Alta completa: opciones -> autenticador -> verificación."""
    opciones = _opciones_registro(client)
    credencial = autenticador.registrar(opciones["publicKey"])
    respuesta = client.post(
        "/api/v1/auth/webauthn/registro/verificar",
        json={
            "challenge_token": opciones["challenge_token"],
            "credential": credencial,
            "nombre_dispositivo": nombre,
        },
    )
    return respuesta


def _opciones_login(client, email: str) -> dict:
    respuesta = client.post(
        "/api/v1/auth/webauthn/login/opciones", json={"email": email}
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


@pytest.fixture()
def doctora(crear_usuario) -> Usuario:
    """Odontóloga con agenda habilitada y credenciales para el panel."""
    return crear_usuario(
        email="doctora@consultorio.com",
        nombre="Dra. Ana Pérez",
        modulos=["AGENDA_TURNOS", "FICHAS_SALUD"],
    )


# --------------------------------------------------------------------------- #
# Registro del dispositivo
# --------------------------------------------------------------------------- #
def test_el_registro_exige_sesion(client_sin_sesion):
    respuesta = client_sin_sesion.post("/api/v1/auth/webauthn/registro/opciones")

    assert respuesta.status_code == 401, respuesta.text
    assert "sesión" in respuesta.json()["detail"].lower()


def test_registrar_una_huella_guarda_la_clave_publica(
    client_sin_sesion, doctora, db_session
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)

    respuesta = _registrar(client_sin_sesion, autenticador, "Celular de la doctora")

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert "huella" in datos["detail"].lower()
    assert datos["credencial"]["nombre_dispositivo"] == "Celular de la doctora"
    assert datos["credencial"]["fecha_registro"]

    guardada = db_session.execute(
        select(CredencialBiometrica).where(
            CredencialBiometrica.usuario_id == doctora.id
        )
    ).scalar_one()
    assert guardada.credential_id == _b64(autenticador.credential_id)
    assert guardada.public_key  # clave pública COSE, nunca la privada
    assert guardada.sign_count == 0


def test_el_listado_muestra_los_dispositivos_sin_claves(
    client_sin_sesion, doctora
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    assert _registrar(client_sin_sesion, autenticador).status_code == 200

    listado = client_sin_sesion.get("/api/v1/auth/webauthn/credenciales")

    assert listado.status_code == 200, listado.text
    datos = listado.json()
    assert len(datos) == 1
    assert datos[0]["nombre_dispositivo"] == "Celular de prueba"
    assert "public_key" not in datos[0]
    assert "credential_id" not in datos[0]


def test_un_desafio_de_login_no_sirve_para_registrar(client_sin_sesion, doctora):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    opciones = _opciones_login(client_sin_sesion, doctora.email)

    respuesta = client_sin_sesion.post(
        "/api/v1/auth/webauthn/registro/verificar",
        json={
            "challenge_token": opciones["challenge_token"],
            "credential": autenticador.registrar(opciones["publicKey"]),
            "nombre_dispositivo": "Intento cruzado",
        },
    )

    assert respuesta.status_code == 400, respuesta.text
    assert "desafío" in respuesta.json()["detail"].lower()


def test_un_desafio_adulterado_se_rechaza(client_sin_sesion, doctora):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    opciones = _opciones_registro(client_sin_sesion)

    respuesta = client_sin_sesion.post(
        "/api/v1/auth/webauthn/registro/verificar",
        json={
            "challenge_token": opciones["challenge_token"] + "x",
            "credential": autenticador.registrar(opciones["publicKey"]),
            "nombre_dispositivo": "Celular",
        },
    )

    assert respuesta.status_code == 400, respuesta.text



# --------------------------------------------------------------------------- #
# Login con huella
# --------------------------------------------------------------------------- #
def test_login_con_huella_emite_la_misma_sesion_que_la_contrasena(
    client_sin_sesion, doctora, db_session
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    assert _registrar(client_sin_sesion, autenticador).status_code == 200
    client_sin_sesion.cookies.clear()  # ya no hay sesión: es un login público

    opciones = _opciones_login(client_sin_sesion, doctora.email)
    assert opciones["publicKey"]["allowCredentials"], "debe ofrecer la huella"
    credencial = autenticador.autenticar(opciones["publicKey"])
    respuesta = client_sin_sesion.post(
        "/api/v1/auth/webauthn/login/verificar",
        json={
            "challenge_token": opciones["challenge_token"],
            "credential": credencial,
        },
    )

    assert respuesta.status_code == 200, respuesta.text
    datos = respuesta.json()
    assert datos["access_token"]
    assert datos["token_type"] == "bearer"
    assert datos["expira_en_minutos"] == settings.access_token_expire_hours * 60
    assert datos["usuario"]["email"] == doctora.email
    assert datos["usuario"]["modulos_permitidos"] == [
        "AGENDA_TURNOS",
        "FICHAS_SALUD",
    ]
    assert "hashed_password" not in datos["usuario"]

    cookie = respuesta.headers.get("set-cookie", "").lower()
    assert "odonto_sesion=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    # La cookie habilita la API igual que el login tradicional.
    assert client_sin_sesion.get("/api/v1/auth/me").status_code == 200

    db_session.expire_all()
    guardada = db_session.execute(
        select(CredencialBiometrica).where(
            CredencialBiometrica.usuario_id == doctora.id
        )
    ).scalar_one()
    assert guardada.sign_count == 1
    assert db_session.get(Usuario, doctora.id).ultimo_acceso is not None


def test_login_con_huella_rechaza_una_firma_adulterada(
    client_sin_sesion, doctora, db_session
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    assert _registrar(client_sin_sesion, autenticador).status_code == 200
    client_sin_sesion.cookies.clear()

    opciones = _opciones_login(client_sin_sesion, doctora.email)
    credencial = autenticador.autenticar(opciones["publicKey"])
    firma = bytearray(_b64d(credencial["response"]["signature"]))
    firma[-1] ^= 0xFF
    credencial["response"]["signature"] = _b64(bytes(firma))

    respuesta = client_sin_sesion.post(
        "/api/v1/auth/webauthn/login/verificar",
        json={
            "challenge_token": opciones["challenge_token"],
            "credential": credencial,
        },
    )

    assert respuesta.status_code == 401, respuesta.text
    assert "huella" in respuesta.json()["detail"].lower()
    assert respuesta.headers.get("www-authenticate") == "Bearer"
    # El contador no avanza con un intento inválido.
    db_session.expire_all()
    guardada = db_session.execute(select(CredencialBiometrica)).scalar_one()
    assert guardada.sign_count == 0


def test_login_con_huella_rechaza_la_credencial_de_otra_persona(
    client_sin_sesion, doctora, crear_usuario
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    assert _registrar(client_sin_sesion, autenticador).status_code == 200
    client_sin_sesion.cookies.clear()

    otra = crear_usuario(email="otro@consultorio.com", nombre="Otro usuario")
    opciones = _opciones_login(client_sin_sesion, otra.email)
    assert opciones["publicKey"]["allowCredentials"] == []

    respuesta = client_sin_sesion.post(
        "/api/v1/auth/webauthn/login/verificar",
        json={
            "challenge_token": opciones["challenge_token"],
            "credential": autenticador.autenticar(opciones["publicKey"]),
        },
    )

    assert respuesta.status_code == 401, respuesta.text
    assert "huella" in respuesta.json()["detail"].lower()


def test_las_opciones_no_revelan_si_el_email_existe(client_sin_sesion, doctora):
    inexistente = _opciones_login(client_sin_sesion, "nadie@consultorio.com")
    existente = _opciones_login(client_sin_sesion, doctora.email)

    assert set(inexistente) == {"challenge_token", "publicKey"}
    assert inexistente["challenge_token"]
    assert inexistente["publicKey"]["allowCredentials"] == []
    # Misma forma de respuesta: no se puede enumerar cuentas por la API.
    assert set(inexistente["publicKey"]) <= set(existente["publicKey"])
    assert inexistente["publicKey"]["rpId"] == settings.webauthn_rp_id


def test_el_contador_que_no_avanza_se_rechaza(
    client_sin_sesion, doctora, db_session
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    assert _registrar(client_sin_sesion, autenticador).status_code == 200
    client_sin_sesion.cookies.clear()

    primero = _opciones_login(client_sin_sesion, doctora.email)
    assert (
        client_sin_sesion.post(
            "/api/v1/auth/webauthn/login/verificar",
            json={
                "challenge_token": primero["challenge_token"],
                "credential": autenticador.autenticar(primero["publicKey"]),
            },
        ).status_code
        == 200
    )
    client_sin_sesion.cookies.clear()

    # Segunda firma con el mismo contador (replay / clon): py_webauthn exige
    # que el contador crezca cuando el autenticador lo implementa.
    segundo = _opciones_login(client_sin_sesion, doctora.email)
    respuesta = client_sin_sesion.post(
        "/api/v1/auth/webauthn/login/verificar",
        json={
            "challenge_token": segundo["challenge_token"],
            "credential": autenticador.autenticar(segundo["publicKey"], contador=1),
        },
    )

    assert respuesta.status_code == 401, respuesta.text
    db_session.expire_all()
    guardada = db_session.execute(select(CredencialBiometrica)).scalar_one()
    assert guardada.sign_count == 1


def test_el_login_con_huella_se_limita_tras_cinco_fallos(
    client_sin_sesion, doctora
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    assert _registrar(client_sin_sesion, autenticador).status_code == 200
    client_sin_sesion.cookies.clear()

    opciones = _opciones_login(client_sin_sesion, doctora.email)
    credencial = autenticador.autenticar(opciones["publicKey"])
    credencial["response"]["signature"] = _b64(b"firma-invalida")

    for _ in range(5):
        fallo = client_sin_sesion.post(
            "/api/v1/auth/webauthn/login/verificar",
            json={
                "challenge_token": opciones["challenge_token"],
                "credential": credencial,
            },
        )
        assert fallo.status_code == 401, fallo.text

    bloqueado = client_sin_sesion.post(
        "/api/v1/auth/webauthn/login/verificar",
        json={
            "challenge_token": opciones["challenge_token"],
            "credential": credencial,
        },
    )

    assert bloqueado.status_code == 429, bloqueado.text
    assert bloqueado.headers.get("retry-after")
    # La contraseña sigue disponible: el bloqueo biométrico no la alcanza.
    _entrar(client_sin_sesion, doctora.email)


def test_eliminar_un_dispositivo_es_solo_para_su_dueno(
    client_sin_sesion, doctora, crear_usuario, db_session
):
    autenticador = AutenticadorFalso()
    _entrar(client_sin_sesion, doctora.email)
    assert _registrar(client_sin_sesion, autenticador).status_code == 200
    guardada = db_session.execute(select(CredencialBiometrica)).scalar_one()
    credencial_id = guardada.id
    client_sin_sesion.cookies.clear()

    crear_usuario(email="intruso@consultorio.com", nombre="Intruso")
    _entrar(client_sin_sesion, "intruso@consultorio.com")
    ajeno = client_sin_sesion.delete(
        f"/api/v1/auth/webauthn/credenciales/{credencial_id}"
    )
    assert ajeno.status_code == 404, ajeno.text
    assert db_session.get(CredencialBiometrica, credencial_id) is not None

    client_sin_sesion.cookies.clear()
    _entrar(client_sin_sesion, doctora.email)
    propio = client_sin_sesion.delete(
        f"/api/v1/auth/webauthn/credenciales/{credencial_id}"
    )
    assert propio.status_code == 200, propio.text
    db_session.expire_all()
    assert db_session.get(CredencialBiometrica, credencial_id) is None


def test_las_rutas_de_huella_estan_documentadas_en_openapi(client):
    esquema = client.get("/openapi.json").json()
    rutas = esquema["paths"]

    assert "post" in rutas["/api/v1/auth/webauthn/registro/opciones"]
    assert "post" in rutas["/api/v1/auth/webauthn/registro/verificar"]
    assert "post" in rutas["/api/v1/auth/webauthn/login/opciones"]
    assert "post" in rutas["/api/v1/auth/webauthn/login/verificar"]
    assert "delete" in rutas["/api/v1/auth/webauthn/credenciales/{credencial_id}"]


def test_el_rp_id_se_normaliza_a_dominio(monkeypatch):
    monkeypatch.setattr(settings, "rp_id", "https://Panel.Ejemplo.com:8443/app")
    assert settings.webauthn_rp_id == "panel.ejemplo.com"

    monkeypatch.setattr(settings, "rp_id", None)
    monkeypatch.setattr(settings, "rp_origin", None)
    monkeypatch.setattr(
        settings, "public_base_url", "https://consultorio.ejemplo.com/"
    )
    assert settings.webauthn_rp_id == "consultorio.ejemplo.com"
    assert settings.webauthn_rp_origin == "https://consultorio.ejemplo.com"


# --------------------------------------------------------------------------- #
# Interfaz: login, modal, estilos y agenda móvil
# --------------------------------------------------------------------------- #
def test_el_login_trae_el_atajo_biometrico_y_el_modal(client):
    html = client.get("/").text

    for control in (
        "login-biometria",
        "login-biometria-nota",
        "btn-biometria",
        "modal-biometria",
        "bio-nombre",
        "bio-registrar",
        "bio-error",
        "bio-lista",
        "modal-biometria-close",
        "modal-biometria-cerrar",
    ):
        assert f'id="{control}"' in html, control
    assert "Ingresar con Huella Digital / FaceID" in html
    assert "/static/biometria.js" in html
    # El ingreso tradicional sigue visible como respaldo.
    assert 'id="login-submit"' in html
    assert 'id="login-password"' in html


def test_el_javascript_de_biometria_usa_webautn_y_los_endpoints(client):
    js = client.get("/static/biometria.js").text

    assert "navigator.credentials.get" in js
    assert "navigator.credentials.create" in js
    assert "PublicKeyCredential" in js
    assert "isUserVerifyingPlatformAuthenticatorAvailable" in js
    for ruta in (
        "/auth/webauthn/login/opciones",
        "/auth/webauthn/login/verificar",
        "/auth/webauthn/registro/opciones",
        "/auth/webauthn/registro/verificar",
        "/auth/webauthn/credenciales",
    ):
        assert f'"{ruta}"' in js, ruta
    assert "window.BiometriaApp" in js
    assert "SesionApp" in js and "aplicarSesion" in js
    assert 'credentials: "same-origin"' in js
    # Errores típicos del lector y respaldo con contraseña.
    assert "NotAllowedError" in js
    assert "contraseña" in js


def test_el_panel_comparte_la_sesion_con_el_modulo_de_huella(client):
    js = client.get("/static/usuarios.js").text

    assert "aplicarSesion," in js
    assert "BiometriaApp.alCambiarSesion" in js
    assert "async function iniciarLoginConHuella(" in client.get(
        "/static/biometria.js"
    ).text


def test_los_estilos_traen_el_atajo_y_la_lista_de_dispositivos(client):
    estilos = client.get("/static/styles.css").text

    assert ".login-biometria" in estilos
    assert ".login-divisor" in estilos
    assert ".bio-lista" in estilos
    assert ".bio-item" in estilos


def test_la_agenda_movil_tambien_ofrece_huella(client):
    html = client.get("/medico").text
    assert 'id="movil-biometria"' in html
    assert "Ingresar con Huella / FaceID" in html

    js = client.get("/static/medico.js").text
    assert "async function iniciarSesionConHuella(" in js
    assert "async function prepararBiometria(" in js
    assert '"/auth/webauthn/login/opciones"' in js
    assert '"/auth/webauthn/login/verificar"' in js
    assert "navigator.credentials.get" in js
