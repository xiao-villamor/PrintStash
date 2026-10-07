# M8 Search settings conditional editing

Status: locally qualified. With the Profile correction and earlier M8 evidence,
this closes the reopened M8 contracts. Final delivery/PR CI remains open.

## Diagnosis and intended correction

`api/v1/inference.py::update_settings` and `patch_settings` accept unconditional
writes. PATCH merges before the write. `modules/search/configuration.py::update`
commits through `audit.record` before reading its response and registers work
nudges after that commit. `SettingsForm` and `AiSearchSetup` both submit full
settings through `useSearchCommands().settings` without a captured editing base.

Give Search settings an independent persisted version, atomic conditional claim,
and response captured inside the accepted transaction. PATCH must merge after
claiming against the refreshed row. Endpoint configuration and generation/job
progress retain their independent owners. Unversioned legacy writes remain
explicitly unprotected during additive compatibility but invalidate opted-in
editors; both first-party settings writers must opt in before acceptance.

The advanced form retains the original draft/base, supports explicit authorized
review, and requires a revised save or adoption. Revised replacement merges only
deliberate changes onto reviewed settings. Setup must stop before subsequent
work on conflict or uncertain save; review never automatically retries it.

Dependencies: M1 query/session ownership, M4 editing contracts, M7 recovery.
Rollback: revert Search client opt-in and owner changes together, retaining the
additive schema. Such a rollback removes conflict protection. No generic form
framework or new runtime is needed.

