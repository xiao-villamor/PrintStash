# Frontend inventory reconciliation

M0 prerequisite checkpoint, 2026-10-07. This is a file-accounting and review-evidence
reconciliation, not a new whole-frontend manual audit or qualification. Remaining
full-file reviews belong to the owning M8–M10 work and final M11 reconciliation.

## Snapshots and scope

- Historical ledger origin: `b0e79c3e8e41a62a4ad1ad7d092b0bc61f7f4b1b`.
- Implementation base: `710e4eb7aafe05642693d6ff2696146e042dca53`.
- Current committed snapshot: `086d3a3df086a73d8ef09568409af72b3bdf79ec`.
- Working-tree overlay: 12 modified tracked frontend files and two untracked
  frontend files, separately identified and SHA-256 stamped in the ledger.
  No uncommitted content is attributed to either commit.

The [ledger](review-ledger.tsv) includes every tracked path at either implementation
snapshot under `frontend/` (including both workspace packages), `browser-extension/`,
`.github/workflows/`, and `backend/unified/`; all 13 historical integration paths
outside those roots; and `docker-bake.hcl` plus root `package.json`, which the platform
review explicitly inspected. Nonignored untracked files in those roots are added
as working-tree-only rows. This scope does not inventory the entire backend.

The 15 exact integration paths outside the four roots are:

```text
.mise.toml
backend/app/api/v1/documents.py
backend/app/api/v1/models.py
backend/app/api/v1/multipart_models.py
backend/app/bootstrap/work.py
backend/app/modules/identity/ws_tickets.py
backend/app/modules/work/events.py
catalogues/casaos/PrintStash/docker-compose.yml
catalogues/runtipi/printstash/docker-compose.yml
deploy/manual-testing/compose.yml
deploy/minio-migration/compose.yml
catalogues/umbrel/printstash/docker-compose.yml
docker-bake.hcl
docker-compose.yml
package.json
```

## Denominators

| Snapshot                                   | Ledger paths present | First-party review scope | Generated/lock metadata | Vendored, excluded from first-party review |
| ------------------------------------------ | -------------------: | -----------------------: | ----------------------: | -----------------------------------------: |
| Implementation base                        |                  784 |                      757 |                       5 |                                         22 |
| Current commit                             |                  871 |                      844 |                       5 |                                         22 |
| Current commit plus working-tree additions |                  873 |                      846 |                       5 |                                         22 |

Base → current commit: **565 unchanged paths, 219 modified paths, 87 additions,
zero deletions**. The two untracked additions are absent at both commits. These
are file/blob comparisons, not implementation or manual-review credits.

The historical ledger had 762 rows: 727 inventoried, 30 targeted inspections, five
metadata-only. All 762 exist at both the implementation base and current commit;
none is falsely marked deleted because it lies outside the frontend directory.
It also included 22 vendored lint files despite its prose exclusion. Those rows
are retained with an explicit `excluded-vendored` scope, not silently removed.

The earlier 775-file count in [the integration review](../frontend-integration-review.md)
was a historical two-root snapshot. Recomputing the later four-root comparison
produces **834 current tracked nonvendored paths**, of which **107 were absent from
the old ledger**. The remaining 35 old rows are 22 vendor paths plus 13 external
integration paths; subtracting 762 from 834 would be incorrect. Adding the two
explicit root integration paths and two untracked files yields the present 873-row
ledger. At the implementation base, 20 paths in those four roots were already
missing from the historical ledger. There are now zero unexplained scoped paths.

## Ownership and roles

Ownership is a path-based inventory assignment, not a claim that feature boundaries
have migrated. Test and support rows stay with their defining package/app.

| Owner                      | Base paths | Current committed paths | With working-tree additions |
| -------------------------- | ---------: | ----------------------: | --------------------------: |
| `backend-integration`      |          6 |                       6 |                           6 |
| `browser-extension`        |         52 |                      54 |                          54 |
| `ci`                       |          7 |                       7 |                           7 |
| `deployment-integration`   |         15 |                      15 |                          15 |
| `frontend-app`             |        639 |                     724 |                         726 |
| `frontend/packages/domain` |         17 |                      17 |                          17 |
| `frontend/packages/ui`     |         48 |                      48 |                          48 |

