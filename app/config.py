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

settings = Settings()
