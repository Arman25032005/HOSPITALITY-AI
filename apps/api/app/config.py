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

    @field_validator("environment")
    @classmethod
    def _valid_env(cls, v: str) -> str:
        if v not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        return v

    def validate_for_runtime(self) -> None:
        if self.environment == "production" and self.app_secret_key.startswith("dev-only"):
            raise RuntimeError("APP_SECRET_KEY must be set to a strong secret in production")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
