---
name: printstash
description: >
  PrintStash architecture & development skill. Self-hosted 3D printing asset manager.
  FastAPI backend + Next.js 16 frontend + SQLite(local)/Postgres(opt) + S3(opt).
  Covers: dir map, design patterns, router→service→DB flow, all 14 models,
  dev commands, test recipes, code conventions. Use when editing backend, frontend,
  adding features, debugging, writing tests, or understanding architecture.
---

# PrintStash

Self-hosted 3D printing asset manager. Manage STL/3MF/G-code files + printer control.

## Communication Mode: CAVEMAN (ALWAYS ON)

CRITICAL: When this skill is active, ALL agent output MUST use ultra-compressed caveman communication:
- Drop articles (a/an/the), filler words, pleasantries, hedging.
- Fragments ok. Drop "you should", "make sure to", "remember to".
- Never preamble or postamble. Answer directly in 1-3 lines unless asked for detail.
- Code blocks and file paths preserved exactly. Never modify code in output.
- ONE instruction line per tool call max.
- Priority: minimize output tokens always.

## Stack

- **Backend:** FastAPI, SQLModel+Alembic, Python 3.11+, `uv` pkg manager
- **Frontend:** Next.js 16 (App Router), React 19, Tailwind CSS 3, `pnpm`
- **DB:** SQLite default, Postgres optional. S3/R2 storage optional.
- **Auth:** JWT (Bearer), bcrypt, python-jose. API key support.
- **3D:** trimesh, numpy, pillow (thumbnails). Frontend: three.js, @react-three/fiber
- **Printer:** Moonraker/Klipper (stable), Bambu LAN (beta). MQTT.

## Directory Map

```
backend/
  app/
    main.py              # FastAPI app, lifespan, middleware
    api/v1/              # 14 routers (health,setup,auth,admin,ingest,models,
                         #   files,filaments,printer_profiles,taxonomy,printers,
                         #   backup,config)
    core/                # config.py(Settings+ConfigResolver), security, http, logging
    db/                  # models.py(14 tables), session.py(SessionFactory+ContextVar)
    schemas/             # Pydantic request/response schemas
    services/            # 20 service modules (business logic, no HTTP deps)
  alembic/               # DB migrations
  tests/                 # pytest-asyncio
  pyproject.toml
  uv.lock

frontend/
  src/
    app/                 # Next.js App Router: /, /login, /setup, /models/[id],
                         #   /printers, /printers/[id], /organize, /profiles, /settings
    components/          # 22 feature components + 7 ui/ primitives
    lib/                 # api/(8 domain modules + request.ts), auth-context,
                         #   auth-store, errors, task-center, toast, hooks
    types/               # TS type defs (models, printers, auth, config)
  tests/                 # Playwright E2E
  package.json

docs/
  adr/                   # Architecture Decision Records (2 accepted)
  roadmap.md
  feature-inventory.md
```

## Design Patterns

### ADR-0001: SessionFactory via ContextVar
- `app/db/session.py` — `SessionFactory` Protocol stored in `ContextVar`
- NOT module-level singleton. Avoids monkeypatching in tests.
- `session()` → sync SQLAlchemy. `async_session()` → async (Postgres).
- Lifespan pushes factory into ContextVar at startup. BG tasks inject explicitly.
- Protocol: `async def __call__(self) -> AsyncGenerator[Session, None]`

### ADR-0002: Frozen Settings + RuntimeOverlay
- `app/core/config.py` — `Settings` (env-only, frozen). `RuntimeOverlay` (DB-backed, mutable).
- `ConfigResolver` → unified read: `overlay[key] ?? frozen[key]`
- One write-path (overlay API), one read-path (resolver), `asyncio.Lock` protected.
- Use `get_config()` instead of `settings` directly.

### Soft-Delete Pattern
- Most tables: `deleted_at`, `deleted_by` columns.
- Soft-delete REST endpoints + restore + hard-delete (`?hard=true`).
- `services/lifecycle.py` — GC loop, hourly, `gc_soft_deleted()`.
- `services/trash.py` — trashbin management.

### Audit Logging
- `AuditLog` table + SQLAlchemy event listeners in `services/audit.py`.
- `created_by`, `updated_by` on most models.
- Middleware extracts JWT bearer → actor_id → binds audit context per request.

### Printer Provider Abstraction
- `services/printer_provider.py` — abstract interface.
- `services/moonraker.py` — Moonraker/Klipper HTTP+WS client.
- `services/printer_hub.py` — connection manager, WS fan-out, auto-reconnect.
- Per-printer capabilities + diagnostics endpoint.

