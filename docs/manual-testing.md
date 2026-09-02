# Pre-release manual testing

Run this checklist before tagging a release. It complements, but does not
replace, the automated gates in [`release-validation.md`](./release-validation.md).
Use the dedicated stack in [`docker-compose.manual-test.yml`](../docker-compose.manual-test.yml)
for a reproducible PostgreSQL, S3, Spoolman, Authentik, and printer-emulator
environment built from the current checkout.

## Evidence levels

Record one evidence level for every result. A green emulator result must never
be reported as hardware validation.

| Mark | Evidence | What it proves |
| --- | --- | --- |
| **A** | Automated | A named tier lane (`test.sh fast`, `contract`, `e2e`, or `full`) or Playwright suite passed on the tested commit. |
| **E** | Emulated | The running product completed the flow against a protocol fake or local service. |
| **H** | Hardware | A supervised test completed against the recorded physical device and firmware. |
| **M** | Manual | A human inspected behavior that automation cannot establish, such as layout or copy. |

The canonical printer claims and safety boundaries are in
[`provider-support.md`](./provider-support.md). Known gaps are in
[`known-limitations.md`](./known-limitations.md). Add a Hardware Validation Log
entry there only from **H** evidence.

## 1. Release record and prerequisites

Create a release record before testing:

| Field | Value |
| --- | --- |
| Candidate version | |
| Commit SHA | |
| Image/build identifiers | |
| Tester and date (UTC) | |
| Host OS/architecture | |
| Docker/Compose versions | |
| Browser versions | |
| Database/storage mode | |
| Test-data revision or checksum | |
| Open blockers/waivers | |

Prerequisites:

- [ ] The candidate commit is fixed for the run; record any later change as a new candidate.
- [ ] Required CI checks and the commands in `release-validation.md` pass.
- [ ] Docker Engine or Docker Desktop has Compose v2, at least 4 CPU cores,
      8 GiB free RAM for the full harness, and enough disk for fixtures and backups.
- [ ] Harness ports are free or overridden in `deploy/manual-testing/.env`.
      Authentik `9000` and SeaweedFS S3 `8333` are intentionally fixed because
      OIDC issuer and presigned S3 URLs must be identical inside and outside Compose.
- [ ] Disposable STL, OBJ, 3MF, STEP/STP, valid G-code, invalid/corrupt files,
      a safe ZIP, a traversal ZIP, a PDF, an image, and Markdown are available.
- [ ] Real-printer tests use a small known-good file, a clear build plate, an
      operator at the printer, and an agreed stop condition.
- [ ] Evidence never records secrets, printer access codes, cookies, or signed URLs.

## 2. Start the manual-test stack

The harness is intentionally separate from normal deployment volumes.

```bash
cp deploy/manual-testing/.env.example deploy/manual-testing/.env
docker compose \
  --env-file deploy/manual-testing/.env \
  -f docker-compose.manual-test.yml \
  --profile identity --profile emulators \
  up -d --build --wait
```

Default topology (all values are test-only and overridable in the copied env file):

| Component | Host URL | URL used by PrintStash | Test credential |
| --- | --- | --- | --- |
| PrintStash web | <http://localhost:3100> | — | Setup token `printstash-manual-setup-token`; create the first local admin |
| PrintStash API | <http://localhost:8100/api/v1/health> | `http://api:8000` | Same PrintStash session/API key |
| Authentik | <http://authentik.localhost:9000> | Same issuer URL through Compose DNS | `akadmin` / `authentik-admin-test` |
| OIDC admin | Via the PrintStash login button | Authentik `printstash` application | `printstash-admin` / `printstash-admin-test` |
| OIDC user | Via the PrintStash login button | Authentik `printstash` application | `printstash-user` / `printstash-user-test` |
| Spoolman | <http://localhost:7912> | `http://spoolman:8000` | None; never expose this test service publicly |
| SeaweedFS S3 | <http://seaweedfs.localhost:8333> | `http://seaweedfs.localhost:8333` | `printstash-manual` / `printstash-manual-s3-secret` |
| SeaweedFS master | <http://localhost:9333> | — | None |
| Moonraker fake | <http://localhost:7125> | `http://mock-moonraker:7125` | None |
| OctoPrint fake | <http://localhost:5000> | `http://mock-octoprint:5000` | API key `printstash-manual-octoprint-key` |
| PrusaLink fake | <http://localhost:8080> | `http://mock-prusalink:8080` | API key `printstash-manual-prusalink-key` |

