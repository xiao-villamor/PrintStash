<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="frontend/public/logo-dark.svg" />
  <img src="frontend/public/logo.svg" alt="PrintStash logo" width="420" />
</picture>

# PrintStash

### A self-hosted workspace for 3D print files, revisions, printers, and the work around them.

PrintStash is a local-first web app for STL, 3MF, OBJ, STEP, and G-code
libraries. It connects Models and G-code revisions to printer fleets, material
state, print history, Documents, share links, notifications, access control,
Pending Imports, and Vault audits. Upload from the browser, capture a model URL,
mirror a NAS folder, or let OrcaSlicer push new G-code after every slice.

![PrintStash demo](screenshots/00-demo-v010.gif)

[![Release](https://img.shields.io/github/v/release/xiao-villamor/PrintStash?style=flat-square&color=22c55e&include_prereleases&sort=semver)](https://github.com/xiao-villamor/PrintStash/releases)
[![CI](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml/badge.svg)](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml)
[![Docker image](https://img.shields.io/badge/ghcr.io-printstash-2496ED?logo=docker&logoColor=white&style=flat-square)](https://github.com/xiao-villamor/PrintStash/pkgs/container/printstash-api)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](./LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&style=flat-square)
![React 19](https://img.shields.io/badge/react-19-61DAFB?logo=react&style=flat-square)
![Vite](https://img.shields.io/badge/vite-8-646CFF?logo=vite&style=flat-square)
![Status: beta](https://img.shields.io/badge/status-beta%20%C2%B7%20self--hosted-f59e0b?style=flat-square)

[**Quick Start**](#quick-start) · [**Features**](#features) · [**Comparison**](#printstash-vs-a-simple-model-vault) · [**Wiki / Docs**](https://www.printstash.org/docs/) · [**Limitations**](#known-limitations--beta-notes) · [**Security**](#security)

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
- Bambu LAN status and pause/resume/cancel, plus evidence-labelled history and
  best-effort artifact capture for externally-started prints, in beta.
- Optional [Spoolman](https://github.com/Donkie/Spoolman) integration (OFF by
  default): show spool inventory, pick a spool per print, and decrement it by the
  real grams used on a Moonraker-measured completion — with double-count
  detection for Moonraker's native Spoolman hook.

**Fleet and material-aware dispatch**
- Queue one print or an atomic multi-copy batch across configured printers.
- Route manually, to the default printer, or to the least-busy eligible printer,
  with exact group constraints and low, normal, or rush priority lanes.
- Track operator-set tools and material feeds on every provider. Bambu AMS trays
  and Moonraker's active Spoolman spool can synchronize automatically.
- Compare G-code material and nozzle metadata before dispatch. Known mismatches
  require an audited confirmation; unknown state remains usable.
- Keep printers out of rotation with maintenance windows, soft drain, or an
  optional operator release gate after a completed print.

**Capture, Documents, sharing, and notifications**
- Pending Imports keep URL and browser captures reviewable across restarts, with
  retry, archive/file selection, tags, and Collection assignment before ingest.
- Attach Markdown notes, PDFs, images, and other files to any Collection.
  Markdown includes a built-in editor, preview, and pasted or dropped images.
- Create expiring, read-only public links for a Model. Original-file downloads
  stay off unless the owner enables them for that link.
- Send print-completed, failed, cancelled, and printer-offline events to generic
  webhooks, Discord, Telegram, or ntfy, with per-event and per-printer controls.

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
- Optional OIDC / SSO works with Authentik, Authelia, and similar providers,
  including PKCE, just-in-time users, admin-group mapping, encrypted settings,
  and local-login fallback.
- Collection-level RBAC shares parts of a library at view/edit/admin levels.
- Per-printer roles grant view, print, control, or admin independently and apply
  to UI sessions, API keys, REST endpoints, and live WebSocket state.
- Audit logs record who changed what, including authenticated browser actions.
- A recycle bin keeps soft-deleted models restorable until retention expires,
  with manual restore, purge-expired, and permanent-delete.

**Vault integrity, backups, and portability**
- Quick and Full Vault audits persist findings, support cancellation, group
  problems by severity, and repair eligible thumbnail, Metadata, and recommended
  Revision findings through narrow audited actions.
- Full backup/restore of the database plus stored files and thumbnails.
- Backup verification checks archive structure and manifest membership, while
  creation fails closed if a vault-owned blob cannot be read consistently.
- Backups can mirror to S3/R2-compatible storage, independent of where vault
  files live.
- Export or import a versioned, hash-verified portable library archive with
  Models, Artifacts, metadata, taxonomy, history, favorites, and saved views.
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
> reverse proxy with TLS and your own authentication, and use
> `docker-compose.prod.yml`, which keeps the API off the host and requires you to
> set your own `VAULT_JWT_SECRET`.
> See [Security](#security).

Requirements: Docker and Docker Compose. Prebuilt images are published for
`linux/amd64` and `linux/arm64` (Raspberry Pi 4/5, ARM NAS, Apple-silicon VMs).
The full image supports STEP/STP preview and thumbnail generation on both
architectures.

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

docker compose up -d
```

There is nothing to edit before that first start. Every variable in the Compose
file has a working default, so `.env` is optional; copy `.env.example` to `.env`
when you actually want to change something. In particular you do **not** need to
invent a JWT secret: the placeholder in the Compose file is public, so the API
refuses to sign with it and instead generates a real secret on first boot and
stores it in its own database (see 0.8.4 in the [changelog](CHANGELOG.md)). Set
`VAULT_JWT_SECRET` yourself only when you want to own that value.

If you only want to run it, the Compose file is the single file you need:

```bash
mkdir printstash && cd printstash
curl -O https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/docker-compose.yml
docker compose up -d
```

For the smallest SQLite/local-files deployment, use
`docker-compose.light.yml`. Its API image omits browser automation and STEP
tessellation but keeps STL/OBJ/3MF thumbnail generation:

```bash
docker compose -f docker-compose.light.yml up -d
```

For a hardened production setup (API kept internal, frontend bound to localhost
behind your own TLS reverse proxy), use the production compose instead. That file
declares `VAULT_JWT_SECRET` as required and refuses to start without it, on the
grounds that a deliberately exposed host should not run on a secret nobody chose:

```bash
echo "VAULT_JWT_SECRET=$(openssl rand -hex 32)" >> .env
docker compose -f docker-compose.prod.yml up -d
```

To build the images from source instead of pulling (contributors), layer the
build overlay: `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`.

**Pin a version** for reproducible deploys. The compose files read
`PRINTSTASH_VERSION` (the image tag); set it in `.env` and bump it to upgrade:

```bash
echo "PRINTSTASH_VERSION=0.9.0" >> .env   # pin latest shipped release; omit to track latest
```

By default the compose files track `latest`. Pin `PRINTSTASH_VERSION` when you
want deliberate upgrades. See [Upgrading](https://www.printstash.org/docs/guides/upgrading/).

Open:

| Service | URL |
| --- | --- |
| Web UI | http://localhost:3000 |
| Health check | http://localhost:3000/api/v1/health |

The `api` service only exposes port 8000 on the internal Compose network, so it
is not reachable from the host. The frontend's nginx proxies `/api/v1` to it, which
is why the health check answers on port 3000. The Swagger and ReDoc pages are not
proxied, so seeing them means publishing the port yourself from a
`docker-compose.override.yml`:

```yaml
services:
  api:
    ports:
      - "127.0.0.1:8000:8000"
```

On first launch the web UI creates the first admin account, gated by a **setup
token**, because the endpoint that creates the very first account cannot require a
login. With `VAULT_SETUP_TOKEN` unset, the API generates one per process and logs
it while the vault is unconfigured:

```bash
docker compose logs api | grep "setup token"
```

Paste that into the wizard at `http://localhost:3000/setup`. The token is
per process, so restarting the `api` container before you finish invalidates it;
set `VAULT_SETUP_TOKEN` in `.env` if you want a stable one. There is no default
username or password.

## Screenshots

### Library, inspection, and revisions

| Vault overview | Model detail | G-code toolpaths |
| --- | --- | --- |
| ![A populated PrintStash vault with collections, filters, favorites, and saved views](screenshots/01-vault-overview.png) | ![Model detail with the interactive 3D viewer and recommended G-code](screenshots/02-model-detail.png) | ![G-code toolpath preview with layer navigation](screenshots/03-gcode-viewer.png) |

### Workflow and insights

| Artifact comparison | Live printer | Statistics |
| --- | --- | --- |
| ![Side-by-side comparison of two G-code revisions](screenshots/04-artifact-compare.png) | ![Live Moonraker printer progress, temperatures, and controls](screenshots/05-printer-live.png) | ![Print cost, filament, time, and workload statistics](screenshots/06-statistics.png) |

### In motion

| Organize several models | Compare G-code revisions |
| --- | --- |
| ![Select several models and apply a tag in one action](screenshots/07-organize-library.gif) | ![Open a model's revision history and compare two G-code artifacts](screenshots/08-revision-compare.gif) |

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
  support through native SDCP/MQTT, plus beta G-code upload since 0.11.3.
  Centauri file inventory and deletion remain unavailable.
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
- **Full and lite images have different optional capabilities.** Both run on
  `linux/amd64` and `linux/arm64` and generate STL/OBJ/3MF thumbnails. The full
  image additionally includes browser-assisted imports and STEP/STP
  tessellation; the lite image stores STEP files without generating their mesh
  preview.

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
in [Reverse proxy with TLS](https://www.printstash.org/docs/getting-started/installation/#reverse-proxy-with-tls).

## License

PrintStash is licensed under the [GNU AGPL-3.0](./LICENSE).
