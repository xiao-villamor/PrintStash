# Real end-to-end tests

These Playwright specs drive the **real** FastAPI backend against a throwaway
SQLite DB + temp data dirs — every create/edit/delete actually hits the database.
This is the difference from `tests/e2e/` (the fast mock-API smoke suite).

```bash
pnpm test:e2e:real
```

For the release-blocking subset (backup recovery, ingestion, organisation,
trash, scans and remote storage), run the repository-level lane instead:

```bash
./scripts/test-critical.sh
```

`backup-recovery.spec.ts` leaves a completed ZIP inspection unselected before
purging a Model and restoring it through Settings, then compares the recovered
Artifact bytes. Historical release archives are checked separately by the
[backup compatibility script](../../../scripts/check-backup-compatibility.sh).

`playwright.real.config.ts` boots the application plus its printer emulator:

- `scripts/start-backend.sh` — wipes state, runs Alembic, launches uvicorn on
  `:8410` against a temp DB under `.data/` (gitignored).
- Vite on `:3310` with `VITE_API_URL` pointed at that backend.
- The standalone Moonraker/Spoolman emulator used by fleet scenarios.

The former Model Overview caption editor flow is replaced by the hash display;
caption conflict handling remains covered by the caption API and component suites.

`model-hashes.spec.ts` verifies the uploaded file SHA-256, runs real geometry analysis, and checks the compact hash controls in desktop and mobile Overview layouts, including complete file and geometric SHA-256 values copied to the clipboard.

`batch.spec.ts` reveals a Model checkbox on hover before starting selection and applying real tags, deletes and moves, including undo and viewer permission checks.

`multipart-models.spec.ts` selects and deletes sets in desktop grid and mobile list views, checks the detail and build planning screens, and verifies that member Models and G-code revisions remain available.

`library-filters.spec.ts` filters a real uploaded Model through file-type, printed-status and calendar dropdowns, restores the range after reload, and clears a print-history date to recover the result.

Library navigation covers desktop grid and phone list reading positions through
detail, Back and Forward with 101 Models spanning several pages. Automatic
refresh traverses the virtual gallery to its last Model, changes the real browse
revision through an unused tag deletion, and verifies that the background update
preserves the visible Model and its screen offset without a Refresh library banner.
The shared outliner seed keeps its 501-Model default for separate large outliner flows.

Storage-provider setup is deliberately isolated because it must begin with an
unconfigured instance and restart after changing transports:

```bash
pnpm test:e2e:storage
```

`playwright.storage.config.ts` owns a separate backend, Vite server, and
loopback WebDAV server. The storage spec always runs the WsgiDAV contract. To
enable the additional real-Nextcloud contract, provide a reachable URL (the
storage launcher starts the official Apache image when Docker is available):

```bash
PLAYWRIGHT_STORAGE_NEXTCLOUD_URL=http://127.0.0.1:8780 pnpm test:e2e:storage
```

The launcher uses the same digest-pinned Nextcloud image and administrator
credentials as the backend provider contract. The contract configures Nextcloud
through Settings, restarts the API, verifies a non-empty remote G-code object,
checks the confirmation guard, and asserts guarded purge returns `blocked` while
the exact remote object remains. Without Docker or the URL it is reported as a
deterministic Playwright skip rather than a false compatibility pass. CI runs
both real-backend suites.

The optional SFTP lifecycle uses the OpenSSH contract harness when
`PLAYWRIGHT_STORAGE_SFTP_HOST` and `PLAYWRIGHT_STORAGE_SFTP_HOST_KEY` are set;
the port, username, and password can be overridden with the corresponding
`PLAYWRIGHT_STORAGE_SFTP_*` variables. It verifies guarded confirmation and a
blocked retained cleanup outcome.

`playwright.critical-backup.config.ts` owns a third clean instance for the
remote-only backup recovery contract. The browser creates the WebDAV backup
destination, disables local backup creation, purges an uploaded Model, restores
the remote archive, and verifies the downloaded Artifact bytes exactly. It is
part of `pnpm test:e2e:critical`, not the ordinary serial real-backend suite, so
state from another scenario cannot make recovery pass accidentally.
Its shared-provider form case opens the storage migration disclosure before
choosing Nextcloud, then verifies credential retention and linked-target protection
when editing the remote connection.

