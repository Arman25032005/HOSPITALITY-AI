from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    environment: str = "development"
    app_secret_key: str = "dev-only-insecure-secret-change-me"
    backend_cors_origins: str = "http://localhost:3000"

    database_url: str = "postgresql+psycopg://hospitality:hospitality@localhost:5432/hospitality"
    redis_url: str = "redis://localhost:6379/0"

    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    log_level: str = "INFO"

    # RAG: "hashing" is a deterministic local lexical embedder for dev/test, never for production.
    embedding_provider: str = "hashing"  # hashing | sentence-transformers
    embedding_model: str = "all-MiniLM-L6-v2"
    vector_store_provider: str = "local"  # local | pinecone
    pinecone_api_key: str = ""
    pinecone_index: str = "hospitality-knowledge"
    rag_top_k: int = 5
    rag_min_coverage: float = 0.5
    rag_min_cosine: float = 0.55
    max_upload_bytes: int = 5 * 1024 * 1024

    @field_validator("environment")
    @classmethod
    def _valid_env(cls, v: str) -> str:
        if v not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        return v

    def validate_for_runtime(self) -> None:
        if self.environment == "production" and self.app_secret_key.startswith("dev-only"):
            raise RuntimeError("APP_SECRET_KEY must be set to a strong secret in production")
        if self.environment == "production":
            if self.vector_store_provider != "pinecone" or not self.pinecone_api_key:
                raise RuntimeError("Production requires VECTOR_STORE_PROVIDER=pinecone with "
                                   "PINECONE_API_KEY set; the local store is dev/test only")
            if self.embedding_provider == "hashing":
                raise RuntimeError("Production requires a real EMBEDDING_PROVIDER, not 'hashing'")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
