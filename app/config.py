from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    # Database
    MW_DATABASE_URL: str = Field(
        default="postgresql://memoryweb:memoryweb@localhost:5432/memoryweb"
    )
    MW_DB_SCHEMA: str = Field(default="memoryweb")

    # Redis / Celery
    MW_REDIS_URL: str = Field(default="redis://localhost:6379/1")
    MW_CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/1")
    MW_CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/1")

    # Ollama
    MW_OLLAMA_BASE_URL: str = Field(default="http://localhost:11434")
    MW_OLLAMA_MODEL: str = Field(default="llama3.2:3b")

    # Embeddings
    MW_EMBED_MODEL: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")
    MW_EMBED_DIM: int = Field(default=384)

    # Server
    MW_PORT: int = Field(default=8100)

    # Source paths (optional — empty = not configured)
    MW_SESSIONS_DIR: str = Field(default="")
    MW_SHARED_CHAT_DIR: str = Field(default="")
    MW_SQLITE_MEMORY_PATH: str = Field(default="")

    # Pipeline tuning
    MW_SEGMENT_MAX_MESSAGES: int = Field(default=20)
    MW_SEGMENT_GAP_MINUTES: int = Field(default=30)
    MW_BATCH_SIZE: int = Field(default=500)
    MW_EMBED_BATCH_SIZE: int = Field(default=64)

    # Security
    MW_API_KEY: str = Field(default="")          # empty = no auth (dev mode)
    MW_CORS_ORIGINS: str = Field(default="*")    # comma-separated list or *

    # Multi-tenant / auth (Migration 013a+)
    MW_AUTH_ENABLED: bool = Field(default=False)          # opt-in, backward compatible
    MW_JWT_SECRET: str = Field(default="change-me-in-production-use-256-bit-random")
    MW_JWT_ALGORITHM: str = Field(default="HS256")
    MW_JWT_EXPIRY_HOURS: int = Field(default=24)

    # License enforcement (Phase 4)
    MW_LICENSE_KEY: str = Field(default="")               # empty = community/owner mode
    MW_LICENSE_SERVER: str = Field(default="https://license.memorybeast.app")

    # Poisoning LLM validation (Phase 2)
    MW_POISON_LLM_ENABLED: bool = Field(default=False)    # opt-in for LLM poisoning layer
    MW_POISON_LLM_MODEL: str = Field(default="llama3.1:8b")
    MW_POISON_LLM_TIMEOUT: float = Field(default=5.0)     # seconds; fail-open if exceeded
    MW_POISON_LLM_MIN_SCORE: float = Field(default=0.2)   # only call LLM if score >= this


settings = Settings()
