# AI service core: config.
# Config, logging, HTTP, or cache used by WhatsApp orchestration.
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


# Settings.
class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = False

    # Dedicated AI PostgreSQL (asyncpg). SQLite is not supported.
    DATABASE_URL: str = ""

    @property
    def async_database_url(self) -> str:
        url = (self.DATABASE_URL or "").strip()
        if not url:
            raise RuntimeError("DATABASE_URL is required and must be a PostgreSQL connection string.")
        if "sqlite" in url.lower():
            raise RuntimeError("SQLite is not supported. Use postgresql+asyncpg:// for the AI service.")
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)

        try:
            from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
            parsed = urlsplit(url)
            # Filter out parameters that asyncpg doesn't accept as keyword arguments
            allowed_params = [
                (k, v)
                for k, v in parse_qsl(parsed.query)
                if k.lower() not in {"pgbouncer", "sslmode", "pool_timeout", "connection_limit"}
            ]
            url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(allowed_params), parsed.fragment))
        except Exception:
            pass

        return url

    # OpenRouter Integration
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_FALLBACK_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    FALLBACK_MODEL: str = "deepseek/deepseek-v4-flash-0731:free"
    FALLBACK_MODEL_2: Optional[str] = "google/gemma-4-26b-a4b-it:free"

    # CGS Backend Internal Service Integration
    CGS_INTERNAL_URL: str = "http://localhost:4000"
    CGS_INTERNAL_SERVICE_KEY: str = "cgs-internal-service-secret-key-998877"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5174,http://localhost:5175,http://localhost:4000,http://localhost:8080,http://127.0.0.1:8080"

    # HTTP & LLM Parameters
    REQUEST_TIMEOUT: int = 30
    # Free models: fail faster so we can skip/failover instead of waiting full timeout
    FREE_MODEL_TIMEOUT: int = 12

    # Background summary merge (idle chats with pending fact addons)
    SUMMARY_INACTIVITY_HOURS: float = 1.0
    SUMMARY_WORKER_INTERVAL_SEC: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