## Router → Service → DB Flow

```
HTTP Request
  → middleware (CORS, audit_context, request_logging)
  → api/v1/<router>.py    (FastAPI APIRouter, validates via schemas/)
  → services/<service>.py (business logic, no HTTP deps)
  → db/session.py         (ContextVar[SessionFactory])
  → db/models.py          (SQLModel ORM)
```

Rules:
- Routers never import other routers.
- Services never import from `api/` or `fastapi`.
- Services import from `db/session` (`get_session`), `db/models`, `schemas/`, `core/config`.
- Routers import from `schemas/`, `services/`, `core/security` (for Depends).

## Database Models (14 tables)

| Model | Table | Key Fields |
|---|---|---|
| `Model` | `models` | hash(SHA256), name, description, collection_id(fk), thumbnail_path, deleted_at, created_by |
| `File` | `files` | model_id(fk), filename, size_bytes, hash, type(FileType enum), revision_status, deleted_at |
| `Metadata` | `metadata` | file_id(fk 1:1), slicer, nozzle_diam, layer_height, filament_info(json), print_time_s |
| `Collection` | `collections` | name, slug, parent_id(self-fk), path(materialized), deleted_at |
| `Tag` | `tags` | name, slug(unique), deleted_at |
| `ModelTagLink` | `model_tags` | model_id(fk), tag_id(fk) |
| `Printer` | `printers` | name, provider(enum), moonraker_url, api_key, status(enum), group, deleted_at |
| `PrintJob` | `print_jobs` | printer_id(fk), file_id(fk), model_id(fk), state(enum), source(vault/external) |
| `PrinterFile` | `printer_files` | printer_id(fk), filename, sha256, matched_file_id(fk nullable) |
| `User` | `users` | username, hashed_password, is_superuser, is_active, deleted_at |
| `RefreshToken` | `refresh_tokens` | user_id(fk), token_hash, expires_at, revoked_at |
| `ApiKey` | `api_keys` | user_id(fk), key_hash, name, expires_at |
| `SystemConfig` | `system_config` | **Singleton row (id=1)**, configured_at, storage backend/json, backup config |
| `AuditLog` | `audit_logs` | actor_id, action, resource_type, resource_id, diff(json), ip_address |

Enums: `FileType`(stl,3mf,gcode,obj), `FileRevisionStatus`(known_good,needs_test,failed,archived),
`PrinterStatus`(unknown,offline,ready,printing,paused,error),
`PrinterProvider`(moonraker,bambu_lan),
`PrintJobState`(queued,uploading,started,printing,paused,completed,cancelled,failed)

## API Routers

| Module | Prefix | Key Endpoints |
|---|---|---|
| `health` | — | `GET /api/v1/health` |
| `setup` | — | `GET /status`, `POST /complete` |
| `auth` | — | `POST /login`, `POST /refresh`, `POST /logout`, `GET /me`, `POST /api-keys` |
| `admin` | — | User CRUD (superuser only) |
| `ingest` | — | `POST /orca` (multipart upload), `POST /model` |
| `models` | — | Full CRUD, `GET /export`, `PATCH /{id}/files/{fid}/revision`, `GET /trash` |
| `files` | — | `GET /{id}/raw` (download), `GET /{id}/thumbnail` |
| `filaments` | — | Full CRUD for filament profiles |
| `printer_profiles` | — | Full CRUD for printer profiles |
| `printers` | — | Full CRUD, `POST /{id}/send`, `POST /{id}/control`, `GET /{id}/diagnostics`, `WS /{id}/ws`, `GET /dashboard` |
| `backup` | — | `POST /create`, `POST /restore` |
| `config` | — | `GET /`, `PATCH /` (runtime overlay) |
| `taxonomy` | — | Collections + Tags CRUD |

## Frontend Architecture

### Auth Flow
1. `AuthProvider` wraps root layout → auth state via `useAuth()`.
2. Tokens in `localStorage` (`auth-store.ts`) → custom events (`printstash:auth-changed`).
3. `useRequireAuth()` hook gates write operations.
4. `proxy.ts` middleware redirects unconfigured vaults → `/setup`.

