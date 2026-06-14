<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="frontend/public/logo-dark.svg" />
  <img src="frontend/public/logo.svg" alt="PrintStash logo" width="420" />
</picture>

# PrintStash

### Self-hosted asset management for people who 3D print more things than they can remember.

PrintStash is a local-first web app for STL, 3MF, OBJ, and G-code libraries.
Upload manually or let OrcaSlicer push new G-code after every slice, then find
files by model name, collection, tags, slicer metadata, material, printer, and
print history.

![PrintStash demo](screenshots/00-demo.gif)

[![Release](https://img.shields.io/github/v/release/xiao-villamor/PrintStash?style=flat-square&color=22c55e&include_prereleases&sort=semver)](https://github.com/xiao-villamor/PrintStash/releases)
[![CI](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml/badge.svg)](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml)
[![Docker image](https://img.shields.io/badge/ghcr.io-printstash-2496ED?logo=docker&logoColor=white&style=flat-square)](https://github.com/xiao-villamor/PrintStash/pkgs/container/printstash-api)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](./LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&style=flat-square)
![React 19](https://img.shields.io/badge/react-19-61DAFB?logo=react&style=flat-square)
![Vite](https://img.shields.io/badge/vite-8-646CFF?logo=vite&style=flat-square)
![Status: beta](https://img.shields.io/badge/status-beta%20%C2%B7%20self--hosted-f59e0b?style=flat-square)

[**Quick Start**](#quick-start) · [**Features**](#features) · [**Wiki / Docs**](https://xiao-villamor.github.io/PrintStash) · [**Limitations**](#known-limitations--beta-notes) · [**Security**](#security)

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

## Who Is This For?

PrintStash is built for people who print more than they can keep track of:

- **Klipper / Moonraker users** who want live printer status, send-to-print, and
  imported print history next to the files themselves.
- **OrcaSlicer (and PrusaSlicer / Bambu Studio / Cura) users** who want every
  exported G-code captured automatically with its slicer settings.
- **Homelab / self-hosters** who want a local app on a NAS or mini PC — no cloud
  account, no subscription, no telemetry.
- **Anyone with a sprawling library** of STLs, 3MFs, STEP files, and G-code who
  is tired of "which version actually printed well?" living in folder names.

## How Is This Different?

It is not just another STL gallery. Most tools store *files*; PrintStash stores
the *whole printing context* and links it together:

- **Models + G-code as revisions** — source meshes and every sliced G-code live
  on one model, with version history and a recommended/known-good verdict.
- **Slicer metadata, parsed** — layer height, material, nozzle/bed temps,
  estimated time, filament weight, and cost extracted from the G-code itself.
- **Printer presence + live status** — see what's on which printer right now, and
  send a revision straight to it.
- **Real print history** — measured filament and actual duration captured from
  Moonraker when a print finishes, with per-print cost.
- **Search across all of it** — find by model name, collection, tag, material,
  slicer, printer, or print outcome — not just filename.

No cloud account, no subscription, no telemetry — it all runs on your hardware.

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

> [!WARNING]
> **Run PrintStash only on a trusted self-hosted network.** Do not expose it
> directly to the public internet. If you need remote access, put it behind a
> reverse proxy with TLS and your own authentication, and change **both**
> `VAULT_JWT_SECRET` and `VAULT_API_KEY` from their placeholder defaults first.
> See [Security](#security).

Requirements: Docker and Docker Compose.

```bash
git clone https://github.com/xiao-villamor/PrintStash.git
cd PrintStash

cp .env.example .env
# Edit .env and set strong, random values for VAULT_JWT_SECRET and VAULT_API_KEY,
# e.g. `openssl rand -hex 32`.

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

### Library & organization

| Asset grid | Search | Collections & filters |
| --- | --- | --- |
| ![Asset grid](screenshots/01-asset-grid.png) | ![Search](screenshots/03-search.png) | ![Collections filter](screenshots/02-collections-filter.png) |

### Model detail

| Overview | 3D viewer | G-code toolpaths |
| --- | --- | --- |
| ![Model detail](screenshots/04-model-detail.png) | ![3D viewer](screenshots/05-3d-viewer.png) | ![G-code viewer](screenshots/06-gcode-viewer.png) |

### Revisions & printers

| G-code revisions | Live printer status | Printer files | Printer diagnostics |
| --- | --- | --- | --- |
| ![Revisions](screenshots/07-revisions.png) | ![Printer status](screenshots/09-printers.png) | ![Printer files](screenshots/17-printer-files.png) | ![Printer diagnostics](screenshots/13-printer-diagnostics.png) |

### Settings & administration

| Overview | Users & access | Storage | Design |
| --- | --- | --- | --- |
| ![Settings overview](screenshots/10-settings.png) | ![Users & access](screenshots/14-settings-access.png) | ![Storage](screenshots/15-settings-storage.png) | ![Design](screenshots/16-settings-design.png) |

### In motion

| Compare G-code revisions | Filter by tag |
| --- | --- |
| ![Revision compare](screenshots/11-revision-compare.gif) | ![Tag filter](screenshots/12-tag-filter.gif) |

## Known Limitations & Beta Notes

PrintStash is a **beta** self-hosted release. It is useful today, but it is
deliberately not a full manufacturing platform. Set expectations accordingly:

- **Bambu LAN is beta** and limited to local status plus pause/resume/cancel.
  Upload, send-to-print, remote file inventory, and remote-file start are
  **not** implemented for Bambu yet. Moonraker/Klipper is the fully supported
  provider.
- **Hardware coverage is still thin.** Provider behavior needs more real-world
  validation across printers, firmware versions, and network/auth setups.
  Reports are very welcome.
- **Slicer metadata parsing varies.** Extraction is best for common OrcaSlicer,
  PrusaSlicer, Bambu Studio, Cura, and Klipper output; missing fields are
  expected — please report them with safe sample files.
- **The G-code viewer is a visualization aid**, not a slicer-grade simulator. It
  does not validate firmware macros, acceleration, pressure advance, or safety.
- **Not for direct public exposure.** It is designed for trusted self-hosted
  networks (see [Security](#security)).

Full detail — including non-goals — lives in
[docs/known-limitations.md](./docs/known-limitations.md).

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
