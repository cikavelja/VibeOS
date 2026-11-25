# VibeOS — Copilot development repository

React + TypeScript and Python/FastAPI. Includes the original 81-task planning baseline, instructions, custom agents, verification templates and a small functional development preview. See START_HERE.md for the implementation workflow and docs/contracts/preview-api.md for limitations.

## Quick start (Docker)

`docker compose up -d --build --wait` from the repository root starts PostgreSQL (`postgres:16.16-alpine`), the FastAPI backend (`127.0.0.1:8000`), and the Vite frontend (`127.0.0.1:5173`), waiting until all three container health checks pass (`docker compose down -v --remove-orphans` tears down). Open http://localhost:5173. API docs: http://localhost:8000/docs. See `docker/README.md` for port and environment overrides.

## Local development (two terminals)

Backend:

```sh
cd backend
uv sync --locked
uv run uvicorn app.main:app --reload
```

Frontend:

```sh
cd frontend
npm ci
npm run dev
```

Register Alice and Bob (passwords at least 10 characters). As Alice, open Studio, add input/counter components and save. Install from Projects, open Installed and save your state. In Projects enter Bob's username and Send copy. Sign out, sign in as Bob, accept the share in Inbox and open the installed app. Its state starts empty.

## Verification

`python scripts/progress.py --write`, `python scripts/verify_locks.py`, `python scripts/verify_compose.py`, and `python scripts/verify_ci.py` from root; `python -m unittest discover -s scripts/tests` from root; `uv sync --locked`, `uv build`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy --strict app tests`, and `uv run pytest -q` from `backend`; `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build`, `npm run test`, `npx playwright install chromium`, and `npm run e2e` from `frontend`. See docs/verification/STARTER_PREVIEW.md for initial packaging notes.

## Scope

Development preview, not production-ready VibeOS. Real AI generation, full window manager, drag-and-drop designer, versioning, portable packages, marketplace, async persistence and MCP remain planned. Reproducible dependency lockfiles (`backend/uv.lock` and `frontend/package-lock.json`) are committed and verified. Task statuses advance only after independent verification against the acceptance criteria. Do not expose publicly.
