# Docker development environment

`docker-compose.yml` at the repository root orchestrates the three local development services:

- **`postgres`** (`postgres:16.16-alpine`): PostgreSQL 16 backed by the named `postgres-data` volume (`/var/lib/postgresql/data`), bound exclusively to loopback `127.0.0.1:${POSTGRES_PORT:-5432}:5432`, with a TCP `pg_isready -h 127.0.0.1` health check.
- **`backend`** (`./backend/Dockerfile`, `python:3.12-slim`): FastAPI + SQLAlchemy + `psycopg` v3 installed reproducibly via `uv sync --locked --no-dev`, running as non-root `USER 10001:10001` on `127.0.0.1:${BACKEND_PORT:-8000}:8000`. Starts after `postgres` reaches `service_healthy` and probes `http://127.0.0.1:8000/api/v1/health`.
- **`frontend`** (`./frontend/Dockerfile`, `node:22-alpine`): React + Vite installed reproducibly via `npm ci`, running as non-root `USER node` on `127.0.0.1:${FRONTEND_PORT:-5173}:5173`. Starts after `backend` reaches `service_healthy` and verifies both `http://127.0.0.1:5173/` and proxied `http://127.0.0.1:5173/api/v1/health`.

## Usage

```sh
docker compose up -d --build --wait
docker compose ps
docker compose down -v --remove-orphans
```

## Environment overrides

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_DB` | `vibeos` | PostgreSQL database name |
| `POSTGRES_USER` | `vibeos` | PostgreSQL role name |
| `POSTGRES_PASSWORD` | `vibeos_local_dev` | Local-only development password (change if overridden in `DATABASE_URL`) |
| `POSTGRES_PORT` | `5432` | Host loopback port (`127.0.0.1`) mapped to `postgres:5432` |
| `BACKEND_PORT` | `8000` | Host loopback port (`127.0.0.1`) mapped to `backend:8000` |
| `FRONTEND_PORT` | `5173` | Host loopback port (`127.0.0.1`) mapped to `frontend:5173` |
| `DATABASE_URL` | `postgresql+psycopg://vibeos:vibeos_local_dev@postgres:5432/vibeos` | Backend SQLAlchemy connection URL |
| `API_TARGET` | `http://backend:8000` | Upstream FastAPI target for the Vite `/api` proxy |

## Static validation

Run `python scripts/verify_compose.py` from the repository root to validate `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, and `.dockerignore` files without mutating disk state. Do not expose the development Compose stack or default local credentials to untrusted networks.
