from collections.abc import Callable

import anyio
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import APP_SERVICE_NAME, APP_VERSION
from app.domain.schemas import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


def _probe_database(factory: Callable[[], Session]) -> None:
    with factory() as session:
        session.execute(text("SELECT 1"))


@router.get("/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    if request.query_params:
        raise HTTPException(status_code=422, detail="Unexpected query parameters")
    factory: Callable[[], Session] | None = getattr(request.app.state, "factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        await anyio.to_thread.run_sync(_probe_database, factory)
    except (SQLAlchemyError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return HealthResponse(status="ok", service=APP_SERVICE_NAME, version=APP_VERSION)
