"""Configuración de la aplicación basada en variables de entorno (pydantic-settings).

Las variables se leen del archivo ``.env`` (si existe) y del entorno del sistema.
"""

import hashlib
import logging
import os
from typing import List, Optional
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.core.config")

# Vercel (serverless) monta el código en un sistema de archivos de sólo lectura:
# el único directorio escribible es ``/tmp``, por eso el SQLite por defecto se
# redirige ahí cuando no hay una base PostgreSQL configurada.
SQLITE_SERVERLESS_URL = "sqlite:////tmp/odontologico.db"

#: URL local por defecto de WAHA (WhatsApp HTTP API). El despliegue puede
#: sobreescribirla con ``WAHA_URL``; por ejemplo ``http://turnos_waha:3000``
#: cuando la app y WAHA comparten una red Docker, o una URL pública
#: ``https://<host>.nip.io`` cuando WAHA corre en otra máquina.
URL_WAHA_POR_DEFECTO = "http://localhost:3000"


class Settings(BaseSettings):
    """Parámetros de configuración de la aplicación.

    Los nombres de las variables de entorno coinciden (case-insensitive) con los
    de los atributos; por ejemplo ``DATABASE_URL`` -> ``database_url``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Un valor vacío en el ``.env`` (p. ej. ``WAHA_API_KEY=""``) significa
        # "sin configurar": se ignora y queda el valor por defecto del atributo.
        env_ignore_empty=True,
    )

    # --- Aplicación ---
    app_name: str = "Franciosi Odontología API"
    app_version: str = "1.0.0"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    # --- Base de datos ---
    # Por defecto SQLite; para PostgreSQL usar
    # "postgresql+psycopg://usuario:password@host:5432/db".
    database_url: str = "sqlite:///./odontologico.db"
    # Destino opcional de ``scripts/migrar_sqlite_a_postgres`` (la base de la
    # nube, p. ej. la de Neon). Vive acá para poder dejarla en el ``.env`` local
    # en lugar de pegar la cadena en la línea de comandos; el despliegue no la
    # usa, porque la app sólo lee ``database_url``.
    database_url_destino: Optional[str] = None

    # --- CORS (interfaz web y clientes externos) ---
    # Orígenes autorizados a consumir la API desde otro dominio. Con ``["*"]``
    # se acepta cualquiera (API local sin cookies). Para restringir, definir en
    # el ``.env`` una lista JSON, por ejemplo:
    # CORS_ORIGINS=["http://localhost:5173","https://panel.midominio.com"]
    cors_origins: List[str] = ["*"]
    # Habilita el envío de credenciales (cookies/Authorization). El navegador lo
    # rechaza cuando el origen es comodín, así que requiere ``cors_origins``
    # explícitos.
    cors_allow_credentials: bool = False

    @model_validator(mode="after")
    def _adaptar_base_al_entorno(self) -> "Settings":
        """Ajusta la base de datos cuando la app corre en Vercel.

        Vercel define la variable de entorno ``VERCEL`` y monta el proyecto en
        un sistema de archivos de **sólo lectura**. Un SQLite relativo
        (``sqlite:///./odontologico.db``) no se puede abrir, así que el
        ``lifespan`` fallaría al crear el esquema y la Function devolvería 500.
        Para que el despliegue arranque igual (útil para demos) se redirige a
        ``/tmp``; los datos son **efímeros**: para persistir hay que definir
        ``DATABASE_URL`` con una base PostgreSQL (por ejemplo Neon).
        """
        if not os.getenv("VERCEL"):
            return self
        if not self.database_url.startswith("sqlite"):
            return self
        if self.database_url.startswith("sqlite:////tmp"):
            return self
        logger.warning(
            "Vercel detectado: se usa SQLite efímero en %s. Definí DATABASE_URL "
            "con PostgreSQL para conservar los datos entre despliegues.",
            SQLITE_SERVERLESS_URL,
        )
        self.database_url = SQLITE_SERVERLESS_URL
        return self

    @model_validator(mode="after")
    def _normalizar_driver_postgres(self) -> "Settings":
        """Fuerza el driver ``psycopg`` (v3) en las URLs de PostgreSQL.

        Los proveedores administrados (Neon, Supabase, Render, Railway) entregan
        la cadena como ``postgresql://usuario:clave@host/db`` (o ``postgres://``,
        el formato viejo de Heroku). SQLAlchemy interpreta ese prefijo como el
        driver ``psycopg2``, **que no está instalado** (se usa psycopg 3), y el
        arranque fallaría con ``ModuleNotFoundError: No module named 'psycopg2'``
        justo dentro de la Function de Vercel. Acá se reescribe al driver real
        para poder pegar la cadena del proveedor tal cual.
        """
        for prefijo in ("postgres://", "postgresql://"):
            if self.database_url.startswith(prefijo):
                logger.info(
                    "DATABASE_URL sin driver explícito: se usa 'psycopg' (v3)."
                )
                self.database_url = (
                    "postgresql+psycopg://" + self.database_url[len(prefijo):]
                )
                break
        return self

    @model_validator(mode="after")
    def _avisar_credenciales_waha_incompletas(self) -> "Settings":
        """Deja un aviso cuando falta la API key de WAHA.

        Sin ``WAHA_API_KEY`` el cliente no puede autenticarse contra
        ``POST /api/sendText``: los envíos se simulan para no romper el panel ni
        las pruebas. El aviso evita diagnosticar a ciegas cuando el despliegue
        parece configurado pero los mensajes no salen.
        """
        if (self.waha_url or "").strip() and not (self.waha_api_key or "").strip():
            logger.warning(
                "WAHA_API_KEY sin configurar: los mensajes de WhatsApp se "
                "simularán. Definí WAHA_URL, WAHA_API_KEY y WAHA_SESSION."
            )
        return self

    # --- WAHA (WhatsApp HTTP API) ---
    # URL base del contenedor/servidor WAHA, sin ``/api``. En Docker suele ser
    # ``http://turnos_waha:3000``; en local, ``http://localhost:3000``.
    waha_url: Optional[str] = URL_WAHA_POR_DEFECTO
    # Valor que WAHA espera en la cabecera ``X-Api-Key``.
    waha_api_key: Optional[str] = None
    # Sesión de WAHA que envía y recibe los mensajes.
    waha_session: str = "default"

    # --- Chatbot de respuestas automáticas (FAQ Bot) ---
    # Código del nomenclador con el que se responde la tarifa de consulta
    # (Capítulo I del Nomenclador de Córdoba: consulta, examen y fichado).
    whatsapp_consulta_codigo: str = "10100"
    # Datos que el bot devuelve con una opción de tipo UBICACION (el número lo
    # define el menú configurado en el panel). Son configurables para que cada
    # consultorio publique los suyos sin tocar el código: basta definirlos en el
    # ``.env``.
    whatsapp_consultorio_direccion: str = "Av. Colón 1234, Córdoba Capital"
    whatsapp_consultorio_horarios: str = (
        "Lunes a viernes de 9:00 a 13:00 y de 15:00 a 19:00"
    )

    # --- Recordatorios ---
    recordatorio_dias: int = 3
    # Código de país que se antepone a los teléfonos cargados en formato local
    # cuando se arma el enlace "Click-to-Chat" (https://wa.me/...).
    # Para Argentina móvil el formato es 54 + 9 + área + número -> "549".
    whatsapp_codigo_pais: str = "549"
    # Secreto que autoriza al cron de Vercel a disparar envíos automáticos.
    cron_secret: Optional[str] = None

    # --- Autenticación del panel (Módulo de usuarios y permisos) ---
    # Clave con la que se firman los JWT (HS256). Debe ser larga y aleatoria y
    # vivir en el ``.env`` (``SECRET_KEY``); ``scripts/crear_admin.py`` la genera
    # si falta. Si no está definida se deriva una clave estable de la
    # configuración (válido para desarrollo y pruebas, no para producción).
    secret_key: Optional[str] = None
    # Horas que dura la sesión firmada (cookie HttpOnly + token Bearer).
    access_token_expire_hours: int = 8
    # Fuerza bruta: intentos fallidos permitidos por IP dentro de la ventana.
    login_max_intentos: int = 5
    login_ventana_segundos: int = 60

    # --- WebAuthn / passkeys (huella digital y Face ID) ---
    # ``RP_ID`` es el dominio sin esquema ni puerto con el que el navegador
    # vincula las passkeys ("localhost" en desarrollo, "mi-panel.vercel.app" en
    # producción). ``RP_ORIGIN`` es el origen completo ("https://…"). Si no se
    # definen se deducen de ``url_publica``, que ya conoce el dominio de Vercel;
    # en producción conviene fijarlos para que un cambio de dominio no invalide
    # las huellas registradas.
    rp_id: Optional[str] = None
    rp_origin: Optional[str] = None
    #: Nombre visible del consultorio en el diálogo de huella/Face ID.
    rp_name: str = "Franciosi Odontología"
    #: Minutos que vale el desafío firmado entre "opciones" y "verificar".
    webauthn_challenge_expire_minutes: int = 2
    #: Tiempo máximo que el navegador espera la biometría (milisegundos).
    webauthn_timeout_ms: int = 60000

    # --- Portal público de turnos (``/reservar``) ---
    # Límites antiabuso por IP. El portal NO exige sesión de empleado, así que
    # estas ventanas son la primera barrera contra bots: las lecturas (doctores,
    # disponibilidad, DNI) tienen un tope amplio y las reservas/cancelaciones uno
    # más estricto. Los contadores son en memoria y por proceso (ver
    # ``app.core.rate_limit``); en serverless complementan, no reemplazan, el
    # control de concurrencia de la base.
    portal_max_peticiones: int = 60
    portal_max_operaciones: int = 12
    portal_ventana_segundos: int = 60
    # Cantidad máxima de turnos futuros vigentes (PENDIENTE o CONFIRMADO) que un
    # mismo DNI puede tener al mismo tiempo desde el portal.
    portal_max_turnos_activos: int = 2
    # Antelación mínima (en horas) para que el paciente pueda cancelar solo.
    portal_cancelacion_horas: int = 12
    # Duración de cada turno reservado desde el portal. Coincide con el bloque
    # que ofrece la agenda del Módulo 1 (30 minutos).
    portal_duracion_turno_min: int = 30
    # Desfase respecto de UTC con el que el portal interpreta "ahora" para
    # descartar horarios ya pasados. La agenda del ERP guarda la **hora de pared
    # del consultorio** (el panel envía ``2026-11-30T09:00`` sin zona), así que
    # comparar contra UTC ocultaría los turnos de la mañana en Argentina
    # (UTC-3). Se expresa en horas y no con ``zoneinfo`` para no depender de la
    # base de zonas horarias en Windows; Argentina no aplica horario de verano.
    portal_desfase_horario_horas: int = -3

    # --- Ficha de salud pública (anamnesis) ---
    # Base absoluta con la que se arma el enlace que recibe el paciente por
    # WhatsApp (``https://mi-consultorio.com/anamnesis/<token>``). Si no se
    # define, en Vercel se usa el dominio del proyecto y en local
    # ``http://localhost:8000``.
    public_base_url: Optional[str] = None
    # Secreto con el que se firman los tokens de la ficha de salud. Si no se
    # define se usa una clave derivada de la configuración de la base. Conviene
    # fijarlo en producción para que los enlaces sean estables e imposibles de
    # falsificar.
    anamnesis_token_secret: Optional[str] = None

    @property
    def url_publica(self) -> str:
        """URL base absoluta para enlaces públicos (sin ``/`` final)."""
        if self.public_base_url and self.public_base_url.strip():
            return self.public_base_url.strip().rstrip("/")
        # Vercel expone el dominio de producción/preview como variable de
        # entorno. Se usa para que los enlaces de WhatsApp funcionen sin tener
        # que configurar la URL a mano en cada despliegue.
        dominio = (
            os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
            or os.getenv("VERCEL_URL")
            or ""
        ).strip()
        if dominio:
            return f"https://{dominio.rstrip('/')}"
        return "http://localhost:8000"

    @property
    def anamnesis_secreto_efectivo(self) -> str:
        """Clave usada para firmar/validar los tokens de anamnesis.

        Prioridad: ``ANAMNESIS_TOKEN_SECRET`` -> una clave derivada de la URL
        de la base y del nombre de la app. La última opción evita un secreto
        hardcodeado y sigue siendo impredecible para quien no conozca la
        configuración del servidor.
        """
        if self.anamnesis_token_secret and self.anamnesis_token_secret.strip():
            return self.anamnesis_token_secret.strip()
        semilla = f"{self.database_url}|{self.app_name}|anamnesis"
        return hashlib.sha256(semilla.encode("utf-8")).hexdigest()

    @property
    def jwt_secret_efectivo(self) -> str:
        """Clave con la que se firman los JWT del panel.

        Prioridad: ``SECRET_KEY`` del entorno -> una clave derivada de la
        configuración. La derivada mantiene el sistema arrancable en desarrollo y
        en las pruebas sin ``.env``, pero **en producción hay que definir
        SECRET_KEY**: si cambia la base o el nombre de la app, las sesiones
        emitidas dejan de valer (los usuarios vuelven a loguearse).
        """
        if self.secret_key and self.secret_key.strip():
            return self.secret_key.strip()
        semilla = f"{self.database_url}|{self.app_name}|sesion"
        return hashlib.sha256(semilla.encode("utf-8")).hexdigest()

    @property
    def cookie_sesion_segura(self) -> bool:
        """Indica si la cookie de sesión debe viajar sólo por HTTPS.

        Se deduce de la URL pública: en Vercel es ``https://`` y la cookie se
        marca ``Secure``; en local (``http://localhost:8000``) no, porque el
        navegador descartaría una cookie ``Secure`` enviada por HTTP y el login
        no funcionaría en desarrollo.
        """
        return self.url_publica.lower().startswith("https://")

    @property
    def webauthn_rp_id(self) -> str:
        """Dominio con el que se vinculan las passkeys (sin esquema ni puerto).

        Prioridad: ``RP_ID`` explícito -> host de ``url_publica`` (que en Vercel
        sale de ``VERCEL_PROJECT_PRODUCTION_URL``). Se normaliza por si alguien
        pega una URL completa en la variable: WebAuthn exige un dominio.
        """
        valor = (self.rp_id or "").strip()
        if valor:
            valor = valor.split("://", 1)[-1].split("/", 1)[0]
            # Un puerto no forma parte del RP ID; IPv6 entre corchetes se deja.
            if valor and not valor.startswith("[") and valor.count(":") == 1:
                valor = valor.split(":", 1)[0]
            return valor.lower() or "localhost"
        host = urlsplit(self.url_publica).hostname or "localhost"
        return host.lower()

    @property
    def webauthn_rp_origin(self) -> str:
        """Origen exacto que valida WebAuthn (``https://dominio[:puerto]``).

        Sin ``RP_ORIGIN`` se usa ``url_publica``: en local es
        ``http://localhost:8000`` (los navegadores tratan ``localhost`` como
        origen seguro y permiten WebAuthn sin HTTPS) y en Vercel es el dominio
        ``https`` del proyecto. No debe llevar barra final.
        """
        if self.rp_origin and self.rp_origin.strip():
            return self.rp_origin.strip().rstrip("/")
        return self.url_publica

    @property
    def webauthn_rp_name(self) -> str:
        """Nombre visible del consultorio en el diálogo de huella/Face ID."""
        return (self.rp_name or "").strip() or self.app_name

    @property
    def whatsapp_envio_real(self) -> bool:
        """Indica si WAHA está configurado para enviar mensajes reales.

        Hacen falta la URL y la API key: ``WAHA_URL`` tiene un valor local por
        defecto, pero sin ``WAHA_API_KEY`` no se puede autenticar el ``POST`` a
        ``/api/sendText``. En ese caso el envío se SIMULA (se registra en logs y
        se genera un identificador de mensaje ficticio), igual que antes.
        """
        return bool(
            (self.waha_url or "").strip() and (self.waha_api_key or "").strip()
        )

    @property
    def waha_url_base(self) -> str:
        """URL de WAHA sin la barra final (``http://host:3000``)."""
        return (self.waha_url or "").strip().rstrip("/")


# Instancia única reutilizable en toda la aplicación.
settings = Settings()
