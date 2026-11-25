from collections.abc import Callable
import threading
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, ForeignKey, String, Text, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class DatabaseUnavailableError(RuntimeError):
    """Raised when the configured database cannot be reached or initialized."""


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)


class SessionToken(Base):
    __tablename__ = "sessions"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires: Mapped[int]


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    definition: Mapped[str] = mapped_column(Text)


class Installation(Base):
    __tablename__ = "installations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    definition: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="{}")


class Share(Base):
    __tablename__ = "shares"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    recipient_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    definition: Mapped[str] = mapped_column(Text)
    installation_id: Mapped[str | None] = mapped_column(
        ForeignKey("installations.id"), nullable=True
    )


def _normalize_driver_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def make_database(url: str) -> tuple[Engine, Callable[[], Session]]:
    driver_url = _normalize_driver_url(url)
    is_sqlite = driver_url.startswith("sqlite")
    connect_args: dict[str, Any] = (
        {"check_same_thread": False} if is_sqlite else {"connect_timeout": 5}
    )
    engine = create_engine(
        driver_url,
        connect_args=connect_args,
        pool_pre_ping=not is_sqlite,
    )
    raw_factory = sessionmaker(engine, expire_on_commit=False)
    schema_lock = threading.Lock()
    schema_initialized = False

    def ensure_schema() -> None:
        nonlocal schema_initialized
        if schema_initialized:
            return
        with schema_lock:
            if schema_initialized:
                return
            try:
                Base.metadata.create_all(engine)
            except (SQLAlchemyError, OSError):
                raise DatabaseUnavailableError("Database unavailable") from None
            schema_initialized = True

    try:
        ensure_schema()
    except DatabaseUnavailableError:
        pass

    def open_session() -> Session:
        ensure_schema()
        return raw_factory()

    return engine, open_session
