import os
from typing import Final, Literal

APP_SERVICE_NAME: Final[Literal["vibeos"]] = "vibeos"
APP_VERSION: Final[str] = "0.1.0"
DEFAULT_DATABASE_URL: Final[str] = "sqlite:///./vibeos.db"
SUPPORTED_DB_SCHEMES: Final[tuple[str, ...]] = (
    "sqlite://",
    "sqlite+",
    "postgresql://",
    "postgresql+",
)


def resolve_database_url(database_url: str | None = None) -> str:
    candidate = (
        database_url
        if database_url is not None
        else os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    normalized = candidate.strip()
    if not normalized:
        raise ValueError("DATABASE_URL must not be empty")
    if not normalized.startswith(SUPPORTED_DB_SCHEMES):
        raise ValueError("Unsupported DATABASE_URL scheme")
    return normalized