## Behaviour coverage matrix (current assessment; created before tests)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| S1 | exposes one editing snapshot through both settings routes | Happy | Authorized GET aliases | Same settings, epoch/version and ETag | Integration | ✅ `backend/tests/integration/api/v1/test_inference.py::TestConditionalSearchSettings::test_exposes_one_editing_snapshot_through_both_settings_routes` |
| S2 | accepts a conditional settings write | Happy | Current base; PUT/PATCH | Saved settings and advancing matching ETag | Integration | ✅ `backend/tests/integration/api/v1/test_inference.py::TestConditionalSearchSettings::test_accepts_a_conditional_settings_write` |
| S3 | rejects a stale settings write | Error | Two writes from one read; PUT/PATCH | 412; first settings survive | Integration | ✅ `backend/tests/integration/api/v1/test_inference.py::TestConditionalSearchSettings::test_rejects_a_stale_settings_write` |
| S4 | requires the opted-in editing precondition | Error | Contract header without If-Match | 428; settings unchanged | Integration | ✅ `backend/tests/integration/api/v1/test_inference.py::TestConditionalSearchSettings::test_requires_the_opted_in_editing_precondition` |
| S5 | rejects malformed editing preconditions | Error | Wrong resource/epoch, malformed or overflowing version, unknown contract | 400/412 without writes | Integration | ✅ `backend/tests/integration/api/v1/test_inference.py::TestConditionalSearchSettings::test_rejects_malformed_editing_preconditions` |
| S6 | legacy writes invalidate an open editor | Edge | Unversioned write after protected read | Legacy accepted; protected stale save refused | Integration | ✅ `backend/tests/integration/api/v1/test_inference.py::TestConditionalSearchSettings::test_legacy_writes_invalidate_an_open_editor` |
| S7 | rejects a restored-database editing base | Error | Same counter in another history | 412; restored settings survive | Integration | ✅ `backend/tests/integration/modules/search/test_settings_edits.py::TestClaim::test_a_restored_database_rejects_a_prior_incarnation` |
| S8 | preserves independent settings changes | Edge | Vault setting or background bookkeeping changes | Search editing base remains usable | Integration | ✅ `backend/tests/integration/modules/search/test_settings_edits.py::TestClaim::test_independent_settings_do_not_invalidate_search_edits` |
| S9 | invalidates drafts after direct settings changes | Edge | Database writer changes Search settings JSON | Previous editing base rejected | Integration | ✅ `backend/tests/integration/modules/search/test_settings_edits.py::TestClaim::test_a_legacy_writer_invalidates_an_earlier_conditional_base` |
| S10 | allows only one concurrent conditional writer | Edge | Separate sessions use same base; SQLite/PostgreSQL | One accepted, one conflict | Integration | ✅ `backend/tests/integration/modules/search/test_settings_edits.py::TestClaim::test_only_one_editor_can_commit_from_a_shared_base` |
| S11 | rechecks current editing authority | Error | Admin disabled/demoted/session revoked | No write; forbidden | Integration | ✅ `backend/tests/integration/modules/search/test_settings_edits.py::TestClaim::test_revoked_administrator_authority_rejects_the_edit` |
| S12 | rolls back rejected configuration | Error | Invalid endpoint/consent/sparse settings after claim | Original settings and base remain usable | Integration | ✅ `backend/tests/integration/api/v1/test_inference.py::TestConditionalSearchSettings::test_rolls_back_a_rejected_configuration` |
| S13 | returns the accepted transaction receipt | Edge | Later write before earlier response | Receipt describes its own accepted write | Integration | ✅ `backend/tests/integration/modules/search/test_configuration.py::TestUpdate::test_returns_the_accepted_transaction_receipt` |
| S14 | merges a patch against locked settings | Edge | Legacy partial writers overlap | Untouched settings preserved | Integration | ✅ `backend/tests/integration/modules/search/test_settings_edits.py::TestClaim::test_legacy_partial_edits_preserve_concurrent_changes` |
| S15 | upgrades existing search configuration | Edge | Prior schema has settings and triggers | Values preserved; new contract works; downgrade/reupgrade works | Integration | ✅ `backend/tests/integration/db/migrations/test_search_edit_version.py::TestSearchEditMigration` (upgrade/round-trip) and `TestOfflineSearchEditMigration` |
| S16 | sends the captured editing base from every settings writer | Happy | Advanced form and guided setup submit | Conditional headers use displayed base | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::keeps the draft editing base across a refetch; frontend/src/components/__tests__/ai-search-setup.test.tsx::requires explicit download consent` |
| S17 | preserves the advanced draft on conflict | Error | Refetch or 412 while editing | Draft retained; no automatic retry | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::preserves the advanced draft on conflict` |
| S18 | saves deliberate edits after review | Happy | Latest settings differ from original base | Explicit revised save preserves unrelated changes | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::saves deliberate edits after review` |
| S19 | requires review after an uncertain save | Error | Response lost | No blind resend; authorized current state review available | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::requires review after an uncertain save` |
| S20 | retires inaccessible review data | Error | Review denied or session changed | Private values and late side effects cannot cross authority | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::retires inaccessible review data after %s; frontend/src/lib/queries/__tests__/search.test.tsx::never dispatches a retired gesture; discards an acknowledgement after delayed publication retires` |
| S21 | preserves native validation on revised saves | Error | Invalid numeric/timezone draft | No write until valid | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::preserves native validation on revised saves` |
| S22 | retains a newer accepted query receipt | Edge | Older mutation settles after newer settings | Cache retains newer settings | Frontend unit | ✅ `frontend/src/lib/queries/__tests__/search.test.tsx::retains a newer accepted query receipt` |
| S23 | stops guided setup after a settings conflict | Error | Enable/download consent fails conditionally | No download/preparation; explicit recovery | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-setup.test.tsx::stops setup after a consent save returns %s` |
| S24 | resolves two administrator edits explicitly | Happy | Two browser editors alter settings | Conflict visible; draft retained; reviewed save preserves remote changes | Playwright | ✅ `frontend/tests/e2e-real/ai-search-settings.spec.ts::resolves competing browser edits through explicit review` |

| S25 | schedules reconciliation after an accepted settings commit | Happy | Settings become enabled | Engine receives Search passes; saved intent is visible | Integration | ✅ `backend/tests/integration/modules/search/test_configuration.py::TestUpdate::test_schedules_reconciliation_after_the_settings_commit` |
| S26 | rejects unrelated-history acknowledgements | Error | Response epoch differs or counter does not advance | Client requires recovery rather than claiming success | Frontend unit | ✅ `frontend/src/lib/api/__tests__/search.test.ts::refuses a $label acknowledgement` |
| S27 | requires adoption after history replacement | Error | Authorized review belongs to another database history | Revised save disabled until explicit adoption | Frontend unit | ✅ `frontend/src/components/__tests__/ai-search-settings.test.tsx::requires adoption after the database history changes` |

## Validation

Backend and both first-party settings writers are implemented in the working
tree. Focused qualification is complete; no performance improvement is claimed.

- Initial regression run: 4 failed in 3.90 s. The missing-ETag failures correctly
  reproduced the missing contract. The other two tests initially used the wrong
  opt-in header (`X-Edit-Contract`); corrected to the existing public
  `X-PrintStash-Edit-Contract` before claiming precondition coverage.
- Router suite after correction: 69 passed in 12.98 s.
- Expanded conditional API cases: 24 passed in 7.13 s.
- Atomic owner/trigger cases on SQLite and PostgreSQL: 24 passed in 67.93 s.
- Accepted-receipt/current-state PATCH tests plus OpenAPI snapshot: 3 passed in
  5.79 s. Reviewed snapshot diff contains only the two additive editing fields
  and conditional request headers on PUT/PATCH.
- Scoped Ruff and configured Pyright: passed (0 type errors).
- Schema migration was autogenerated against an isolated database at
  `09232f601ed9`; it adds only `system_config.search_edit_version`. Trigger
  installation/removal was added explicitly. Native column removal preserves
  existing singleton triggers. Migration validation: 6 passed in 113.48 s, including both supported databases and offline SQL.

- Legacy partial-write overlap: 2 passed in 52.37 s (SQLite/PostgreSQL).
- Commit-triggered Search reconciliation: 1 passed in 2.80 s.
- Model versus migration-chain schema parity/autogenerate gate: 5 passed in
  184.08 s. Only this relevant schema file was run; not a full backend suite.
- Frontend affected API/query/form/setup files: 106 passed in 11.23 s.
- Real browser: competing editors passed in 6.0 s. The old focus-triggered
  freshness case timed out: headless Chromium did not produce the required
  visibility/refetch transition. The same retained-draft/read contract now uses
  actual offline/reconnect browser state, with a bounded response wait. That
  case passed in 9.3 s (47.6 s including isolated server setup). This does not
  claim new evidence for focus events specifically.
- Frontend format (773 files), warnings-denied lint, app/UI/domain typecheck and
  production build passed. Vite build: 2.66 s; existing locale-shell and large
  chunk notices remain. Build duration is not a comparable performance measure.
- Scoped backend Ruff/format: 13 files passed. Existing Search test callers were
  updated from actor ids to actor snapshots for authority rechecks; affected
  caption/expansion/generation coverage: 62 passed in 53.22 s.

Frontend failure history: the retained-draft regression failed first because no
review action existed. Later failures were an obsolete setup assertion expecting
immediate retry after 503, a wrong semantic-weight label in a new test, and lint
requiring an explained key assertion/removal of conditional test assertions.
Those were corrected before the passing focused runs. Receipt fixtures now
advance versions rather than claiming a successful unchanged acknowledgement.

## Ownership and completion limits

`settings_edits.py` owns atomic comparison, current actor authority and Search
ETags. `configuration.py` owns validation, locked partial merging, commit and its
receipt. Its singleton lock shares the existing configuration coordination path.
The JSON-field trigger invalidates old editors for legacy/direct writes while
excluding unrelated singleton fields. Frontend `useSearchCommands` owns reads,
commands, session fences and receipt publication. Components own only drafts,
explicit review snapshots and guided gestures; adopted setup values are discarded
after the next accepted command. `SearchSettingsSnapshot` displays the same
non-secret review values in both flows.

Correctness is supported by the matrix. Maintainability gains are the single
Search command/transaction owners and explicit editing boundaries, not file size.
No performance improvement, full backend coverage, final PR CI or global plan
completion is claimed. Legacy unversioned writers remain unprotected as documented
in the additive compatibility contract. No new framework/server/runtime was added.

The first isolated schema preparation via the CLI failed because its default
`/data` path was not writable. Preparation then used `run_migrations` with an
explicit temporary SQLite URL; no application data was touched.