The checked-in credentials make the harness repeatable, not secure. Change them
when the workstation is shared and destroy the stack afterwards. Never reuse
them for a real vault, identity provider, object store, or printer.

When the `identity` profile is omitted, set `VAULT_OIDC_ENABLED=false` in the
copied env file before starting. With the profile selected, keep it `true` and
run the documented readiness check; container health alone does not prove that
the OIDC blueprint has been reconciled.

Use the exact profiles and first-run steps in
[`deploy/manual-testing/README.md`](../deploy/manual-testing/README.md). That
README is canonical for harness credentials, URLs, reset, seed, backup, and teardown.

Before feature testing:

- [ ] `docker compose ... ps` shows every selected service healthy or running as documented.
- [ ] `http://localhost:3100/api/v1/health` returns the candidate version and
      healthy database, storage, and backup components.
- [ ] PrintStash is using PostgreSQL, not a fallback SQLite file.
- [ ] A test Artifact reaches SeaweedFS and remains readable after an API restart.
- [ ] Spoolman and the selected Authentik/printer emulator endpoints load.

## 3. Test data and repeatability

Build a small, named fixture set and retain hashes in the release record:

- `mesh-small.stl`: fast preview and thumbnail baseline.
- `mesh-large.stl`: memory/performance boundary.
- `mesh.obj`, `assembly.3mf`, `part.step`: conversion paths.
- Two G-code revisions with different slicer metadata, plus malformed G-code.
- Duplicate bytes under two filenames and different bytes under one filename.
- ZIP with nested supported files, mixed unsupported files, Unicode names,
  duplicate names, an empty folder, and an unsafe traversal member.
- Markdown with links and pasted images; PDF and image Documents.
- A portable library archive from the previous release and one from the candidate.
- A previous-release database/volume snapshot with live and trashed data.

Reset only the isolated harness. This is destructive to its named test volumes;
verify the Compose project shown by `config --volumes` first:

```bash
docker compose --env-file deploy/manual-testing/.env \
  -f docker-compose.manual-test.yml down --volumes --remove-orphans
```

## 4. Clean install, setup, and sessions

- [ ] A clean start applies migrations and serves `/setup` without a default user.
- [ ] An absent/incorrect setup token cannot create the first administrator.
- [ ] The documented test setup token creates exactly one first administrator;
      setup cannot be repeated.
- [ ] Login with a wrong password fails without disclosing whether a user exists;
      repeated failures are rate-limited.
- [ ] Login, refresh, remember-me, page reload, logout, and expired-session
      recovery behave consistently.
- [ ] Logged-out read/write behavior matches policy; write actions never fail silently.
- [ ] Cookies have expected `HttpOnly`, `SameSite`, path, and Secure behavior;
      repeat Secure behavior behind the release TLS proxy configuration.
- [ ] Changing the JWT secret invalidates old sessions predictably.
- [ ] Restart API, frontend, and host separately; configured state and files persist.

## 5. OIDC, users, roles, and API keys

Use Authentik only as a local test identity provider. The harness uses HTTP and
must never be copied as production identity configuration.

- [ ] Authentik discovery loads from the same issuer seen by browser and API.
- [ ] The OIDC login button shows the configured display name.
- [ ] A regular Authentik user is provisioned as non-admin and cannot access
      Statistics, user management, storage, or maintenance admin actions.
- [ ] `printstash-admins` membership maps to admin at the documented login boundary;
      removing it removes the effective mapping at that boundary.
- [ ] OIDC state, nonce, PKCE, callback, issuer, audience, and signature checks
      reject tampered, expired, replayed, or mismatched responses.
- [ ] Provider denial/outage gives a useful error without leaking tokens/secrets.
- [ ] A local administrator can still sign in while Authentik is unavailable.
- [ ] Create, use, and revoke a named API key; only creation reveals the key and
      reuse after revocation fails.
