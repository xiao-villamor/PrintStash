# Manual Testing Checklist

Hands-on browser testing to run before tagging a release, on top of the
automated and smoke checks in [`release-validation.md`](./release-validation.md).
That doc covers clean install, SQLite upgrade, backend/frontend CI, and the
quick smoke list. This doc is the manual sweep of the UI workflows.

UI flows already exercised against a real backend by the Playwright suite in
`frontend/tests/e2e-real/` (`cd frontend && pnpm test:e2e:real`) are **not**
repeated here — this checklist is only the things that suite does not cover:
viewers, thumbnails, real printers, Spoolman, imports, statistics, shared
volumes, multi-role RBAC, and anything needing real hardware or files.

Run against a fresh `docker compose up -d --build` install (or a copy of a real
library for the upgrade path). Log in as the admin created at setup. Tick a box
only after you have seen the result yourself in the browser.

## 0. Setup & Auth

- [ ] `/setup` creates the first admin (storage backend, data dirs, backup
      retention, optional backup S3/R2 fields).
- [ ] Logged-out: write actions show a sign-in requirement, not a silent fail.

## 1. Vault / Library

- [ ] `/` loads in grid and list modes; thumbnails render.
- [ ] Printer filter and printer-presence filter change the result set.
- [ ] Drag a model into a collection and move a collection subtree.
- [ ] Upload mesh+G-code together, and pick an existing tag during upload.
- [ ] Upload a `.zip`; pick a subset of files on extraction; confirm each 3D
      file becomes its own model under an auto-created collection.
- [ ] URL import of a single file and of a model page (Printables/MakerWorld/
      Thingiverse) — source URL is kept, asset is fetched.
- [ ] Task center shows queued → running → completed (and a failed case).

## 2. Model Detail

- [ ] `/models/{id}` opens; viewer shows the mesh.
- [ ] 3D viewer: solid, X-ray, wireframe, fit-to-view, grid, zoom, reset,
      screenshot.
- [ ] G-code toolpath mode: layer slider, travel toggle, bed overlay.
- [ ] 3MF/OBJ/STEP open via the cached STL preview path.
- [ ] Edit a model's collection and description; the change persists.
- [ ] Files tab lists source meshes separately from G-code.
- [ ] History tab: import Moonraker history for a matching file without
      duplicating jobs.
- [ ] The slicer-open action on a revision.

## 3. Printers

- [ ] Add a Moonraker/Klipper printer (mock or real); status reaches online.
- [ ] `/printers/{id}`: live/reconnecting WebSocket state, snapshot metrics.
- [ ] Status tab: current file, progress, temps; pause/resume/cancel (if safe).
- [ ] Files tab: sync remote inventory, vault-match links, start a remote file.
- [ ] Jobs tab: print-job history populates.
- [ ] Diagnostics tab: support level, capability/connectivity checks, rerun.
- [ ] Send a vault G-code to a printer from model detail (Klipper sync panel).
- [ ] (If available) Add a Bambu LAN printer; verify status, upload-only send,
      explicit Send & Print on a supervised disposable job, and
      pause/resume/cancel only where safe.
- [ ] (If available) Add a PrusaLink FDM printer with Digest auth; verify
      status/temperatures, file sync/delete, upload-only, supervised explicit
      start, pause/resume/cancel, restart, and reconnect behavior.
- [ ] (If available) repeat PrusaLink connection with legacy API-key auth.
- [ ] (If available) add a Neptune 4-family printer with the Elegoo preset;
      verify it persists as `elegoo_neptune4` and passes the Moonraker checks.
- [ ] (If available) add an original Centauri Carbon; verify SDCP status,
      temperatures, supervised start-existing-file, pause/resume/cancel, and
      reconnect while paused using its saved mainboard ID.
- [ ] (If available) enable LAN Only on a Centauri Carbon 2, add its IP/access
      code, then verify MQTT status and supervised controls. Confirm upload and
      file inventory remain unavailable in UI and API capabilities.

## 4. Statistics (admin)

- [ ] `/statistics` loads with completed-print data.
- [ ] Period selector (7d/30d/90d/1y/all) drives the whole page.
- [ ] Metric cards, time series (area/line/bar switch), top collections, top
      filaments all render; empty state shows for an empty period.
- [ ] Cost figures use the currency set in Settings → Design.

## 5. Profiles

- [ ] Usage counts reflect matching files.
- [ ] Upload G-code and confirm filament/printer presets auto-detect; a preset
      the user has edited is not overwritten or renamed by detection.

## 6. Settings

- [ ] Storage: restore from a backup archive recovers the DB and files.
- [ ] Shared volumes: add a volume, run a manual scan, confirm files index in
      place; upload a revision and confirm write-back adds (never overwrites).
- [ ] Notifications: send a Test to a channel and confirm real delivery (the
      add/delete-channel flow itself is automated).

## 7. Spoolman (if enabled)

- [ ] Settings → Spoolman: set base URL/API key, Test connection passes,
      `/health` spoolman component reports reachable.
- [ ] Sync filaments; linked presets are read-only (edit/delete rejected).
- [ ] Select a spool on send-to-printer / manual log; it persists in history.
- [ ] After a Moonraker-measured completion, the spool decrements once.
- [ ] Native-hook detection warning shows and write-back defaults off when the
      Moonraker active-spool hook is present.

## 8. Public Share Links

- [ ] Genuine time-based expiry: a link past its `expires_at` shows the
      invalid/expired public page after the clock actually elapses. (The
      same inactive-link 404 path is automated via revocation; only the
      elapsed-time component is manual, since the backend floors expiry at 1 day.)

## 9. Cross-cutting

- [ ] Theme-aware favicon swaps with the theme.
- [ ] Mobile width: filter drawer and nav usable.
- [ ] No console errors during the above flows.
