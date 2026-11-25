# VibeOS — Copilot development repository

React + TypeScript and Python/FastAPI. Includes the original 81-task planning baseline, instructions, custom agents, verification templates and a small functional development preview. See START_HERE.md for the implementation workflow and docs/contracts/preview-api.md for limitations.

## Quick start (Docker)

`docker compose up --build` from the repository root. Open http://localhost:5173. API docs: http://localhost:8000/docs. Docker startup was not tested in the packaging environment.

## Local development (two terminals)

Backend:

```sh
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Frontend:

```sh
cd frontend
npm install
npm run dev
```

Register Alice and Bob (passwords at least 10 characters). As Alice, open Studio, add input/counter components and save. Install from Projects, open Installed and save your state. In Projects enter Bob's username and Send copy. Sign out, sign in as Bob, accept the share in Inbox and open the installed app. Its state starts empty.

## Verification

`python scripts/progress.py --write` from root; `python -m pytest scripts/tests -q` from root; `python -m pytest -q` from backend; `npm run build`, `npm test`, `npx playwright install chromium`, `npm run e2e` from frontend. See docs/verification/STARTER_PREVIEW.md for actual packaging checks. No planned task is automatically marked VERIFIED.

## Scope

Development preview, not production-ready VibeOS. Real AI generation, full window manager, drag-and-drop designer, versioning, portable packages, marketplace, async persistence and MCP remain planned. No dependency lockfiles are claimed; generate and commit them in M00 after resolving dependencies. All planning task status remains NOT_STARTED until independently reviewed against the original criteria. Do not expose publicly.
