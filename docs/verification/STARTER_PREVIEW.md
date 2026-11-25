# Packaging verification — development preview

These results concern the provisional starter, not acceptance of any of the 81 planned tasks. No task is marked VERIFIED. No Git commit exists yet.

| Check | Command / evidence | Result |
|---|---|---|
| Backend API | `cd backend && python -m pytest -q` | PASS: 3 tests, exit 0 |
| Planning graph and reports | `python scripts/progress.py --write` | PASS: 81 tasks, 0 verified, exit 0 |
| Planning tests | `python -m pytest scripts/tests -q` | PASS: 5 tests, exit 0 |
| Frontend dependencies | `cd frontend && npm install --fetch-retries=0 --fetch-timeout=8000 --no-audit --no-fund` | BLOCKED: EAI_AGAIN resolving registry.npmjs.org, exit 1 |
| Frontend compilation, unit tests, browser tests | npm build/test and Playwright | NOT_RUN: dependency install blocked |
| Docker | docker compose build / up | NOT_RUN: Docker executable unavailable |
| Product CI | GitHub Actions | NOT_RUN: not pushed to GitHub |

Backend tests ran with Python 3.13.5 and installed FastAPI/Pydantic/SQLAlchemy/httpx. Python 3.12 and a clean dependency install remain unverified. Tests cover authentication requirement, schema rejection, logout, sender/recipient authorization, idempotent sequential share acceptance, and independent private state. They do not prove comprehensive security or concurrent operation. The frontend smoke test and counter tests are included but not executed.

Known architectural exceptions: synchronous SQLite and automatic schema creation, no migration tooling or production authentication/rate limiting, no package signing, AI provider, or full window manager. Preview is local development only. Dependency lockfiles must be produced in M00. Review implementation against the original target contracts before accepting any task.
