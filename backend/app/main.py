import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.security import hash_password, check_password, token_digest
from app.domain.schemas import Credentials, Definition, ShareRequest, StateRequest
from app.infrastructure.database import make_database, User, SessionToken, Project, Installation, Share


def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(api):
        engine, factory = make_database(database_url or os.getenv("DATABASE_URL", "sqlite:///./vibeos.db"))
        api.state.factory = factory
        yield
        engine.dispose()
    api = FastAPI(title="VibeOS development preview", lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)
    def db():
        with api.state.factory() as session:
            yield session
    def current(auth: HTTPAuthorizationCredentials | None = Depends(bearer), session: Session = Depends(db)):
        token = session.get(SessionToken, token_digest(auth.credentials)) if auth else None
        if not token or token.expires <= int(time.time()):
            raise HTTPException(401, "Authentication required")
        return token.user_id
    def owned(session, model, identifier, field, user):
        row = session.get(model, identifier)
        if not row or getattr(row, field) != user:
            raise HTTPException(404, "Resource not found")
        return row
    def definition_json(definition):
        ids = [c.id for c in definition.components]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Duplicate component IDs")
        return definition.model_dump_json()
    def install(session, user, definition):
        item = Installation(user_id=user, definition=definition, state="{}")
        session.add(item)
        session.flush()
        return item
    @api.get("/api/v1/health")
    def health():
        return {"status": "ok", "service": "vibeos"}
    @api.post("/api/v1/auth/register", status_code=201)
    def register(body: Credentials, session: Session = Depends(db)):
        user = User(username=body.username.lower(), password_hash=hash_password(body.password))
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "Username unavailable")
        return {"username": user.username}
    @api.post("/api/v1/auth/login")
    def login(body: Credentials, session: Session = Depends(db)):
        user = session.scalar(select(User).where(User.username == body.username.lower()))
        if not user or not check_password(body.password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")
        token = secrets.token_urlsafe(32)
        session.add(SessionToken(digest=token_digest(token), user_id=user.id, expires=int(time.time())+3600))
        session.commit()
        return {"token": token, "username": user.username}
    @api.post("/api/v1/auth/logout")
    def logout(auth: HTTPAuthorizationCredentials = Depends(bearer), user: str = Depends(current), session: Session = Depends(db)):
        session.delete(session.get(SessionToken, token_digest(auth.credentials)))
        session.commit()
        return {"ok": True}
    @api.post("/api/v1/projects", status_code=201)
    def create_project(body: Definition, user: str = Depends(current), session: Session = Depends(db)):
        project = Project(owner_id=user, definition=definition_json(body))
        session.add(project); session.commit()
        return {"id": project.id, "definition": body.model_dump()}
    @api.get("/api/v1/projects")
    def projects(user: str = Depends(current), session: Session = Depends(db)):
        return [{"id": p.id, "definition": json.loads(p.definition)} for p in session.scalars(select(Project).where(Project.owner_id == user))]
    @api.post("/api/v1/projects/{identifier}/install", status_code=201)
    def install_project(identifier: str, user: str = Depends(current), session: Session = Depends(db)):
        project = owned(session, Project, identifier, "owner_id", user)
        item = install(session, user, project.definition); session.commit()
        return {"id": item.id}
    @api.post("/api/v1/projects/{identifier}/share", status_code=201)
    def share(identifier: str, body: ShareRequest, user: str = Depends(current), session: Session = Depends(db)):
        project = owned(session, Project, identifier, "owner_id", user)
        recipient = session.scalar(select(User).where(User.username == body.recipient.lower()))
        if not recipient or recipient.id == user:
            raise HTTPException(400, "Invalid recipient")
        item = Share(sender_id=user, recipient_id=recipient.id, definition=project.definition)
        session.add(item); session.commit()
        return {"id": item.id}
    @api.get("/api/v1/shares/incoming")
    def incoming(user: str = Depends(current), session: Session = Depends(db)):
        return [{"id": s.id, "definition": json.loads(s.definition), "accepted": s.installation_id is not None} for s in session.scalars(select(Share).where(Share.recipient_id == user))]
    @api.post("/api/v1/shares/{identifier}/accept")
    def accept(identifier: str, user: str = Depends(current), session: Session = Depends(db)):
        share = owned(session, Share, identifier, "recipient_id", user)
        if share.installation_id:
            return {"id": share.installation_id}
        item = install(session, user, share.definition)
        result = session.execute(update(Share).where(Share.id == identifier, Share.installation_id.is_(None)).values(installation_id=item.id))
        if result.rowcount != 1:
            session.rollback()
            return {"id": owned(session, Share, identifier, "recipient_id", user).installation_id}
        session.commit()
        return {"id": item.id}
    @api.get("/api/v1/installations")
    def installations(user: str = Depends(current), session: Session = Depends(db)):
        return [{"id": i.id, "definition": json.loads(i.definition), "state": json.loads(i.state)} for i in session.scalars(select(Installation).where(Installation.user_id == user))]
    @api.put("/api/v1/installations/{identifier}/state")
    def state(identifier: str, body: StateRequest, user: str = Depends(current), session: Session = Depends(db)):
        item = owned(session, Installation, identifier, "user_id", user)
        components = {c.id: c.type for c in Definition.model_validate_json(item.definition).components}
        for key, value in body.values.items():
            if components.get(key) == "input" and isinstance(value, str) and len(value) <= 2000:
                continue
            if components.get(key) == "counter" and type(value) is int and abs(value) <= 1000000:
                continue
            raise HTTPException(422, "Invalid component state")
        item.state = json.dumps(body.values); session.commit()
        return {"state": body.values}
    return api

app = create_app()
