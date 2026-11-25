# Quality gates
M00 establishes these scripts and CI. Commands below are intended contracts, not currently runnable product checks.

Frontend (frontend/): npm ci; npm run build; npm run typecheck; npm run lint; npm run test; npx playwright test (configured E2E environment).
Backend (backend/): uv sync --locked; uv run ruff check .; uv run ruff format --check .; uv run mypy app; uv run pytest; uv run alembic upgrade head (disposable test DB).
Integration: docker compose build; run health checks and integration tests against SQLite and PostgreSQL as applicable.

Record exact command, directory, exit code, output or CI URL, test names, commit, and per-criterion PASS/FAIL/NOT_RUN. Do not mark VERIFIED for mandatory NOT_RUN. Never weaken tests to pass. Independent review must inspect assertions and actual behavior. CI is necessary but not sufficient; human approves merge. Mock provider tests do not establish real provider connectivity. Configure branch protection manually in GitHub.

MVP E2E: two authenticated users; A creates app visually and with AI, previews, installs, enters private data and shares; B accepts permissions, installs, enters separate data; refresh both; test denied cross-user reads, version update and fork.