`helpers.ts` seeds the first admin via `/setup` once and injects a real JWT into
the browser, so tests boot authenticated. The suite runs serially on one DB, so
tests use unique (timestamped) names and clean up after themselves.

Requires the backend dev venv (`backend/.venv`); falls back to `uv run`.

## Model-detail review

```bash
# Supply an existing Model URL and account through the environment.
# Keep credentials out of shell history, screenshots and committed files.
pnpm review:model-detail
```

Requires `PRINTSTASH_REVIEW_URL` (an existing `/models/<id>` URL),
`PRINTSTASH_REVIEW_USERNAME` and `PRINTSTASH_REVIEW_PASSWORD`, plus Python 3
for reading the downloaded sliced archive. The opt-in `playwright.review.config.ts`
connects to that deployment directly. It starts no servers, resets no database,
and generates no Models or metadata. Ordinary real-backend CI excludes `review/`.

Four journeys inspect **details and previews in light and dark**, always at
**1920×1080, device scale 1**, with navigation collapsed. The target must have a
source 3MF and a sliced-3MF Revision with multiple plates. All seven detail tabs,
scrollable content, revision comparison, model/revision/cover/share/history/send
and queue dialogs, plate selection, camera/layer/display controls and expanded
views use the Model's actual data. Empty states remain empty; the flow never
creates extra revisions or history just to fill a screenshot. It downloads the
actual sliced archive, verifies its SHA-256 against the API, and records embedded
settings verbatim beside the persisted metadata.

The review opens forms without submitting them. Login and single-use events-stream
authentication tickets are the only allowed POSTs. Browser API writes are blocked
and fail the journey; the Model version and Artifacts must
remain unchanged. A labelled browser-only 503 checks Source error/retry presentation
before returning to the real response. No print, queue entry, share link, import,
edit or history record is submitted. This is a read-only inspection, not a
persistence or hardware test.

Open each journey's `index.html` under `reports/model-detail-review/` at the
repository root. The gallery links to viewport screenshots, ARIA snapshots,
control coordinates and computed typography. `metadata.json` records real input
evidence; `manifest.json` includes completion state and console/network diagnostics.
Partial evidence survives failed journeys. Tracing is off to avoid recording login
credentials; all artifacts contain private library data and must stay private.
A later run replaces the output directory, so copy any baseline you want to retain.

A passing journey is **not a clean design or accessibility audit**. Inspect every
captured state, including the before/after-Fit pairs. Compare shared controls and
plate context across modes; mesh-only shading does not imply an equivalent toolpath
action. `clippedBy`, `outsideScrollport` and `coveredAtCenter` are candidates that
need interpretation: scrolling, backdrops, disabled controls and custom input
wrappers can be intentional. For a later Impeccable pass, supply the galleries,
metadata and diagnostics with `DESIGN.md`; separate measured faults from hierarchy
and consistency judgments. This command does not install or execute Impeccable.

## Coverage

`library-inspector.spec.ts` uploads a real G-code Model, selects a loopback printer
emulator, records a failed outcome through the inline inspector's revision shortcut
and Mod+Enter note action, verifies the exact persisted record, then reloads the
Library and checks its history. The flow removes its Model and printer afterward.
Its toolpath flow also opens a real G-code preview in the narrow Library inspector,
switches to View, and exercises camera, travel and fit controls.

Dropdown placement covers Statistics customization at phone width, an open-menu
resize, a short viewport, and member Model actions. Checkbox choices survive a
reload; Escape returns focus to the Model action trigger.

G-code layer controls use a real 31-layer PrusaSlicer file at 320px, 390px and
desktop widths. Pointer hit tests and actual layer changes cover both ends of the
slider; switching back to 3D restores the mesh camera controls.

