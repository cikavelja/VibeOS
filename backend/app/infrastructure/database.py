from sqlalchemy import create_engine, String, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from uuid import uuid4

def uid():
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
    installation_id: Mapped[str | None] = mapped_column(ForeignKey("installations.id"), nullable=True)

def make_database(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
