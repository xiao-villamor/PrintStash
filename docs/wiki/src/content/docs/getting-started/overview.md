---
title: Overview
description: What PrintStash is, what it helps with, and what not to expect yet.
---

If you print often enough, file names stop being a reliable memory system.
Three months later you want the bracket that fit perfectly, but the folder has
`bracket_v3_final_REAL.gcode`, `bracket_fixed.gcode`, and no note saying which
one came off the bed straight.

PrintStash is a self-hosted app for keeping that context near the files: source
meshes, G-code revisions, slicer metadata, thumbnails, printer copies, and short
notes about what worked. It runs on your own hardware. There is no cloud account,
subscription, or telemetry.

## How it fits your workflow

You slice in OrcaSlicer, PrusaSlicer, Bambu Studio, or Cura. The G-code lands in
PrintStash either by manual upload or through the OrcaSlicer post-processing
hook. PrintStash reads the comments your slicer wrote into the file, pulls out
fields like layer height, infill, material, estimated time, and filament use, and
groups that G-code under a logical *model* alongside the source mesh.

Later you search by name, filter by collection or tag, and open the model. You
see every revision, which one you marked as the good one, what the toolpath looks
like layer by layer, and, if you connected a Moonraker printer, whether that file
is already on the printer.

## What it does today

- **Ingest:** STL, 3MF, OBJ, STEP/STP, and G-code via the web UI, the REST API,
  or the OrcaSlicer push hook, plus import from a URL or `.zip` (including
  Printables/MakerWorld/Thingiverse model pages). Files are deduplicated by
  content hash, so re-uploading the same mesh doesn't create clutter.
- **Organize:** hierarchical collections, flat tags, full-text search, sortable
  grid/list views, and drag-and-drop between collections.
- **Mirror folders & NAS:** point PrintStash at a server folder or NAS share and
  it indexes files in place (shared volumes) with two-way write-back, scheduled
  scans, and optional real-time watching.
- **Inspect:** an in-browser mesh viewer (solid / x-ray / wireframe) and a
  client-side G-code toolpath viewer that scrubs layer by layer.
- **Track revisions:** every model with G-code has exactly one *recommended*
  revision. Label the others `known_good`, `needs_test`, or `failed`, add notes,
  and compare two revisions' settings side by side.
- **Talk to printers:** Moonraker/Klipper with live status, send-to-print,
  remote file sync, and print-history import with measured filament and cost.
  Bambu LAN, PrusaLink, and Elegoo Centauri integrations are in beta.
- **Measure:** a Statistics dashboard turns completed prints into cost,
  filament, and print-time trends, and share a single model via an expiring,
  read-only public link.
- **Stay portable:** metadata export to JSON/CSV, full backup/restore, audit
  logs, and a health endpoint, all on SQLite by default, with Postgres and S3/R2
  optional.

## How far along it is

PrintStash is early open-source software. The current build is useful for a home
library and a Moonraker/Klipper workflow, and the backup, upgrade, and migration
paths are real. It is **not** a slicer, firmware replacement, or print-farm
scheduler. See
[Known limitations](/reference/known-limitations/) for the honest
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
| Printers  | Moonraker/Klipper (stable); Bambu, PrusaLink, Centauri (beta) |

New here? Go to [Installation](/getting-started/installation/). Want
the vocabulary first? [Core concepts](/concepts/core-concepts/)
explains how models, artifacts, and revisions relate.
