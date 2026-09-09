# Vault migration UI verification

The migration Settings entry replaces direct location changes on a configured
Vault. Existing credentials remain editable. Provider fields come from the
capability catalogue and candidate secrets are write-only. The server owns the
plan, copy worker, pause, recovery, activation and cleanup decisions; closing a
page never changes a migration's state.

## Requirement traceability

| Requirement | UI surface | Verification |
| --- | --- | --- |
| Phase 3: configured Vault location changes use migration | Storage configuration migration entry; current credentials only | Configured Vault migration entry tests |
| Phase 3: candidate configuration with write-only secrets | Existing provider picker and typed fields; candidate values cleared after preflight | Destination preflight tests; provider picker contract tests |
| Phase 3: preflight findings, capacity and owned totals | Plan summary, capacity resources/unknown values, Quick audit, resource-kind totals, blocking errors | Migration safeguards; persisted evidence; resource projection tests |
| Phase 3: operational policy | Source authority/suspensions copy, retention, concurrency and bandwidth fields | Policy submission/minimum tests; start confirmation |
| Phase 3: explicit identity/backup confirmation | Source/destination identities, verified preflight evidence, start confirmation | Reviewed plan request; real browser start |
| Phase 3/10: safe durable progress | Timeline, copied/verified/reused/failed bytes and objects, delta, throughput, activity, errors | Persisted evidence, report failure, worker polling tests |
| Phase 3/10: operator controls | Pause, resume, retry, recover, discard; recovery blocks automatic continuation | Migration execution and safeguards tests |
| Phase 3/completion: authority, maintenance and rollback | Source authority while copying, final mutation freeze, destination active state, reverse-migration caveat | Final write pause, uncertain cutover, real browser download |
| Phase 3/completion: audit and retained-source handling | Quick result, Full audit, grace deadline, fresh backup, explicit cleanup or retain/manual choice | Completion evidence tests; real browser Full audit |
| Phase 10: report and notifications | Safe JSON download, notification preference, event count | API report artifact; migration notification checkbox; real browser download |
| Completion: restart, online delta, active Artifact reads | Reloadable migration entry | Isolated real-backend migration Playwright scenario |

## Behaviour matrix

