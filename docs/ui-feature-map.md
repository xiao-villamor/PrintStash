# UI Feature Map

Validated on 2026-06-12 with the frontend Playwright suite and mock API. This
map tracks user-visible routes and controls so docs, release notes, and future
implementation work can stay aligned with the actual app.

## App Shell

- Shared authenticated layout with vault, organize, profiles, printers, and
  settings navigation.
- Top bar search, profile menu, theme-aware favicon, and task notifications for
  background upload/ingestion jobs.
- Auth-aware write controls show sign-in requirements instead of failing
  silently.

## First Run And Login

- `/setup` creates the first admin account and configures storage backend
  (`local` or S3-compatible), data directories, backup retention, and optional
  backup S3/R2 credentials.
- `/login` signs users in with username/password and hydrates client auth for
  protected UI actions and API requests.

## Vault

- `/` renders the model library in grid or list mode.
- Library controls include search, collection breadcrumbs, tag filters, printer
  filters, printer-presence filters, sorting, refresh, mobile filter drawer, and
  empty/loading states.
- Collection actions include create, move model by drag/drop, move collection
  subtree, delete empty collection, and recursive collection deletion when
  needed.
- Upload modal accepts STL/3MF/OBJ and G-code. It supports mesh-only,
  G-code-only, or mesh+G-code uploads, model name override, collection
  assignment, existing tags, and inline tag creation.
- Upload work creates task-center notifications and tracks queued, running,
  completed, and failed ingestion states.

## Model Detail

- `/models/{id}` has a full-screen viewer plus right-side workflow panel.
- Viewer modes include 3D model and G-code toolpath. 3D mode supports solid,
  X-ray, wireframe, fit-to-view, screenshot, build-plate grid, zoom, and reset.
- Header actions include edit/save/cancel model metadata and delete-to-trash.
- Overview tab shows the recommended print card, printer/material/layer/time/
  filament/slicer summary, quick send/compare/add-revision actions, and quick
  revision outcome actions.
- Overview edit mode supports collection picker, description, tag search, tag
  removal, and inline tag creation.
- Settings tab shows selected slicer, material, filament, temperature, geometry,
  and print-setting metadata using user-configurable visibility preferences.
- Revisions tab lists every G-code revision with labels, status, recommended
  marker, printer-presence badges, download, slicer-open action, and inline
  editing for label/status/notes/recommended.
- Revision compare lets users choose two G-code revisions and compare key
  slicer/material/print metadata side by side.
- Files tab lists source mesh files separately from G-code revisions.
- History tab shows model-level print history, manual job logging, and
  Moonraker history import for matching printer/file combinations.
- Klipper sync panel sends selected G-code to selected printers, reports online
  printer count, and shows uploaded remote filenames.

## Printers

- `/printers` lists configured printers with provider, support level, status,
  address, last seen/error, refresh, add printer, remove printer, and open detail
  actions.
- Add-printer modal supports Moonraker/Klipper and Bambu LAN fields, including
  provider-specific credentials and capability constraints.
- `/printers/{id}` shows live/reconnecting WebSocket state, provider badge, beta
  badge, status, address, support notes, and current snapshot metrics.
- Printer detail Status tab shows current file, state, progress, elapsed/total
  time, hotend/bed temperatures, and pause/resume/cancel controls.
- Files tab shows remote file inventory, sync action, vault-match links,
  missing/match state, remote-file start action, and unsupported-provider empty
  state.
- Jobs tab shows print-job history with file, state, progress, started time, and
  finished time.
- Diagnostics tab shows provider support level, capability checks, unsupported
  actions, connectivity checks, notes, and rerun diagnostics action.

## Profiles

- `/profiles` manages filament presets and printer presets.
- Filament presets expose name, material type, brand, cost/kg, notes, usage
  count, create/update/delete actions, and detected-profile preservation rules.
- Printer presets expose slicer preset name, printer model, nozzle diameter,
  notes, usage count, create/update/delete actions, and detected-profile
  preservation rules.

## Settings

- `/settings` sections: Overview, Access, Storage, Appearance, Trash, About.
- Overview shows vault stats, file/source/G-code counts, storage backend usage,
  configured printers, DB/indexed size, service health, app version, and
  metadata-only JSON/CSV export.
- Access manages named API keys, one-time API key display/copy, active key list,
  revocation, and username+API-key login guidance.
- Storage exposes runtime storage configuration and manual full backup.
- Appearance controls model-card metric slots and model-detail metadata
  visibility preferences, with reset actions.
- Trash controls retention days, purge-expired action, deleted-model list,
  restore, and permanent delete confirmation.
- About shows app identity, current version, GitHub link, star badge, and
  version history from the bundled changelog.