- [ ] Username plus API key obtains a token for a slicer/script flow.
- [ ] Admin creates, disables, re-enables, changes role, and deletes a disposable user.
- [ ] Collection owner/editor/viewer grants enforce browse, upload, edit, move,
      share, trash, restore, and purge. Direct API calls cannot bypass the UI.
- [ ] Audit records identify the real local, OIDC, or API-key actor.

## 6. Library navigation and organization

- [ ] Empty, loading, error, and populated library states are understandable.
- [ ] Grid/list switching, sorting, progressive loading, thumbnail sizing,
      configurable card fields, and back/forward are stable.
- [ ] Search handles names, Unicode, punctuation, and no-result queries.
- [ ] Create, rename, nest, move, and delete Collections; breadcrumbs stay correct.
- [ ] Drag one Model and batch-move several Models between Collections.
- [ ] Create, apply, remove, and filter tags; repeated tags use AND semantics.
- [ ] Open the separate **Multipart models** tab in the Vault and create a
      Multipart Model with a clear name and description.
- [ ] Add a named piece, choose an existing Model from the picker, then add a
      second existing Model as an alternative. Save and reload the grouping;
      confirm both Models remain visible in **Models** with their own files,
      previews, and G-code revisions.
- [ ] Add another piece after the first save, remove an alternative, and
      confirm the changes affect only the grouping. Reuse the same Model in a
      second Multipart Model.
- [ ] Trash or restore a referenced Model and reload the grouping; it must show
      as unavailable while trashed and become available again after restore.
      Deleting a Multipart Model must remove only the grouping, never its Models
      or files.
- [ ] Favorite, bulk tag/move/trash, and partial failures report accurately.
- [ ] Every facet restores from its URL: Artifact type, material, slicer,
      printer model, Revision status, printed state, outcome, vault/external
      storage, and upload date. Within-group OR and cross-group AND hold.
- [ ] Save, rename, load, and delete a Saved View; a copied URL restores state.
- [ ] Printer-presence and Collection filters change only intended results.

## 7. Uploads, archives, and ingestion

- [ ] Upload every supported Artifact type and mesh plus G-code together.
- [ ] The same source-mesh hash deduplicates into the intended Model; unrelated
      bytes never merge only because filenames match.
- [ ] Add a G-code Revision to an existing Model. Version order and the single
      recommended Revision invariant remain correct.
- [ ] Choose Collection, tags, and external-library target during upload.
- [ ] ZIP review lists supported members, preserves safe Unicode paths, permits
      a subset, and rejects traversal, absolute paths, symlinks, expansion limits,
      corrupt archives, and unsupported-only archives safely.
- [ ] Oversized uploads fail at proxy and API boundaries with no orphan staging,
      blob, or database row.
- [ ] Cancel, network interruption, duplicate submit, API restart, and refresh
      do not claim false success or create duplicate Artifacts.
- [ ] Task Center shows queued, running, completed, partial, failed, cancelled,
      and retry states; completed tasks remain terminal after restart.
- [ ] Parser failures preserve the Artifact and show bounded error details.
- [ ] OrcaSlicer/API-key upload creates the expected Model/Revision and never logs the key.

## 8. URL capture, Pending Imports, extension, and provenance

Follow [`vault-maintenance-and-capture.md`](./vault-maintenance-and-capture.md).

- [ ] Direct public file and safe archive URLs enter Pending Imports; no Model
      exists until confirmation.
- [ ] Private, loopback, link-local, credential-bearing, redirect-loop, DNS
      rebinding, and secret-query URLs are rejected or sanitized.
- [ ] Printables server resolution displays only available data; richer browser
      capture does not overstate server support.
- [ ] MakerWorld uses browser transfer, not server-side cookie reuse.
- [ ] Thingiverse lists the page's individual files, transfers only the checked
      files from the active browser, and offers manual attachment when its file
      controls are unavailable.
- [ ] MyMiniFactory OAuth connect/callback/disconnect works with test registration;
      tokens are user-scoped and redacted.
- [ ] Cults credentials validate metadata access only; automatic file acquisition
      is not offered.
- [ ] Pending Import edit, selection, tags, Collection, retry, partial completion,
      dismiss, expiry, and concurrent finalization preserve staging ownership.
- [ ] Pair an extension device with the five-minute single-use code; rename/revoke
      it and confirm revoked credentials stop. Exercise limits and failed exchanges.
