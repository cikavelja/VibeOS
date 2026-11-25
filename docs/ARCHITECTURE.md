# Architecture
React + TypeScript strict + Vite; Tailwind, Zustand, TanStack Query, react-rnd, Lucide, Monaco, Recharts, Zod, IndexedDB cache. Python 3.12+ FastAPI, Pydantic v2, async SQLAlchemy, Alembic, SQLite local and PostgreSQL production, uv, structlog. LiteLLM provider adapter (OpenAI, Azure/Foundry, Anthropic, Gemini, Ollama), LangGraph bounded workflow, optional Python MCP SDK. Resolve and lock compatible dependency versions during M00.

## Boundaries
Browser shell owns window state. Declarative runtime renders allowlisted components and actions; API owns persisted state and authorization. REST /api/v1, SSE for agent streams, WebSocket for desktop events. Backend layers: api, core, domain, application, infrastructure, agents, runtime, mcp. Thin routes, typed services, dependency inversion; no microservices initially.

## Data model
User owns desktop, virtual files, project and installations. Project has mutable draft and immutable released versions. Installation pins version and user, with independent instance state and permission grants. Share references recipient and immutable version. Fork creates new project and provenance; transfer changes project ownership only. Include collaborator roles, publication, reviews, reports, audit logs, provider and MCP configurations. All IDs UUID; ownership checked server-side. State migration transactional and rollbackable.

## Runtime
Manifest + component tree + typed initial state + bindings + workflows. Component types include layouts, text, forms, tables, lists, tabs, modals, images, progress and charts. Workflows allow state changes, arithmetic, filtering, sorting, aggregation, validation, conditionals, approved calls, timers and IPC. No eval/exec. Vibe Studio visual designer, JSON editor and AI share declarative model; arbitrary code apps use separate editor. Preview is isolated from real data. Built-ins: file explorer, text editor, calculator, virtual terminal, settings, AI assistant, task manager, Vibe Studio and Vibe Store.

## Agent
Context selection -> intent routing -> generator/modifier/system/data analysis nodes -> validation -> permission/confirmation -> execution -> events. Bounded retries, iterations, token and time limits; cancellation and redacted traces. Pandas approved operations only. Mock provider for tests, real provider smoke test separately. No raw chain-of-thought exposed.

## Distribution
Send copy, subscribe to updates, import/export .vibeapp, fork, collaborate and ownership transfer. Recipient reviews permissions and installs independently. Default packages omit private data and secrets. Marketplace free distribution first; no payments. Optional sandbox code apps use isolated origin, CSP and typed per-instance channel. No generated Python execution in API.

## Persistence
Database authoritative; IndexedDB cache scoped to authenticated user and cleared on logout. Storage adapter local initially; optional S3/Azure later. VFS Desktop, Documents, Downloads, Pictures, Applications, System/Themes, System/Settings, Trash.

See contracts and ADRs. This is a planning baseline, not implemented software.