First-run onboarding (`playwright.onboarding.config.ts`) checks centered desktop
and mobile layouts in Spanish, inline password visibility, immediately visible
server folders, keyboard step navigation, and the account → first Model flow.
The activation route also exercises the language/theme menus, the simplified
upload dialog, mounted-folder help, postponing/resuming, and connecting a folder
with automatic scanning. Screenshots include the Spanish chooser and dialogs at
mobile and desktop sizes. Dismissing the guide reminder is checked across reloads
and navigation between Settings and the empty library.

An environment-provisioned owner (`playwright.environment-admin.config.ts`) starts
with `VAULT_SETUP_MODE=environment` and `VAULT_SETUP_ADMIN_*` set, as an app-store
install form leaves it: sign in with the provisioned credentials, get redirected to
the storage step from any page, choose storage, then upload a first Model.

auth (centered login in both themes at desktop/mobile sizes, short-screen scrolling,
UI login, wrong-password, username + API-key login then revoke) ·
vault (search, tag filter, list/grid toggle, empty state, narrow responsive toolbar) · collections
(create / nest / anchored heading rename at 1080p preserving the visible tree, populated nested collection, Model identity and downloaded Artifact bytes across reload, plus keyboard pen/Escape cancellation / subtree count / delete / recursive-delete non-empty from the sidebar / lazy child expansion and Model move) ·
documents (markdown editor, collection README, GFM tables) · tags (quick create/assign from a card,
global delete) ·
uploads (real Job recovery after lost event/read delivery without reload, resumable transfer pause/reload, viewport-admitted bulk previews, legacy browser queue recovery and clearing across reloads, mesh-only source, BGCODE metadata, into a collection, ZIP preparation in Tasks → select all or select a folder → selected import) · full backup recovery
(purge → UI restore → byte-for-byte download) · filament & printer presets
(create / edit / delete) · model lifecycle (upload → quick rename with unchanged Model identity and tags across reload → edit details → trash → restore → purge) · model detail (heading double-click rename with reload persistence and keyboard pen/Cancel, edit tags with save/cancel, log a manual print, download
a revision) · G-code revisions (add, auto-recommend, re-recommend,
status, compare) · public share links (view-only vs downloadable, revoke → 404) ·
multipart sets (real tag/cover conflict review without silently rebasing composition drafts, empty-set first action, external cover, tags, favorites, reusable members, collection browsing with multiple part selection) ·
RBAC (create user, search a nested collection and grant access without a whole-tree read, non-admin sees only granted
collections, view vs edit role gates editing + deleting) · user management
(promote/disable/reset password) · API keys · settings overview (system status
and vault stats) · supervised API restart · display currency · auto-mark-known-good toggle · metadata
export (JSON/CSV) · manual and uploaded backups · reusable remote storage
connection for backups and Library sources · notification channels (add webhook + delete; competing channel drafts and master-switch intents with explicit review/revised save) ·
remote storage loading layout and provider choice at desktop and mobile widths ·
About (running version + changelog) · design customization (metadata visibility,
card-metric slots + reset) · printer add/remove and short-screen provider setup (320px, 390px and landscape; accessible dialog name, reachable actions and keyboard cancellation) · cross-cutting (theme
persistence, health version, routes free of uncaught errors).

Fleet Status queues an uploaded Revision for a fixed destination through the
real backend and Moonraker emulator. The browser observes the print heat up and
cool, reloads to verify job history, and opens Queue and Maintenance selects
with the keyboard.

The Fleet overview shows measured nozzle and bed temperatures and a stopped
part-cooling fan on an idle Moonraker emulator. Enter opens the printer.

Completed printer Jobs can open the queue dialog with the original sliced
Revision, saved plate and destination. The browser confirms the repeat job
and verifies both print runs persist through reload.

`printer-file-history.spec.ts` browses synced Moonraker paths, starts and
deletes selected remote files, then compares recorded and unrecorded durations,
filters outcomes, and opens evidence from the resulting Jobs history.

