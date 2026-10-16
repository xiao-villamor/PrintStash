<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="frontend/public/logo-dark.svg" />
  <img src="frontend/public/logo.svg" alt="PrintStash logo" width="320" />
</picture>

# PrintStash

**Your 3D printing library, organized.**

An open-source, self-hosted home for your 3D models, G-code revisions, and print history.<br />
Keep the settings that worked with each Model, and send your next print to a compatible printer.

[![Release](https://img.shields.io/github/v/release/xiao-villamor/PrintStash?style=flat-square&color=22c55e)](https://github.com/xiao-villamor/PrintStash/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/xiao-villamor/PrintStash/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-2496ED?logo=docker&logoColor=white&style=flat-square)](https://github.com/xiao-villamor/PrintStash/pkgs/container/printstash)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](./LICENSE)
[![Status: beta](https://img.shields.io/badge/status-beta-f59e0b?style=flat-square)](#project-status--roadmap)

[**Get Started**](#quick-start) · [**Documentation**](https://www.printstash.org/en/docs/) · [**Releases**](https://github.com/xiao-villamor/PrintStash/releases) · [**Contributing**](#contributing)

<a href="screenshots/02-model-detail.png">
  <img src="screenshots/02-model-detail.png" alt="A Model's 3D preview beside its recommended G-code, slicer settings, and printer actions" width="960" />
</a>

*From the original Model to the G-code worth printing again.*

</div>

## Why PrintStash?

- **Organize everything.** Bring models, files, Collections, and imports into
  one searchable library, including files that stay on your NAS.
- **Keep the G-code that works.** Save revisions, slicer settings, and print
  results together, so a successful print is easy to repeat.
- **Connect your workflow.** Inspect a Model, open it in your slicer, then send
  prepared G-code to a compatible printer. The library also works on its own.

## Quick Start

You need **Docker with the Compose plugin**, an AMD64 or ARM64 host, and space
for your files. Start with **1 GB RAM**; 2 GB or more helps with large meshes.

> [!WARNING]
> **Use a trusted network and restrict access until setup is complete.** The
> first person to finish registration becomes the administrator. For remote
> access, configure a [reverse proxy with TLS and access controls](https://www.printstash.org/en/docs/getting-started/installation/#reverse-proxy-with-tls).

The commands below use the `latest` image, which can also be published from
`main`. To install the latest published release, create a `.env` file containing
`PRINTSTASH_VERSION=0.14.0` beside the downloaded Compose file before running
`docker compose up -d`. See [version pinning](https://www.printstash.org/en/docs/guides/upgrading/#pinning-images).

```bash
mkdir -p printstash && cd printstash
curl -fsSL https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/docker-compose.yml -o docker-compose.yml
docker compose up -d
```

Open **[http://localhost:3000](http://localhost:3000)**, or
`http://<server-ip>:3000` from your trusted local network:

1. **Create your administrator account and check storage.** There is no default
   username, password, or console token to copy in 0.14.0 and later. App-store
   deployments may provision the administrator instead. See the
   [first-run guide](https://www.printstash.org/en/docs/getting-started/first-run/).
2. **Add files.** Upload a Model or connect an existing folder under
   **Settings → Library sources** and run its first scan. Host/NAS folders must be
   [mounted inside the container](https://www.printstash.org/en/docs/guides/shared-volumes/#mount-a-folder-into-docker).
3. **Open a Model.** Explore the preview, add G-code as a Revision, and connect
   a printer when you are ready.

The default runs **one container**, with SQLite and local storage in one
persistent Docker volume at `/data`. No build step, `.env`, PostgreSQL, or S3
service is required. From a Git checkout, the same `docker compose up -d`
command works in the repository root.

For ports, HTTPS, updates, PostgreSQL, remote storage, or SSO, read the
[**deployment guide**](https://www.printstash.org/en/docs/getting-started/installation/).
[docker-compose.advanced.yml](./docker-compose.advanced.yml) lists all settings.
Always back up before upgrading; check [upgrade notes](https://www.printstash.org/en/docs/guides/upgrading/) for your
version, especially when moving from 0.13.x or earlier to the single `/data` volume.

## Features

A **Model** connects a design's files, G-code Revisions, and print history.
**Collections** organize the library; **Multipart Models** group related Models
as named pieces and alternatives. These core workflows are available in
[0.14.0](https://github.com/xiao-villamor/PrintStash/releases/tag/v0.14.0);
[0.15.0 changes](#project-status--roadmap) are listed separately below.

### Library & Organization

Browse previews, search and filter your library, organize nested Collections,
and keep favorites and saved views. Bulk edits handle tags and organization;
Multipart Models keep multi-piece projects together without duplicating their
files. Attach notes and Documents, or share a Model with an expiring, read-only
link; original downloads are off unless you enable them.
Optional [AI Search](https://www.printstash.org/en/docs/guides/ai-search/) and Similar Models
help explore the library; learned search needs operator-supplied local models,
and similarity is review evidence, not a guarantee of printable compatibility.
Track part quantities and confirmed output with [multipart builds](https://www.printstash.org/en/docs/guides/multipart-models/#create-and-track-a-build).

### Imports & Storage

Upload files or ZIP archives, review marketplace captures in **Pending Imports**,
or index an existing folder or NAS in place. Remote Library sources are
read-only; mounted folders can optionally accept new files without overwriting
existing ones. Storage maturity and safety guarantees vary by provider.

See [Library sources](https://www.printstash.org/en/docs/guides/shared-volumes/),
[storage providers](https://www.printstash.org/en/docs/guides/storage-providers/), and the
[browser extension](https://www.printstash.org/en/docs/guides/browser-capture/). MakerWorld packages need
browser transfer; Thingiverse downloads need the extension or manual upload.
Site access and metadata can be incomplete and may need review.

### 3D Preview & G-code Revisions

Inspect meshes and G-code layers in the browser, open originals in your slicer,
and keep **known good**, **needs test**, **failed**, or **archived** Revisions.
Recommend a file for the next print and compare its settings and recorded results.
STL, 3MF, OBJ, STEP/STP, DXF, G-code, and `.bgcode` are supported inputs; DXF
originals have no preview, and [BGCODE previews](https://www.printstash.org/en/docs/reference/known-limitations/#files-and-previews) have
conversion limits. The [OrcaSlicer hook](https://www.printstash.org/en/docs/guides/user-guide/#skip-the-manual-upload-the-orcaslicer-hook) can attach a slice
to its source Model.

**You still need a slicer.** The toolpath viewer does not simulate firmware or
validate whether G-code is safe for your printer. Extracted settings depend on
the slicer's output; originals remain available when a preview cannot be made.

### Printer Management & Print Queues

Monitor status and temperatures, send files, and control prints where the provider
supports it. Queue copies, organize printer groups, and route work manually, to a
default printer, or to the least-busy eligible printer. Fleet scheduling uses
plain-text G-code. Proven material or nozzle mismatches block automatic routing
and require confirmation for manual sends; unknown material state remains usable.

Check [printer compatibility](#printer-compatibility) before connecting hardware.

### Materials, History & Analytics

Keep printer and filament profiles, print outcomes, and costs alongside your
Models. Moonraker can supply measured duration and filament use. Optional
[Spoolman](https://github.com/Donkie/Spoolman) integration adds spool inventory
and consumption tracking, with checks against double-counting native Moonraker
usage. Statistics summarize print time, material, and cost; opt-in notifications
report print events through Discord, Telegram, ntfy, or webhooks.

### Backup, Security & Administration

Create and verify backups, audit file integrity, restore trashed Models, and
export a portable library. Built-in backups cover file-backed SQLite or
PostgreSQL, managed files, and thumbnails; **Library source originals need
separate backups**. Schedule an opt-in daily backup and copy it to local or
supported remote destinations. Physical cleanup is guarded by storage capabilities,
administrator approval, a verified independent backup, and quarantine.

Manage local accounts, scoped API keys, Collection and printer permissions, or
optional OIDC/SSO. See [disaster recovery](https://www.printstash.org/en/docs/guides/backup-and-restore/),
[storage safety](https://www.printstash.org/en/docs/guides/data-safety/), and [security](./SECURITY.md).

## Screenshots

<details>
<summary>Explore the library, G-code revisions, and printer workflow</summary>

| Model Library | G-code Revisions | Printer Management |
| --- | --- | --- |
| [![Library with Collections, filters, favorites, and model previews](screenshots/01-vault-overview.png)](screenshots/01-vault-overview.png) | [![G-code revisions with needs-test and known-good labels](screenshots/04-artifact-compare.png)](screenshots/04-artifact-compare.png) | [![Moonraker printer showing print progress, temperatures, and controls](screenshots/05-printer-live.png)](screenshots/05-printer-live.png) |
| Find a Model in your Collections. | Keep tested settings and compare revisions. | Follow prints on compatible printers. |

Select a screenshot to see it at full size. Screenshots use demonstration data;
printer actions depend on the [integration](#printer-compatibility).

### Workflow demo

![PrintStash library, Model details, revisions, and printer workflow](screenshots/00-demo-v010.gif)

This demo was recorded for an earlier version; some interface details have changed.

</details>

## Printer Compatibility

**Moonraker/Klipper is the primary integration.** Beta providers implement the
workflows below but still need broader physical-printer and firmware validation.
The app exposes capabilities and disables unsupported actions.

| Integration | Status | Capabilities and limits |
| --- | --- | --- |
| **Moonraker / Klipper** | Stable | Live monitoring, upload/start, pause/resume/cancel, remote files, matching history import, measured filament use |
| **Elegoo Neptune 4 / Pro / Plus / Max** | Via Moonraker | Moonraker integration with a dedicated setup preset |
| **Bambu LAN** | Beta | Local status, plain-text G-code upload and explicit start, pause/resume/cancel, external-job history and best-effort file capture; no remote file inventory |
| **PrusaLink** | Beta | Local status, plain-text G-code and validated `.bgcode` upload/start, remote files, pause/resume/cancel; no Prusa Connect cloud |
| **OctoPrint / OctoPi** | Beta | Local status, G-code upload/start, remote files, pause/resume/cancel |
| **Elegoo Centauri Carbon / Carbon 2** | Beta | Local status, G-code upload/start, pause/resume/cancel; no remote file inventory or history import |

See the [**printer guide**](https://www.printstash.org/en/docs/guides/printers/) for setup,
supported actions, diagnostics, and validation limits. Automated
protocol tests do not establish hardware certification. Bambu file recovery
depends on the printer's cache; Centauri Carbon 2 upload remains unconfirmed
on physical hardware.

## Documentation

Read the [**official PrintStash documentation**](https://www.printstash.org/en/docs/)
for installation, everyday workflows, and administration.

| Start here | Guides |
| --- | --- |
| **Installation & Deployment** | [Deployment](https://www.printstash.org/en/docs/getting-started/installation/) · [First run](https://www.printstash.org/en/docs/getting-started/first-run/) · [Upgrading](https://www.printstash.org/en/docs/guides/upgrading/) |
| **Library Sources** | [Folders and NAS](https://www.printstash.org/en/docs/guides/shared-volumes/) · [Browser capture](https://www.printstash.org/en/docs/guides/browser-capture/) · [Pending Imports](https://www.printstash.org/en/docs/guides/browser-capture/#review-pending-imports) |
| **Printer Compatibility** | [Provider support](https://www.printstash.org/en/docs/guides/printers/) · [OrcaSlicer](https://www.printstash.org/en/docs/guides/user-guide/#skip-the-manual-upload-the-orcaslicer-hook) · [Multipart builds](https://www.printstash.org/en/docs/guides/multipart-models/#create-and-track-a-build) |
| **Storage & Backups** | [Storage providers](https://www.printstash.org/en/docs/guides/storage-providers/) · [Data safety](https://www.printstash.org/en/docs/guides/data-safety/) · [Disaster recovery](https://www.printstash.org/en/docs/guides/backup-and-restore/) |
| **Security** | [Users and access](https://www.printstash.org/en/docs/guides/users-and-access/) · [HTTPS and proxies](https://www.printstash.org/en/docs/getting-started/installation/#reverse-proxy-with-tls) |
| **Development & Contributing** | [Developer guide](https://www.printstash.org/en/docs/contributing/development/) · [Architecture](https://www.printstash.org/en/docs/reference/architecture/) · [API reference](https://www.printstash.org/en/docs/reference/api/) |

## Project Status & Roadmap

PrintStash is **beta**. Library management and Moonraker/Klipper are its
best-established workflows. The supported deployment runs **one API process per
Vault**; library data, uploads, and printer actions need a live server, including
in the installed web app. English and Spanish localization is in progress, with
English fallbacks on some screens. See [known limitations](https://www.printstash.org/en/docs/reference/known-limitations/).

- **Published: [0.14.0](https://github.com/xiao-villamor/PrintStash/releases/tag/v0.14.0).**
  Includes durable background work, guided ZIP imports, browser first-run setup,
  and a single persistent data volume, alongside the core workflows above.
- **Preparing: [0.15.0](./CHANGELOG.md#0150).** This branch prepares changes
  integrated since 0.14.0: scrolling library pagination, subtree filters, editable
  Library sources, clearer import and preview progress, multi-material G-code
  fixes, and a unified image with optional GPU support. These changes are not yet
  a published release. GPU use needs device access and operation qualification;
  detection alone does not enable AI features. CPU processing remains available;
  see [compute support](https://www.printstash.org/en/docs/guides/previews-and-gpu/).
- **Next:** follow [Unreleased](./CHANGELOG.md#unreleased) and the
  [roadmap](https://www.printstash.org/en/roadmap/) for future work.

**Before upgrading to 0.15.0:** back up and stop all API and worker processes
before upgrading them together to DBOS 3.0. Source installs
require Python 3.14 or newer. Read the [upgrade and rollback guidance](https://www.printstash.org/en/docs/guides/upgrading/#version-0150).

## Contributing

Bug reports, hardware validation, documentation, translations, and focused PRs
are welcome. Start with [CONTRIBUTING.md](./CONTRIBUTING.md) and
the [development guide](https://www.printstash.org/en/docs/contributing/development/).
[Report a bug](https://github.com/xiao-villamor/PrintStash/issues) or discuss ideas
in [Discussions](https://github.com/xiao-villamor/PrintStash/discussions).

## Security

Report vulnerabilities as described in [SECURITY.md](./SECURITY.md).
Use PrintStash on trusted self-hosted networks. For public access, put it behind
a reverse proxy with TLS and your own access controls; the
[deployment guide](https://www.printstash.org/en/docs/getting-started/installation/#reverse-proxy-with-tls) explains how
to bind the frontend to `127.0.0.1`.

## License

PrintStash is licensed under the [GNU AGPL-3.0](./LICENSE).

## Trademark

PrintStash is the name and mark of this self-hosted, open-source project,
published at [xiao-villamor/PrintStash](https://github.com/xiao-villamor/PrintStash)
and [printstash.org](https://www.printstash.org). It is not affiliated with,
endorsed by, or connected to any separately hosted service using the same name.
Services reusing the name should clearly distinguish themselves from this project.
