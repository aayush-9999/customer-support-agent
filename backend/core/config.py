# backend/core/config.py

from functools import lru_cache
from typing import Literal

from pydantic import Field, MongoDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for the Customer Support Agent.
    All values are read from environment variables (or .env file).
    Validated at startup — if something is missing, the app fails fast.
    """

    db_tool_mode: Literal["mongo", "postgres"] = Field(default="mongo")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # silently ignore unknown env vars
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = Field(default="Leafy Customer Support Agent")
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    debug: bool = Field(default=False)

    # ── Groq ───────────────────────────────────────────────────────────────
    groq_api_key: str = Field(..., description="Groq API key — required")
    groq_model: str = Field(default="meta-llama/llama-4-scout-17b-16e-instruct")
    groq_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    groq_max_tokens: int = Field(default=1024, gt=0)
    groq_timeout_seconds: int = Field(default=30, gt=0)

    # ── MongoDB ────────────────────────────────────────────────────────────
    mongo_uri: str = Field(..., description="MongoDB connection URI — required")
    mongo_db_name: str = Field(default="leafy_popup_store")
    mongo_orders_collection: str = Field(default="orders")
    mongo_customers_collection: str = Field(default="customers")
    mongo_tickets_collection: str = Field(default="tickets")
    mongo_connect_timeout_ms: int = Field(default=5000, gt=0)


            
    postgres_host:            str | None = Field(default=None)
    postgres_port:            int        = Field(default=5432)
    postgres_db:              str | None = Field(default=None)
    postgres_user:            str | None = Field(default=None)
    postgres_password:        str | None = Field(default=None)
    postgres_max_connections: int        = Field(default=10)

    # ── Agent ──────────────────────────────────────────────────────────────
    agent_max_iterations: int = Field(default=10, gt=0, le=25)
    agent_system_prompt_path: str = Field(
        default="knowledge/brand/voice.md"
    )
    agent_escalation_prompt_path: str = Field(
        default="knowledge/brand/escalation.md"
    )

    # ── Knowledge base ─────────────────────────────────────────────────────
    knowledge_manifest_path: str = Field(default="knowledge/manifest.json")
    knowledge_base_dir: str = Field(default="knowledge/")

    # Add inside Settings class in backend/core/config.py
    jwt_secret_key: str = Field(
        default="change-this-in-production-use-openssl-rand-hex-32",
        description="Secret key for JWT signing"
    )

    # ── Validators ─────────────────────────────────────────────────────────
    @field_validator("groq_api_key")
    @classmethod
    def groq_key_must_not_be_placeholder(cls, v: str) -> str:
        if v.strip() in ("", "your-groq-api-key", "sk-xxx"):
            raise ValueError(
                "GROQ_API_KEY is set to a placeholder. Add your real key to .env"
            )
        return v.strip()

    @field_validator("mongo_uri")
    @classmethod
    def mongo_uri_must_not_be_placeholder(cls, v: str) -> str:
        if v.strip() in ("", "your-mongo-uri", "mongodb://localhost"):
            raise ValueError(
                "MONGO_URI is set to a placeholder. Add your real URI to .env"
            )
        return v.strip()

    @field_validator("groq_temperature")
    @classmethod
    def temperature_precision(cls, v: float) -> float:
        return round(v, 2)

    @property
    def postgres_uri(self) -> str | None:
        if not all([self.postgres_host, self.postgres_db, self.postgres_user, self.postgres_password]):
            return None
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Helpers ────────────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    def redacted_summary(self) -> dict:
        """Safe dict for logging — never logs secrets."""
        return {
            "app_name": self.app_name,
            "environment": self.environment,
            "log_level": self.log_level,
            "groq_model": self.groq_model,
            "groq_temperature": self.groq_temperature,
            "groq_max_tokens": self.groq_max_tokens,
            "mongo_db_name": self.mongo_db_name,
            "agent_max_iterations": self.agent_max_iterations,
            "groq_api_key": "***" + self.groq_api_key[-4:],
            "mongo_uri": "***" + self.mongo_uri[-6:],
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns the singleton Settings instance.
    Cached after first call — safe to call anywhere without performance cost.
    Usage:
        from backend.core.config import get_settings
        settings = get_settings()
    """
    return Settings()


# Module-level convenience alias
settings = get_settings()