- [ ] Extension disconnect removes its Vault host permission and credential; no
      broad browsing permission remains.
- [ ] Supported Chrome/Chromium and Firefox builds cross the real backend boundary.
- [ ] Source distinguishes captured, inferred, and overridden fields; clearing an
      override reveals captured data.
- [ ] Snapshots and cover survive refresh, export/import, trash/restore, and backup.
- [ ] Provenance contains no HTML, cookies, OAuth codes, signed URLs, credentials,
      or local staging paths.

## 9. Model detail, viewers, thumbnails, and metadata

- [ ] Model detail tabs load directly and survive refresh/back/forward.
- [ ] STL/OBJ solid, X-ray, wireframe, grid, fit, zoom, reset, and screenshot work.
- [ ] 3MF and STEP/STP produce/open cached STL previews; concurrent first-open
      requests do not corrupt or duplicate them.
- [ ] G-code layers, travel toggle, and bed overlay are correct.
- [ ] Invalid, truncated, empty, huge, and unsupported files show bounded fallback.
- [ ] Thumbnails are valid WebP with correct framing/orientation/transparency;
      cards never show raw mesh bytes, broken images, or stale previews.
- [ ] Rebuild thumbnails on a mixed library and verify legacy handling.
- [ ] Edit title/description/Collection and verify persistence, authorization,
      audit history, and conflict/error feedback.
- [ ] Metadata matches fixtures: dimensions, volume/triangles, slicer/version,
      printer/nozzle, layers/infill/support, material, temperatures, time, and cost.
- [ ] Missing metadata remains unknown rather than invented.

## 10. Files, G-code Revisions, and print history

- [ ] Files separates source meshes, previews, Documents, and Revisions.
- [ ] Add two Revisions; edit label, notes, and each outcome status.
- [ ] The first G-code is recommended, choosing another clears the former, and
      trashing it promotes the newest live Revision or leaves none.
- [ ] Compare two Revisions and verify every displayed difference.
- [ ] Downloaded bytes and filename match the selected Artifact.
- [ ] Slicer-open is shown only where supported and fails clearly without a handler.
- [ ] Manual/imported history is idempotent for one external job but preserves
      genuinely repeated prints as distinct jobs.
- [ ] A first successful print marks the intended Revision known-good exactly once.

## 11. Trash, purge, and retention

Use disposable data.

- [ ] Trash an Artifact/Revision and a Model; they leave live queries and enter Trash.
- [ ] Restore each with Collections, tags, provenance, thumbnails, Documents,
      and recommended Revision invariants intact.
- [ ] Purge removes rows and only explicitly owned vault blobs.
- [ ] Purging a linked external Artifact never deletes source bytes.
- [ ] Retention GC removes eligible trash once and leaves newer/restored rows.
- [ ] Missing/unmounted or unexpectedly empty external roots abort reconciliation
      without mass trashing.
- [ ] Concurrent restore/purge or interrupted deletion gives a recoverable,
      audited result.

## 12. Library sources, Documents, and sharing

Use PostgreSQL+S3 or SQLite+S3 to validate indexing without copying source
bytes. Use the README's clean SQLite+local mode for upload/revision write-back;
the local backend is a deliberate safety requirement for that operation. Export
evidence and reset the disposable volumes before changing modes.

- [ ] Seed the harness's ignored `deploy/manual-testing/external-library` folder
      and add container path `/manual-external`. Files remain linked in place;
      only managed previews/metadata enter vault storage.
- [ ] Scheduled scan and watcher detect add/change/remove. Network-like filesystems
      use documented schedule fallback.
- [ ] Write-back uses collision-safe filenames and never overwrites bytes; a
      Revision follows its Model's library.
- [ ] Create/edit/render Markdown; paste/drop an image; attach/download/delete
      PDF, image, and arbitrary Documents with correct roles.
- [ ] Markdown sanitizes scripts, dangerous URLs, HTML, and hostile images.
- [ ] Create a public Model link with downloads off/on, use it privately, revoke
      it, and verify real elapsed-time expiry.
- [ ] Public pages expose no internal paths, private notes, credentials, other
      Models, or unauthorized originals.

## 13. PostgreSQL, S3, backup, restore, and upgrade

