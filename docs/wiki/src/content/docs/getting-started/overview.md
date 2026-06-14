---
title: Overview
description: What PrintStash is, why it exists, and how far along it is.
---

If you 3D print regularly, you end up with a folder of cryptic filenames, a
slicer that remembers settings you don't, and a printer that only knows what's
running right now. Three months later you want to reprint that bracket that fit
*perfectly* — and you have `bracket_v3_final_REAL.gcode`, `bracket_fixed.gcode`,
and no idea which one came off the bed straight.

PrintStash is a self-hosted app that keeps the whole picture in one place: the
mesh, every G-code revision, the slicer settings that produced them, a
thumbnail, which printer it lives on, and what happened the last time you ran it.
It runs on your own hardware. No cloud account, no subscription, no telemetry.

## How it fits your workflow

You slice in OrcaSlicer (or PrusaSlicer, Bambu Studio, Cura — the parser handles
all of them). The G-code lands in PrintStash, either because you uploaded it or
because a post-processing hook pushed it automatically on export. PrintStash
reads the slicer comments, pulls out layer height, infill, material, estimated
time, filament weight, and groups the file under a logical *model* alongside the
source mesh.

Later you search by name, filter by collection or tag, and open the model. You
see every revision, which one you marked as the good one, what the toolpath looks
like layer by layer, and — if you've connected a Moonraker printer — whether the
file is already sitting on the printer ready to go.

## What it does today

- **Ingest:** STL, 3MF, OBJ, and G-code via the web UI, the REST API, or the
  OrcaSlicer push hook. Files are deduplicated by content hash, so re-uploading
  the same mesh doesn't create clutter.
- **Organize:** hierarchical collections, flat tags, full-text search, sortable
  grid/list views, and drag-and-drop between collections.
- **Inspect:** an in-browser mesh viewer (solid / x-ray / wireframe) and a
  client-side G-code toolpath viewer you can scrub layer by layer.
- **Track revisions:** every model with G-code has exactly one *recommended*
  revision. Label the others `known_good`, `needs_test`, or `failed`, add notes,
  and compare two revisions' settings side by side.
- **Talk to printers:** Moonraker/Klipper with live status, send-to-print,
  remote file sync, and print-history import. Bambu LAN status and controls are
  in beta.
- **Stay portable:** metadata export to JSON/CSV, full backup/restore, audit
  logs, and a health endpoint — all on SQLite by default, Postgres and S3/R2
  optional.

## How far along it is

PrintStash is an early, honestly-scoped open-source project. The current release
is genuinely useful for a home library and a Moonraker/Klipper workflow, and the
backup, upgrade, and migration paths are real. It is **not** a slicer, a firmware
replacement, or a print-farm scheduler, and it doesn't pretend to be. See
[Known limitations](/PrintStash/reference/known-limitations/) for the honest
list of rough edges and explicit non-goals.

The thing that helps most right now is real-world feedback: which slicer profiles
parse cleanly, which printers connect, where the setup wizard trips you up.
That belongs in
[Discussions](https://github.com/xiao-villamor/PrintStash/discussions) or an
issue.

## The stack, briefly

| Layer     | Choice                                                     |
| --------- | ---------------------------------------------------------- |
| Backend   | Python 3.11+, FastAPI, SQLModel, Alembic                  |
| Frontend  | React 19, React Router 7, TanStack Query, Vite, Tailwind  |
| Database  | SQLite by default; Postgres optional                       |
| Storage   | Local disk by default; S3/R2-compatible optional           |
| Printers  | Moonraker/Klipper (stable), Bambu LAN (beta)               |

New here? Go to [Installation](/PrintStash/getting-started/installation/). Want
the vocabulary first? [Core concepts](/PrintStash/concepts/core-concepts/)
explains how models, artifacts, and revisions relate.
