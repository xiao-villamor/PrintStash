<div align="center">

# PrintStash

### Self-hosted asset management for people who 3D print more things than they can remember.

PrintStash is a local-first web app that remembers your STL, 3MF, OBJ, and G-code files
**so you don't have to.** Upload manually or let OrcaSlicer push new G-code after every
slice, then search by model name, category, tags, slicer metadata, material, printer,
and print history.

![PrintStash demo](screenshots/00-demo.gif)

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](./LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&style=flat-square)
![Next.js 16](https://img.shields.io/badge/next.js-16-black?logo=next.js&style=flat-square)
![Docker ready](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&style=flat-square)
![Status](https://img.shields.io/badge/status-0.1%20initial%20self--hosted-f59e0b?style=flat-square)

[**Quick Start**](#quick-start) · [**Features**](#features) · [**Screenshots**](#screenshots) · [**Roadmap**](#roadmap) · [**Contributing**](#contributing) · [**Discussions**](https://github.com/xiao-villamor/PrintStash/discussions)

</div>

---

## Like the idea?

PrintStash is brand new and built in the open. If "a small, boring library that just
*remembers* my prints" sounds useful, star the repo, [open a discussion](https://github.com/xiao-villamor/PrintStash/discussions),
and tell us what your homelab looks like. Every printer report, parser fixture, and
clunky-UI complaint genuinely shapes where 0.2 goes.

## Why this exists

Most 3D printing workflows are good at producing files and bad at remembering them.
Slicers know the settings. Printers know what is running *now*. File shares know where
blobs live. None of them make it easy to answer:

- Which version of this part did I **actually** print?
- What filament and layer height did I use?
- Did I **already** slice this for the printer in the garage?
- Where did that G-code go after OrcaSlicer exported it?

PrintStash tries to be the small, boring library in the middle. It stores the files,
extracts the metadata, keeps versions together, and gives you an API/UI you can run at
home — no cloud account, no subscription, no telemetry.

## Features

**Ingest and organize**
- File ingestion for STL, 3MF, OBJ, and G-code
- OrcaSlicer post-processing hook — one Python file, stdlib only, never breaks a slice
- Metadata extraction from G-code: slicer settings, filament info, and more
- Content-hash deduplication and full model version history
- Categories, tags, fast search, and auto-generated thumbnails

**Inspect every model**
- Split model-detail views — overview, files, revisions, history, settings
- In-browser mesh preview plus a client-side G-code toolpath viewer
- G-code revision notes, outcome labels, recommended-version marker, and metadata compare
- Print history with manual entries and Moonraker history import for matching files

**Talk to your printers**
- Moonraker/Klipper integration: live status, send-to-print, remote-file start, controls, file-inventory sync
- Provider diagnostics showing capabilities, unsupported actions, and live connectivity checks
- Printer presence badges and filters showing where a model's G-code already exists
- Beta Bambu LAN support for status and pause/resume/cancel controls

**Run it your way**
- Library stats, storage usage, configurable model-card metrics, trash restore/purge, and thumbnail rebuild tooling
- First-run setup wizard with JWT auth, refresh/logout, and per-user API keys for scripts
- Metadata-only JSON/CSV export for portability, audits, spreadsheets, and local AI context
- Optional Postgres, S3/R2 storage, backup/restore archives, health probes, and audit logs

> **Current state — 0.1 initial self-hosted release.** Production hardening is in place;
> current focus is release validation, real-world homelab feedback, and
> printer-provider maturity. Docker Compose is the main install path. SQLite + local disk
> are the default. Postgres, S3, backups, and audit logs stay optional while the project matures.

**Known rough spots** (full list in [docs/known-limitations.md](./docs/known-limitations.md)):

- Bambu LAN upload/send parity is not done yet — Bambu is beta and status/control-only in 0.1
- Printer integrations need more real-world hardware testing
- The UI is functional, but workflow polish is still in progress

## Quick Start

Requirements: Docker and Docker Compose.

```bash
git clone https://github.com/xiao-villamor/PrintStash.git
cd PrintStash

cp .env.example .env
# Edit .env and change VAULT_JWT_SECRET.

docker compose up -d --build
```

Open:

| Service | URL |
| --- | --- |
| Web UI | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Health check | http://localhost:8000/api/v1/health |

On first launch, the web UI walks you through creating the first admin account.
There is no default username or password.

Tagged releases publish Docker images to GitHub Container Registry:

| Image | Purpose |
| --- | --- |
| `ghcr.io/xiao-villamor/printstash-api:<version>` | FastAPI backend |
| `ghcr.io/xiao-villamor/printstash-frontend:<version>` | Next.js frontend |

The checked-in Compose file builds locally by default so contributors can run from
source. Self-hosted release installs can pin the GHCR images in a small Compose override.

## OrcaSlicer Hook

The hook is intentionally boring: one Python file, stdlib only, exits `0` even if the
vault is offline so it never breaks a slice/export.

```bash
# OrcaSlicer -> Print Settings -> Advanced -> Post-processing Scripts
/usr/bin/python3 /path/to/PrintStash/scripts/printstash_orca_push.py \
  --url http://your-printstash-host:8000 \
  --username YOUR_USERNAME \
  --password YOUR_PASSWORD \
  --category "Functional/Brackets"
```

After that, exported G-code is pushed into PrintStash automatically.

## Demo Path

For a clean first look, follow [docs/demo-walkthrough.md](./docs/demo-walkthrough.md).
The short version:

1. Start Docker Compose and complete first-run setup.
2. Upload one STL/3MF and one G-code file.
3. Open the model detail page to show metadata, thumbnails, revisions, and the mesh viewer.
4. Add a Moonraker printer or open provider diagnostics to show capability checks.
5. Export metadata from Settings as JSON or CSV.

## Screenshots

| Asset grid | Model detail | 3D viewer |
| --- | --- | --- |
| ![Asset grid](screenshots/01-asset-grid.png) | ![Model detail](screenshots/04-model-detail.png) | ![3D viewer](screenshots/05-3d-viewer.png) |

| Search | Categories | Setup |
| --- | --- | --- |
| ![Search](screenshots/03-search.png) | ![Categories](screenshots/02-category-filter.png) | ![Setup wizard](screenshots/06-setup-wizard.png) |

## API

The frontend uses the same REST API that scripts and third-party tools can use.
Swagger docs are available at `/docs`.

Common endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/ingest/orca` | Upload a file from OrcaSlicer or curl |
| `GET` | `/api/v1/models` | List and search models |
| `GET` | `/api/v1/models/stats` | Read library counts and configured storage usage |
| `GET` | `/api/v1/models/{id}` | Read one model with files and metadata |
| `PATCH` | `/api/v1/models/{id}` | Update name, description, category, tags |
| `GET` | `/api/v1/models/export?format=json` | Export library metadata without raw file blobs (auth required) |
| `GET` | `/api/v1/models/trash` | List soft-deleted models (auth required) |
| `POST` | `/api/v1/models/{id}/print-jobs` | Manually log a print job for a model |
| `POST` | `/api/v1/models/{id}/print-jobs/import-printer/{printer_id}` | Import matching Moonraker print history |
| `PATCH` | `/api/v1/models/{id}/files/{file_id}/revision` | Update G-code revision status, notes, recommended marker |
| `GET` | `/api/v1/files/{id}/download` | Download a stored file |
| `GET` | `/api/v1/files/{id}/download-url` | Get a direct pre-signed S3 URL or local fallback |
| `GET` | `/api/v1/files/{id}/stl` | Serve STL directly or convert 3MF/OBJ to cached STL for preview |
| `POST` | `/api/v1/files/thumbnails/rebuild` | Queue thumbnail regeneration for existing mesh files |
| `GET` | `/api/v1/printers` | List registered printers |
| `POST` | `/api/v1/printers/{id}/send` | Send vault G-code to a printer |
| `GET` | `/api/v1/printers/{id}/status` | Read printer status |
| `GET` | `/api/v1/printers/{id}/diagnostics` | Check provider capabilities and connectivity |
| `WS` | `/api/v1/printers/{id}/ws` | Live printer status stream |
| `GET` | `/api/v1/auth/api-keys` | List current user's active API keys |
| `POST` | `/api/v1/backups` | Create a full backup archive (superuser) |

Example upload:

```bash
curl -F "file=@my_print.gcode" \
  -F "model_name=Desk Bracket" \
  -F "category=Functional/Brackets" \
  -H "Authorization: Bearer YOUR_LOGIN_TOKEN" \
  http://localhost:8000/api/v1/ingest/orca
```

## Configuration

Most installs only need to edit secrets in `.env`.

| Variable | Default | Notes |
| --- | --- | --- |
| `VAULT_JWT_SECRET` | `changeme...` | Change before exposing the UI |
| `VAULT_DB_URL` | `sqlite:////data/db/printstash.sqlite` | SQLite by default; Postgres optional |
| `VAULT_STORAGE_BACKEND` | `local` | `local` or `s3` |
| `VAULT_DATA_DIR` | `/data/files` | Container path for stored files |
| `VAULT_THUMB_DIR` | `/data/thumbs` | Container path for generated thumbnails |
| `VAULT_MAX_UPLOAD_MB` | `512` | Upload size limit |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000` | Browser-reachable WebSocket URL |

The `VAULT_` prefix is retained for config compatibility from early development.
See [.env.example](./.env.example) for the full list, including S3/R2, backups,
MinIO, Postgres, and lifecycle settings.

## Upgrades and Recovery

Read [UPGRADE.md](./UPGRADE.md) before upgrading an existing install. Backup and
restore recovery steps live in [docs/disaster-recovery.md](./docs/disaster-recovery.md).
Release smoke checks are listed in [docs/release-validation.md](./docs/release-validation.md).

## Development

Backend:

```bash
cd backend
uv sync --extra dev

VAULT_DB_URL=sqlite:///./dev.sqlite \
VAULT_DATA_DIR=./_data/files \
VAULT_THUMB_DIR=./_data/thumbs \
uv run uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
pnpm install
pnpm dev
```

Tests and formatting:

```bash
cd backend
uv run pytest tests -v
uv run ruff check app/ tests/
uv run ruff format app/ tests/
```

```bash
cd frontend
pnpm lint
```

## Architecture

PrintStash is a FastAPI backend, a Next.js frontend, and a storage layer that starts
simple on SQLite/local disk.

```text
Browser / scripts / OrcaSlicer
          |
          v
Next.js UI + FastAPI API
          |
          +-- ingestion, metadata extraction, thumbnails
          +-- taxonomy, search, auth, audit, backup
          +-- printer providers: Moonraker stable, Bambu LAN beta
          |
          v
SQLite + local files by default
Postgres + S3/R2 optional
```

The repository keeps architecture decisions documented in [docs/adr](./docs/adr),
with release and operations notes in [docs](./docs).
For release-ready community issues, see [docs/community-starter-issues.md](./docs/community-starter-issues.md).

## Roadmap

The living roadmap is in [docs/roadmap.md](./docs/roadmap.md). The short version:

- Polish G-code revision history and provider maturity, especially Bambu LAN upload/send support
- Harden backup/restore and operational monitoring
- Improve printer-farm workflows and scheduling
- Keep cloud-style features optional, not required for home installs

Roadmap feedback is welcome in
[the roadmap discussion](https://github.com/xiao-villamor/PrintStash/discussions/1).

## Contributing

Bug reports, hardware notes, docs fixes, and small PRs are very welcome. Please
start with [CONTRIBUTING.md](./CONTRIBUTING.md). If you are not sure whether an
idea fits, open a discussion first — we'd rather talk early than turn good work away.

**Good first contributions:**

- Test a printer/provider combination and report what happens
- Improve install notes for your NAS, mini PC, or homelab setup
- Add parser fixtures for real slicer output
- Tighten UI flows that feel clunky after repeated use

Not sure where to start? Check [docs/community-starter-issues.md](./docs/community-starter-issues.md).

## License

PrintStash is licensed under the [GNU AGPL-3.0](./LICENSE).

<div align="center">

---

**If PrintStash saves you from re-slicing one bracket, give it a star and tell a friend.**

</div>