Keep a backup copy outside the harness volumes.

Use the default PostgreSQL+S3 mode for database/storage behavior and migration
testing. PrintStash's integrated backup/restore deliberately supports SQLite
only: in PostgreSQL mode verify the stable `database_backup_not_supported`
result and use the README's external `pg_dump`/`pg_restore` plus S3 snapshot
procedure. After exporting evidence and resetting the disposable volumes, start
the harness in its documented SQLite+S3 mode for every integrated backup/restore
check below.

- [ ] PostgreSQL constraints, transactions, search/filter, concurrent updates,
      and migrations match supported SQLite behavior.
- [ ] S3 upload, stream/range read, multipart upload, thumbnail, trash/restore/
      purge, and restart persistence work against SeaweedFS.
- [ ] Stop SeaweedFS during upload/read/delete. PrintStash fails closed without
      committing false success; recovery leaves no orphan temp data or rows.
- [ ] **SQLite+S3:** Create/verify a backup with database, owned Artifacts, thumbnails, Documents,
      provenance, and manifest hashes.
- [ ] Unreadable/changing owned blobs fail backup instead of being omitted; linked
      external bytes respect the ownership boundary.
- [ ] **SQLite+S3:** Download a backup outside Compose volumes and, when in scope,
      mirror one to backup S3.
- [ ] **SQLite+S3:** Restore cleanly and compare record counts, sample hashes, permissions,
      connections, thumbnails, and viewers.
- [ ] Corrupt, truncated, traversal, wrong-version, and manifest-mismatched backups
      are rejected before replacement.
- [ ] **SQLite+S3:** Interrupted restore preserves maintenance mode and documented recovery.
- [ ] JSON/CSV and portable archive export/import preserve hashes/invariants;
      re-import exercises conflicts.
- [ ] Upgrade a previous-release SQLite volume and verify migrations and smoke data.
- [ ] Upgrade previous-release PostgreSQL plus S3 without destructive reset,
      taking and restoring database and object-store snapshots as one recovery point.
- [ ] If applicable, follow [`minio-migration.md`](./minio-migration.md) and verify
      normal, Unicode, and multipart objects before switching.
- [ ] Rollback restores pre-upgrade database and blobs together; old code is never
      pointed at a migrated database.

## 14. Printer providers: emulated contract sweep

Standalone fakes validate wiring, not firmware.

### Moonraker/Klipper — stable

- [ ] **E:** Add the documented Moonraker emulator URL, observe WebSocket status,
      temperatures, progress, and reconnect.
- [ ] **E:** Sync files, match a vault filename, upload without start, explicitly
      start, pause, resume, cancel, and import matching history.
- [ ] **E:** Configure the Neptune 4 preset and confirm the documented provider
      variant without vendor maintenance commands.

### OctoPrint — beta

- [ ] **E:** Use the emulator API key; verify status, inventory, upload-only,
      explicit start, delete, pause/resume/cancel, and reconnect.
- [ ] **E:** Unsupported raw G-code and measured consumption remain unavailable.

### PrusaLink — beta

- [ ] **E:** Test legacy API-key and, where emulated, Digest modes.
- [ ] **E:** Verify status/temperatures, inventory, upload-only, explicit start,
      delete, controls, restart, credential failure, and reconnect.
- [ ] **E:** Upload a valid `.bgcode` without starting it; verify the remote
      suffix, byte size, binary content type, and that no job starts implicitly.
- [ ] **E:** Corrupt a `.bgcode` checksum and verify PrintStash rejects it before
      the emulator receives an upload.

### Bambu LAN and Elegoo Centauri — beta

- [ ] **A:** Record provider integration/conformance commands. MQTT/FTPS/SDCP
      fakes are in-process, not standalone harness services.
- [ ] **A:** Named tests cover wrong access codes, transport errors, identity,
      duplicate reports, cache misses, and bounded external capture.
- [ ] **M:** UI hides unsupported inventory/delete/raw G-code; upload never starts.

For every provider:

- [ ] Diagnostics reports support, configuration, connectivity, and capabilities
      without credentials.
- [ ] Offline, timeout, malformed response, auth failure, mid-action disconnect,
      and recovery show bounded errors and no duplicate action/job.