Test paths below are relative to `frontend/`. `panel` means
`src/components/__tests__/vault-migration-panel.test.tsx`; `api` means
`src/lib/api/__tests__/vault-migration.test.ts`. Each named case asserts a visible
outcome or the public HTTP request/returned artifact. Backend ownership,
provider-contract and failpoint matrices are maintained with the migration owner.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | requires a backup before preflight | Edge | No chosen backup | Preflight disabled; verification requirement visible | Frontend unit | ✅ panel::requires a backup before preflight |
| 2 | confirms the final write pause | Happy | Ready run | Confirmation explains paused writes | Frontend unit | ✅ panel::confirms the final write pause |
| 3 | requires recovery before resuming a stopped copy | Error | Recovery-required copy | Recover shown; Resume absent | Frontend unit | ✅ panel::requires recovery before resuming a stopped copy |
| 4 | renders unknown remote capacity | Edge | Unknown quota warning | Unknown free capacity explained | Frontend unit | ✅ panel::renders unknown remote capacity |
| 5 | blocks an expired plan | Edge | Past expiry | Start disabled | Frontend unit | ✅ panel::blocks an expired plan |
| 6 | retains the source throughout its grace period | Edge | Future grace; selected backup | Cleanup disabled; no automatic deletion | Frontend unit | ✅ panel::retains the source throughout its grace period |
| 7 | keeps candidate bytes when discard is cancelled | Edge | Cancel discard dialog | No mutation request | Frontend unit | ✅ panel::keeps candidate bytes when discard is cancelled |
| 8 | reports retained cleanup findings | Error | Partial cleanup | Retained-object findings visible | Frontend unit | ✅ panel::reports retained cleanup findings |
| 9 | rechecks state after an uncertain cutover response | Error | Cutover conflict; saved recovery state | Recovery shown; no false success | Frontend unit | ✅ panel::rechecks state after an uncertain cutover response |
| 10 | resumes a bounded copy to readiness | Happy | Paused run | Resume response enables cutover | Frontend unit | ✅ panel::resumes a bounded copy to readiness |
| 11 | preflights the selected destination | Happy | Valid fields and exact backup source | Exact destination/backup/policy request | Frontend unit | ✅ panel::preflights the selected destination |
| 12 | explains preflight refusal | Error | Capacity, collision, backup refusal | Actionable localized failure | Frontend unit | ✅ panel::explains preflight refusal $code |
| 13 | requests a durable pause | Happy | Copying | Pause POST yields Resume control | Frontend unit | ✅ panel::requests a durable pause |
| 14 | starts the reviewed plan before copying | Happy | Confirmed plan | Exact plan digest submitted | Frontend unit | ✅ panel::starts the reviewed plan before copying |
| 15 | recovers without starting another copy implicitly | Edge | Interrupted paused run | Recovery only; explicit Resume remains | Frontend unit | ✅ panel::recovers without starting another copy implicitly |
| 16 | shows completed source cleanup | Happy | Successful cleanup response | Completion visible | Frontend unit | ✅ panel::shows completed source cleanup |
| 17 | loads recoverable runs when backup listing fails | Error | Maintenance rejects backups | Recovery remains reachable | Frontend unit | ✅ panel::loads recoverable runs when backup listing fails |
| 18 | shows a failed status request | Error | Migration listing unavailable | Failure visible | Frontend unit | ✅ panel::shows a failed status request |
| 19 | prevents incomplete destination submission | Edge | Required roots empty | Validation; no POST | Frontend unit | ✅ panel::prevents incomplete destination submission |
| 20 | refreshes a completed operation after a response was lost | Edge | Saved active state | Refresh displays success | Frontend unit | ✅ panel::refreshes a completed operation after a response was lost |
| 21 | submits an explicit migration policy | Happy | Chosen limits | Exact limits sent | Frontend unit | ✅ panel::submits an explicit migration policy |
| 22 | rejects bandwidth below the supported minimum before requesting a plan | Edge | 100 bytes/second | Minimum explained; no POST | Frontend unit | ✅ panel::rejects bandwidth below the supported minimum before requesting a plan |
| 23 | shows persisted migration evidence | Happy | Saved progress/identities/capacity/audit/timeline | Exact evidence displayed | Frontend unit | ✅ panel::shows persisted migration evidence |
| 24 | keeps cleanup disabled without successful Full audit even after grace | Error | Critical audit finding | Cleanup disabled | Frontend unit | ✅ panel::keeps cleanup disabled without successful Full audit even after grace |
| 25 | shows the requested Full audit result | Happy | Active destination | Full result; source remains retained | Frontend unit | ✅ panel::shows the requested Full audit result |
| 26 | retains source indefinitely through an explicit action | Happy | Retain chosen | Retained outcome; credentials retained request | Frontend unit | ✅ panel::retains source indefinitely through an explicit action |
| 27 | requires confirmation before removing credentials for manual cleanup | Edge | Manual cleanup chosen | No mutation until confirm; cleanup controls removed after result | Frontend unit | ✅ panel::requires confirmation before removing credentials for manual cleanup |
| 28 | retries a reported recoverable object failure | Error | Retryable object failure | Safe error; explicit retry POST | Frontend unit | ✅ panel::retries a reported recoverable object failure |
| 29 | polls durable worker progress without issuing copy or pause mutations | Happy | Copy worker finishes | GET updates readiness; unmount sends no POST | Frontend unit | ✅ panel::polls durable worker progress without issuing copy or pause mutations |
| 30 | shows owned byte totals by resource kind | Happy | Safe report census | Exact kind counts/bytes visible | Frontend unit | ✅ panel::shows owned byte totals by resource kind |
| 31 | permits cleanup after a successful Full audit with noncritical warnings | Edge | Completed audit, no critical, grace passed | Cleanup enabled with fresh backup | Frontend unit | ✅ panel::permits cleanup after a successful Full audit with noncritical warnings |
| 32 | configured Vault uses migration for location changes | Happy | Managed current local Vault | Migration entry; direct location/save controls absent | Frontend unit | ✅ src/components/__tests__/storage-config-card.test.tsx::routes location changes through the verified migration flow |
| 33 | keeps current credentials editable without exposing location fields | Happy | Current S3 Vault | Secret input visible; bucket input absent | Frontend unit | ✅ src/components/__tests__/storage-config-card.test.tsx::keeps current credentials editable without exposing location fields |
| 34 | notification preference includes Vault migration | Happy | Notification draft | Migration checkbox visible | Frontend unit | ✅ src/components/__tests__/notifications-panel.test.tsx::offers Vault migration events on configured channels |
| 35 | reads current migration history without cached progress | Edge | Repeated reads | Two fresh GET requests | Frontend unit | ✅ api::reads current migration history without cached progress |
| 36 | encodes the run identity | Edge | Slash in ID | Encoded URL | Frontend unit | ✅ api::encodes the run identity |
| 37 | submits the exact backup source for preflight | Happy | Exact backup source | Public request preserves source | Frontend unit | ✅ api::submits the exact backup source for preflight |
| 38 | starts the exact reviewed plan | Happy | Plan digest | Exact start request | Frontend unit | ✅ api::starts the exact reviewed plan |
| 39 | requests each state transition explicitly | Happy | Advance/cutover/recover/pause/resume/retry/audit | Exact operation URL and empty body | Frontend unit | ✅ api::requests $action explicitly |
| 40 | selects retained-source cleanup with a fresh backup | Happy | Source cleanup | Run confirmation, fresh backup and source flag | Frontend unit | ✅ api::selects retained-source cleanup with a fresh backup |
| 41 | selects candidate discard without authorizing source cleanup | Edge | Discard | Source flag false | Frontend unit | ✅ api::selects candidate discard without authorizing source cleanup |
| 42 | retains source bytes indefinitely | Happy | Retain | Credentials removal false | Frontend unit | ✅ api::retains source bytes indefinitely |
| 43 | requires an explicit request to remove retained credentials | Edge | Manual cleanup | Credentials removal true | Frontend unit | ✅ api::requires an explicit request to remove retained credentials |
| 44 | reads the safe migration report | Happy | Report request | Exact encoded report URL | Frontend unit | ✅ api::reads the safe migration report |
| 45 | downloads a safe report as a standalone JSON artifact | Happy | Report response | JSON Blob, filename and object URL cleanup | Frontend unit | ✅ api::downloads a safe report as a standalone JSON artifact |
| 46 | retains capacity denial details | Error | HTTP507 | Status and business error preserved | Frontend unit | ✅ api::retains capacity denial details |
| 47 | verified migration resumes after restart with online delta Artifacts | Happy | Real baseline, new ingestion, API restart | Recovery, activation, byte-exact downloads, Full audit, JSON download | Playwright | ✅ tests/e2e-real/migration/vault-migration.spec.ts |
| 48 | offers recovery instructions during maintenance | Error | Recovery-required run | Recovery docs available | Frontend unit | ✅ panel::offers recovery instructions during maintenance |
| 49 | opens another persisted run from history | Happy | Two saved runs | Selected run state shown | Frontend unit | ✅ panel::opens another persisted run from history |
| 50 | prepares a fresh destination after completed cleanup | Happy | Cleaned run | Fresh blank fields; backup required | Frontend unit | ✅ panel::prepares a fresh destination after completed cleanup |
| 51 | discards only the confirmed candidate | Happy | Confirmed discard | Source flag false; discarded result | Frontend unit | ✅ panel::discards only the confirmed candidate |
| 52 | surfaces report download failure without losing saved progress | Error | Report unavailable | Error visible; saved plan remains | Frontend unit | ✅ panel::surfaces report download failure without losing saved progress |
| 53 | hides the migration entry without an administrator session | Edge | No administrator session | No dead migration control | Frontend unit | ✅ src/components/__tests__/storage-config-card.test.tsx::hides the migration entry without an administrator session |
| 54 | sends fresh candidate credentials without displaying them in the checked plan | Happy | Remote candidate with fresh secret | Secret request; safe destination summary without secret | Frontend unit | ✅ panel::sends fresh candidate credentials without displaying them in the checked plan |
| 55 | blocks destinations unavailable for Vault use | Edge | Unavailable provider | Preflight disabled | Frontend unit | ✅ panel::blocks destinations unavailable for Vault use |
| 56 | links the slice to the mesh it came from | Edge | Mesh plus G-code; earlier test upload fully drained before fetch replacement | G-code request carries the mesh hash without cross-test background work | Frontend unit | ✅ src/components/__tests__/upload-modal/uploading.test.tsx::links the slice to the mesh it came from |
| 57 | keeps the active provider tier visible when location changes require migration | Regression | Configured guarded Vault with migration-managed location | Support, expected tier and actual active tier remain visible | Frontend unit | ✅ src/components/__tests__/storage-config-card.test.tsx |
| 58 | keeps guarded deletion consequences visible when location changes require migration | Regression | Configured guarded Vault with an empty catalogue consequences list | Tier note, catalog retention and unavailable physical deletion remain visible | Frontend unit | ✅ src/components/__tests__/storage-config-card.test.tsx |

