# Changelog

## 0.8.1

### Added

- **Live printer controls (Moonraker).** The printer Status tab now lets you set
  the hotend and bed target temperature, apply one-tap PLA/PETG/ABS preheat
  presets or Cooldown, Home all axes, and trigger an Emergency stop. Controls are
  gated behind a new `can_send_gcode` capability, so they're shown only for
  providers that accept G-code (hidden for Bambu LAN).

### Fixed

- **Mobile scrolling.** Pages use the dynamic viewport height (`dvh`) so they
  scroll fully under the mobile browser chrome; the model detail page scrolls
  when its right-hand panel overflows; and the vault, model, and document pages
  keep their last content clear of the fixed bottom navigation bar instead of
  hiding it behind the nav.
- **Thumbnails.** Brighter, higher-contrast mesh lighting on the dark theme.

## 0.8.0b1

### Fix

- Minor fix after the merge of 0.8.0 with 0.7.1b1 (@JrVolt)

## 0.8.0

### Added

- **Spoolman integration (optional, OFF by default).** Connect a self-hosted
  [Spoolman](https://github.com/Donkie/Spoolman) instance under
  Settings → Spoolman with a base URL and optional API key, behind a master
  switch. A Test connection action and a live status badge confirm reachability,
  and the connection is reported in `/health` (informational — a Spoolman outage
  never marks the service degraded or blocks a print).
- **Spool inventory display.** The Spoolman card lists spools with their
  remaining weight, pulled live from Spoolman (the source of truth).
- **Filament preset sync (Spoolman → PrintStash, one-way).** A "Sync from
  Spoolman" action on the Profiles page imports Spoolman filaments as
  `FilamentProfile` presets keyed by `spoolman_filament_id` (deriving `$/kg` from
  `price / weight`, plus material, brand, density, and diameter). Synced presets
  are **read-only** in PrintStash (edit them in Spoolman); the first sync adopts
  matching local presets by name+material instead of duplicating, and filaments
  removed in Spoolman are unlinked (reverted to editable local), never deleted.
  Sync also runs automatically when the integration is enabled. This keeps
  filament data in one source of truth instead of drifting across two apps.
- **Exact per-print cost & weight from the selected spool.** When a print used a
  synced spool, its cost comes from that filament's real Spoolman price and its
  grams use the spool's real density/diameter (`mm_to_grams` override) instead of
  the static per-material table — replacing fuzzy metadata matching for those
  prints.
- **Per-print spool selection.** Choose which spool a print consumes when
  sending a job to a printer (`send-to-printer`) or logging a print manually.
  The spool is persisted on the print record (`PrintJob.spool_id`/`spool_name`)
  and shown in the model's print history.
- **Consumption write-back.** On a Moonraker-measured print completion,
  PrintStash decrements the selected Spoolman spool by the measured
  `filament_used_g` (server-side via Spoolman's `/spool/{id}/use`), reusing the
  existing `print_results` + `mm_to_grams` pipeline. Once per job, after the job
  is committed, so it never blocks the print path. Bambu reports no live
  consumption, so its prints are skipped.
- **Double-count safety.** Before writing consumption back, PrintStash checks
  Spoolman's active spool at completion time; if Moonraker's own Spoolman
  integration is already decrementing it, PrintStash skips its own write so a
  print is never counted twice — even if you never opened the settings card.
  The UI also warns when this is detected. An operator who has disabled
  Moonraker's hook can set a "write back anyway" override to make PrintStash
  own the consumption.
- **Collection documents & READMEs.** Attach documentation to any collection:
  write Markdown in an in-app editor (live preview, paste-or-drop image embeds)
  or upload PDFs and other files. New Markdown docs open straight in the editor
  and aren't created until you save. PDFs render inline in a themed pdf.js viewer
  (page navigation, zoom, fit-to-width) that matches the app instead of the
  browser's default chrome. Each collection can also carry a README shown at the
  top of its page. Access follows the collection's role (view / edit / admin).

### Changed

- **Mobile UI refresh.** Rebuilt the bottom navigation as a five-slot
  Material-style bar (four destination tabs plus an always-present "More"
  sheet) instead of cramming up to seven items across the width — readable
  labels, a clear active indicator, and the account actions (username +
  Log out) moved into the More sheet.
- **Search on mobile.** The top-bar search is now available on small screens;
  the redundant user-avatar menu is hidden there since navigation and account
  actions live in the bottom nav.
- **Library header on mobile.** The "All Models" header stacks its title and
  actions on narrow screens (so the heading no longer wraps), with tighter
  padding and smaller folder cards.

### Fixed

- Dark-mode Catalog stat cards no longer show bright/stray dividers — the
  Collections/Tags summary now uses theme-aware hairline separators.
- The internal `__external__` sentinel model (placeholder for external print
  jobs) no longer leaks into the "All Models" grid — the library browse now
  excludes it, matching the header count that already did.
- Batch tag/move no longer creates a tag or destination collection as a side
  effect when every selected model fails the permission check; taxonomy is only
  created once at least one model qualifies. Batch permission checks were also
  collapsed from a per-model grants query into a single pass.
- Upload drag-and-drop is steadier: the drop highlight no longer flickers when
  the cursor crosses items inside the dropzone, the copy cursor shows while
  dragging, and a folder that fails to read now surfaces an error instead of
  silently doing nothing.
- Spoolman **Test connection** now probes the values typed into the form, so you
  can verify a connection before saving — it no longer tests only the last-saved
  config. **Save** and **Test** now show clear success/error feedback (a "Saved."
  / "Connected — Spoolman vX" line and an immediate status-badge update) instead
  of appearing to do nothing, and a failed connection names the error instead of
  showing a bare "transport error:".
- The PrintStash logo and the document **Back** link now return to the Documents
  tab when that's where you were, rather than always resetting to Models.

### Internal

- New `app.services.spoolman` (`SpoolmanClient` over the shared httpx pool, plus
  a blocking `use_spool_weight_sync` for the worker-thread finish tick),
  `app.api.v1.spoolman` router (superuser, secret-masked config), a
  `record_spool_usage` helper in `print_results`, a `_spoolman_probe` health
  component, and the `spoolman_*` switches on `SystemConfig` with an Alembic
  migration. Covered by client/helper/API unit tests and a frontend API-contract
  test; e2e mock routes added for the Spoolman endpoints.
- Consumption write-back gained a write-time double-count guard: a blocking
  `active_spool_sync` probe in `app.services.spoolman` and a
  `spoolman_write_force` override on `SystemConfig` (third Alembic migration).
  `record_spool_usage` skips the decrement when Spoolman reports an active spool
  unless the override is set. Covered by unit tests.
- `app.services.filament_sync` (Spoolman→preset reconcile), `spoolman_filament_id`
  /`density_g_cm3`/`diameter_mm` on `FilamentProfile` and `spool_filament_id` on
  `PrintJob` (second Alembic migration), a `density` override on `mm_to_grams`,
  and `model_views.filament_cost_for_job` for spool-exact cost. Read-only
  enforcement on linked presets in the filaments API.
- Collection documents: `app.api.v1.documents` (CRUD, file upload, Markdown image
  embeds) with a `Document` model and a collection `readme` field across two
  Alembic migrations. Frontend adds `react-pdf`/`pdfjs-dist` (lazy-loaded, code
  split) for the inline PDF viewer; the nginx config now serves `.mjs` as
  `application/javascript` so the pdf.js module worker loads (browsers reject a
  module worker served as `application/octet-stream`). `POST /spoolman/test`
  gained optional `base_url`/`api_key` overrides so the UI can probe unsaved
  values. Covered by document/README and Spoolman API tests.

## 0.7.3

### Added

- **PrusaSlicer binary G-code (`.bgcode`) is now a first-class file type.** It
  was previously skipped on upload, URL/zip import, and shared-volume scans
  ("returns empty metadata gracefully", tracked as a 0.7.0 follow-up). The
  parser now reads bgcode's container blocks directly: slicer/printer/print
  metadata (slicer + version, printer model, nozzle/bed temps, layer height,
  infill, filament length/weight/cost, estimated time, material, …) and the
  largest embedded PNG/JPG thumbnail are extracted just like a text `.gcode`.
  Only the metadata and thumbnail blocks are read — they're stored uncompressed
  or zlib-deflated, so this needs **no new dependency**; the heatshrink-
  compressed G-code body is skipped, keeping the read cheap on large files.

### Changed

- **Binary G-code is metadata-only where it can't be more.** A `.bgcode` file's
  toolpath is heatshrink-compressed, and Moonraker/Klipper and Bambu LAN print
  plain-text G-code only — so for `.bgcode` files the in-browser toolpath
  preview shows a notice instead of an empty plot, "Open in slicer" is hidden,
  and send-to-printer is excluded (and rejected by the API with
  `binary_gcode_not_printable` as a backstop). Metadata and thumbnail still
  display, and the file downloads normally.

### Internal

- New stdlib-only `app.services.bgcode` reader (container walk, deflate, INI
  metadata, thumbnail blocks) with safety caps for truncated/hostile files.
  Covered by synthetic-container unit tests plus a guarded real-fixture test,
  and the `.bgcode`-skipped assertions in the import and shared-volume suites
  were updated to expect ingestion.

## 0.7.2

### Changed

- **Database migrations now run automatically on startup.** The API image's
  entrypoint runs `alembic upgrade head` before the server launches, so migrations
  happen however the container is started (Compose, Portainer, Unraid, bare
  `docker run`) — editing or removing the Compose `command:` can no longer skip
  them (issue #29). A failed migration aborts startup *before* the app serves a
  request. The previous explicit `docker compose run … alembic upgrade head` step
  is now optional.

### Fixed

- **Fresh installs and upgrades come up cleanly on SQLite *and* PostgreSQL.** The
  migration runner now bootstraps a brand-new database directly from the models
  (then stamps it at head) instead of replaying the historical migration chain,
  whose SQLite-authored baseline failed outright on a fresh Postgres. Existing
  databases still upgrade normally. (#29)
- **A database started once without migrations is adopted safely.** If the schema
  was built by the app's own `create_all()` and never recorded a migration version
  (e.g. after the Compose migration step was removed), the runner now detects it,
  stamps it at head, and continues — no "table already exists" crash on the next
  upgrade, and no data is touched. A one-time manual repair is also documented. (#29)
- **Deleting a model keeps you in its folder.** It previously returned to the root
  "All Models" view; it now returns to the collection the model lived in. (#30)
- **The PrintStash logo returns you to the collection you were browsing** instead
  of always resetting to "All Models" — the last-viewed folder is remembered. (#30)

## 0.7.1b1 - Upload methods improvement & Docker Compose light 

### Added
- **Drag file(s) or folder directly** in the viewport (or over a specific folder) to open a prefilled upload wizard (@JrVolt)
- **Added docker-compose.light.yml** since env only JWT secret and all the other service aren't used in basic config.
  so i made a light compose that requires no env with JWT in compose and removed optional services. (@JrVolt)

### Changed
- **Drag is now available in every tab** of the upload wizard (@JrVolt)

## 0.7.1

### Fixed

- **Further hardening against OOM during library scans.** The mesh-density caps
  added in 0.7.0 (#24) are the primary fix for scan OOMs; this adds a second,
  format-blind backstop for the cases the triangle estimate can't size up — e.g.
  a 3MF with no parseable mesh parts, where the estimate came up empty and the
  file was loaded anyway. A byte-size ceiling (`VAULT_MESH_MAX_LOAD_MB`, default
  200 MB) is now checked before any load, and the 3MF estimator falls back to the
  total uncompressed payload when it finds no mesh parts. Such files are indexed
  and skipped (3MF keeps its embedded preview) instead of risking a crash. If you
  hit a scan OOM on an older image, upgrading to 0.7.0+ is the actual fix. (#29)
- **RAM-aware mesh cap.** The thumbnail/geometry cap now scales with the memory
  the process actually has. PrintStash reads the cgroup limit (or host RAM) and
  derives a per-format triangle ceiling from `VAULT_MESH_MEMORY_BUDGET_FRACTION`
  (default 0.5), combined with the static cap — so a 4 GB container automatically
  skips meshes a 32 GB host renders, without per-host tuning. Measured cost is
  format-specific (3MF's XML loader is ~4.5× heavier per triangle than STL), and
  the cap accounts for that. (#29)
- **~50 % less thumbnail-render memory + no leak.** The software renderer now
  works in float32 instead of float64, roughly halving the peak RSS of the arrays
  that scale with triangle count (a 2.2 M-triangle model's render dropped from
  ~3.0 GB to ~1.6 GB) with no visible change to thumbnails. And every mesh's
  buffers are explicitly freed with the heap returned to the OS (`malloc_trim`)
  after each file, so a long scan's memory recedes between files instead of only
  climbing. (#29)
- **Face-chunked thumbnail rendering — peak memory is now O(chunk), not O(mesh).**
  The software rasteriser built every per-face array (screen-space triangles,
  smoothed corner normals, per-vertex colours, …) for the *whole* mesh at once,
  so peak RAM scaled with total triangle count. It now processes faces in chunks
  of `VAULT_MESH_RENDER_FACE_CHUNK_SIZE` (default 200k), building and freeing each
  chunk's arrays before the next, so a million-triangle mesh no longer materialises
  several large arrays simultaneously. Thumbnails are visually identical. (#29)
- **Concurrency-aware render budget.** Bulk/folder uploads (#26) run in a
  background-task threadpool and could fire many simultaneous renders that
  collectively OOM the box. A new `VAULT_MAX_RENDER_JOBS` (default 1) caps how
  many mesh load+render jobs run at once via a semaphore, and the RAM-aware
  triangle cap now divides its budget by the same value so each concurrent job
  stays within its share. `VAULT_MESH_MEMORY_BUDGET_FRACTION` stays 0.5 by
  default, with 0.30–0.35 now documented as safer for production hosts. (#29)
- **Large 3MF files skip the costly XML parse.** A 3MF whose estimate exceeds the
  adaptive cap now uses its embedded slicer preview directly instead of handing
  the archive to the loader, whose XML parse is the dominant memory cost.
  Controlled by `VAULT_USE_EMBEDDED_3MF_PREVIEW_FOR_LARGE_FILES` (default on). (#29)
- **"All Models" now shows the real library total.** The count on the root
  "All Models" view only reflected models sitting loose at the root, so a library
  whose models all live in (folder-mirrored) collections showed `0`. It now shows
  the access-scoped library total. (#30)

## 0.7.0 - Notifications & event hooks

### Added

- **Outbound notifications on print and printer events.** PrintStash can now
  alert you when a print completes, fails, or is cancelled, and when a printer
  goes offline — no need to watch the dashboard. Cancellations are a separate
  event from failures, so you can mute self-cancellations without silencing real
  failures.
- **Four delivery targets.** Generic JSON webhooks, Discord, Telegram, and ntfy.
  Configure channels under **Settings → Notifications** (administrators only).
- **Per-event and per-printer toggles.** Each channel subscribes to the events
  you choose and, optionally, only specific printers.
- **Richer, linked notifications.** Messages now lead with an event glyph and
  carry a "View model" link straight to the model's source page (Printables,
  MakerWorld, …) where one is known: Discord makes the embed title clickable
  (with a footer), Telegram adds an inline link, and ntfy gets a tap target plus
  a "View model" action button. Only safe `http(s)` links are ever rendered.
- **Reliable delivery.** Events are enqueued atomically with the state change
  that produced them (so none are lost) and delivered by a background dispatcher
  with automatic retry and exponential backoff. Each channel shows its last
  delivery status, and a recent-deliveries log records outcomes. A **Test**
  button sends a sample notification to verify a channel end to end. The whole
  feature is off until you enable it with a master switch.
- **Much better mesh thumbnails.** The STL/3MF/OBJ thumbnail renderer now uses
  crease-aware smooth (Gouraud) shading instead of flat per-facet shading, with
  retuned lighting and a subtle specular highlight. Organic models render as
  smooth surfaces like the interactive 3D viewer instead of a faceted, washed-out
  blob, while mechanical parts keep their crisp flat faces and sharp edges. The
  camera is also fixed: models are framed in a Z-up 3/4 "hero" view (the way the
  viewer shows them) instead of staring straight down the Z axis, which showed
  the top of upright models rather than their front.

### Fixed

- **Filament length no longer corrupted on OrcaSlicer files.** Real OrcaSlicer
  output embeds a `;Filament used:4.20m` comment in its start G-code, which was
  mistaken for Cura's metres figure and multiplied the true millimetre length by
  1000 (e.g. a benchy reported ~4,200,000 mm of filament). The metres conversion
  now only applies when no explicit `[mm]` length is present.
- **Bed temperature now detected on OrcaSlicer / BambuStudio.** These slicers
  name the heated bed `hot_plate_temp`, which the parser previously ignored.
- **Infill percentage now detected on PrusaSlicer.** It writes `fill_density`
  (underscore), which the older space-separated pattern missed.
- **Telegram notifications no longer fail on ordinary filenames.** The Telegram
  renderer now uses `parse_mode=HTML` with every interpolated value escaped,
  instead of legacy Markdown. A common name like `benchy_v2.gcode` (a single
  `_`) previously made the message unparseable and Telegram rejected it with
  HTTP 400; arbitrary printer/model/file names are now always safe.
- **ntfy notifications no longer fail on Unicode titles.** Notification titles
  carry an em-dash (`—`) and printer names can contain any Unicode, which broke
  *every* ntfy send because HTTP headers must be latin-1. Non-latin-1 header
  values are now RFC 2047-encoded (and decoded by ntfy for display).
- **Library scans no longer get OOM-killed by a dense mesh ([#24]).** A
  high-polygon model (a gyroid/lattice "infill core" 3MF or STL — tens of
  millions of triangles from a small file) made `trimesh.load` + the thumbnail
  rasteriser allocate **~700 MB of RAM per million triangles**, peaking at
  multiple GB *inside the load itself*. On a NAS scan that quietly drove the
  process to ~7 GB RSS and the kernel OOM-killed it; Docker misreported the death
  as a clean exit and restarted it, so it looped. The mesh's triangle count is
  now estimated **before** loading (exact for binary STL, from the uncompressed
  mesh XML for 3MF); anything over `mesh_max_render_triangles` (2,000,000 by
  default, env `VAULT_MESH_MAX_RENDER_TRIANGLES`) skips the load entirely. The
  file is still indexed, and a 3MF still shows its embedded slicer preview.
  Measured effect on a 5.2 M-triangle mesh: peak RSS drops from ~5.9 GB to
  ~45 MB. Mesh loading also now runs with `process=False` to trim memory further.
- **Dense-mesh OOM guard closed in three more spots ([#24]).** Hardening the
  pre-load triangle estimate after the initial fix:
  - **Binary STL with trailing bytes** (some exporters append metadata) failed
    the exact `84 + 50·N` size check and fell back to the *ASCII* density of
    ~250 B/triangle — a 5x underestimate for a binary file that could let an
    over-cap mesh slip through to the very OOM load the cap exists to stop. The
    estimator now distinguishes ASCII from binary STL and uses the binary
    body size (50 B/facet) as a safe upper bound.
  - **PLY meshes** (scans, point-cloud exports) were not estimated at all and so
    relied on the post-load backstop, i.e. they still loaded fully first. The
    face count is now read straight from the PLY header (which is ASCII even for
    binary bodies), so over-cap PLY files are skipped before loading too.
  - **"Download as STL"** ran an unbounded `trimesh.load` to convert a 3MF/OBJ,
    so a single click on a monster mesh could OOM the process and take every
    request down with it. The same cap now applies; an over-cap conversion
    returns a clean error instead of crashing the server. (Raw STL downloads are
    streamed byte-for-byte and never affected.)
- **An interrupted library scan no longer crash-loops the container ([#24]).**
  A scan stranded RUNNING by a process restart was reset to ERROR but kept its
  old `last_scanned_at`, so the scheduler found it immediately due again and
  re-ran it on the very next tick — a tight restart loop whenever the scan was
  what killed the process. The reset now stamps `last_scanned_at`, so an
  interrupted scan waits for its next scheduled slot (a manual scan is always
  still available).
- **Dense OBJ meshes are now guarded before loading too ([#24]).** OBJ is a
  first-class mesh type but was the one format the pre-load estimator didn't
  size, so a dense OBJ bypassed `mesh_max_render_triangles` and hit the exact
  full-`trimesh.load` OOM the cap exists to prevent. The estimator now counts
  OBJ face directives (triangulating n-gons for a conservative upper bound)
  without building the mesh, so over-cap OBJ files are skipped like STL/PLY/3MF.
- **Serving an STL no longer reads the whole file into RAM ([#24]).** The
  raw-STL preview/download path slurped the entire blob into memory before
  responding, so a multi-GB STL ballooned RSS per request. It now streams off
  disk (`FileResponse`/chunked) like the regular download route; the cached
  3MF/OBJ→STL conversion is streamed too.
- **A scan that hits an unexpected error can no longer strand a library
  `RUNNING` ([#24]).** Only the per-file loop was guarded, so a failure in the
  folder walk (a NAS mount dropping mid-scan), the deletion pass, or the final
  commit escaped with the row still committed `RUNNING` — and the scheduler
  permanently skips `RUNNING` libraries, so every future scheduled scan was
  silently dead until a restart. The whole scan now lands in a terminal state
  (`ERROR`, with `last_scanned_at` stamped) on any unexpected failure.
- **Scans with per-file failures now report `partial`, not `ok` ([#24]).** A
  scan where some files failed to index still showed a green `ok` status,
  hiding a persistent error behind the `errors` array. Such scans now finish
  with a new `partial` status (terminal, like `ok`); a clean run stays `ok`.
- **Unchanged NAS files no longer re-hash on mtime jitter ([#24]).** The
  "unchanged" skip tolerance was `1e-6` s — effectively exact-match — so any
  sub-second/FAT-granularity mtime drift forced a full sha256 re-hash of the
  file over the network on every scan. The tolerance is now 2 s (the FAT
  worst case); a genuine edit is still caught by the content-hash compare.
- **"Open in slicer" now works on self-hosted instances ([#27]).** The download
  URL handed to the slicer required a logged-in bearer token, but a slicer is a
  separate process with no login session — so it fetched the URL unauthenticated,
  got a 401, and showed a "load cancelled" error (or silently no-op'd if already
  running). The button now mints a short-lived, file-scoped download token and
  embeds it in a URL that *ends* in the original filename
  (`…/slicer/<token>/part.3mf`), so OrcaSlicer/Bambu Studio can fetch the file
  *and* detect its format. (Slicers take the URL tail as the download name, so a
  trailing `?token=…` query made them save e.g. `part.3mf?token=…` and never
  open it — the token has to come before the filename.)
  Each slicer is also gated by file type: Bambu Studio only opens 3MF via URL
  (other formats error with "unknown format"), matching what Manyfold ships,
  while OrcaSlicer handles STL/3MF/OBJ/STEP/G-code. The macOS `bambustudioopen://`
  scheme handling from the previous release is retained.
- **Zip imports now preserve the archive's folder structure.** Importing a zip
  with sub-folders flattened every entry onto a single auto-collection named
  after the archive, losing the layout the author organised the pack with. Each
  entry now keeps its archive-relative path: a file inside `Terrain/` becomes a
  model in a `Terrain` sub-collection nested under the archive's collection,
  while files at the archive root stay on the collection itself. Path entries
  are still validated against traversal (`..`, absolute, drive-letter) before
  extraction.
- **3D viewer no longer lays models on their side.** STL and other print meshes
  are authored Z-up, but the interactive viewer dropped them into its Y-up scene
  unrotated — so a model rested on its back and the floor grid sliced through its
  middle. Meshes are now stood upright before measuring (object `+Z` → screen-up,
  matching the thumbnail renderer), so they sit on the grid the right way up.

[#24]: https://github.com/xiao-villamor/PrintStash/issues/24
[#27]: https://github.com/xiao-villamor/PrintStash/issues/27

### Internal

- Parser hardening is now driven by self-sourced fixtures trimmed from real
  public slicer output (OrcaSlicer 2.3.2, PrusaSlicer 2.6.1), with regression
  tests for each bug above. Binary `.bgcode` remains unparsed (returns empty
  metadata gracefully) and is tracked as a follow-up.
- New end-to-end test suite (`backend/tests/e2e/`) boots the real app against
  contract-enforcing fakes and covers auth, ingest, notification delivery, and
  sharing. Marked `e2e` and runnable in isolation with `pytest tests/e2e`.

## 0.6.7 - Collection & file-level import

### Added

- **Import a whole collection from a URL.** Pasting a Printables or MakerWorld
  *collection* URL into Upload → From URL now fans the collection out into a new
  PrintStash collection (named after the source), importing every member model.
  Each model records its own member page as `source_url`, and a member that
  itself ships multiple files expands into multiple models. Multi-file members
  and per-member failures are handled independently, so one bad member never
  aborts the batch.
- **Auto or review.** A "Review collection items before importing" toggle lets
  you either import everything automatically or first pick which member models
  to keep (mirroring the existing ZIP entry-selection flow).
- **Per-file selection for multi-file model pages.** A Printables model page with
  several files (e.g. a model with 11 STLs) now lists those files from the source
  *without downloading*, so you can choose exactly which ones to import; only the
  selected files are fetched. Single-file pages still import directly.

### Notes

- Printables works fully anonymously. MakerWorld sits behind a Cloudflare
  challenge, so collection import there is best-effort and may require a browser
  cookie, matching the existing single-model MakerWorld behaviour. Tinkercad is
  intentionally out of scope (no clean download API).

## 0.6.6 - Operations hardening (R2)

### Added

- **Prometheus metrics at `/metrics`.** Request latency (by route template),
  terminal ingestion-job counts, and live printer status by provider are now
  exported in Prometheus text format for Grafana/Prometheus scraping. The
  endpoint is open by default on the trusted internal network; set
  `VAULT_METRICS_TOKEN` to require a static bearer token.
- **Richer `/api/v1/health` output.** The health probe now reports background
  ingestion-job counts and external-library scan status alongside the existing
  database, storage, backup, and printer-provider sections.
- **Unraid Community Applications support.** Added CA-ready repository metadata
  (`ca_profile.xml`, root `icon.svg`) and Docker templates under `templates/`
  (`printstash-api.xml`, `printstash-frontend.xml`) plus a step-by-step
  `unraid/README.md` for installing PrintStash on Unraid (create the
  `printstash` network, install the API then the frontend, finish in the setup
  wizard).

### Fixed

- **External library scans no longer get stranded by a restart.** If the backend
  was restarted mid-scan, the library stayed marked `running` forever and the
  scheduler skipped it indefinitely. Orphaned scans are now reset to `error` at
  startup so they are picked up again on the next scheduled tick.

## 0.6.5 - First-run polish

### Fixed

- **The UI is interactive immediately after first-run setup.** Completing the
  setup wizard logged you in by writing the token to local storage and firing an
  in-tab auth event, but the auth provider only subscribed to that event when a
  token already existed *at mount* — which is never the case on a brand-new
  install. So the freshly created admin session wasn't observed: the app showed
  the vault but treated you as signed out, leaving Upload, New collection, and
  the admin/settings menu unresponsive until the JWT was picked up on the next
  full page load (a reload, or the ~2 minute window before the next probe). The
  provider now subscribes to auth changes unconditionally, so the setup login is
  reflected right away. Covered by a regression test.

### Changed

- **Faster first paint.** Route components are now code-split with lazy imports,
  so the initial load only ships the shell plus the landing route instead of
  every page up front.
- **React Query Devtools no longer ship in production.** They're lazily loaded
  and gated to dev builds, trimming the production bundle.

## 0.6.4 - Backup restore and download

### Added

- **Restore from the UI.** Settings -> Storage now lists available backups and
  lets an admin restore one with a destructive confirmation step.
- **Download backups to your computer.** Each listed backup has a Download
  action, and admins can also fetch an archive from
  `/api/v1/backups/{id}/download`.

## 0.6.3 - Reliability & correctness hardening

A maintenance release focused on edge-case correctness across importing,
printing, library browsing, and backups, plus a large expansion of the
automated test suite (backend 351 → 547 tests; frontend 85 → 93).

### Fixed

- **Bambu printers now report paused/finished state.** A Bambu printer that
  paused or finished a print showed as "Unknown" and its job never advanced to
  Paused/Completed, because Bambu's state words (`RUNNING`/`PAUSE`/`FINISH`)
  weren't translated into the vocabulary the rest of the app uses. Bambu status
  and job tracking now follow the full print lifecycle.
- **Reprinting the same file keeps your history.** Starting a second print of a
  file that was already printed once revived the first (completed) job instead
  of recording a new one, erasing the earlier print's outcome. Each run is now
  its own history entry.
- **Library and trash lists page reliably.** They could repeat or skip a model
  between pages when many shared the same timestamp (e.g. right after a bulk ZIP
  import). Ordering now has a stable tiebreaker.
- **Search is case-insensitive on PostgreSQL.** Model-name search matched case
  only on SQLite; it now behaves identically on PostgreSQL.
- **G-code metadata edge cases.** Fractional time estimates (`1.5h`) parse
  correctly (previously read as 5h); Cura filament length converts from metres
  even when the comment spacing differs; and a genuine `0` (0% infill, an
  unheated 0 °C bed) is shown and exported instead of being dropped as blank.
- **Imports.** Page URLs from look-alike domains (e.g. `evilmakerworld.com`) are
  no longer treated as MakerWorld; downloaded filenames containing a semicolon
  are preserved in full; and archive entries using Windows-style `..\..\`
  traversal are rejected.
- **Printer file timestamps** from Moonraker are stored as UTC rather than the
  server's local time.

### Hardening

- A malformed G-code with an unterminated embedded-thumbnail block no longer
  buffers the entire file into memory during import.
- A printer-side filename that resembles a Vault revision marker can no longer
  be matched ahead of the genuine trailing marker.

### Performance

- Listing cloud (S3/R2) backups no longer streams each full archive just to read
  its manifest — the manifest is now the first entry, so only a small header is
  read per backup.

### Internal

- Substantially expanded unit coverage: G-code parsing, import resolvers and the
  SSRF/zip-slip guards, cost & filament maths, collection-RBAC boundaries, the
  backup round-trip and archive layout, external-library scanner helpers,
  printer-file correlation, embedded-3MF thumbnails, and the frontend formatters.

## 0.6.2 - Shared volumes: scheduling + real-time watching

External Libraries grow up: the feature is now **Shared volumes** (a folder on
the server *or* a NAS), with proper scheduling and optional real-time syncing.

### Highlights

- **Statistics dashboard.** A new admin-only Statistics page turns completed
  prints into trends — total cost, prints, filament used, average filament per
  print, and total print time — with a cost/filament/prints time series
  (area/line/bar), top collections, and most-used filaments, filtered by period
  (7/30/90 days, 1 year, all time). A configurable display **currency**
  (Settings → Design) is applied across all cost figures.
- **Cron-based scheduling.** Each volume's scan runs on a schedule you pick from
  a preset (hourly, every 6 hours, daily, weekly) or a custom cron expression —
  or set it to **Manual only**. This replaces the fixed "every N minutes" field;
  existing libraries are migrated to an equivalent cron schedule automatically.
- **Real-time watching.** Local folders are watched (via `watchfiles`) and
  reconcile within seconds of a change, so you no longer have to wait for the
  next scheduled scan. A burst of changes is debounced and then runs the same
  safe reconcile as a normal scan.
- **Network-aware fallback.** Filesystem events aren't delivered for network
  mounts (NFS/SMB/CIFS), so watching auto-detects the filesystem and falls back
  to scheduled scans on network folders. A per-volume control (Auto / On / Off)
  lets you override the detection. Each volume shows its detected filesystem and
  whether watching is active.

### Renamed

- The **Settings → NAS folders** tab is now **Shared volumes**, reflecting that
  it works for local server folders as well as NAS shares.

### Reliability

- **Fixed:** opening the Shared volumes settings tab returned a 500. The
  `watch_mode` column's migration default stored the lowercase enum value while
  SQLAlchemy reads enums by member name, so existing rows failed to load. The
  default is corrected and a follow-up migration repairs affected rows.

## 0.6.1 - About tab freshness

A small patch release.

### Highlights

- **Paste a model page to import.** Import from URL previously accepted only
  direct file/`.zip` links — pasting the Printables/MakerWorld/Thingiverse page
  you were looking at failed. The server now resolves the page to its
  downloadable asset (Printables, MakerWorld, and Thingiverse), keeping the
  original page as the model's source URL. MakerWorld/Thingiverse downloads that
  require a login accept an optional session cookie.

### Reliability

- **Fixed:** the Settings → About "Latest changes" card stayed on the previous
  release because its entry list was hand-maintained and easy to forget. A
  drift-guard test now fails whenever `changelog.ts` falls behind
  `package.json`, so the About tab can't ship stale.

### Performance

- **Fixed:** 3D model (STL) previews took ~20-30s to load on the first open.
  The `/files/{id}/stl` endpoint wrapped the rendered bytes in a
  `StreamingResponse(BytesIO(...))`, which Starlette iterates line-by-line —
  catastrophic for binary mesh data, where stray `0x0A` bytes split the body
  into tens of thousands of tiny ASGI chunks (~1s per MB). It now returns the
  body in a single `Response`, dropping an 11 MB preview from ~26s to ~0.02s.
  Affects both the model detail viewer and public share links.

## 0.6.0 - NAS External Libraries

Mirror a folder you already have — typically on a NAS — into PrintStash without
copying it. The folder stays the source of truth; PrintStash indexes files
where they live and stores only thumbnails and metadata.

### Highlights

- **External libraries (NAS folder mirroring).** Point PrintStash at a folder
  and it indexes every supported file in place (`File.is_external`), storing
  only the generated thumbnail + metadata. Opt-in and OFF by default
  (`SystemConfig.external_libraries_enabled`). Manage libraries in Settings
  (superuser only).
- **Two-way sync.** A scan reconciles the index with the folder — new files
  indexed, removed files trashed, edited files re-hashed and refreshed — while
  web uploads/revisions *write back* into the folder so it stays complete.
  Revisions follow their model's library automatically; new uploads pick a
  destination in the upload modal.
- **Folder hierarchy → collections.** In MIRROR mode a file's subfolder chain
  becomes its collection path; SINGLE mode routes everything into one chosen
  collection.
- **Your bytes are never touched.** PrintStash only ever *adds* files to the
  folder (collision-safe naming), never overwrites. Trash hard-delete and the
  orphan-blob GC skip external files entirely — removing a model or a library
  drops the index rows but leaves the originals on the NAS.
- **Unmount safety.** A scan aborts without deleting anything when the root is
  missing/unreadable, or empty while indexed files still exist — an unmounted
  share can never trigger mass deletion.
- **Periodic background scans.** Enabled libraries are rescanned on their
  configured interval; manual scan available via the API and UI.

### Reliability

- **Fixed:** the periodic scan scheduler crashed on a naive/aware datetime
  comparison (`last_scanned_at` reads back from the DB without tzinfo), which
  the scan loop swallowed — silently stopping scheduled scans after each
  library's first run. Normalised via `core.time.ensure_utc`.
- **Fixed:** the frontend e2e suite broke once the upload modal began probing
  `/api/v1/config` for the NAS feature flag — the mock API now serves the vault
  config (and `/libraries`), and a new e2e case covers the upload modal's NAS
  write-back destination selector.
- Real-use-case test suites for NAS mirroring (safety invariants, write-back,
  reconcile, scheduler, full API round trip) and for ingestion/revisions driven
  by real STL/3MF/g-code fixtures. Frontend unit tests pin the External
  Libraries API client and the `external_libraries_enabled` config flag.

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
