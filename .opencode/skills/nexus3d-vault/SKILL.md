# Nexus3D Vault — Architecture & Workflow Skill

This document is the living architecture reference for AI coding agents (and humans)
contributing to the **Nexus3D Vault** project.

---

## Project Identity

**Nexus3D Vault** (aka PrintStash) is a self-hosted, Plex-style asset management platform
for 3D printing workflows. It ingests source meshes (STL / 3MF) and sliced jobs (G-Code),
extracts technical metadata, deduplicates assets, and exposes everything via a REST API.

---

## Current Stage: 4 (Cloud Readiness) — active

Stage 3 is **complete** — all Moonraker/Klipper features implemented and tested (99 tests).
Stage 4 is now active. See AGENTS.md roadmap and `docs/stage4.md` for the execution plan.

### Architectural improvements (2026-05-27)

- **SessionFactory** Protocol + ContextVar replaces 3 ad-hoc session injection mechanisms (ADR-0001).
- **Frozen settings + RuntimeOverlay** split via ConfigResolver — no more mutation of the global settings singleton (ADR-0002).
- **Taxonomy migration complete** — legacy `category` and `tags_csv` columns dropped; all resolution via FK joins.
- **Extraction pipeline tested** — gcode_parser, thumbnail, hashing, taxonomy have fixture-based unit tests.
- **Storage pass-through collapsed** — routers call `get_backend()` directly; only pure layout helpers remain in `storage.py`.
- **Frontend auth consolidated** — single `auth-store.ts` owns all localStorage; React context is a thin consumer.
- **Frontend types split** — domain files under `types/`; `index.ts` is a barrel re-export.
- **First-run setup expanded** — setup now captures local vs S3/R2 storage plus backup retention/off-site backup settings.
- **External print sentinel rows are lazy** — `__external__` model/file rows are created only when the printer hub captures a non-vault print job.
- **G-code revisions** — `File` rows for G-code can carry an outcome label, notes, and a per-model recommended marker. Comparison remains metadata-first; no raw G-code diffs or delta storage in 1.0.

---

## Architecture Layers

```
┌─────────────────────────────────────────────┐
│  Frontend (Next.js 14 App Router)           │
│  components/ pages/ lib/api.ts types/       │
├─────────────────────────────────────────────┤
│  API v1 (FastAPI routers)                   │
│  health setup auth ingest models files      │
│  taxonomy printers backup config            │
├─────────────────────────────────────────────┤
│  Services                                   │
│  ingestion gcode_parser dedup thumbnail     │
│  moonraker printer_hub storage auth backup  │
│  mesh_processing mesh_render                │
├─────────────────────────────────────────────┤
│  DB (SQLModel / SQLAlchemy 2 → SQLite)     │
│  models session init                        │
├─────────────────────────────────────────────┤
│  Core (config security logging time http)   │
└─────────────────────────────────────────────┘
```

### Backend Package Layout

```
backend/app/
├── main.py              # FastAPI app factory + lifespan
├── core/                # config, security, logging, time, http
├── db/                  # SQLModel models, session, init
├── schemas/             # Pydantic request/response DTOs
├── services/            # gcode parser, thumbnail, dedup, storage,
│                        # moonraker, printer_hub, auth, backup, etc.
└── api/v1/              # versioned routers
```

### Data Flow — Stage 4f (Provider Hub)

```
OrcaSlicer ──POST──► /api/v1/ingest/orca ──► DB (File, Model, Metadata)
                                                 │
                             Frontend ──POST──► /api/v1/printers/{id}/send
                                                 │
                                                 ▼
                       Provider (Moonraker/BambuLAN) dispatch layer
                                                 │
                        PrinterHub (background live-state workers)
                 │
                 ├──► DB: Printer.status, PrintJob.state
                 └──► Frontend WS: live status fan-out
```

---

## Key Design Decisions

### Stage 4f — Printer Hub + Provider Architecture

The `PrinterHub` is a singleton stored on `app.state.printer_hub` (FastAPI lifespan).
It maintains one persistent live-state worker per printer and resolves provider
at runtime (`moonraker` or `bambu_lan`) via the provider factory. Status updates flow:

