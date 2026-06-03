# AGENTS.md — PrintStash

This document guides AI coding agents (and humans) contributing to the **PrintStash**
project. Read this in full before making changes.

---

## 1. Project Identity

**PrintStash** is a self-hosted, Plex-style asset management platform for 3D printing
workflows. It ingests source meshes (STL / 3MF) and sliced jobs (G-Code) — primarily via
an OrcaSlicer post-processing hook — extracts technical metadata, deduplicates assets,
and exposes everything via a clean REST API.

- **Mission:** Eliminate the gap between "I sliced a thing" and "I can find/reprint that thing 6 months later."
- **Self-host first.** Cloud features are opt-in (Stage 4).
- **API-first.** The UI (Stage 2) is a consumer of the same API any third-party tool can use.

---

## 2. Roadmap (Authoritative)

| Stage | Codename             | Scope                                                              | Status     |
| ----- | -------------------- | ------------------------------------------------------------------ | ---------- |
| 1     | The Headless Vault   | FastAPI, SQLite, Docker, OrcaSlicer ingestion, G-code parser       | completed  |
| 2     | The Visual Experience| Next.js 14 frontend, Shadcn UI, R3F 3D viewer, asset grid          | completed  |
| 3     | The Hub              | Moonraker/Klipper bidirectional integration, multi-printer farm    | completed  |
| 4     | Cloud Readiness      | OAuth2/JWT, multi-tenant, Postgres, S3, audit logs                 | **active** |

Do **not** introduce features from a later stage into an earlier one without explicit
sign-off. Architectural decisions for later stages should still be considered (e.g., we
use SQLModel today specifically so the Postgres jump in Stage 4 is painless).

---

## 3. Tech Stack (Locked)

- **Backend:** Python 3.11+, FastAPI, SQLModel (over SQLAlchemy 2), Uvicorn.
- **DB:** SQLite (Stage 1–3) → Postgres (Stage 4). Migrations via Alembic from Stage 4.
- **3D processing:** Trimesh + numpy (lazy import — only when STL/3MF endpoints invoked).
- **Frontend (Stage 2+):** Next.js 14 App Router, Tailwind, Shadcn/ui, React Three Fiber.
- **Infra:** Docker Compose, named volumes, no external services in Stage 1.
- **Integration:** OrcaSlicer post-processing scripts (stdlib only — no `requests`).

---

## 4. Repository Layout

```
3dnexus/
├── AGENTS.md                       ← you are here
├── README.md
├── docker-compose.yml
├── .env.example
├── .opencode/
│   └── skills/
│       └── nexus3d-vault/          ← architecture & workflow skill
│           └── SKILL.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                 ← FastAPI app factory + lifespan
│       ├── core/                   ← config, security, logging
│       ├── db/                     ← SQLModel models, session, init
│       ├── schemas/                ← Pydantic request/response DTOs
│       ├── services/               ← gcode parser, thumbnail, dedup, storage
│       └── api/v1/                 ← versioned routers
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.ts
│   └── src/
│       ├── app/                    ← Next.js 14 App Router pages
│       ├── components/             ← UI components (Shadcn + custom)
│       ├── lib/                    ← API client, utils
│       └── types/                  ← Shared TypeScript types
├── scripts/
│   └── nexus3d_orca_push.py        ← OrcaSlicer post-processing hook
└── docs/
    └── stage1.md                   ← Stage 1 execution log
```

---

## 5. Conventions

### Code style
- **Python:** PEP 8, 4-space indent, type hints on every public function. Prefer
  `from __future__ import annotations`. Format with `ruff format`; lint with `ruff check`.
- **Naming:** `snake_case` modules/functions, `PascalCase` classes, `SCREAMING_SNAKE` consts.
- **Imports:** stdlib → third-party → local, separated by blank lines.
- **No bare `except:`** — always catch specific exceptions and log.

### API
- All endpoints live under `/api/v1/...`. Bumping versions is the only way we break clients.
- Responses are JSON unless explicitly streaming a binary blob.
- Errors use FastAPI's `HTTPException` with a stable error string (`detail: "model_not_found"`).
- Write endpoints require `X-API-Key` header (Stage 1). Real auth comes in Stage 4.

