"""Configuración de la aplicación basada en variables de entorno (pydantic-settings).

Las variables se leen del archivo ``.env`` (si existe) y del entorno del sistema.
"""

import hashlib
import logging
import os
from typing import List, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.core.config")

# Vercel (serverless) monta el código en un sistema de archivos de sólo lectura:
# el único directorio escribible es ``/tmp``, por eso el SQLite por defecto se
# redirige ahí cuando no hay una base PostgreSQL configurada.
SQLITE_SERVERLESS_URL = "sqlite:////tmp/odontologico.db"

#: Base de la Graph API de Meta (WhatsApp Cloud API). Se usa cuando no se define
#: ``WHATSAPP_API_URL``: así, con sólo el token y el id del número, el envío real
#: queda habilitado sin tener que pegar la URL de la API a mano.
# La versión debe mantenerse alineada con la configurada en Meta Developers.
# v21 dejó de estar disponible; el proyecto de producción usa v26.
URL_WHATSAPP_POR_DEFECTO = "https://graph.facebook.com/v26.0"


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
        # Un valor vacío en el ``.env`` (p. ej. ``WHATSAPP_TOKEN=""``) significa
        # "sin configurar": se ignora y queda el valor por defecto del atributo.
        # Sin esto, un ``WHATSAPP_TOKEN`` vacío taparía un
        # ``WHATSAPP_ACCESS_TOKEN`` real (ver ``whatsapp_access_token``).
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
    def _avisar_credenciales_whatsapp_dudosas(self) -> "Settings":
        """Deja en el log un aviso si las credenciales de WhatsApp no encajan.

        La Cloud API espera el **Phone Number ID** de Meta: un número largo, sólo
        dígitos (p. ej. ``123456789012345``). El teléfono del consultorio
        (``+5493516432863``) **no** sirve en ese lugar: el ``POST`` a
        ``/<id>/messages`` responde 400/404 y el recordatorio queda ``FALLIDO``.
        No se aborta el arranque —el valor se usa igual—, pero el aviso evita
        diagnosticar a ciegas cuando el envío real falla.
        """
        telefono_o_id = (self.whatsapp_phone_number_id or "").strip()
        if telefono_o_id and not telefono_o_id.isdigit():
            logger.warning(
                "WHATSAPP_PHONE_NUMBER_ID=%r no parece el 'Phone Number ID' de "
                "Meta (debe ser un número largo, sin '+', espacios ni guiones). "
                "Si Meta responde 400/404, copiá el ID de WhatsApp -> API Setup.",
                telefono_o_id,
            )
        if self.whatsapp_access_token and not self.whatsapp_verify_token:
            logger.warning(
                "WHATSAPP_VERIFY_TOKEN sin configurar: Meta no podrá verificar "
                "el webhook GET /api/v1/whatsapp/webhook."
            )
        if self.whatsapp_access_token and not self.whatsapp_app_secret:
            logger.warning(
                "WHATSAPP_APP_SECRET sin configurar: se rechazarán los POST "
                "del webhook para impedir mensajes falsificados."
            )
        return self

    # --- WhatsApp Cloud API (opcional) ---
    # Base de la Graph API de Meta; se puede sobreescribir desde el ``.env``.
    whatsapp_api_url: Optional[str] = URL_WHATSAPP_POR_DEFECTO
    whatsapp_phone_number_id: Optional[str] = None
    # El token se acepta con los dos nombres que se ven en la documentación de
    # Meta: ``WHATSAPP_ACCESS_TOKEN`` (el histórico del proyecto) y
    # ``WHATSAPP_TOKEN`` (el corto). Gana el primero que esté definido.
    whatsapp_access_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_TOKEN"),
    )
    # Secreto del webhook: el valor que se carga en Meta (Webhook -> Verify token)
    # y que Meta reenvía como ``hub.verify_token`` en el ``GET`` de verificación.
    # Es independiente del access token (nunca debe usarse el token de la API
    # como verify token).
    whatsapp_verify_token: Optional[str] = None
    # Clave secreta de la aplicación de Meta (App Dashboard -> Settings -> Basic).
    # Meta firma cada POST del webhook con esta clave en X-Hub-Signature-256;
    # no es el verify token ni el access token de la Cloud API.
    whatsapp_app_secret: Optional[str] = None
    # Plantilla Utility aprobada en WhatsApp Manager. Debe tener cuatro
    # variables en el cuerpo y dos botones de respuesta rápida (ver .env.example).
    whatsapp_template_recordatorio: str = "recordatorio_turno"
    whatsapp_template_idioma: str = "es_AR"

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

    # --- Ficha de salud pública (anamnesis) ---
    # Base absoluta con la que se arma el enlace que recibe el paciente por
    # WhatsApp (``https://mi-consultorio.com/anamnesis/<token>``). Si no se
    # define, en Vercel se usa el dominio del proyecto y en local
    # ``http://localhost:8000``.
    public_base_url: Optional[str] = None
    # Secreto con el que se firman los tokens de la ficha de salud. Si no se
    # define se reutiliza el ``WHATSAPP_VERIFY_TOKEN`` (o, en su defecto, una
    # clave derivada de la configuración de la base). Conviene fijarlo en
    # producción para que los enlaces sean estables e imposibles de falsificar.
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

        Prioridad: ``ANAMNESIS_TOKEN_SECRET`` -> ``WHATSAPP_VERIFY_TOKEN`` ->
        una clave derivada de la URL de la base y del nombre de la app. La
        última opción evita un secreto hardcodeado y sigue siendo impredecible
        para quien no conozca la configuración del servidor.
        """
        if self.anamnesis_token_secret and self.anamnesis_token_secret.strip():
            return self.anamnesis_token_secret.strip()
        if self.whatsapp_verify_token and self.whatsapp_verify_token.strip():
            return self.whatsapp_verify_token.strip()
        semilla = f"{self.database_url}|{self.app_name}|anamnesis"
        return hashlib.sha256(semilla.encode("utf-8")).hexdigest()

    @property
    def whatsapp_envio_real(self) -> bool:
        """Indica si hay credenciales completas para enviar mensajes reales.

        Alcanza con el token (``WHATSAPP_TOKEN`` o ``WHATSAPP_ACCESS_TOKEN``) y
        el id del número: la URL de la API tiene un valor por defecto
        (``URL_WHATSAPP_POR_DEFECTO``), así que no hace falta definirla. Si falta
        alguna de esas dos credenciales el envío se SIMULA (se registra en logs y
        se genera un identificador de mensaje ficticio).
        """
        return bool(self.whatsapp_phone_number_id and self.whatsapp_access_token)


# Instancia única reutilizable en toda la aplicación.
settings = Settings()