## Execution evidence

CI run 34390353141 passed the complete **Frontend** job, including the corrected
two-sided coverage gate. Its real-browser WebDAV workflow found missing provider
status in migration-managed Settings. The shared summary is now restored without
exposing location editing: **38 component tests passed**, including two new
regressions observed red before the fix. The original real WebDAV/restart/GC
preview scenario passed (**1 passed, 43.1 seconds**); format, lint and types passed.
The full CI rerun after this final correction is pending.

PR #164's initial CI app coverage run passed **2,066 tests**; domain and UI
coverage runs also passed. The two-sided gate required raising app statements
from 82.0 to 82.4 (measured 82.67%) and component branches from 74.3 to 74.7
(measured 74.91%). No floor was lowered. The rerun verifies these new floors.

The instrumented full app run reported **2,065 passed, 1 failed**: a previous
test's background upload could reach the next test's request recorder. Upload
tests now drain their task-centre work before replacing fetch. The complete
upload file passed under coverage after this correction: **18 passed**. The
same file also passed shuffled with seed 104: **18 passed**. Frontend lint,
format and type checks passed after the lifecycle correction. The
full corrected gate subsequently passed in CI run 34390353141.

Real Chromium migration against an isolated supervised backend: **1 passed**,
including an actual process restart, online delta ingestion, exact downloads,
Full audit and report download. Full frontend tests initially reported
**2,064 passed / 2 failed**; the affected Settings expectation was updated and
both failing files were rerun successfully (**137 passed**). Shared UI and domain
suites passed (**198** and **60** tests). Format, lint and type checking passed.
The aggregate frontend coverage gate subsequently passed in CI run 34390353141.

Earlier focused UI/API run: 115 passed; one existing storage-configuration case exceeded
its five-second timeout while the host was contended. Its full focused file was
then run with a 20-second command override: **25 passed**. The timeout override
was not committed. Additional focused coverage and the real browser run are
recorded below when complete. Aggregate gates belong to the integration run.

Focused migration UI/API coverage: **51 passed**, 87.8% statements, 79.84% branches, 87.95% functions, 91.46% lines. Additional recovery-navigation and administrator-entry checks plus repository test hygiene: **68 passed**. Static design detector: **zero findings** for the migration panel and storage configuration card.