`printer-library-upload.spec.ts` prepares a saved Revision from Files on a
disposable Moonraker emulator. It verifies the upload-only request, confirmed
remote filename, inspectable inventory entry, and absence of a printer start.

The mock-API browser suite also exercises operator recovery for an active fleet
job whose physical print has disappeared; the real printer contract covers the
automatic emergency-stop reconciliation path.

`util.ts` uploads a model through the real ingest flow; its G-code embeds the
model name so the backend's content-hash dedupe doesn't collapse separate
uploads. `helpers.ts` also exposes `authBundleFor`/`authedContext` to drive a
second browser as a non-admin user.

### Named storage presets

`playwright.storage-presets.config.ts` reuses the real WebDAV setup harness in an
isolated database. `storage-presets/storage-presets.spec.ts` saves a Koofr preset,
reloads its provider identity, probes the source, scans existing G-code and
downloads identical bytes through the linked Artifact. This is transport evidence,
not hosted-account certification. Run `pnpm exec playwright test -c
playwright.storage-presets.config.ts`.

Storage insights: reads real capacity evidence, persists a measurement, and confirms cleanup of a receipt-verified expired staging fixture in the disposable suite database. The file must remain until the confirmation is accepted.
Collection usage: compares readable sizes at desktop and mobile widths and opens the Uncollected Model drilldown using representative display data.
Settings navigation: checks that moving storage and reading storage insights stay discoverable, routine maintenance leads with a quick check, and Similar Models starts with Matches while keeping analysis settings and history in separate tabs on desktop and mobile. Its review flow exercises inline filters, explicit comparison modes and confirmation without changing Model or Artifact identity.
Maintenance: waits for both schedules and backup sources to load, opens both scheduling forms and advanced controls, checks matching mobile form and action widths without horizontal overflow, and saves an audit policy through the real API before reloading it.
Collection storage: keeps the two-column collection list and its container height stable while opening Models in the same area; links to Model details, fits every row above the pager for a 17-model collection on desktop and mobile, and keeps page navigation understandable; empty activity does not add a disclosure.

Native S3 browser delivery has a dedicated fixture and config:
`pnpm exec playwright test --config playwright.delivery.config.ts` (also included
in `pnpm test:e2e:storage`). It starts real SeaweedFS with test-owned TLS, a real
API backed by isolated SQLite, and Vite. Chromium invokes the production
`downloadAuthenticatedFile` helper and checks the saved Unicode filename, exact
bytes, actual provider CORS, omitted app credentials, and zero API body bytes.
Only the test certificate's trust check is relaxed; browser CORS remains enabled.

- `artifact-cache.spec.ts`: persist cache limits in Settings, explicitly clear idle cache files, then reset to environment defaults.

- `migration/vault-migration.spec.ts` (isolated `playwright.migration.config.ts`): real backup, preflight, online delta ingestion, API restart, recovery/resume, explicit cutover, byte-exact Artifact downloads, Full audit and JSON report. Source cleanup remains disabled during grace. Included in `pnpm test:e2e:real`.

Search clarity: the AI-search flow verifies that Enter preserves the live library filter, explicit AI search opens results, and retrieval explanations stay hidden. Library workflows use visible Library tools beside Filters in the library toolbar, with the same actions wrapping on narrow panes.

AI Search settings: the Settings browser flow waits for loaded AI data, visits guided setup, search types, AI servers and technical options, captures desktop and mobile views, and checks visible choices and horizontal fit.

- ZIP uploads: a failed preparation retains input capacity; Tasks confirms and discards it safely.

`viewer-stl.spec.ts`: an adversarial 3MF displays a persisted memory refusal across browser reloads while its original remains downloadable. A 17 MB STL opens its 3D view automatically, with a visible canvas and enabled screenshot control.

`provenance.spec.ts` imports an actual browser capture, edits its Source metadata,
creates a competing conditional edit, and verifies that the retained draft is
persisted only after reviewing the current source version and explicitly retrying.

`library-navigation.spec.ts` returns from real Model detail to a paginated Library,
checking entry identity and scroll geometry in desktop grid and mobile list layouts,
including browser Forward/Back. It seeds and cleans up a unique scale fixture. It also
opens a Model and returns, checking that the page keeps one events connection and
sends no further setup probe.

