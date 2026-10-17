<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="frontend/public/logo-dark.svg" />
  <img src="frontend/public/logo.svg" alt="PrintStash logo" width="320" />
</picture>

# PrintStash

Self-hosted library for 3D models, G-code revisions, and print history.<br />
Keep each model's files, slicer settings, and print results together.

[![Release](https://img.shields.io/github/v/release/xiao-villamor/PrintStash?style=flat-square&color=22c55e)](https://github.com/xiao-villamor/PrintStash/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/xiao-villamor/PrintStash/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/xiao-villamor/PrintStash/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-2496ED?logo=docker&logoColor=white&style=flat-square)](https://github.com/xiao-villamor/PrintStash/pkgs/container/printstash)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](./LICENSE)
[![Status: beta](https://img.shields.io/badge/status-beta-f59e0b?style=flat-square)](#project-status--roadmap)

[**Get Started**](#quick-start) · [**Documentation**](https://www.printstash.org/en/docs/) · [**Releases**](https://github.com/xiao-villamor/PrintStash/releases) · [**Contributing**](#contributing)

<a href="screenshots/02-model-detail.png">
  <img src="screenshots/02-model-detail.png" alt="A Model's 3D preview beside its recommended G-code, slicer settings, and printer actions" width="960" />
</a>

</div>

## Quick Start

You need **Docker with the Compose plugin**, an AMD64 or ARM64 host, and space
for your files. Start with **1 GB RAM**; 2 GB or more helps with large meshes.
The default is one container, SQLite, and a persistent volume at `/data`.

> [!WARNING]
> Use a trusted network and restrict access during setup: **the first registered
> account becomes the administrator**. For remote access, configure [TLS and access controls](https://www.printstash.org/en/docs/getting-started/installation/#reverse-proxy-with-tls).

```bash
mkdir -p printstash && cd printstash
curl -fsSL https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/docker-compose.yml -o docker-compose.yml
printf 'PRINTSTASH_VERSION=0.15.0\n' > .env
docker compose up -d
```

Open **[http://localhost:3000](http://localhost:3000)** (or your server's IP),
create the administrator account, and upload a model. There are no default
credentials. To index a folder or NAS, [mount it inside the container](https://www.printstash.org/en/docs/guides/shared-volumes/#mount-a-folder-into-docker)
and add it under **Settings → Library sources**.

The `.env` file pins the image to **0.15.0**. Without a pin, `latest` can include
unreleased changes. See [installation options](https://www.printstash.org/en/docs/getting-started/installation/)
for ports, PostgreSQL, remote storage, and SSO. Existing installations should
read the [upgrade notes](#project-status--roadmap) first.

## Features

- **Library:** Collections, tags, filters, favorites, saved views, and multipart
  models. Optional [AI search](https://www.printstash.org/en/docs/guides/ai-search/)
  supports local models and compatible inference servers.
- **Imports:** Upload files and ZIPs, index folders or NAS in place, and review
  [browser captures](https://www.printstash.org/en/docs/guides/browser-capture/).
  Remote sources are read-only; mounted folders can accept new files without
  overwriting originals.
- **Previews and revisions:** View meshes and G-code layers, compare slicer
  settings, mark tested revisions, and open files in your slicer.
- **Printers and queues:** Monitor temperatures, send G-code, control prints,
  and queue copies across printer groups. Actions depend on the integration.
- **Materials and history:** Filament profiles, print outcomes, time and cost
  tracking, plus optional [Spoolman](https://github.com/Donkie/Spoolman) integration.
- **Backups and access:** Verified backups, file audits, trash recovery, library
  export, user permissions, API keys, and OIDC/SSO.
  **Library source originals need separate backups.**

**PrintStash does not slice models or verify that G-code is safe for your printer.**
STL, 3MF, OBJ, STEP/STP, DXF, G-code, and `.bgcode` are accepted; DXF has no preview,
and BGCODE conversion has [limits](https://www.printstash.org/en/docs/reference/known-limitations/#files-and-previews).
Print queues use plain-text G-code. Material or nozzle mismatches block
automatic routing; unknown material state does not.

## Screenshots

<details>
<summary>Library, G-code revisions, and printers</summary>

| Model Library | G-code Revisions | Printer Management |
| --- | --- | --- |
| [![Library with Collections, filters, favorites, and model previews](screenshots/01-vault-overview.png)](screenshots/01-vault-overview.png) | [![G-code revisions with needs-test and known-good labels](screenshots/04-artifact-compare.png)](screenshots/04-artifact-compare.png) | [![Moonraker printer showing print progress, temperatures, and controls](screenshots/05-printer-live.png)](screenshots/05-printer-live.png) |

### Demo

![PrintStash library, Model details, revisions, and printer workflow](screenshots/00-demo-v010.gif)

Screenshots use demonstration data. The GIF shows an earlier version of the interface.

</details>

## Printer Compatibility

**Moonraker/Klipper is the primary integration.** All providers below support
status, upload/start, and pause/resume/cancel. Uploads use plain-text G-code
unless noted.

| Integration | Status | Capabilities and limits |
| --- | --- | --- |
| **Moonraker / Klipper** | Stable | Remote files, matching print history, measured filament use |
| **Elegoo Neptune 4 / Pro / Plus / Max** | Via Moonraker | Dedicated setup preset |
| **Bambu LAN** | Beta | External-job history; file recovery depends on the printer cache. No remote file listing |
| **PrusaLink** | Beta | Local FDM; remote files and validated `.bgcode` upload. No Prusa Connect |
| **OctoPrint / OctoPi** | Beta | Remote files |
| **Elegoo Centauri Carbon / Carbon 2** | Beta | No file listing or history import. Carbon 2 upload unconfirmed on hardware |

Hardware validation is limited, especially for beta integrations; automated tests
are not printer certification. Check the [printer guide](https://www.printstash.org/en/docs/guides/printers/)
for setup, firmware limits, and unsupported actions.

## Documentation

| Topic | Guides on printstash.org |
| --- | --- |
| Installation | [Setup](https://www.printstash.org/en/docs/getting-started/installation/) · [First run](https://www.printstash.org/en/docs/getting-started/first-run/) · [Upgrading](https://www.printstash.org/en/docs/guides/upgrading/) |
| Library | [Folders and NAS](https://www.printstash.org/en/docs/guides/shared-volumes/) · [Multipart models](https://www.printstash.org/en/docs/guides/multipart-models/) |
| Printing | [Printers](https://www.printstash.org/en/docs/guides/printers/) · [OrcaSlicer](https://www.printstash.org/en/docs/guides/user-guide/#skip-the-manual-upload-the-orcaslicer-hook) |
| Storage | [Provider support](https://www.printstash.org/en/docs/guides/storage-providers/) · [Backups](https://www.printstash.org/en/docs/guides/backup-and-restore/) · [Data safety](https://www.printstash.org/en/docs/guides/data-safety/) |
| Access | [Users and permissions](https://www.printstash.org/en/docs/guides/users-and-access/) |
| Development | [Developer guide](https://www.printstash.org/en/docs/contributing/development/) · [API](https://www.printstash.org/en/docs/reference/api/) |

## Project Status & Roadmap

**Beta · Published: [0.15.0](./CHANGELOG.md#0150).** This release adds automatic
loading as you scroll, filters across subfolders, editable Library sources, and a unified
image with optional [GPU support](https://www.printstash.org/en/docs/guides/previews-and-gpu/).
GPU acceleration depends on the hardware and operation; CPU processing remains available.

Run **one API server process per library**. The web app needs a running server.
See [known limitations](https://www.printstash.org/en/docs/reference/known-limitations/)
and the [roadmap](https://www.printstash.org/en/roadmap/).

**Upgrading to 0.15.0:** back up, stop all API and worker processes, then upgrade
them together to DBOS 3.0. Source installs require Python 3.14 or newer.
Read the [upgrade and rollback instructions](https://www.printstash.org/en/docs/guides/upgrading/#version-0150),
including volume migration when coming from 0.13.x or earlier.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development and contribution guidelines.
[Report bugs](https://github.com/xiao-villamor/PrintStash/issues),
[discuss ideas](https://github.com/xiao-villamor/PrintStash/discussions), or help
with translations, documentation, and printer testing.

## Security

Report vulnerabilities through [SECURITY.md](./SECURITY.md).

## License

[GNU AGPL-3.0](./LICENSE).

## Trademark

PrintStash is the name and mark of this self-hosted, open-source project,
published at [xiao-villamor/PrintStash](https://github.com/xiao-villamor/PrintStash)
and [printstash.org](https://www.printstash.org). It is not affiliated with,
endorsed by, or connected to any separately hosted service using the same name.
Services reusing the name should clearly distinguish themselves from this project.
