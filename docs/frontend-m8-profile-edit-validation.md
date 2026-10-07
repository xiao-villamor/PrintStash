# M8 — conditional preset editing

Status: Profile contract qualified locally, after M7 acceptance `c50f5fc7`. The existing Profile Query owners
remain; this increment corrects unversioned autosave/blur writes. Filament and
printer presets share the explicit preset-edit contract, with Spoolman authority
specific to filament. Search configuration follows this increment.

The form captures its base at first edit. Only deliberately changed fields are
submitted. Conflicts and uncertain receipts stop blur saves until an authorized
read and explicit revised save or adoption. Deletion/recreation and restored
history must not authorize an obsolete draft. Legacy writes remain accepted but
advance the version. No unrelated routing, styling or framework change.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| P1 | rejects a competing preset edit | Error | Two editors share a filament/printer base | First accepted; second 412; first fields retained | Integration | ✅ `backend/tests/integration/api/v1/test_filaments.py / test_printer_profiles.py::test_rejects_a_competing_preset_edit` |
| P2 | requires the advertised preset precondition | Error | conditional-v1 without If-Match | 428; persisted fields unchanged | Integration | ✅ `backend/tests/integration/api/v1/test_filaments.py / test_printer_profiles.py::test_requires_the_advertised_preset_precondition` |
| P3 | invalidates preset bases after legacy writes | Edge | Legacy PATCH before conditional save | Legacy write advances version; stale save refused | Integration | ✅ `backend/tests/integration/modules/printing/test_profile_edits.py::TestClaim::test_a_legacy_writer_invalidates_an_earlier_conditional_base` |
| P4 | rejects malformed preset bases | Error | Wrong kind/id/version/contract | No settings write | Integration | ✅ `backend/tests/integration/api/v1/test_filaments.py / test_printer_profiles.py::test_rejects_malformed_preset_bases` |
| P5 | rejects a restored preset history | Error | Database epoch changes | Old draft refused | Integration | ✅ `backend/tests/integration/modules/printing/test_profile_edits.py::TestClaim::test_a_restored_database_rejects_a_prior_incarnation` |
| P6 | rejects a recreated preset identity | Error | Deleted row ID reused by new profile | Prior editing base refused | Integration | ✅ `backend/tests/integration/modules/printing/test_profile_edits.py::TestClaim::test_rejects_a_recreated_preset_identity` |
| P7 | rolls back an invalid preset edit | Error | Duplicate name after claim | Original base still usable | Integration | ✅ `backend/tests/integration/api/v1/test_filaments.py / test_printer_profiles.py::test_rolls_back_a_duplicate_preset_name` |
| P8 | rechecks current preset editor authority | Error | Administrator revoked after initial lookup | No accepted write | Integration | ✅ `backend/tests/integration/modules/printing/test_profile_edits.py::TestClaim::test_revoked_administrator_authority_rejects_the_edit` |
| P9 | refuses a filament adopted by Spoolman | Error | Sync links an edited local preset | No local override | Integration | ✅ `backend/tests/integration/modules/printing/test_profile_edits.py::TestClaim::test_refuses_a_filament_adopted_by_spoolman` |
| P10 | serializes simultaneous preset writes | Edge | SQLite/PostgreSQL writers share base | Exactly one accepted transaction | Integration | ✅ `backend/tests/integration/modules/printing/test_profile_edits.py::TestClaim::test_only_one_editor_can_commit_from_a_shared_base` |
| P11 | preserves profile rows across migration | Edge | Existing linked/local profiles | Fields, indexes and edit triggers survive upgrade/downgrade | Integration | ✅ `backend/tests/integration/db/migrations/test_profile_edit_versions.py::TestProfileEditMigration` |
| P12 | preserves a conflicting profile draft | Error | Blur PATCH returns 412 | Local fields remain; further blur cannot submit | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::preserves a conflicting %s profile draft for explicit review` |
| P13 | sends the original profile base after background refresh | Edge | Catalog refreshes while a dirty row is open | PATCH retains captured base; no silent rebase | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::sends the original %s base after background refresh` |
| P14 | reviews an uncertain profile save | Error | Save loses response | Read required before retry; explicit adoption avoids duplicate PATCH | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::adopts an uncertain %s save without another write` |
| P15 | retires profile review with its session | Edge | Session replaced while review/save pending | No old-session publication or draft reuse | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::retires a pending %s review with its session` |
| P16 | preserves newer observed profile receipts | Edge | Newer catalog read precedes delayed acknowledgement | Newer row retained | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::retains a newer %s row across a delayed acknowledgement` |
| P17 | validates revised profile fields | Error | Invalid cost/nozzle/name | No PATCH; local feedback retained | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::validates a revised %s numeric draft` |
| P18 | retires an inaccessible profile review | Error | Authorized review denies/deletes/links profile | No revised save; recovery remains explicit | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::retires a $failure $kind profile review; refuses a reviewed filament that now belongs to Spoolman` |
| P19 | rejects invalid profile acknowledgements | Error | Wrong identity/history/non-advancing version | No confirmed success | Frontend unit | ✅ `frontend/src/lib/api/__tests__/filaments.test.ts / printer-profiles.test.ts::rejects an acknowledgement with $label` |
| P20 | completes profile conflict recovery | Happy | Two real editors change different fields | Explicit recovery persists both after reload | Playwright real | ✅ `frontend/tests/e2e-real/profiles.spec.ts::two filament/printer preset editors recover a competing save` |
| P21 | clears obsolete validation after adopting a profile | Edge | Invalid local cost then explicit adoption | Canonical value without stale validation feedback | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::clears obsolete filament validation after adopting current values` |
| P22 | requires adoption after preset history changes | Error | Review returns another row/database identity | Revised save unavailable with an explanation; adoption starts a fresh draft | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::requires adoption of a different %s history` |
| P23 | keeps a pending profile locked after an earlier saved indicator expires | Edge | Second save waits beyond the first success timer | Fields stay locked; pending draft cannot be replaced | Frontend unit | ✅ `frontend/src/components/__tests__/filament-profiles-card.test.tsx::keeps a pending profile locked after an earlier saved indicator expires` |


## Contract and inspection

The two public reads now include `edit_epoch` and `edit_version`. Every first-party
PATCH captures that pair at the first edit and sends `conditional-v1` plus the
kind-specific `If-Match`. Advertised missing bases return 428; stale or malformed
bases return 412; an unknown contract returns 400. Existing unversioned API clients
remain explicitly unprotected but invalidate captured conditional bases.

`modules/printing/profile_edits.py` owns the atomic claim, current administrator
check and Spoolman write prohibition. The row has a stable internal UUID because
SQLite can reuse a hard-deleted integer ID. The public epoch binds that identity,
kind and ID to the database history. A restored history or recreated row cannot
inherit an old draft. Settings changes by direct/legacy/background writers advance
the version through the immutable database trigger contract. Timestamps and derived
usage counts do not participate. Receipts are read while the write lock is held.

The autogenerated migration `09232f601ed9` detected exactly four added columns.
Its reviewed backfill applies a historical identity bound to each existing kind/ID;
the temporary default is removed, so new rows must provide their generated UUID.
Frozen table shapes permit offline SQLite rendering of that default-removal rebuild.
Upgrade/downgrade, indexes and model/chain parity were verified on both databases.

The existing Profile Query owner now carries required editing bases, authorized
review reads and publication that preserves newer observed rows. The component
retains only local fields, original bases, review snapshots and feedback. It sends
only deliberately changed fields. Conflict or an uncertain acknowledgement stops
blur saves until explicit review. Adoption clears obsolete validation. A changed
history requires adoption; linked Spoolman presets cannot be revised locally.

Inspected complete current API clients, query owner, component draft/save/review
paths, backend routes/schema/model definitions, new claim/trigger contracts,
profile detection and Spoolman writers, matching test files and the real-browser
spec. All first-party preset construction uses the ORM's UUID default. This is an
incremental owner review, not a new comprehensive review of the entire frontend.

## Actual validation

- Initial API regressions: **2 failed** per preset kind before implementation
  (missing editing fields and accepted advertised writes without a precondition).
- API CRUD/conditional selection: **58 passed**, 15.99s. Added malformed-header
  and duplicate-name rollback cases: **16 passed**, 11.20s.
- Atomic owner on SQLite/PostgreSQL, including simultaneous writers, rollback,
  revocation, restored/recreated identities and Spoolman adoption: **46 passed**,
  79.39s. Existing detection/Spoolman writers: **24 passed**, 5.29s.
- Migration and required schema/parity checks: **166 passed**, 424.79s. This was
  the only complete schema qualification for this increment; no backend full
  suite or coverage run is claimed.
- OpenAPI snapshot: **1 passed**, 11.80s; diff reviewed: only the two additive
  read bases and optional conditional headers on their PATCH operations.
- Configured Pyright plus the new claim module: zero errors. Scoped backend Ruff
  and formatting passed across the 16 affected Python files.
- First UI conflict cases: **2 failed** before implementation, then **2 passed**.
  Existing component/query/client selection: **65 passed**, 17.09s. Recovery
  additions: **54 passed**, 26.08s. Adoption feedback/history explanation then
  reproduced **3 failures** and passed the corrected 3-case selection.
- The previous saved-indicator timer unlocking a later pending row was reproduced
  separately (**1 failed**); the corrected seven-file query/component/client,
  translation and suite-hygiene gate passed **124 tests**, 26.21s. Final component
  verification after lint-directed equivalent payload construction: **58 passed**,
  27.06s. Original-base background refresh: **2 passed**, 6.46s. Final denied/deleted review variants: **4 passed**, 7.68s.
- Real backend/browser: **5 passed**, 1.8m including setup. Both concurrent-editor
  flows passed (8.6s each), alongside the three existing profile flows.
- Final frontend formatting, lint with warnings denied, application/workspace
  types and production build passed (4.83s build). Existing large-chunk warning
  remains. Earlier static failures (new DTO fixtures, explicit union narrowing,
  a missing localized brand, unknown error parameter and empty-object spreads)
  were corrected; they are not represented as a single uninterrupted green run.

No performance improvement or full-suite/coverage/PR-CI completion is claimed.
M8 remains open for Search configuration. Earlier M8 owner qualification is
preserved separately. There is no new runtime or deployment requirement.

## Removal and rollback

Removed unconditioned first-party profile PATCHes, whole-row blur payloads and
blind receipt replacement. No second profile cache or generic form/repository
layer was introduced. An older success timer no longer clears a pending save.
Rollback the client cutover and form recovery together; keep additive server
fields during mixed-client rollout. Downgrading removes the new contract and
cannot claim conflict protection. Final PR and required CI belong to M11.

After those test-only additions, lint, application/workspace types and formatting
were rerun successfully (772 formatted files). `git diff --check` passed.