- [ ] Send defaults to upload-only. Start requires explicit human action and is
      disabled where unsafe.

## 15. Real-printer validation

Run providers affected by the release plus one stable Moonraker smoke. Record
model, firmware, network mode, commit, actions, and outcome.

- [ ] **H Moonraker:** status, sync, upload-only, explicit start, controls,
      reconnect, and history import.
- [ ] **H Bambu:** LAN status, idle gate, upload-only, supervised start, controls,
      external-job identity, cache capture, and metadata-only fallback.
- [ ] **H PrusaLink:** Digest (and legacy API key if applicable), upload/start,
      files/delete, controls, restart, and reconnect; no Prusa Connect.
- [ ] **H OctoPrint:** status, files, upload/start/delete, controls, and reconnect.
- [ ] **H Neptune 4:** preset identity plus the Moonraker smoke.
- [ ] **H Centauri Carbon:** mainboard ID, SDCP, chunked upload, start-existing,
      controls, and reconnect while paused.
- [ ] **H Carbon 2:** LAN Only MQTT status/controls and chunked upload; inventory
      remains unavailable.

Never infer one model/firmware result from another. If required hardware is
unavailable, publish the gap rather than waiving it silently.

## 16. Spoolman, profiles, materials, and fleet dispatch

- [ ] Configure the documented internal Spoolman URL; connection test and health pass.
- [ ] Create vendor/filament/spool data, sync it, and verify linked profiles are read-only.
- [ ] G-code detects printer/filament profiles; user-edited profiles are not overwritten.
- [ ] Select a spool for manual log/send; it persists in job history.
- [ ] Remaining-weight warning is correct and non-blocking for low, untracked,
      and unknown-required-weight cases.
- [ ] Moonraker active spool resolves only through the configured inventory and
      is labelled externally tracked.
- [ ] Measured completion decrements once. Retry, duplicate terminal status,
      restart, cancel, and failure do not double-decrement.
- [ ] Native Moonraker hook detection warns and defaults write-back off; forced
      mode is explicit and audited.
- [ ] When Spoolman stops, unrelated operation continues with visible degradation.
- [ ] Queue one job and atomic multi-copy batch. Exercise manual/default/least-busy,
      groups, priority, drain, maintenance, release gate, cancel, and retry.
- [ ] Known nozzle/material mismatch requires audited confirmation and is excluded
      from safe auto-routing; unknown remains schedulable; color is advisory.
- [ ] Provider synchronization never deletes manual tool/feed state after failure.

## 17. Notifications, statistics, audit, and maintenance

- [ ] Create generic webhook, Discord, Telegram, and ntfy channels as in scope;
      send Test, disable, edit, and delete.
- [ ] Event/printer filters suppress only intended deliveries.
- [ ] Completed, failed, cancelled, and offline events deliver once across restart/reconnect.
- [ ] Payloads contain no credentials, paths, signed URLs, or excess private data.
- [ ] Statistics is admin-only. Test empty/populated 7/30/90-day, year, and all-time periods.
- [ ] Cards, chart modes, top Collections/filaments, timezone boundaries, and
      currency match hand-calculated fixtures.
- [ ] JSON/CSV has stable headers/types, role filtering, Unicode, and no secrets.
- [ ] Audit records setup/config, roles, library, printer, routing, restore, repair,
      and destructive actions with actor and redacted diffs.
- [ ] Quick audit checks reachability/size, links, thumbnails, Metadata, Revisions,
      work, and unclaimed storage; Full additionally hashes and verifies backups.
- [ ] Repairs are explicit, idempotent, audited, and cannot widen ownership.

## 18. Browser, responsive, accessibility, and localization matrix

Run login, browse, upload, detail, send, and settings on:

| Platform | Browser | Width/input | Result |
| --- | --- | --- | --- |
| Linux/Windows | Latest Chrome or Chromium | Desktop mouse/keyboard | |
| Linux/Windows | Latest Firefox | Desktop mouse/keyboard | |
| macOS | Latest Safari | Desktop trackpad/keyboard | |
| iOS | Current Safari | Phone touch | |
| Android | Current Chrome | Phone touch | |
| Any | Chrome/Firefox | 320 px, 768 px, 1280+ px | |

