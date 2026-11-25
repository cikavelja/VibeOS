# ADR 0002 — preview migration strategy (accepted)

## Context
The repository ships with a provisional starter preview (`backend/app/main.py`, `frontend/src/main.tsx`, `docs/contracts/preview-api.md`) and baseline smoke tests (`backend/tests/test_api.py`, `frontend/src/runtime/state.test.ts`, `tests/e2e/desktop.spec.ts`), while all 81 tasks in `docs/tasks.json` start at `NOT_STARTED`. Each task requires preserving applicable regression checks (`AC-03`) without prematurely implementing later milestones.

## Decision
Introduce target layered modules (`backend/app/api/`, `backend/app/core/`, `frontend/src/health/`, `frontend/src/services/`) incrementally per assigned task while keeping provisional preview endpoints and UI flows operational until the designated milestone task formally supersedes them (`M01` for desktop shell, `M02` for async SQLAlchemy/Alembic and auth hardening, `M03` for declarative runtime/projects, `M04` for Vibe Studio, and `M07` for `.vibeapp` and versioned sharing).

## Consequences
- Foundation and early milestone tasks (`M00`–`M01`) can add clean, typed, tested modules without breaking preview regression suites.
- Provisional synchronous SQLite helpers remain isolated to preview persistence until `M02-T01` introduces async SQLAlchemy and Alembic migrations.