- `sso-settings.spec.ts`: conditional edits across two tabs preserve the losing
  draft and require explicit review before saving against the latest version;
  SSO configuration still persists without returning client secrets.

- `external-libraries.spec.ts`: conditional source activation detects a competing
  configuration edit and requires explicit review before the revised command.
  The main Deep CI browser lane supplies a disposable mounted root, so exact-name
  upload, scanned-STL preview and root re-enrollment contracts all execute. For a
  selected local run, set `PLAYWRIGHT_EXTERNAL_LIBRARY_ROOT` to an existing
  test-owned directory; each case cleans only its own child root.

- `onboarding/first-model.spec.ts`: first-folder activation sends the reviewed
  configuration precondition before source creation and scan.

- `storage/storage-provider.spec.ts`: the WebDAV lifecycle includes a competing
  configuration edit, retained credential draft and explicit conditional retry.

- `printers.spec.ts`: two settings editors detect a stale save, retain the losing
  draft and explicitly save revised changes without replacing untouched settings.

- `profiles.spec.ts`: two editors per preset kind recover a conditional conflict
  while preserving the other editor’s notes, verified after reload.

- `provider-connections.spec.ts`: issue a pairing code, claim without an account session, resolve competing browser-name drafts through explicit review, then confirm revocation.

- `collections.spec.ts`: ancestor heading/bulk renames through browser Back, reload and persisted Recent, retaining the Model and downloadable Artifact.
- `saved-views.spec.ts`: keyboard save and rename starts in the named field, replaces the selected name, and persists after reload.

- `uploads.spec.ts`: repeated identical STL uploads report the existing Model, retain the destination after reload, and link to the unchanged Model and downloadable bytes.

- `slicer-profiles.spec.ts`: real G-code uploads group repeated filament/printer settings, preserve changed values, and expose source/process details at desktop and mobile widths. Consultation opens without editable fields; the explicit editor and overview fit both widths without horizontal scrolling.

- `resin.spec.ts`: CTB/GOO source upload, persisted print metadata, rendered embedded preview, original-byte download.
- `documents.spec.ts`: collection spreadsheets and binary resin attachments remain downloadable with their original filenames and bytes.

- `model-covers.spec.ts`: source selection, upload, real 3D-view capture, preservation through thumbnail regeneration, and reset to automatic.
- `gcode-plates.spec.ts`: upload sliced 3MF exports with or without meshes, then switch between distinct plate toolpaths in the real viewer.

`inbox-capture.spec.ts`: pairs a browser, interrupts a two-file transfer, resumes only the pending file, imports both Models through review, and verifies revocation denies importer recovery. Provider bytes are controlled; the API, staging and ingestion are real.

- `gcode-plates.spec.ts`: upload sliced 3MF exports with or without meshes, switch plate toolpaths, preview a dense 750-layer revision, and send the selected revision to a printer emulator. Desktop layout cases check that Layers and View controls fit without clipping or overlap in normal and expanded previews and across model tabs. The default render is 1920×1080 with the main navigation collapsed; explicit 1280×720 and 1440×1000 cases reproduce the reported sidebar clipping.

The default Klipper dashboard scenario in `fleet.spec.ts` homes and jogs an axis,
applies an override, then preheats, feeds filament, and cools down. It verifies
the reported position, speed, and extrusion availability from real emulator telemetry.

`printer-sections.spec.ts` exercises the combined per-printer materials and maintenance
workspace on the Moonraker emulator, persists routing and notes, and verifies
Diagnostics and browser history at the desktop viewport.

- `printer-config.spec.ts` uses the throwaway Moonraker config root to check nested navigation, dirty-draft guards, keyboard save, exact CRLF readback after reload, external-edit review, and read-only files. It switches to reported provider values with an unsaved draft and verifies the same draft can still be saved. It also verifies gutter alignment while paging and scrolling to the end of a long source file with horizontal overflow.
