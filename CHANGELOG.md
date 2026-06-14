# Changelog

## 0.5.0 - Import, CAD, Sharing & Measured Prints

A big release closing the ingest/discovery gap and deepening the
print-tracking loop that sets PrintStash apart.

### Highlights

- **Import from URL or ZIP.** Paste a direct file/`.zip` URL or upload a `.zip`
  and pick which files to import. Each 3D file becomes its own model, grouped
  under an auto-created collection named after the archive. Server-side fetches
  are SSRF-guarded (no private/loopback/metadata hosts) and archives are
  zip-slip / zip-bomb protected.
- **STEP / STP CAD files.** Ingested, tessellated (via `cascadio`/OpenCASCADE),
  previewed in the browser, and thumbnailed like any mesh.
- **Public share links.** Create expiring, read-only links to a single model.
  View-only by default (the viewer renders a tessellated mesh; original-file
  download is opt-in per link). Strictly isolated: an unauthenticated, GET-only
  router; tokens stored hashed; uniform 404 on bad/expired/revoked tokens; file
  access re-scoped to the shared model; per-IP rate limiting.
- **Measured filament + duration.** When a print finishes, real filament used
  and actual duration are captured from Moonraker and shown in print history,
  with real per-print cost (Bambu leaves filament null — no live data).
- **Auto known-good revisions.** A revision is promoted to *known-good* after its
  first successful print (never overriding a manual failed/archived verdict).
  Toggle in Settings → Design.
- **Delete G-code revisions.** The Revisions tab gains a delete action per
  revision. Deletes are soft — the file follows the trash lifecycle and its blob
  is reclaimed by the GC — consistent with model deletion.
- **Print history without a registered printer.** Manual print records can now be
  logged against a free-text printer name ("Other (not listed)…") instead of
  requiring a preconfigured Moonraker/Bambu printer. `print_jobs.printer_id` is
  now nullable, backed by a new `printer_name` column.

## 0.4.0 - Vite + React Router Frontend

The frontend is rebuilt as a Vite single-page app on React Router, migrated off
Next.js. The app was already ~95% client-rendered behind auth, so server
rendering added complexity without benefit — and under multi-user RBAC it broke
outright, because server-side renders had no access to the browser-held token.

### Highlights

- Frontend migrated from Next.js to Vite + React Router (client SPA). All reads
  now fetch client-side with the stored token.
- Fixes the model detail page server error and the broken 3D preview / file
  downloads that appeared once assets required authentication.
- TanStack Query is the cache layer: shared `collections`/`tags` cache across the
  grid, detail, and upload views; revalidation on window focus (so another
  user's edits surface); and automatic refetch after any mutation.
- Production image now serves the static build via nginx, which proxies
  `/api/v1` and WebSockets to the API same-origin (replacing the Next rewrites).
- Tooling moved to Vite build, `tsc` typecheck, and a standard ESLint flat
  config.

## 0.3.0 - Multi-User & RBAC

PrintStash gains real multi-user support with per-collection access control.

### Highlights

- Collection-level role-based access control: each user can be granted `view`,
  `edit`, or `admin` on a collection, and the UI gates actions accordingly.
- Admin user management and access controls.
- Authenticated asset delivery: thumbnails, mesh/STL previews, and file
  downloads now require a signed-in user and carry the access token.
- Lossless WebP thumbnails.
- Settings UI refinements and collections sidebar (outliner) fixes.

### Upgrade Notes

Read [UPGRADE.md](./UPGRADE.md) before upgrading an existing install.

## 0.1.0 - Initial Self-Hosted Release

PrintStash 0.1 is the first tagged self-hosted release. It is focused on the
local-first library workflow: ingest STL/3MF/G-code, extract slicer metadata,
keep model revisions searchable, and integrate with Moonraker/Klipper printers.

### Highlights

- Docker Compose is the primary install path, with SQLite and local disk as the
  default storage stack.
- Alembic migrations provide a repeatable schema upgrade path.
- OrcaSlicer post-processing ingestion remains dependency-free and exits `0` on
  vault outages.
- G-code revisions support labels, outcome status, notes, recommended versions,
  and metadata comparison.
- Model detail includes split overview/files/revisions/history/settings views,
  mesh preview, and a client-side G-code toolpath viewer.
- Settings includes vault stats, storage usage, metadata/card display
  preferences, API-key management, backup creation, and trash restore/purge.
- Model print history supports manual entries and Moonraker history import for
  matching G-code filenames.
- Moonraker/Klipper is the primary supported printer provider.
- Bambu LAN is available as beta status/control support only. Upload, send, start,
  and file inventory parity are intentionally not part of 0.1.
- Postgres, S3/R2 storage, cloud backups, and audit logs are optional adapters for
  larger self-hosted installs.

### Validation

- Backend unit/API suite covers ingestion, auth, migrations, parser fixtures,
  thumbnails, storage/file serving, printer providers, print jobs, and API
  hardening.
- Frontend CI runs typecheck, lint, and production build.
- Additional parser fixtures cover OrcaSlicer, PrusaSlicer, Bambu Studio, Cura,
  and a common Klipper/Orca profile.

### Upgrade Notes

Read [UPGRADE.md](./UPGRADE.md) before upgrading an existing install.