1. Provider status stream/poll → `PrinterHub._handle_status()`
2. Hub merges into in-memory snapshots (`printer_id → {object: {field: value}}`)
3. Hub writes coarse status + `last_seen_at` to DB
4. Hub syncs active `PrintJob` rows (state, progress, timestamps)
5. Hub fans out to vault WebSocket subscribers (frontend UI)

**Reconnection**: exponential backoff (1s → 30s max), re-reads printer config on each
reconnect (supports provider + credentials updates).

**Stop**: `asyncio.Event` per printer — setting it terminates the WS loop gracefully.

### Provider clients

- `MoonrakerProvider`: thin adapter over `MoonrakerClient`; full upload + control support.
- `BambuLanProvider`: LAN-first local control path (status + pause/resume/cancel);
  upload/send parity intentionally deferred.

### Printer-to-print_job mapping

`PrintJob.remote_filename` is the filename as seen on the Moonraker side. When the
`PrinterHub` receives a status update with a `print_stats.filename`, it looks up the
most recent `PrintJob` row with that `remote_filename` + `printer_id` and syncs its state.

Jobs started **outside** the vault (directly on Klipper) create an external
`PrintJob` row on first matching live-state update. The sentinel `__external__`
model/file rows are created lazily at that moment, not during first startup.

---

## API Surface (Stage 3)

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/v1/printers` | GET | — | List printers |
| `/api/v1/printers/{id}` | GET | — | Get printer |
| `/api/v1/printers` | POST | yes | Register printer |
| `/api/v1/printers/{id}` | PATCH | yes | Update printer |
| `/api/v1/printers/{id}` | DELETE | yes | Remove printer |
| `/api/v1/printers/{id}/send` | POST | yes | Upload gcode to printer |
| `/api/v1/printers/{id}/pause` | POST | yes | Pause print |
| `/api/v1/printers/{id}/resume` | POST | yes | Resume print |
| `/api/v1/printers/{id}/cancel` | POST | yes | Cancel print |
| `/api/v1/printers/{id}/status` | GET | — | One-shot snapshot |
| `/api/v1/printers/{id}/jobs` | GET | — | Print history |
| `/api/v1/printers/{id}/ws` | WS | — | Live status stream |
| `/api/v1/models/{model_id}/files/{file_id}/revision` | PATCH | yes | Update G-code revision status, notes, recommended marker |

### G-code revision fields

On `files`:
- `revision_status`: nullable (`known_good`, `needs_test`, `failed`, `archived`)
- `revision_notes`: nullable text
- `is_recommended`: boolean; only one live G-code file per model should be marked recommended

---

## Database — Stage 3 Models

### `printers` table
- `id`, `name`, `moonraker_url`, `api_key`, `notes`
- `status` (PrinterStatus enum: unknown/offline/ready/printing/paused/error)
- `last_seen_at`, `last_error`
- `created_at`, `updated_at`

### `print_jobs` table
- `id`, `printer_id`→printers, `file_id`→files, `model_id`→models
- `remote_filename`, `state` (PrintJobState enum), `progress` (0.0–1.0)
- `error`, `started_at`, `finished_at`
- `created_at`, `updated_at`

---

## Testing Conventions (Stage 3+)

- Test files live under `backend/tests/` matching service/router names.
- `conftest.py` provides `app` (TestClient with in-memory SQLite) and `db_session` fixtures.
- Moonraker HTTP/WS calls are mocked using `pytest-asyncio` + `unittest.mock`.
- DB tests use `sqlite:///:memory:` with a fresh `init_db()` per test.
- Long-running ops (WS loops) are tested with controlled `asyncio.Event` stop signals.

Run tests:
```bash
cd backend && uv run pytest tests/ -v
```

---

## Current Focus

1. Stabilize provider reliability and error taxonomy under mixed fleets.
2. Extend Bambu support to upload/send parity in a later phase.
3. Keep API path compatibility for existing Moonraker-based consumers.

---

## Useful Commands

```bash
# Backend dev
cd backend
uv sync
VAULT_DB_URL=sqlite:///dev.sqlite VAULT_DATA_DIR=./_data VAULT_API_KEY=devkey \
  uv run uvicorn app.main:app --reload

# Frontend dev
cd frontend && pnpm install && pnpm dev

# Docker
docker compose up --build

# Test
cd backend && uv run pytest tests/ -v

# Lint
cd backend && uv run ruff check app/ tests/
cd frontend && pnpm lint
```
