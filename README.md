<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="frontend/public/logo-dark.svg" />
  <img src="frontend/public/logo.svg" alt="PrintStash logo" width="420" />
</picture>

# PrintStash

### Organize your 3D models, keep the G-code that works, and manage your printers.

PrintStash is an open-source, self-hosted web app for your 3D printing library.
Bring together files from your computer, model marketplaces, and NAS; preview
them, open them in your slicer, and keep print settings and results with each
Model. Run it on your own server with SQLite and local disk to get started.

![PrintStash demo](screenshots/00-demo-v010.gif)

[![Release](https://img.shields.io/github/v/release/xiao-villamor/PrintStash?style=flat-square&color=22c55e&include_prereleases&sort=semver)](https://github.com/xiao-villamor/PrintStash/releases)
[![CI](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml/badge.svg)](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml)
[![Docker image](https://img.shields.io/badge/ghcr.io-printstash-2496ED?logo=docker&logoColor=white&style=flat-square)](https://github.com/xiao-villamor/PrintStash/pkgs/container/printstash-api)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](./LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&style=flat-square)
![React 19](https://img.shields.io/badge/react-19-61DAFB?logo=react&style=flat-square)
![Vite](https://img.shields.io/badge/vite-8-646CFF?logo=vite&style=flat-square)
![Status: beta](https://img.shields.io/badge/status-beta%20%C2%B7%20self--hosted-f59e0b?style=flat-square)

[**Quick Start**](#quick-start) · [**Features**](#features) · [**Printers**](#printer-compatibility) · [**Screenshots**](#screenshots) · [**Docs**](#documentation) · [**Limitations**](#known-limitations--beta-notes)

</div>

---

## Quick Start

> [!WARNING]
> **Run PrintStash on a trusted self-hosted network.** For remote access, use
> a reverse proxy with TLS and your own access controls. The
> [production Compose file](./docker-compose.prod.yml) keeps the API internal
> and requires your own signing secret. See [Security](#security).

Install Docker with the Compose plugin, then download the
[**simple Compose file**](./docker-compose.simple.yml) and start PrintStash:

```bash
mkdir -p printstash && cd printstash
curl -fsSL https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/docker-compose.simple.yml -o docker-compose.yml
docker compose up -d
```

Open **[http://localhost:3000](http://localhost:3000)** (or
`http://<server-ip>:3000` from another machine) and complete setup:

1. **Create your administrator account.** There is no default username or
   password. In v0.13.0, the wizard asks for the setup token from
   `docker compose logs api`; look for the line containing `setup token`.
   Source builds from `main` use the new [browser registration flow](./docs/first-run.md)
   without a console token. Keep access restricted until setup is complete.
2. **Add your first files.** Upload a Model, or connect an existing folder under
   **Settings → Library sources** and run its first scan. Folder paths must be
   accessible inside the API container; see the [mounting guide](./docs/deployment.md#data-and-host-folders).
3. **Explore the Model.** Inspect its preview, open a file in your slicer, and
   add G-code as a Revision. Connect a printer when you want to send a print.

The simple deployment uses the full prebuilt images, SQLite, and persistent
Docker volumes. No `.env`, build step, PostgreSQL, or S3 service is needed.
Images support `linux/amd64` and `linux/arm64`. Start with 1 GB RAM; 2 GB or
more helps with large meshes.

**From a Git checkout**, use `docker compose -f docker-compose.simple.yml up -d`
and include `-f docker-compose.simple.yml` in subsequent Compose commands.

For a single container containing the full API and web UI, use
[docker-compose.unified.yml](./docker-compose.unified.yml). Build and publishing
instructions are in the [deployment guide](./docs/deployment.md#one-container-image).

For ports, version pinning, host folders, upload limits, SSO, HTTPS, updates,
and the purpose of the other Compose files, see
[**Deployment and optional settings**](./docs/deployment.md).

## Features

A **Model** keeps a printable design's files, G-code Revisions, and print history
in one place. **Collections** organize the library; a **Multipart Model** groups
Models that make up one object, such as a box with a base and a choice of lids.
You can use the library without connecting a printer.

[Import](#bring-your-files-together) · [Organize](#find-and-organize-your-models) ·
[Multipart Models](#keep-multipart-projects-together) · [Preview](#inspect-files-and-open-your-slicer) ·
[Revisions](#keep-track-of-g-code-that-works) · [Print](#send-prints-and-manage-your-fleet) ·
[Materials and costs](#track-filament-costs-and-results) · [Share](#add-notes-share-and-stay-informed) ·
[Backups](#protect-and-move-your-library) · [Access](#manage-access-and-personalize-your-workspace)

### Bring your files together

- **Upload from your browser:** STL, 3MF, OBJ, STEP/STP, G-code, and PrusaSlicer
  binary G-code (`.bgcode`). Import ZIP archives with file selection and keep
  their folder structure as nested Collections.
- **Capture from model sites:** send supported URLs or use the
  [browser extension](./browser-extension/README.md) for Printables, MakerWorld,
  and Thingiverse. MakerWorld packages require browser transfer; Thingiverse
  files require the extension or manual upload. Per-user MyMiniFactory OAuth
  and Cults metadata connections are also available.
- **Review before importing:** **Pending Imports** is an inbox for captures.
  Choose files, a Collection, and tags, see partial results, and retry failed
  captures. Imported Models retain available source information, with fields
  you can review and correct. Paired browsers keep marketplace cookies in the browser.
- **Use the library you already have:** index mounted folders or NAS shares,
  S3-compatible storage, WebDAV/Nextcloud, SFTP, or Google Drive **in place**.
  Remote sources are read-only; mounted folders can optionally accept new files
  without overwriting existing ones. Scan manually, on a schedule, or watch
  supported local folders. See [Library sources and NAS setup](./docs/library-sources.md).
- **Send slices automatically:** the [OrcaSlicer post-processing hook](./scripts/printstash_orca_push.py)
  uploads exported G-code using your account and API key. Content-hash
  deduplication recognizes identical files.

### Find and organize your Models

- Browse thumbnails in grid or list views, navigate nested Collections, and
  move Models with drag-and-drop.
- Search names, filenames, Collection paths, tags, and source information.
  Narrow results by file type, material, slicer, printer model, Revision status,
  print outcome, storage location, or upload date.
- Star favorites and save filtered views so your usual searches are one click away.
- Select multiple Models to update tags, move them between Collections, change
  Revision labels, or send them to the trash in one action.
- Choose the metrics shown on Model cards and the metadata visible on detail pages.

### Keep multipart projects together

- Group existing Models into ordered, named pieces such as a base, handle, and
  lid. Offer alternatives for a piece, such as a short or long handle.
- Give the Multipart Model its own cover, description, tags, Collection, and
  Markdown, PDF, or image guides.
- Browse Multipart Models alongside ordinary Models in the main library.
  Use **Organized**, **Everything**, **Multipart sets only**, or **Parts only**
  to choose how groupings and their members appear.
- Reuse a Model in several projects. Each keeps its own files, Revisions, and
  print history; removing a grouping leaves those Models and files intact.

### Inspect files and open your slicer

- Rotate and zoom source meshes in the browser, with solid, X-ray, and wireframe
  modes, a build-plate grid, fit-to-view, and screenshots.
- Preview plain-text G-code layer by layer, toggle travel moves, and view the
  printer profile's bed outline.
- Inspect extracted dimensions, volume, triangle count, and slicer settings
  where available. Common OrcaSlicer, PrusaSlicer, Bambu Studio, Cura, and
  Klipper-style output can supply nozzle, layer height, infill, temperatures,
  material, and estimated time, filament use, and cost.
- Open a file directly in **OrcaSlicer, Bambu Studio, or PrusaSlicer** using
  slicer deep links. STEP/STP mesh previews require the full API image.

### Keep track of G-code that works

- Keep several G-code Revisions with each Model, with labels, notes, and
  **known good**, **needs test**, **failed**, or **archived** status.
- Mark one Revision as recommended so the next print starts from your preferred
  file. A Model with G-code always has one recommended Revision.
- Compare two files side by side for slicer and material settings, estimates,
  and recorded print outcomes.
- Review each Model's print history, import matching Moonraker jobs, or log a
  print manually. A Revision's first successful print marks it known good.

### Send prints and manage your fleet

- See printer status, progress, and temperatures; send G-code, choose whether to
  start it, and pause, resume, or cancel on [supported printers](#printer-compatibility).
- See which printers already hold a Model's G-code and start an existing remote
  file where the provider supports it.
- Queue a print or a batch of copies. Route work manually, to a default printer,
  or to the least-busy eligible printer, with printer groups and low, normal,
  or rush priorities. Fleet scheduling uses plain-text G-code.
- Check loaded material and nozzle size against G-code before dispatch.
  Known mismatches require confirmation for manual sends and block automatic
  routing; unknown material state remains usable.
- Plan maintenance windows, stop new work reaching a printer while its current
  print finishes, or require an operator to release it after each print.
- With Bambu LAN, keep history of externally started jobs and, when available,
  recover their G-code or project 3MF. The history distinguishes archived files
  from printer-reported metadata; recovery depends on the printer's cache.

### Track filament, costs, and results

- Manage printer and filament presets from **Profiles**. Record loaded tools
  and material feeds manually for any provider; Bambu AMS and Moonraker's active
  Spoolman spool can synchronize when that information is available.
- Optionally connect **[Spoolman](https://github.com/Donkie/Spoolman)** to see
  inventory and remaining filament, import filament presets, and select a spool
  for a print. Measured Moonraker completions can deduct the grams used, with
  checks to avoid double-counting Moonraker's native Spoolman integration.
- Keep actual duration, measured filament use, and per-print cost from Moonraker
  alongside slicer estimates and recorded outcomes.
- Use the admin **Statistics** dashboard to explore print counts, filament,
  cost, and print time over a selected period, with charts and Collection and
  filament breakdowns. Choose a display currency in Settings.

### Add notes, share, and stay informed

- Keep Markdown notes, PDFs, images, and other Documents with a Collection.
  The Markdown editor includes preview, tables, and pasted or dropped images.
- Share a Model through an expiring, read-only public link. Original-file
  downloads are disabled unless you enable them for that link.
- Receive print-completed, failed, cancelled, and printer-offline notifications
  through **Discord, Telegram, ntfy, or generic webhooks**. Notifications are
  opt-in, with per-event and per-printer controls.

### Protect and move your library

- Restore trashed Models before permanent cleanup. Physical cleanup is guarded
  by storage capabilities, administrator approval, a verified independent
  backup, and quarantine; see [storage safety and recovery](./docs/storage-data-safety.md).
- Run **Quick or Full Vault audits** to find missing files, integrity problems,
  and metadata issues. Review findings and use supported repairs for thumbnails,
  parsed metadata, and recommended Revisions.
- Create, verify, and restore backups of the SQLite database, managed files, and
  thumbnails. Schedule a daily backup and send copies to local storage,
  S3-compatible storage, WebDAV, SFTP, or Google Drive. PostgreSQL needs
  operator-managed database backups; Library source originals need separate backups.
- Export or import a portable library archive with files, metadata, tags,
  Collections, history, favorites, saved views, and captured source information.
  Export metadata alone as JSON or CSV when you need it for analysis.
- Start with local disk and optionally use PostgreSQL or remote managed storage
  such as S3, R2, B2, Wasabi, Nextcloud/WebDAV, or SFTP. Support maturity and
  available safety guarantees vary by provider; see the [storage guide](./docs/storage-providers.md).

### Manage access and personalize your workspace

- Create local accounts and named API keys for scripts and slicer hooks, or
  connect **OIDC / SSO** through Authentik, Authelia, or a similar identity provider.
- Grant view, edit, or admin access to Collections and separate view, print,
  control, or admin access to individual printers. Audit logs record changes.
- Use responsive desktop and mobile layouts, light/dark themes, and an
  installable web app. English and Spanish localization is in progress; some
  screens fall back to English. Library data and printer actions need a live
  connection to your server.
- Check storage usage, backup status, and printer connectivity in administration;
  health endpoints and Prometheus metrics support external monitoring.

### Available on main, awaiting a release

The features above describe v0.13.0. Source builds from `main` also include
[multipart build tracking](./docs/multipart-builds.md) with quantities and
confirmed usable output, [BGCODE toolpath previews](./docs/bgcode-preview.md),
[guided browser registration](./docs/first-run.md), and backup destination
history with retries for failed copies. These are **not included in v0.13.0
images**. See [Unreleased](./CHANGELOG.md#unreleased) for the complete list.

## Printer Compatibility

Moonraker/Klipper is the primary integration. **Beta** providers have implemented
workflows, but still need broader validation on physical printers and firmware.
The app shows each printer's capabilities and disables unsupported actions.

| Printer / service | Support level | What you can do |
| --- | --- | --- |
| **Moonraker / Klipper** | Stable | Live status, upload/start, pause/resume/cancel, remote files, matching print-history import, measured filament use |
| **Elegoo Neptune 4 / Pro / Plus / Max** | Via Moonraker | Uses the Moonraker integration through a dedicated setup preset |
| **Bambu LAN** | Beta | Local status, plain-text G-code upload with explicit start, pause/resume/cancel, external-job history and best-effort file capture; no remote file inventory |
| **PrusaLink** | Beta | Local status, plain-text G-code and validated `.bgcode` upload/start, remote files, pause/resume/cancel; no Prusa Connect cloud |
| **OctoPrint / OctoPi** | Beta | Local status, G-code upload/start, remote files, pause/resume/cancel |
| **Elegoo Centauri Carbon / Carbon 2** | Beta | Local status, G-code upload/start, pause/resume/cancel; no remote file inventory or history import |

See [Provider support](./docs/provider-support.md) for authentication, unsupported
actions, diagnostics, and the hardware validation record.

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

- **You still need a slicer.** PrintStash organizes files and dispatches prepared
  G-code. Its toolpath viewer does not simulate firmware behavior or validate
  whether a file is safe for your printer.
- **Printer support depends on the provider and firmware.** The
  [compatibility table](#printer-compatibility) summarizes available actions;
  physical hardware validation is still limited, especially for beta providers.
- **Metadata and marketplace captures can be incomplete.** Slicer output and
  source-site access vary. Missing fields, expired browser sessions, or
  unavailable downloads may require manual review or another capture.
- **Full and lite images differ.** Both generate STL/OBJ/3MF thumbnails. The
  full image also includes browser-assisted imports, STEP/STP mesh previews,
  and optional remote storage transports. Lite stores STEP files without mesh
  previews and retains native local and S3 managed storage.
- **Backups have a defined scope.** Built-in database backup/restore supports
  SQLite; PostgreSQL and files indexed from external Library sources need
  separate backup procedures.
- **One server process per library.** The supported deployment runs one API
  process per Vault. Printer actions, uploads, and library data require that
  server to be online, including when using the installed web app.

See [Known limitations](./docs/known-limitations.md) for the full boundaries and
[Security](#security) for deployment guidance.

## Documentation

| I want to… | Start here |
| --- | --- |
| Learn the app's workflows | [User documentation](https://www.printstash.org/docs/) |
| Configure Docker, ports, folders, or SSO | [Deployment guide](./docs/deployment.md) |
| Upgrade an existing installation | [Upgrade notes](./UPGRADE.md) · [v0.13.0 release guide](./docs/0.13.0-release-guide.md) |
| Connect a NAS or an existing library | [Library sources](./docs/library-sources.md) |
| Choose remote storage | [Storage providers](./docs/storage-providers.md) · [Compatibility details](./docs/provider-support.md#storage-and-library-source-compatibility) |
| Set up browser capture | [Browser extension](./browser-extension/README.md) · [Pending Imports guide](./docs/vault-maintenance-and-capture.md) |
| Check backups or recover data | [Disaster recovery](./docs/disaster-recovery.md) · [Storage safety](./docs/storage-data-safety.md) |
| Follow releases and future work | [Changelog](./CHANGELOG.md) · [Roadmap](./docs/roadmap.md) |

## Project Status

PrintStash is in **beta**, with a published release for self-hosted use.
Library management and Moonraker/Klipper are the best-established workflows;
other printer integrations have explicit [support levels](#printer-compatibility).

| At a glance | Current status |
| --- | --- |
| **Latest release** | [v0.13.0](https://github.com/xiao-villamor/PrintStash/releases/tag/v0.13.0) · [Changelog](./CHANGELOG.md#0130) |
| **Recommended installation** | Docker Compose with SQLite and local disk; full and lite images for amd64 and arm64 |
| **Upgrading** | Back up first, then follow the [upgrade notes](./UPGRADE.md#0130-notes) |
| **Development** | Changes after v0.13.0 are listed under [Unreleased](./CHANGELOG.md#unreleased); future plans are in the [roadmap](./docs/roadmap.md) |

## Contributing

Bug reports, hardware notes, docs fixes, and small PRs are welcome. Start with
[CONTRIBUTING.md](./CONTRIBUTING.md). Good first contributions include printer
reports, parser fixtures, install notes, and small UI workflow improvements.

Not sure where to start? See
[community starter issues](./docs/community-starter-issues.md), ask in
[Discussions](https://github.com/xiao-villamor/PrintStash/discussions), or
[report an issue](https://github.com/xiao-villamor/PrintStash/issues).

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
