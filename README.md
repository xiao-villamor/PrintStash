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
index a mounted or remote library source, or let OrcaSlicer push new G-code
after every slice.

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

PrintStash [0.13.0 is published](https://github.com/xiao-villamor/PrintStash/releases/tag/v0.13.0),
with full and lite API images and a frontend image for amd64 and arm64. It adds runtime-probed storage safety tiers, reusable
remote storage connections, paired-browser marketplace capture with Model
Source provenance, richer Bambu LAN history evidence, Multipart Models, and
safer 3MF/STL preview handling. Read the
[0.13.0 changelog](./CHANGELOG.md#0130) and
[upgrade notes](./UPGRADE.md#0130-notes) before upgrading. Changes on `main`
after that release are listed under [Unreleased](./CHANGELOG.md#unreleased).

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
- Multipart Models group existing Models into named parts and alternatives
  (for example, a short or long handle) without moving, hiding, or deleting
  their files. Each Model remains independently visible, downloadable, and
  sliceable, and can be reused in more than one Multipart Model. Open the
  separate **Multipart models** tab in the Vault to create a grouping, add a
  part, then choose existing Models as its alternatives.
- Nested collections; direct Model, Collection, and Artifact tags; unified search
  across names, files, collection paths, effective tags, and provenance; filters,
  thumbnails, grid/list views,
  sorting, breadcrumbs, and drag-and-drop between collections.

**Library sources (mounted folders, NAS, and remote protocols)**
- Point PrintStash at a mounted folder, S3 prefix, WebDAV collection, SFTP
  directory, or Google Drive folder and it indexes files **in place**. Only
  thumbnails and metadata are stored in the Vault.
- Discovery picks up added, removed, and edited files without a recursive
  full-download loop. Remote sources are read-only. Mounted folders may accept
  create-only web uploads and revisions without overwriting existing bytes.
- Keep it current with a per-volume schedule (presets or custom cron), manual
  "Scan now", and optional real-time watching of local folders.
- Network folders (NFS/SMB) can't deliver filesystem events, so watching
  auto-detects the filesystem and falls back to the schedule — with a per-volume
  override. An unmounted share can never trigger a mass delete.
- Remote scans use durable cursors, one global scan slot, 1,000-key pages, a
  2 GiB and 15-minute slice budget, 8 MiB/s content pacing, 4 metadata calls/s
  for WebDAV/SFTP, and a 24-hour provider-error backoff. A weekly rotating hash
  check catches same-size, same-mtime replacements.

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
- Pair named, revocable browsers for authenticated Printables file selection
  and MakerWorld package transfer without sending marketplace cookies to
  PrintStash. MyMiniFactory OAuth and Cults metadata connections are per-user.
- Captured Models retain bounded source snapshots, confirmed/inferred fields,
  and explicit user overrides; portable archives can carry the provenance as
  an optional backward-compatible sidecar.
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
- A recycle bin keeps soft-deleted models restorable until retention expires.
  Scheduled cleanup only creates a bounded preview. Physical deletion requires
  an exact administrator approval, Verified active storage, a fresh verified
  backup on independent S3, and a configurable quarantine (seven days by
  default), with every proof checked again before finalization.

**Vault integrity, backups, and portability**
- Quick and Full Vault audits persist findings, support cancellation, group
  problems by severity, and repair eligible thumbnail, Metadata, and recommended
  Revision findings through narrow audited actions.
- Full backup/restore of the database plus stored files and thumbnails.
- Backup verification checks archive structure and manifest membership, while
  creation fails closed if a vault-owned blob cannot be read consistently.
- Backups can replicate to S3-compatible storage, WebDAV, SFTP, or Google Drive,
  independent of where Vault files live. Restore rechecks the provider identity
  and archive hash before replacing data.
- Export or import a versioned, hash-verified portable library archive with
  Models, Artifacts, metadata, taxonomy, history, favorites, and saved views.
- Metadata export to JSON or CSV for analysis, migration planning, or audits.
- Model-card metrics and the metadata fields shown on detail pages are
  configurable.
- Local disk is the default Vault storage, with optional S3/R2, B2, Wasabi,
  self-hosted S3, Nextcloud/WebDAV, or SFTP and optional Postgres. Reusable
  remote connections also support Google Drive for Library sources and backup
  replicas. Storage support maturity is distinct from the
  Verified/Guarded/Unguarded tier measured at runtime; remote presets remain
  beta except the generic/native S3 path.
- Health checks report database, measured storage capabilities, backup, and
  printer-provider readiness.

Storage and migration guides:

- [0.13.0 release and migration guide](./docs/0.13.0-release-guide.md)
- [Library sources and NAS recipes](./docs/library-sources.md)
- [Storage provider and protocol matrix](./docs/provider-support.md#storage-and-library-source-compatibility)
- [Garbage collection safety and recovery](./docs/storage-data-safety.md)
- [Upgrade and migration to 0.13.0](./UPGRADE.md#0130-notes)

## Quick Start

> [!WARNING]
> **Run PrintStash only on a trusted self-hosted network.** Do not expose it
> directly to the public internet. If you need remote access, put it behind a
> reverse proxy with TLS and your own authentication, and use
> `docker-compose.prod.yml`, which keeps the API off the host and requires you to
> set your own `VAULT_JWT_SECRET`.
> See [Security](#security).

Install Docker with the Compose plugin, then download the
[**simple Compose file**](./docker-compose.simple.yml) and start PrintStash:

```bash
mkdir -p printstash && cd printstash
curl -fsSL https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/docker-compose.simple.yml -o docker-compose.yml
docker compose up -d
```

Open **[http://localhost:3000](http://localhost:3000)** (or
`http://<server-ip>:3000` from another machine). Get the first-login setup token:

```bash
docker compose logs api | grep "setup token"
```

Paste it into the setup wizard to create your admin account. There is no default
username or password. Restarting the API before setup generates a new token.

The simple deployment uses the full prebuilt images, SQLite, and persistent
Docker volumes. No `.env`, build step, PostgreSQL, or S3 service is needed.
Images support `linux/amd64` and `linux/arm64`. Start with 1 GB RAM; 2 GB or
more helps with large meshes.

**From a Git checkout**, use `docker compose -f docker-compose.simple.yml up -d`
and include `-f docker-compose.simple.yml` in subsequent Compose commands.

For ports, version pinning, host folders, upload limits, SSO, HTTPS, updates,
and the purpose of the other Compose files, see
[**Deployment and optional settings**](./docs/deployment.md).

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
  authentication, status, streamed plain-text G-code and validated `.bgcode`
  upload/start, files, and print controls. Prusa Connect cloud is not used.
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
[docs/known-limitations.md](./docs/known-limitations.md). Storage provider setup,
runtime safety tiers, and required credentials are documented in
[docs/storage-providers.md](./docs/storage-providers.md).

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

## Trademark

PrintStash is the name and mark of this self-hosted, open-source project, published here at
[xiao-villamor/PrintStash](https://github.com/xiao-villamor/PrintStash) and
[printstash.org](https://www.printstash.org). The project is not affiliated with, endorsed by,
or connected to any separately hosted service using the PrintStash name. If you run a
service that reuses the name, please distinguish it clearly from the self-hosted project so
users are not misled.
