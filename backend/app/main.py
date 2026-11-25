from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
import json
import secrets
import time
from typing import Any, TypeVar, cast

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.health import router as health_router
from app.core.config import APP_VERSION, resolve_database_url
from app.core.security import check_password, hash_password, token_digest
from app.domain.schemas import Credentials, Definition, ShareRequest, StateRequest
from app.infrastructure.database import (
    Base,
    DatabaseUnavailableError,
    Installation,
    Project,
    SessionToken,
    Share,
    User,
    make_database,
)

ModelT = TypeVar("ModelT", bound=Base)


def create_app(database_url: str | None = None) -> FastAPI:
    resolved_url = resolve_database_url(database_url)

    @asynccontextmanager
    async def lifespan(api_instance: FastAPI) -> AsyncIterator[None]:
        engine, factory = make_database(resolved_url)
        api_instance.state.factory = factory
        yield
        engine.dispose()

    api = FastAPI(
        title="VibeOS API",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    api.include_router(health_router)
    bearer = HTTPBearer(auto_error=False)

    def db() -> Iterator[Session]:
        try:
            with api.state.factory() as session:
                yield session
        except HTTPException:
            raise
        except (SQLAlchemyError, OSError, DatabaseUnavailableError):
            raise HTTPException(
                status_code=503, detail="Database unavailable"
            ) from None

    def current(
        auth: HTTPAuthorizationCredentials | None = Depends(bearer),
        session: Session = Depends(db),
    ) -> str:
        token = (
            session.get(SessionToken, token_digest(auth.credentials)) if auth else None
        )
        if not token or token.expires <= int(time.time()):
            raise HTTPException(401, "Authentication required")
        return token.user_id

    def owned(
        session: Session,
        model: type[ModelT],
        identifier: str,
        field: str,
        user: str,
    ) -> ModelT:
        row = session.get(model, identifier)
        if not row or getattr(row, field) != user:
            raise HTTPException(404, "Resource not found")
        return row

    def definition_json(definition: Definition) -> str:
        ids = [c.id for c in definition.components]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Duplicate component IDs")
        return definition.model_dump_json()

    def install(session: Session, user: str, definition: str) -> Installation:
        item = Installation(user_id=user, definition=definition, state="{}")
        session.add(item)
        session.flush()
        return item

    @api.post("/api/v1/auth/register", status_code=201)
    def register(body: Credentials, session: Session = Depends(db)) -> dict[str, str]:
        user = User(
            username=body.username.lower(), password_hash=hash_password(body.password)
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(409, "Username unavailable") from exc
        return {"username": user.username}

    @api.post("/api/v1/auth/login")
    def login(body: Credentials, session: Session = Depends(db)) -> dict[str, str]:
        user = session.scalar(
            select(User).where(User.username == body.username.lower())
        )
        if not user or not check_password(body.password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")
        token = secrets.token_urlsafe(32)
        session.add(
            SessionToken(
                digest=token_digest(token),
                user_id=user.id,
                expires=int(time.time()) + 3600,
            )
        )
        session.commit()
        return {"token": token, "username": user.username}

    @api.post("/api/v1/auth/logout")
    def logout(
        auth: HTTPAuthorizationCredentials | None = Depends(bearer),
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> dict[str, bool]:
        if auth is not None:
            stored = session.get(SessionToken, token_digest(auth.credentials))
            if stored is not None and stored.user_id == user:
                session.delete(stored)
                session.commit()
        return {"ok": True}

    @api.post("/api/v1/projects", status_code=201)
    def create_project(
        body: Definition,
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> dict[str, Any]:
        project = Project(owner_id=user, definition=definition_json(body))
        session.add(project)
        session.commit()
        return {"id": project.id, "definition": body.model_dump()}

    @api.get("/api/v1/projects")
    def projects(
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> list[dict[str, Any]]:
        rows = session.scalars(select(Project).where(Project.owner_id == user))
        return [{"id": p.id, "definition": json.loads(p.definition)} for p in rows]

    @api.post("/api/v1/projects/{identifier}/install", status_code=201)
    def install_project(
        identifier: str,
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> dict[str, str]:
        project = owned(session, Project, identifier, "owner_id", user)
        item = install(session, user, project.definition)
        session.commit()
        return {"id": item.id}

    @api.post("/api/v1/projects/{identifier}/share", status_code=201)
    def share(
        identifier: str,
        body: ShareRequest,
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> dict[str, str]:
        project = owned(session, Project, identifier, "owner_id", user)
        recipient = session.scalar(
            select(User).where(User.username == body.recipient.lower())
        )
        if not recipient or recipient.id == user:
            raise HTTPException(400, "Invalid recipient")
        item = Share(
            sender_id=user, recipient_id=recipient.id, definition=project.definition
        )
        session.add(item)
        session.commit()
        return {"id": item.id}

    @api.get("/api/v1/shares/incoming")
    def incoming(
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> list[dict[str, Any]]:
        rows = session.scalars(select(Share).where(Share.recipient_id == user))
        return [
            {
                "id": s.id,
                "definition": json.loads(s.definition),
                "accepted": s.installation_id is not None,
            }
            for s in rows
        ]

    @api.post("/api/v1/shares/{identifier}/accept")
    def accept(
        identifier: str,
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> dict[str, str | None]:
        share_row = owned(session, Share, identifier, "recipient_id", user)
        if share_row.installation_id:
            return {"id": share_row.installation_id}
        item = install(session, user, share_row.definition)
        result = cast(
            CursorResult[Any],
            session.execute(
                update(Share)
                .where(Share.id == identifier, Share.installation_id.is_(None))
                .values(installation_id=item.id)
            ),
        )
        if result.rowcount != 1:
            session.rollback()
            return {
                "id": owned(
                    session, Share, identifier, "recipient_id", user
                ).installation_id
            }
        session.commit()
        return {"id": item.id}

    @api.get("/api/v1/installations")
    def installations(
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> list[dict[str, Any]]:
        rows = session.scalars(select(Installation).where(Installation.user_id == user))
        return [
            {
                "id": i.id,
                "definition": json.loads(i.definition),
                "state": json.loads(i.state),
            }
            for i in rows
        ]

    @api.put("/api/v1/installations/{identifier}/state")
    def state(
        identifier: str,
        body: StateRequest,
        user: str = Depends(current),
        session: Session = Depends(db),
    ) -> dict[str, dict[str, str | int]]:
        item = owned(session, Installation, identifier, "user_id", user)
        components = {
            c.id: c.type
            for c in Definition.model_validate_json(item.definition).components
        }
        for key, value in body.values.items():
            if (
                components.get(key) == "input"
                and isinstance(value, str)
                and len(value) <= 2000
            ):
                continue
            if (
                components.get(key) == "counter"
                and type(value) is int
                and abs(value) <= 1000000
            ):
                continue
            raise HTTPException(422, "Invalid component state")
        item.state = json.dumps(body.values)
        session.commit()
        return {"state": body.values}

    return api


app = create_app()
