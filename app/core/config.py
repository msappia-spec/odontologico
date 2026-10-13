"""Configuración de la aplicación basada en variables de entorno (pydantic-settings).

Las variables se leen del archivo ``.env`` (si existe) y del entorno del sistema.
"""

from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    )

    # --- Aplicación ---
    app_name: str = "Sistema Odontológico API"
    app_version: str = "1.0.0"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    # --- Base de datos ---
    # Por defecto SQLite; para PostgreSQL usar
    # "postgresql+psycopg://usuario:password@host:5432/db".
    database_url: str = "sqlite:///./odontologico.db"

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

    # --- WhatsApp Cloud API (opcional) ---
    whatsapp_api_url: Optional[str] = None
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_access_token: Optional[str] = None

    # --- Recordatorios ---
    recordatorio_dias: int = 3
    # Código de país que se antepone a los teléfonos cargados en formato local
    # cuando se arma el enlace "Click-to-Chat" (https://wa.me/...).
    # Para Argentina móvil el formato es 54 + 9 + área + número -> "549".
    whatsapp_codigo_pais: str = "549"

    @property
    def whatsapp_envio_real(self) -> bool:
        """Indica si hay credenciales completas para enviar mensajes reales.

        Si es ``False`` el envío se SIMULA (se registra en logs y se genera un
        identificador de mensaje ficticio).
        """
        return all(
            (
                self.whatsapp_api_url,
                self.whatsapp_phone_number_id,
                self.whatsapp_access_token,
            )
        )


# Instancia única reutilizable en toda la aplicación.
settings = Settings()