### Database
- Every table has `id` PK, `created_at`, and (where mutable) `updated_at`.
- Hashes are `sha256` hex strings (lowercase, 64 chars), indexed when used for dedup.
- File paths stored in DB are **container-absolute** (`/data/files/...`); the host mapping
  is a deployment concern, not a data concern.

### File storage layout (inside the container)
```
/data/
├── files/
│   ├── _incoming/<uuid>.<ext>            ← staging for in-flight uploads
│   └── <model_slug>/v<version>/<name>    ← canonical home after ingest
├── thumbs/<file_id>.png                  ← extracted previews
└── db/nexus3d.sqlite                     ← single SQLite file
```

### Background work
- Stage 1 uses FastAPI `BackgroundTasks`. Do **not** introduce Celery/Redis until Stage 3+.
- Long-running jobs must be idempotent — re-running on the same file must converge.

### Logging
- Use the stdlib `logging` module via `app.core.logging.get_logger(__name__)`.
- Log at `INFO` for ingestion lifecycle events, `WARNING` for recoverable issues, `ERROR`
  for failures with stack traces.

### Testing
- `pytest` lives under `backend/tests/`. Each service module gets a dedicated test file.
- Use the `tmp_path` fixture for any disk I/O. Never touch the real `/data` volume in tests.

---

## 6. Workflow for Agents

When asked to add a feature:

1. **Identify the stage.** If it belongs to a later stage, push back and propose a Stage-1-safe
   stub instead of silently building forward.
2. **Update `docs/stageN.md`** with what you're about to do (one bullet under the relevant section).
3. **Implement** in the smallest reviewable unit. Prefer adding a service module over inflating a router.
4. **Wire** through `app/api/v1/__init__.py` if a new router is introduced.
5. **Document** any new endpoint in the OpenAPI summary/description fields — Swagger is our only
   UI in Stage 1.
6. **Update the SKILL** at `.opencode/skills/nexus3d-vault/SKILL.md` if the change alters
   architecture, data model, or public surface.

When asked to debug:

1. Reproduce against `GET /api/v1/health` and a known-good fixture in `backend/tests/fixtures/`.
2. Add a regression test before fixing the bug.
3. Log the root cause in `docs/stage1.md` under "Known Issues / Resolved".

---

## 7. Non-Negotiables

- **Never** block the OrcaSlicer export on a vault outage. The post-processing script must
  always exit 0, even on failure (failures are logged client-side).
- **Never** store secrets in the repo. Use `.env` (git-ignored) and `.env.example` (committed).
- **Never** hard-delete files in Stage 1 — `DELETE` endpoints soft-delete via a flag.
  (Hard delete + GC is a Stage 4 concern.)
- **Never** import `trimesh` at module top-level — it's heavy. Lazy-import inside the
  function that needs it.
- **Always** stream large files (G-code can be hundreds of MB). No `.read()` of full bodies
  for ingestion — use `shutil.copyfileobj` against `UploadFile.file`.

---

## 8. Useful Commands

```bash
# Backend local dev (uv)
cd backend
uv sync
VAULT_DB_URL=sqlite:///./dev.sqlite VAULT_DATA_DIR=./_data/files \
VAULT_THUMB_DIR=./_data/thumbs VAULT_API_KEY=devkey \
uv run uvicorn app.main:app --reload

# Frontend local dev (pnpm)
cd frontend
pnpm install
pnpm dev

# Docker
docker compose up --build

# Smoke test
curl -F "file=@sample.gcode" -F "model_name=Bracket" \
     -H "X-API-Key: devkey" \
     http://localhost:8000/api/v1/ingest/orca
```

---

## 9. Contact / Ownership

This is a personal/self-host project. Decisions are made by the repository owner.
When in doubt, prefer the boring, reversible option.

## Agent skills

### Issue tracker

GitHub Issues (`xiao-villamor/PrintStash`). See `docs/agents/issue-tracker.md`.

### Triage labels

Standard defaults (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