These counts include excluded metadata/vendor rows; filter `manual_scope` for the
first-party denominator. Each row has a separate role: source, test, test-support,
configuration, package-manifest, tooling, localization, asset, documentation,
generated-contract or lockfile. `classification` preserves the old coarse label;
`role` supplies the more precise inventory categorization.

## Exclusions

Tracked exclusions remain visible in the ledger:

- `frontend/tools/oxlint/anti-slop/**`: exactly 22 vendored dependency files,
  excluded from first-party manual source review.
- `frontend/src/generated/printer-contracts.ts` and
  `frontend/src/generated/__tests__/printer-contracts.test.ts`: generated contract
  output, provenance/compatibility review only.
- `frontend/pnpm-lock.yaml`, `browser-extension/pnpm-lock.yaml`, and
  `frontend/scripts/viewer-representation-pilot/package-lock.json`: generated lock
  metadata, no manual application-source credit.

Dependency/install/build/runtime output is excluded by taking tracked Git trees
plus `git ls-files --others --exclude-standard`, not by enumerating installed
folders. Relevant exact ignored patterns at this snapshot are `node_modules/`,
`.next/`, `frontend/.next/`, `frontend/tsconfig.tsbuildinfo`, `frontend/out/`,
`frontend/dist/`, `frontend/coverage/`, `frontend/packages/*/coverage/`,
`frontend/test-results/`, `frontend/playwright-report/`,
`frontend/tests/e2e-real/.data/`, `frontend/tests/e2e-real/.storage-data/`,
`frontend/tests/e2e-real/.delivery-data/`, `/frontend/tests/performance/.startup-data/`,
`/frontend/.startup-results/`, and extension-local `.output/` and `.wxt/`.
The repository's other ignore rules still apply to the untracked overlay.
Generated `/locale-shell.js` and localized manifests are build output; their
first-party producer `frontend/scripts/localization-assets.ts` remains in scope.
Assets and checked-in test fixtures remain inventoried; inventory never implies
that binary assets have been read as source or browser-qualified.

## Review evidence and remaining work

The first three ledger columns retain every old classification/status verbatim.
New rows use `not-in-historical-ledger`; adding a path or modifying its bytes does
not grant a manual review. `recorded_review` separately records **231 recorded-full,
31 recorded-targeted and 584 inventory-only** rows within the **846 first-party**
paths. These are recovered historical evidence categories, not 231 files freshly
read at this checkpoint. No current exact-revision full-file certification is
inferred from a changed path, passing suite, import graph or code-writing history.

Evidence links point to explicit full/targeted read records:

- [Integration manual inspection](../frontend-integration-review.md#manual-inspection-in-this-tranche):
  24 complete-file reads and specifically bounded printer/fleet/popup reads.
- [State validation](../frontend-state-validation.md), including its exact manual
  source/test/config tables and PWA full-file table. Rows explicitly saying full
  are preserved as recorded-full; partial Settings or workflow reads stay partial.
- [Package ledger closure](../frontend-state-validation.md#m0-package-ledger-closure-after-lazy-checkpoint),
  introduced by `efba56d7`: all **17 domain and 48 UI package files**, including
  the remaining component mirrors, were explicitly reported read. This closes that
  historical package file-read inventory, not every app consumer or runtime test.
- The bounded platform record below preserves the supplied explicit full/targeted
  file list. It does not convert its open configuration findings into fixes.

`remaining_review` marks complete-file work outstanding for inventory/targeted
rows and scope/revision revalidation for recorded-full rows. Every dirty first-party
row additionally says `uncommitted-review-outstanding`. Missing reconciled evidence
means no claim is made here; it does not erase a narrower behaviour matrix elsewhere.
The later owning milestone must connect its exact current bytes to evidence and
inspect any uncovered assertions or consumers before claiming closure.

Broad unresolved review areas remain app feature consumers, settings/provider/storage
forms, viewer/worker/delivery boundaries, specialty browser harnesses and their
backend launchers, complete workflow jobs, extension popup lifetimes/adapters, and
assertion-by-assertion test coverage. Recorded package/PWA reads reduce those gaps
only within their explicit scope. No physical-device, complete accessibility,
all-browser or whole-frontend performance certification is supplied by M0.

## Platform review record

A supplied bounded source review at `1bfdc041a5b5a10208f6c7650e0588d909cdaf46`
explicitly recorded the following full-file reads. Its source artifact was
`frontend-platform-review-proposal.md`, SHA-256
`784cdb793805f2425d1a71cd48ca6af241f205b983251a7292048ecd50407d25`.
This is preserved provenance, not a new read or execution claim:

- `frontend/vite.config.ts`, `frontend/vite.delivery.config.ts`, `frontend/vitest.fast.config.ts`.
- `frontend/package.json`, `frontend/pnpm-workspace.yaml`, `frontend/tsconfig.json`.
- `frontend/packages/domain/package.json`, `frontend/packages/domain/vitest.config.ts`, `frontend/packages/domain/tsconfig.json`.
- `frontend/packages/ui/package.json`, `frontend/packages/ui/vitest.config.ts`, `frontend/packages/ui/tsconfig.json`.
- `frontend/playwright.config.ts`, `frontend/playwright.real.config.ts`, `frontend/playwright.delivery.config.ts`.
- `frontend/playwright.storage.config.ts`, `frontend/playwright.storage-presets.config.ts` (added to this bounded read after finding the routing omission).
- `frontend/nginx.conf`, `frontend/security-headers.conf`, `frontend/Dockerfile`.
- `backend/unified/nginx.conf`, `backend/unified/Dockerfile`, `backend/unified/run.sh`, `docker-bake.hcl`, `docker-compose.yml`, root `package.json`.
- `browser-extension/package.json`, `browser-extension/wxt.config.ts`, `browser-extension/wdio.conf.ts`, `browser-extension/vitest.config.ts`, `browser-extension/vitest.real-backend.config.ts`, `browser-extension/vitest.store.config.ts`.
- `frontend/src/lib/pwa.ts`, `frontend/public/sw.js` (preservation review only).
- `.github/workflows/docker-publish.yml`, `.github/workflows/ghcr.yml`, `.github/workflows/nightly.yml`.
- `frontend/tests/repo/delivery-config.test.ts`; `frontend/tests/e2e-real/delivery/harness.html`.

Its targeted reads covered CI frontend/extension wiring; Deep CI frontend/browser/
extension jobs; container-publish build/smoke/export sections; tooling-experiments'
frontend job; native-download headline assertions; request artifact fallback;
branding/i18n/native-dialog moved-path checks; and main/PDF/Query import sites.
Unreviewed workflow jobs, specialty configs, package implementations, transitive
dependencies, live providers and physical browsers were explicitly excluded by that
record. Later package closure is accounted for separately above.

## Reproduction and checkpoint checks

Run as the repository's Linux `local` user. Enumerate both immutable trees with
`git ls-tree -r <sha>`; retain each path's blob ID. Union the declared roots,
explicit external integration paths and historical ledger paths. Add nonignored
untracked scoped files, compare `git diff HEAD --name-only`, and SHA-256 the dirty
bytes. `base_blob` and `head_blob` use `absent` only for a genuine missing path;
`base_to_head` is calculated from those IDs. `working_tree` and `working_sha256`
are a separate overlay. For M11, repeat against the final commit and reconcile
new/changed/deleted paths rather than overwriting the implementation-base evidence.

Checked here: unique path rows; all 762 historical values preserved; every base and
current scoped path accounted for; all 873 paths exist at an origin snapshot or the
working tree; dirty hashes match the captured bytes. No production source, test,
configuration, install, build or runtime suite was changed or executed for this
inventory. Concurrent edits after capture require refreshing the overlay hashes.

| #   | Behaviour (test name)             | Category | Precondition / input                                         | Observable outcome asserted                                                 | Tier                 | Status                                                                                                   |
| --- | --------------------------------- | -------- | ------------------------------------------------------------ | --------------------------------------------------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------- |
| 1   | records every scoped source path  | Happy    | Immutable base/current trees plus nonignored working overlay | No unexplained path omitted from ledger                                     | Repository inventory | ⏭️ N/A — documentation-only reconciliation; checked with Git/path metadata, no product behaviour changed |
| 2   | preserves historical review scope | Edge     | Existing 762 ledger entries plus explicit later read records | Historical statuses retained; inventory not promoted to fresh manual review | Repository inventory | ⏭️ N/A — evidence comparison, not a runtime test                                                         |
| 3   | separates uncommitted provenance  | Edge     | 12 tracked modifications and two untracked additions         | Dirty bytes have separate hashes/status; commit evidence remains immutable  | Repository inventory | ⏭️ N/A — snapshot metadata checked without executing application code                                    |

Subsequent M0 qualification: the coordinator independently verified all 762 old
row values, zero omissions in the four declared roots, unique rows and the 14
original overlay hashes. The later observer-only change to
`frontend/tests/performance/library-startup.spec.ts` is a fifteenth modified file
relative to this inventory snapshot, not a new path. Its exact source hash and
read/run evidence live in [measurement qualification](baseline-measurement.md).
The frozen inventory snapshot above is not silently relabelled as final M11 scope.

## M11 final-source reconciliation

Source snapshot: `4094518e66a83c4c4f990873cd23d95d13bbc29f` (927 scoped paths).
The historical columns and counts above remain immutable checkpoint evidence.
The six `final_*` columns identify current presence, Git blob, SHA-256 of inspected
working bytes, bounded review scope and evidence. Generated files, lock metadata
and binary assets receive provenance credit only. No installed dependency or build
output is counted as manually reviewed application source.

| Final evidence category | Paths |
|---|---:|
| historical-targeted-read; complete-file-gap | 14 |
| final-complete-read | 146 |
| historical-complete-read; revision-reconciliation-required | 61 |
| inventory-only; manual-file-gap | 285 |
| feature-qualification-reference; scope-is-record-specific | 387 |
| binary-or-generated-provenance-only | 9 |
| excluded-or-generated-provenance-only | 25 |

These categories are deliberately not a single “files reviewed” percentage. A
feature test reference does not prove a complete read, and a historical full read
does not certify changed bytes. The ledger exposes every remaining per-file gap.
Architecture owner coverage is broader than complete-file review and is anchored
to the contracts below.

| Owner / integration boundary | Current evidence |
|---|---|
| Library routes, filtering, membership and pagination | M2/M3 closure records; server browse contract; canonical URL and revision-checked page owner |
| Editing, source metadata, moves, bulk actions and builds | M4/M8 records; conditional edit base/receipt; explicit review and target reconciliation |
| History, rendering and protected assets | M5/M6 records; bounded per-entry snapshots; leased Blob cache and viewport admission |
| Authentication, transport, public shares | M1 record; session transport fences; real anonymous-share/private-navigation regression |
| Events, uploads, Jobs, Inbox and printers | M7 record; event/telemetry lifetimes; durable Job owner versus local transfer owner |
| Settings, accounts, sources, providers, backups and storage | M9 records; current settings-owner complete reads in final-review; Spoolman, cache and maintenance final caller corrections |
| Shared UI/domain packages | Recorded package full-read inventory, M10 consumer tests and enforced package export/dependency directions |
| Extension connection, pairing and provider capture | M10 extension matrices, immutable capture operation and receipt cleanup; provider parser algorithms retain partial manual-review coverage |
| Locale, PWA, build, deployment and browser configuration | M10 platform record, M11 EN/ES initial-render and production resource observations; current platform reads in final-review |
| Tests and contributor/tooling conventions | Behavior matrices, repository hygiene/dependency tests, current frontend skill and architecture conventions |

Remaining manual gaps include provider-specific parsing detail, some presentation
leaves, test assertions and developer-only media tooling. They are not labeled
complete from successful builds or tests. This work does not certify every existing
test assertion, all browser engines, accessibility, live external providers, physical
printers or decoded/GPU memory. Known historical Next.js scaffolder settings and
`.mise.toml` labels are recorded tooling debt; actual delivery remains static Vite.
