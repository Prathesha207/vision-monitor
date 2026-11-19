from pathlib import Path
import os
import sys
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

_ROOT = Path(__file__).parent.parent
_ENV_PATH = _ROOT / ".env"
_DEFAULT_DB_PATH = (_ROOT / "vision_ai.db").resolve()


class Settings(BaseSettings):
    DATABASE_URL: str = f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}"
    APP_NAME: str = "Vision AI Command Center"
    DEBUG: bool = True

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v):
        if isinstance(v, str) and v.startswith("sqlite:///"):
            raw_path = v[len("sqlite:///"):]
            # If path is relative (e.g. ./vision_ai.db or vision_ai.db), anchor to _ROOT
            if not os.path.isabs(raw_path) or raw_path.startswith("./") or raw_path.startswith(".\\"):
                clean_path = raw_path.lstrip("./").lstrip(".\\")
                target = (_ROOT / clean_path).resolve()
                return f"sqlite:///{target.as_posix()}"
        return v

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, v):
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes", "debug", "dev", "development")
        return bool(v)

    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
