# CONTEXT.md — PrintStash

Domain vocabulary for this project. Use these terms when naming modules, writing
docs, or discussing architecture. Add new terms here when a concept crystallises
during architecture reviews.

## Core domain

- **Model** — a logical 3D printing asset (e.g. "Bracket v2"), deduplicated by
  source mesh sha256 hash. One Model has many File rows (versions).
- **File** — a physical artifact stored on disk: a single STL, 3MF, or G-code
  blob. Versioned under its parent Model.
- **Metadata** — slicer-derived facts extracted from a File (filament usage,
  estimated print time, nozzle diameter, etc.). 1:1 with File.
- **Category** — a hierarchical taxonomy node (e.g. "Functional/Brackets").
  Self-referential via `parent_id`. Model references Category via `category_id` FK.
- **Tag** — a flat, non-hierarchical label. Many-to-many with Model via
  `ModelTagLink`.
- **Ingestion** — the end-to-end pipeline that accepts a raw file (STL/3MF/G-code),
  hashes it, deduplicates against existing Models, versions it, extracts metadata
  and thumbnail, resolves taxonomy, and persists everything. Runs as a background task.
- **G-code parser** — extracts slicer metadata from G-code file headers and footers.
  Pure Python.
- **Thumbnail** — a PNG preview extracted from G-code (base64-embedded) or rendered
  from a 3D mesh (software rasteriser).
- **Dedup** — the process of matching an incoming file's hash against existing Models
  to avoid creating duplicates. The `Model.hash` column is the dedup key.
- **Slug** — a URL-safe, kebab-case identifier derived from the Model name. Must be
  unique; appends `-2`, `-3` etc. on collision.

## Printer hub (Stage 3)

- **Printer** — a registered 3D printer with a Moonraker endpoint URL and API key.
- **PrinterHub** — a singleton background worker that maintains one persistent
  WebSocket subscription per Printer, fanning out status updates to vault WebSocket
  clients and writing state to the DB.
- **Moonraker** — the HTTP + WebSocket API exposed by Klipper firmware. The
  `MoonrakerClient` wraps HTTP calls (upload, start, pause, resume, cancel) and
  WebSocket subscriptions.
- **PrintJob** — a record of a file sent to a Printer, tracking state (queued →
  uploading → started → printing → completed/cancelled/failed) and progress.
- **Dashboard** — an aggregated view of all Printers' status, progress, and
  recent jobs. Computed by querying Printer + PrintJob rows, not from live
  WebSocket state.
- **External print job** — a job initiated outside the vault (e.g. via
  Mainsail/Fluidd directly). Captured by PrinterHub using sentinel Model/File
  rows as placeholders.

## Auth

- **API key** — a shared secret (`X-API-Key` header) used by the OrcaSlicer
  post-processing hook and other scripts. Stage 1 auth; coexists with JWT in
  Stage 3+.
- **JWT** — JSON Web Token issued at login, used by the web frontend. Stored in
  localStorage. The `auth-store` module (frontend) is the single writer to
  localStorage for tokens and user data.

## Configuration

- **Settings** — the frozen, env-only Pydantic model (`VAULT_*` env vars). Never
  mutated after import. Safe to import anywhere.
- **RuntimeOverlay** — DB-backed mutable configuration overrides (from the
  `system_config` table). Persisted by the setup wizard and Settings UI.
- **ConfigResolver** — the single read-path for effective configuration:
  `overlay[key] ?? frozen[key]`. Uses attribute access so callers write
  `get_config().data_dir` — same feel as the old `settings` object.

## Infrastructure

- **SessionFactory** — a Protocol (`session()`, `async_session()`, `scoped_session()`)
  stored in a `contextvars.ContextVar`. Every background task and request handler
  obtains database sessions through this single seam. Replaces the previous three
  ad-hoc injection mechanisms (FastAPI deps, module-level engine import, hardcoded
  `Session(engine)`). See ADR-0001.
- **StorageBackend** — ABC with two adapters: `LocalStorageBackend` (filesystem)
  and `S3StorageBackend` (object storage). The `get_backend()` function returns
  the active adapter based on `Settings.storage_backend`.
- **JobRegistry** — in-memory dict tracking the state of background ingestion jobs.
  Designed to be swappable for Redis in Stage 4.

## Frontend

- **auth-store** — the single module that owns all localStorage reads and writes
  for auth tokens and user data. The React `AuthContext` is a thin consumer.
- **API client** — domain-split modules under `lib/api/` (e.g. `models.ts`,
  `printers.ts`), each exporting functions for one backend domain.
- **Types** — domain-split modules under `types/` matching the API client split.