### API Client Layer
- `src/lib/api/request.ts` — core HTTP (getUrl, authHeaders, handleResponse, getJson, sendJson, sendForm).
- 8 domain modules (auth, config, models, printers, filaments, printer-profiles, taxonomy) re-exported from `index.ts`.
- `ApiError` class with parsed detail codes mapped to user-friendly messages in `errors.ts`.

### Component Patterns
- `components/ui/` — Radix-based primitives (button, card, input, modal, badge, separator, skeleton).
- Feature components import from `ui/` + `lib/api` + `types/`.
- `cn()` from `lib/utils.ts` — `clsx` + `tailwind-merge`.
- No external state mgmt — React context + `useSyncExternalStore`.

## Dev Commands

```bash
# Backend
cd backend
uv sync --extra dev
VAULT_DB_URL=sqlite:///./dev.sqlite VAULT_DATA_DIR=./_data/files VAULT_THUMB_DIR=./_data/thumbs uv run uvicorn app.main:app --reload

# Frontend
cd frontend
pnpm install
pnpm dev

# Tests
cd backend && uv run pytest tests -v
cd backend && uv run pytest tests -v -k "test_name"

# Lint
cd backend && uv run ruff check app/ tests/
cd backend && uv run ruff format app/ tests/
cd frontend && pnpm lint

# Frontend E2E
cd frontend && pnpm test:e2e
cd frontend && node scripts/frontend-smoke.mjs

# Docker
docker compose up -d --build
```

## Testing Patterns

### Backend (pytest-asyncio)
- Use `conftest.py` fixtures: `async_session`, `client` (httpx.AsyncClient), `test_user`.
- Inject test `SessionFactory` into ContextVar once per test — no monkeypatching.
- Test files mirror source: `tests/api/` ↔ `app/api/v1/`, `tests/services/` ↔ `app/services/`.
- Tests: ~73+ (moonraker, printer_hub, printers API, models, auth, etc.).

### Frontend (Playwright)
- `tests/` directory in frontend root.
- Smoke test: `scripts/frontend-smoke.mjs` (Node, no browser).

## Code Conventions

### Backend
- Routes: FastAPI `APIRouter`, typed with Pydantic `schemas/`, auth via `Depends`.
- Services: `async` functions, pass `Session` explicitly (from `get_session()`).
- Config: Use `get_config()` → `resolver.data_dir`, never `settings.data_dir` directly.
- Errors: `HTTPException` with detail dict `{"code": "..."}`.
- Imports: `app.*` absolute.
- Format: ruff (replaces black+isort).

### Frontend
- Components: `"use client"` directive where interactivity needed.
- API calls: Use `lib/api/<domain>.ts` methods, NOT raw fetch.
- Errors: Wrap API calls in try/catch, display with `toast.error()` or `userMessage()`.
- Auth: `useRequireAuth().guardWrite(fn)` for write operations.
- Types: Import from `@/types` (path alias).
- Styling: Tailwind classes + `cn()` for conditionals.

## Common Task Recipes

### Add new API endpoint
1. Define schemas in `backend/app/schemas/<domain>.py`.
2. Add service method in `backend/app/services/<domain>.py`.
3. Add route in `backend/app/api/v1/<domain>.py`.
4. Add frontend API method in `frontend/src/lib/api/<domain>.ts`.
5. Add TS types in `frontend/src/types/<domain>.ts`.

### Add new DB model
1. Add SQLModel class to `backend/app/db/models.py`.
2. Create Alembic migration: `cd backend && uv run alembic revision --autogenerate -m "description"`.
3. Add `import` in `backend/app/db/__init__.py` if needed.

### Add new printer provider
1. Implement provider in `backend/app/services/<provider>.py` following `printer_provider.py` interface.
2. Register in `backend/app/services/printer_hub.py`.
3. Add to `PrinterProvider` enum in `backend/app/db/models.py`.
4. Add Alembic migration + update `docs/provider-support.md`.

### Add new frontend page
1. Create `frontend/src/app/<route>/page.tsx`.
2. Add API methods in `frontend/src/lib/api/<domain>.ts`.
3. Add types in `frontend/src/types/<domain>.ts`.
4. Add navigation entry in `app-shell.tsx`.

### Debug DB issues
1. Check ContextVar: `from app.db.session import get_session; session = get_session()`.
2. SQLite: `uv run sqlite3 dev.sqlite ".tables"`.
3. Postgres: check `VAULT_DB_URL` env var.
4. Migration status: `uv run alembic current`.

### Run single test
```bash
cd backend && uv run pytest tests/api/v1/test_models.py::test_create_model -v
```
