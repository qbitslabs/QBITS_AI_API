# AI service core: config.
# Config, logging, HTTP, or cache used by WhatsApp orchestration.
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, Any


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

    # LLM / Groq / OpenRouter Integration
    GROQ_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: str = ""
    GROQ_FALLBACK_API_KEY: Optional[str] = None
    OPENROUTER_FALLBACK_API_KEY: Optional[str] = None

    # Base URL: Defaults to Groq OpenAI-compatible endpoint
    GROQ_BASE_URL: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://api.groq.com/openai/v1"

    # Default model chain (Groq production chat models supporting tool calls)
    DEFAULT_MODEL: str = "llama-3.3-70b-versatile"
    FALLBACK_MODEL: str = "llama-3.1-8b-instant"
    FALLBACK_MODEL_2: Optional[str] = "openai/gpt-oss-120b"

    def model_post_init(self, __context: Any) -> None:
        # Cross-populate API keys so either GROQ_API_KEY or OPENROUTER_API_KEY works on Render
        if not self.OPENROUTER_API_KEY and self.GROQ_API_KEY:
            self.OPENROUTER_API_KEY = self.GROQ_API_KEY
        elif not self.GROQ_API_KEY and self.OPENROUTER_API_KEY:
            self.GROQ_API_KEY = self.OPENROUTER_API_KEY

        if not self.OPENROUTER_FALLBACK_API_KEY and self.GROQ_FALLBACK_API_KEY:
            self.OPENROUTER_FALLBACK_API_KEY = self.GROQ_FALLBACK_API_KEY
        elif not self.GROQ_FALLBACK_API_KEY and self.OPENROUTER_FALLBACK_API_KEY:
            self.GROQ_FALLBACK_API_KEY = self.OPENROUTER_FALLBACK_API_KEY

        # If GROQ_BASE_URL is set, use it
        if self.GROQ_BASE_URL:
            self.OPENROUTER_BASE_URL = self.GROQ_BASE_URL.strip().rstrip("/")

        # Auto-heal: If user has a Groq key (starts with 'gsk_') but left OPENROUTER_BASE_URL
        # pointing to openrouter.ai in their Render dashboard, force Groq endpoint
        key = self.api_key
        if key.startswith("gsk_") and "openrouter.ai" in self.OPENROUTER_BASE_URL:
            self.OPENROUTER_BASE_URL = "https://api.groq.com/openai/v1"

        # Sanitize models when targeting Groq
        if "groq.com" in self.OPENROUTER_BASE_URL:
            if ":free" in self.DEFAULT_MODEL:
                self.DEFAULT_MODEL = "llama-3.3-70b-versatile"
            # Prompt guard is a classifier, not a conversational LLM
            if "prompt-guard" in (self.FALLBACK_MODEL or "") or ":free" in (self.FALLBACK_MODEL or ""):
                self.FALLBACK_MODEL = "llama-3.1-8b-instant"
            if ":free" in (self.FALLBACK_MODEL_2 or ""):
                self.FALLBACK_MODEL_2 = "openai/gpt-oss-120b"

    @property
    def api_key(self) -> str:
        """Active primary LLM API key."""
        return (self.GROQ_API_KEY or self.OPENROUTER_API_KEY or "").strip()

    @property
    def fallback_api_key(self) -> str:
        """Active fallback LLM API key."""
        return (self.GROQ_FALLBACK_API_KEY or self.OPENROUTER_FALLBACK_API_KEY or "").strip() or self.api_key

    @property
    def llm_base_url(self) -> str:
        """Active LLM base URL."""
        return self.OPENROUTER_BASE_URL.strip().rstrip("/")

    # CGS Backend Internal Service Integration
    CGS_INTERNAL_URL: str = "https://qbits-ai-api.onrender.com"
    CGS_INTERNAL_SERVICE_KEY: str = "cgs-internal-service-secret-key-998877"
    CORS_ORIGINS: str = "https://cgs-wa-api.onrender.com,https://cgs-backend-zcic.onrender.com,https://cgs-admin-frontend-pearl.vercel.app,https://cgs-frontend-mu.vercel.app/"

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
