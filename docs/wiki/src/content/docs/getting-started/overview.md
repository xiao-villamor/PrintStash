---
title: Overview
description: What PrintStash is, who it's for, and the project's current status.
---

PrintStash is a self-hosted asset manager for 3D printing. Upload manually or
let OrcaSlicer push new G-code after every slice, then find files by model name,
collection, tags, slicer metadata, material, printer, and print history.

## What you get

**Ingest and organize**

- Upload STL, 3MF, OBJ, and G-code files
- Push exported G-code from OrcaSlicer with a post-processing hook
- Deduplicate by content hash and keep model version history
- Organize with collections, tags, search, filters, and thumbnails

**Inspect every model**

- View source files, recommended G-code, slicer settings, and mesh metadata
- Preview meshes and G-code toolpaths in the browser
- Track revision status, notes, labels, and recommended versions
- Log print history manually or import matching Moonraker history

**Talk to printers**

- Manage Moonraker/Klipper printers with live status and send-to-print
- Sync printer file inventories back to vault models
- Run provider diagnostics for capabilities and connectivity
- Use beta Bambu LAN status and controls

**Run locally**

- First-run setup wizard, JWT auth, API keys, trash restore/purge
- JSON/CSV metadata export, backup/restore, health checks, audit logs
- Optional Postgres and S3/R2-compatible storage

## Project status

PrintStash is an early open-source, self-hosted project. The current release is
usable for local libraries and Moonraker/Klipper workflows, with Docker Compose
as the primary install path. SQLite plus local disk is the default; Postgres and
S3/R2-compatible storage are optional.

Hardware reports, parser fixtures, install notes, docs fixes, and UX feedback
are welcome in
[Discussions](https://github.com/xiao-villamor/PrintStash/discussions) or issues.

## Stack

- **Backend:** Python 3.11+, FastAPI, SQLModel, Alembic
- **Frontend:** React 19, React Router 7, TanStack Query, Vite, Tailwind
- **Storage:** SQLite (default) or Postgres; local disk (default) or S3/R2
- **Printers:** Moonraker/Klipper (stable), Bambu LAN (beta)