- [ ] Navigation, filters, dialogs, tables, viewers, toasts, and Task Center have
      no clipped action or page-level horizontal scroll.
- [ ] Keyboard order/focus is logical; dialogs trap/restore focus; Escape is safe.
- [ ] Labels, landmarks, headings, errors, live status, and icon controls are
      understandable to a screen reader.
- [ ] Light/dark/system contrast is legible; theme favicon updates.
- [ ] At 200% zoom/increased text, content reflows and actions remain available.
- [ ] Reduced motion removes nonessential motion; routes do not animate.
- [ ] English and Spanish have no raw keys/broken interpolation; dates, numbers,
      costs, units, and plurals localize correctly.
- [ ] Console has no uncaught, CSP/mixed-content, repeated-request, or secret errors.

## 19. Security and privacy checks

- [ ] Behind documented TLS proxy, Host/forwarded headers, WebSocket, uploads,
      callbacks, Secure cookies, and rate-limit client IPs are correct.
- [ ] Direct API IDOR checks cover Models, Artifacts, Collections, Documents,
      shares, printers, jobs, users, backups, connections, devices, and config.
- [ ] Stored/reflected XSS, Markdown injection, filenames, content-type confusion,
      and download disposition do not execute attacker content.
- [ ] Cookie-authenticated mutations and Bearer/API-key paths enforce intended CSRF rules.
- [ ] SSRF covers redirects/DNS changes in imports, webhooks, Spoolman, OIDC,
      provider connections, printers, and S3 configuration.
- [ ] Secrets are encrypted/hashed as designed and absent from APIs, diagnostics,
      audit, health, metrics, logs, exports, browser storage, and errors.
- [ ] Public endpoints expose only intended health/setup/auth/share behavior.
- [ ] File/archive/backup/import validation respects size, count, path, redirect,
      timeout, and concurrency bounds.
- [ ] Dependency, image, license, and vulnerability results have no untriaged blocker.

## 20. Observability, failure recovery, and performance

- [ ] Health reflects stopped/recovered PostgreSQL, SeaweedFS, backup target,
      Spoolman, and printer components.
- [ ] Logs provide time, level, bounded context, and job identifiers without secrets.
- [ ] Metrics are bounded and omit URLs, filenames, usernames, payloads, credentials.
- [ ] Restart API during upload, conversion, thumbnail rebuild, capture, backup,
      restore preflight, printer action, and polling; no operation claims false success.
- [ ] Disk full/read-only, DB/S3 outage, malformed provider, slow network, and
      browser offline/online preserve data and offer recovery.
- [ ] Concurrent editing and repeated buttons do not silently overwrite or duplicate work.
- [ ] Record cold/warm library, search, detail, thumbnail, 3MF/STEP, large upload,
      backup, and restore times on a named host.
- [ ] Exercise at least 1,000 Models or the stated target and compare CPU/RAM,
      query behavior, UI, WebSocket, and tasks with the previous release.
- [ ] On native ARM when supported, record startup, STEP, thumbnail, memory, and
      image architecture. QEMU is not performance evidence.

## 21. Exit criteria

- [ ] Automated gates pass on the exact candidate SHA.
- [ ] Every critical journey has **M**, **E**, or **H** evidence as appropriate;
      changed provider claims have **H** evidence or an explicit limitation.
- [ ] Clean install and supported SQLite/PostgreSQL/S3 upgrade/restore pass with hashes/counts.
- [ ] No issue risks security, data loss/corruption, unauthorized access, false
      print start, unrecoverable migration, or failed backup/restore.
- [ ] Other defects have owner, severity, reproduction, workaround, and ship/defer decision.
- [ ] Behavior, provider support, limitations, upgrade notes, changelog, and versions agree.
- [ ] Test credentials and harness volumes are destroyed after evidence retention.

## Evidence log template

| ID | Area/test | Level | Environment | Expected | Actual | Evidence | Issue | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MT-001 | Example: OIDC regular-user login | E/M | Firefox, PostgreSQL/S3 | Non-admin provisioned | | screenshot/log | | Pass/Fail/Blocked |

Every failure needs candidate SHA, UTC timestamp, role, browser/device, fixture
hash, steps, expected/actual result, redacted logs, severity, and whether a
retry or reset changed the outcome.
