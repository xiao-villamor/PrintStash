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

[**Quick Start**](#quick-start) · [**Features**](#features) · [**Comparison**](#printstash-vs-a-simple-model-vault) · [**Wiki / Docs**](https://xiao-villamor.github.io/PrintStash) · [**Limitations**](#known-limitations--beta-notes) · [**Security**](#security)

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

## Features

**Ingest and organize**
- STL, 3MF, OBJ, STEP/STP, and G-code upload from the browser.
- URL imports and `.zip` archives, with per-file selection on extraction.
- An OrcaSlicer post-processing hook pushes exported G-code automatically: it
  logs in with username + API key, then uploads under a JWT Bearer token.
- Content-hash dedup groups files into logical models and keeps version history
  in one place rather than scattered across folders.
- Nested collections, flat tags, search, filters, thumbnails, grid/list views,
  sorting, breadcrumbs, and drag-and-drop between collections.

**Shared volumes (mirror a folder or NAS)**
- Point PrintStash at a folder on the server or a NAS and it indexes files **in
  place** — no copying, no second source of truth; only thumbnails and metadata
  are stored in the vault.
- Two-way sync: scans pick up added, removed, and edited files, and web uploads
  and revisions write back into the folder (never overwriting existing bytes).
- Keep it current with a per-volume schedule (presets or custom cron), manual
  "Scan now", and optional real-time watching of local folders.
- Network folders (NFS/SMB) can't deliver filesystem events, so watching
  auto-detects the filesystem and falls back to the schedule — with a per-volume
  override. An unmounted share can never trigger a mass delete.

**Preview and inspect**
- A browser 3D viewer for source meshes — solid, X-ray, and wireframe modes,
  plus build-plate grid, fit-to-view, zoom, reset, and screenshot.
- G-code toolpath preview with layer navigation, travel visibility, and bed
  overlays derived from printer profiles.
- One model detail page covers the source files, recommended G-code, slicer
  settings, mesh metadata, and print history.
- Slicer metadata is parsed out of common OrcaSlicer, PrusaSlicer, Bambu Studio,
  Cura, and Klipper-style output: slicer/version, printer profile, nozzle, layer
  height, infill, material, filament brand/type, temperatures, estimated time,
  and filament length/weight/cost.
- Mesh metadata where the file carries it — bounding box, volume, triangle count.

**G-code revisions**
- Multiple G-code revisions per model, each with a label, notes, and outcome
  status.
- Statuses are `known_good`, `needs_test`, `failed`, or `archived`; exactly one
  revision is recommended at a time.
- A side-by-side compare view diffs two revisions on slicer, material, and print
  metadata.
- The first successful print auto-marks a revision known-good.

**Printer workflows**
- Moonraker/Klipper printers with live WebSocket status and send-to-print.
- Remote file inventory sync, matched back to vault files where the filenames
  line up.
- Vault-initiated jobs track through upload/start/status states, and the UI shows
  which printer already holds a model's G-code or can start a supported remote
  file.
- Print history import pulls measured filament use, actual duration, and
  per-print cost from Moonraker.
- Provider diagnostics cover capabilities, configuration, and connectivity.
- Bambu LAN status and pause/resume/cancel, in beta.
- Optional [Spoolman](https://github.com/Donkie/Spoolman) integration (OFF by
  default): show spool inventory, pick a spool per print, and decrement it by the
  real grams used on a Moonraker-measured completion — with double-count
  detection for Moonraker's native Spoolman hook.

**Statistics and cost insights**
- A Statistics dashboard (admin-only) turns completed prints into trends: total
  cost, prints, filament used, average filament per print, and total print time.
- A cost / filament / prints time series with selectable area, line, or bar
  charts, plus top collections and most-used filaments breakdowns.
- Period filter (7/30/90 days, 1 year, all time) and a configurable display
  currency (Settings → Design) applied across cost figures.

**Users, access, and administration**
- A first-run setup wizard creates the first admin account. There is no default
  password.
- JWT login with refresh/logout, admin user management, and named API keys for
  scripts and slicer hooks.
- Collection-level RBAC shares parts of a library at view/edit/admin levels.
- Audit logs record who changed what.
- A recycle bin keeps soft-deleted models restorable until retention expires,
  with manual restore, purge-expired, and permanent-delete.

**Backups, portability, and customization**
- Full backup/restore of the database plus stored files and thumbnails.
- Backups can mirror to S3/R2-compatible storage, independent of where vault
  files live.
- Metadata export to JSON or CSV for analysis, migration planning, or audits.
- Model-card metrics and the metadata fields shown on detail pages are
  configurable.
- Local disk by default, with optional S3/R2 object storage and Postgres, plus
  upload limits, trash retention, and backup retention.
- Health checks report database, storage, backup, and printer-provider readiness.

## Quick Start

> [!WARNING]
> **Run PrintStash only on a trusted self-hosted network.** Do not expose it
> directly to the public internet. If you need remote access, put it behind a
> reverse proxy with TLS and your own authentication, and change
> `VAULT_JWT_SECRET` from its placeholder default first.
> See [Security](#security).

Requirements: Docker and Docker Compose. Prebuilt images are published for
`linux/amd64` and `linux/arm64` (Raspberry Pi 4/5, ARM NAS, Apple-silicon VMs).
On ARM, STEP/STP files upload and store but don't get a 3D preview — see
[Known Limitations](#known-limitations--beta-notes).

A modest host is enough. As a starting point:

| Resource | Minimum | Comfortable |
| --- | --- | --- |
| RAM | 1 GB | 2 GB+ |
| CPU | 1 core | 2+ cores |
| Disk | ~1 GB for images | + room for your library |

SQLite + local disk is the default; thumbnailing large meshes is the most
memory-hungry step, so give it 2 GB if you upload big STLs. Storage grows with
your library — the files themselves dominate, the database stays small.

The default `docker-compose.yml` pulls prebuilt images from GHCR — no build step.

```bash
git clone https://github.com/xiao-villamor/PrintStash.git
cd PrintStash

cp .env.example .env
# Edit .env and set a strong, random value for VAULT_JWT_SECRET,
# e.g. `openssl rand -hex 32`.

docker compose up -d
```

For a hardened production setup (API kept internal, frontend bound to localhost
behind your own TLS reverse proxy), use the production compose instead:

```bash
docker compose -f docker-compose.prod.yml up -d
```

To build the images from source instead of pulling (contributors), layer the
build overlay: `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`.

**Pin a version** for reproducible deploys. The compose files read
`PRINTSTASH_VERSION` (the image tag); set it in `.env` and bump it to upgrade:

```bash
echo "PRINTSTASH_VERSION=0.6.4" >> .env   # pin; omit to track latest
```

By default the compose files track `latest`. Pin `PRINTSTASH_VERSION` when you
want deliberate upgrades. See [Upgrading](https://xiao-villamor.github.io/PrintStash/guides/upgrading/).

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

- **Bambu LAN is beta** with local status, plain-text G-code upload, explicit
  start, and pause/resume/cancel. Remote inventory/deletion is not implemented.
- **PrusaLink is beta** for local FDM printers, with Digest or legacy API-key
  authentication, status, upload/start, files, and print controls. Prusa
  Connect cloud is not used.
- **Elegoo support covers Neptune 4, Pro, Plus, and Max** through Moonraker;
  Centauri Carbon and Carbon 2 additionally have beta local status/control
  support through native SDCP/MQTT. Centauri upload/inventory is unavailable.
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
- **ARM has no STEP preview.** Images run on `linux/amd64` and `linux/arm64`, but
  the OpenCASCADE tessellation dependency ships no Linux ARM wheel, so on ARM
  STEP/STP files upload and store without a generated 3D preview. All other file
  types and features are identical across architectures.

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
access controls. The production compose (`docker-compose.prod.yml`) binds only
the frontend to `127.0.0.1`; copy-pasteable Caddy / Traefik / nginx examples are
in [Reverse proxy with TLS](https://xiao-villamor.github.io/PrintStash/getting-started/installation/#reverse-proxy-with-tls).

## License

PrintStash is licensed under the [GNU AGPL-3.0](./LICENSE).
