<div align="center">

# PrintStash

### Self-hosted asset management for people who 3D print more things than they can remember.

PrintStash is a local-first web app for STL, 3MF, OBJ, and G-code libraries.
Upload manually or let OrcaSlicer push new G-code after every slice, then find
files by model name, collection, tags, slicer metadata, material, printer, and
print history.

![PrintStash demo](screenshots/00-demo.gif)

[![CI](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml/badge.svg)](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](./LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&style=flat-square)
![React 19](https://img.shields.io/badge/react-19-61DAFB?logo=react&style=flat-square)
![Vite](https://img.shields.io/badge/vite-8-646CFF?logo=vite&style=flat-square)
![Docker ready](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&style=flat-square)
![Status](https://img.shields.io/badge/status-early%20self--hosted-f59e0b?style=flat-square)

[**Quick Start**](#quick-start) · [**Features**](#features) · [**Contributing**](#contributing) · [**Security**](#security)

</div>

---

## Project Status

PrintStash is an early open-source, self-hosted project. The current release is
usable for local libraries and Moonraker/Klipper workflows, with Docker Compose
as the primary install path. SQLite plus local disk is the default; Postgres and
S3/R2-compatible storage are optional.

Hardware reports, parser fixtures, install notes, docs fixes, and UX feedback
are welcome in
[Discussions](https://github.com/xiao-villamor/PrintStash/discussions) or issues.

## Why This Exists

Most 3D printing workflows produce files faster than they help you remember
them. Slicers know settings, printers know what is running now, and folders know
where blobs live. PrintStash keeps files, versions, metadata, thumbnails,
printer presence, and print history together in one local app.

No cloud account, no subscription, no telemetry.

## Features

**Ingest and organize**
- Upload STL, 3MF, OBJ, STEP/STP, and G-code files
- Import from a URL or a `.zip` archive with selective per-file extraction
- Push exported G-code from OrcaSlicer with a post-processing hook
- Deduplicate by content hash and keep model version history
- Organize with collections, tags, search, filters, and thumbnails

**Inspect every model**
- View source files, recommended G-code, slicer settings, and mesh metadata
- Preview meshes (incl. STEP/STP CAD) and G-code toolpaths in the browser
- Track revision status, notes, labels, and recommended versions
- Auto-mark a revision known-good after its first successful print
- Log print history manually or import matching Moonraker history — with measured
  filament use, real duration, and per-print cost
- Share a model via an expiring, read-only public link (view-only by default)

**Talk to printers**
- Manage Moonraker/Klipper printers with live status and send-to-print
- Sync printer file inventories back to vault models
- Run provider diagnostics for capabilities and connectivity
- Use beta Bambu LAN status and controls

**Run locally**
- First-run setup wizard, JWT auth, API keys, trash restore/purge
- JSON/CSV metadata export, backup/restore, health checks, audit logs
- Optional Postgres and S3/R2-compatible storage

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

On first launch, the web UI creates the first admin account. There is no default
username or password.

## Screenshots

| Asset grid | Model detail | 3D viewer |
| --- | --- | --- |
| ![Asset grid](screenshots/01-asset-grid.png) | ![Model detail](screenshots/04-model-detail.png) | ![3D viewer](screenshots/05-3d-viewer.png) |

| Search | Categories | Setup |
| --- | --- | --- |
| ![Search](screenshots/03-search.png) | ![Categories](screenshots/02-category-filter.png) | ![Setup wizard](screenshots/06-setup-wizard.png) |

## Contributing

Bug reports, hardware notes, docs fixes, and small PRs are welcome. Start with
[CONTRIBUTING.md](./CONTRIBUTING.md). Good first contributions include printer
reports, parser fixtures, install notes, and small UI workflow improvements.

Not sure where to start? See
[community starter issues](./docs/community-starter-issues.md) or open a
discussion.

## Security

Read [SECURITY.md](./SECURITY.md) before reporting vulnerabilities.
PrintStash is designed for trusted self-hosted networks; do not expose it
directly to the public internet without a reverse proxy, TLS, and your own
access controls.

## License

PrintStash is licensed under the [GNU AGPL-3.0](./LICENSE).
