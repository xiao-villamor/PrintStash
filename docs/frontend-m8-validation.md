# M8 — remaining Library surfaces

> Closure correction (2026-10-07): reopened. The results below remain historical
> evidence for implemented behaviors, but do not prove complete plan acceptance.
> See [M5–M8 reassessment](frontend-milestone-reassessment.md) for the missing
> competing-edit contracts and corrected execution order. M7 has since satisfied
> its reopened contract; M8 is now active. The closure text below is historical.


Status: locally closed after M7 acceptance c4b43c25. Scope: Model/Multipart detail,
Documents, manufacturing Builds, Search/AI, Similarity, Profiles and Statistics.
M9 is next; M10–M11 remain pending. Preserve existing qualified owners and detailed matrices
in [feature ownership](frontend-feature-ownership-validation.md),
[Model detail](library-detail-state-validation.md), M4 editing and source recovery.

## Entry review and ordered work

Complete reads this pass: `pages/multipart-builds.tsx`, its page mirror,
`lib/api/multipart-builds.ts`, its wire mirror and `types/multipart-builds.ts`.
The Builds page still owns effect-based lists/detail/printer/Model copies and a
five-second interval. Its Result key includes server version/state, so a polling
update remounts the editor and can erase the entered result. Listing an old offset
can overwrite a newer filter, failures can display empty/loading success, and
accepted writes have no shared cache publication boundary. These are concrete
source findings; new behavioral reproductions precede fixes.

1. Qualify and migrate manufacturing at its actual state owners; retain its
   existing backend version/idempotency and printer routing contracts.
2. Reconcile the other preserved M8 owners against draft, denial, continuation,
   viewer cancellation and route acceptance; run their affected gates.
3. Update documentation and close M8 only after its own criteria pass.

## Manufacturing design before production changes

`features/library/builds.ts` will own list/detail query identities, cancellable
reads, visible polling and confirmed-write reconciliation. Reuse existing Model,
Multipart and printer Query owners rather than copying those catalogs. The page
owns filters/forms and navigation; a result draft captures its attempt version
and idempotency key, survives background changes and requires explicit review
before adopting a newer editing base. A successful confirmed result clears that
draft; failure retains it. No framework, generic repository or universal form
layer. Remove old read effects/interval/result copies with their consumer cutover.
Rollback the owner and consumer together, retaining session and stale-write fences.
Build versions are an existing independent backend contract; this increment does
not invent Model edit epochs for manufacturing.

## New behavior matrix before tests

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| B1 | keeps the selected build history after an older page resolves | Edge | Hold old filter GET; choose archived; late old response | Only current filter rows visible | Frontend unit | ✅ assertion qualified |
| B2 | distinguishes failed build history from empty history | Error | Initial GET503 | Retry/Refresh error; no empty-success copy | Frontend unit | ✅ assertion qualified |
| B3 | recovers an unavailable build on the same route | Error | Initial detail GET503 then recovery | Explicit Retry/Refresh shows detail without navigation | Frontend unit | ✅ assertion qualified |
| B4 | retires an abandoned build read | Edge | Held detail GET; unmount | HTTP signal aborted | Frontend unit | ✅ assertion qualified |
| B5 | removes a cached build after access is denied | Error | Loaded detail; current read403 | Private detail and commands absent | Frontend unit | ✅ assertion qualified |
| B6 | preserves a result draft across a changed attempt | Edge | Dirty result; background newer version | Entered units and captured base retained, explicit review offered | Frontend unit | ✅ assertion qualified |
| B7 | confirms a reviewed result against its new base | Happy | Changed attempt reviewed explicitly | Retained draft sent with reviewed version/new idempotency key | Frontend unit | ✅ assertion qualified |
| B8 | keeps a confirmed build after an obsolete read | Edge | Old detail GET held; mutation ACK | Confirmed state survives late response | Frontend unit | ✅ assertion qualified |
| B9 | rejects a retired manufacturing gesture | Edge | Account changes before old command | No mutation under replacement session | Frontend unit | ✅ assertion qualified |
| B10 | preserves a creation name while composition arrives | Edge | User types while composition GET pending | User-entered name survives resolved default | Frontend unit | ✅ assertion qualified |

Existing quantity/excess validation, exact API payloads, conditional conflicts,
printer permission gates, revision selection, archive/duplicate and real
replacement manufacturing lifecycle remain required regression coverage.
The old real lifecycle parses a Model link as a bare path and submits Multipart
composition without the now-required M4 editing precondition; its arrangement
needs canonical URL parsing and the actual conditional headers before execution.

## Receipt and lifetime acceptance additions (before tests)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| B11 | keeps confirmed progress when a later read is older | Edge | Confirm v2; subsequent successful GETv1 | Current progress and unavailable queue retained | Frontend unit | ✅ assertion qualified |
| B12 | rejects a receipt for another build | Error | Queue ACK identifies another aggregate | Visible failure; original detail remains | Frontend unit | ✅ assertion qualified |
| B13 | keeps a result receipt key after an uncertain response | Error | POST network refusal then explicit same-result retry | Same version/value/idempotency key; no automatic retry | Frontend unit | ✅ assertion qualified |
| B14 | removes denied build history from the current view | Error | Loaded list then current403 | No cached private history or empty-success copy | Frontend unit | ✅ assertion qualified |
| B15 | keeps a transiently unavailable build recoverable | Error | Loaded detail then503 | Existing detail retained with error and successful Refresh | Frontend unit | ✅ assertion qualified |
| B16 | reviews concurrent confirmation before preserving the replacement lifecycle | Edge | Actual second inspection while result draft dirty | Draft3 retained; explicit review then confirmed3; replacement1 completes both attempts | Real browser | ✅ `tests/e2e-real/multipart-builds.spec.ts::three usable legs leave one replacement with both attempts retained` |

## Preserved-owner reconciliation: read permissions

Current-source review found Statistics and both Profile catalogs continue rendering
cached authorized data after a confirmed403. This violates M8's read-permission
criterion despite earlier draft/retry qualification. Reproduce before changing
those consumers; retain cached data through transient503 and explicit retry.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| R1 | protects cached statistics during HTTP 403 | Error | Successful period then403 | No private cost/chart; retry remains | Frontend unit | ✅ assertion qualified |
| R2 | protects cached statistics during HTTP 503 | Error | Successful period then503 | Prior cost/chart retained; retry remains | Frontend unit | ✅ assertion qualified |
| R3 | hides a denied filament catalog | Error | Authorized row then403 | No private row or empty-success copy; Retry recovers | Frontend unit | ✅ assertion qualified |
| R4 | hides a denied printer catalog | Error | Authorized printer row then403 | No private row or empty-success copy | Frontend unit | ✅ assertion qualified |

| B17 | pauses manufacturing observation while hidden | Edge | Detail visible then focus hidden for two periods | No background read; focus restoration obtains current state | Frontend unit | ✅ assertion qualified |
| B18 | stops automatic reads after manufacturing access denial | Error | Detail refetch403 then two intervals | No repeated private read; explicit Refresh remains | Frontend unit | ✅ assertion qualified |

## Actual implementation and removal

`features/library/builds.ts` owns query identities, cancellable list/detail reads,
foreground observation, error-paused polling and receipt publication. Commands
capture the session and mounted lifetime, cancel obsolete reads before dispatch
and after the acknowledgement, validate aggregate identity/version, and preserve
newer known versions. Confirmed changes invalidate history and Fleet projections;
no automatic command retry. The API endpoints now use the transport without its
legacy invalidation bridge. The page uses canonical Model/Multipart/printer reads,
keeps forms local and captures selected values before asynchronous preparation.
Result drafts keep their original attempt version/key until explicit latest review;
uncertain-response retry retains the same idempotency key. Confirmed results clear
the draft. Review shows the current confirmed count beside retained input.

Removed: effect-owned Build list/detail/Model/printer arrays; independent interval;
version-keyed Result remounts; composition-name effect that overwrote typed input;
transport-owned Build write invalidation. Null Model selection is an explicit
inactive canonical option, never an invented Model0 request. Existing routing,
quantity/excess validation, archiving, duplicate history and permission rules stay.
Profile and Statistics consumers hide cached server data on authoritative denial,
keep transiently unavailable data recoverable and retain existing draft semantics.

## Preserved owners reconciled for M8

| Surface | Current owning contracts | Evidence retained / current review |
|---|---|---|
| Model detail | `features/library/model-detail.ts`, conditional editing, child publication | Model detail matrix plus M4 version/epoch and M5 return-navigation acceptance; this pass reviewed complete detail options/client composition, refresh/draft tests and child assertion groups |
| Multipart detail | `features/library/multipart.ts`, captured editing base and acknowledged aggregate | M4 conditional composition, provenance, choices and shared-Model real flow; current publication owner read in full |
| Documents | `lib/queries/documents.ts`, captured document editor and protected preview lifetime | Current owner read in full; page permission/recovery/preview/draft branches inspected; existing F1–F6 and document conditional-edit, unknown-outcome, gesture and protected-byte assertions retained |
| Search/AI | `lib/queries/search.ts`, URL intent, preferences and captured generation proposal | Current key/options/polling/cancellation/catalog signatures and page denial/continuation branches inspected; earlier A1–A20 complete review ledger retained; native inference artifact qualification remains separate |
| Similarity | `lib/queries/similarity.ts`, paged candidates/history and captured decisions | Current status/run/history/candidate keys, cancellation and foreground/terminal polling reviewed; comparison denial and failed continuation branches inspected; prior complete source ledger and two real flows retained |
| Captions | `lib/queries/captions.ts`, versioned local editor | Full query/command owner reviewed; tests cover version capture, explicit review, lost ACK and private disposal; earlier complete presentation review retained |
| Profiles / Statistics | Canonical catalog/period options; sparse local profile edits; browser widget preferences | Current options/permission and presentation branches reviewed, previous complete ledger retained; cached denial gaps R1–R4 now reproduced and covered |
| Builds | New feature owner, canonical secondary reads, captured result draft | Page/API/types/mirrors read completely before edit; B1–B18 exercise the migration and preserved real lifecycle |

This is a reconciliation of existing manual review plus named new inspection,
not a claim that every source was reread in this pass. The repository-wide ledger
and remaining source review belong to M10/M11. Existing Profiles and Search settings
have no backend conditional editing protocol: local draft preservation does not
promise detection of external last-writer-wins conflicts. No new protocol or
stateless/server runtime migration is introduced.

## Execution record

- Original Build page/API baseline:48/48,5.31s. Nine new failures reproduced while
  original40 page assertions passed; separate obsolete-read case also failed.
- First migration gate:54 passed/4 failed. A select event value was consumed after
  asynchronous cancellation; capture it before dispatch. Explicit Build staleTime0
  restores revalidation regardless of global/default cache policy. Next58/58,6.29s.
- Added receipt identity/monotonicity/idempotency, denial and recovery controls:
 68/68 across three matching mirrors,8.53s. A nonexistent hygiene selector matched
 no file in that invocation; actual `tests/repo/suite-hygiene.test.ts` was included
 in subsequent80- and87-test runs.
- Cached Profile/Statistics denial:3 genuine failures and one transient control
 pass,3.57s; corrected80/80 in five files,8.12s.
- Polling test setup initially enabled fake time after interval creation and
  assumed focus-triggered refetch despite the harness disabling it. Corrected
  setup captures timers from mount and observes the next period. Hidden control
  passed; repeated-denial reads genuinely failed(4 vs2). Error-paused policy then
  passed in the87/87 affected five-file gate,8.68s.
- Preserved-owner gate:538 passed/2 failed,43.97s. Model edit hit the existing5s
  deadline under concurrent checks; isolated whole Model index mirror passed.
  Model recovery selector matched both page and secondary Retry controls; scope
  it to the actual Model error alert. Both original failures remain recorded;
  no timeout, collector, coverage floor or assertion was removed.
- Real manufacturing with actual concurrent inspection, explicit review and
  replacement output:1/1,15.8s scenario,53.4s invocation.
- Format761files, lint, app/UI/domain types and production build passed before
  the final polling/selector additions; final qualification follows below.

Correctness is supported by the above observable contracts. Maintainability:
Builds no longer coordinates duplicate remote copies/timers and has a single
receipt owner; the existing domain-specific editors stay explicit. No M8 timing,
CPU, memory or bundle improvement is claimed. M6's measured startup comparison
is independent. Rollback would revert each consumer and owner together; retain
regressions and never restore known session, denial or stale-write defects.

### Creation route review (before regression additions)

The final consumer review requires the creation form to retire with its selected
Multipart route, including a pending creation acknowledgement. Cached denied
composition data must also stop supplying the creation form. Both are M8 route
and permission criteria, not a new workflow.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| B19 | retires creation feedback with its composition route | Edge | Submit7; navigate8 and type; old ACK arrives | New draft8 remains; no old detail navigation | Frontend unit | ✅ `src/pages/__tests__/multipart-builds.test.tsx` (matching named case) |
| B20 | hides a denied creation composition | Error | Loaded composition then403 | No cached name/creation command; Refresh remains | Frontend unit | ✅ `src/pages/__tests__/multipart-builds.test.tsx` (matching named case) |

Final preserved-owner consolidation:40 matching files/681 assertions passed in
77.93s with maxWorkers2, without changing timeouts. The last creation-route review
then reproduced two additional failures: late create ACK could navigate away from
a replacement composition, and a denied composition kept its cached create form.
The list/form is now keyed by the selected Multipart URL; denied composition
hides that form. Their post-fix focused gate is recorded below. No other production
code changed after the681-test consolidation. Real Profiles3/3 passed in1.1m
(7.2s lifecycle,6.4s draft refresh,4.0s printer lifecycle).

Closing affected creation/API/repository gate:71/71 across3 files,6.87s. Final
format761files, lint, app/UI/domain typechecks and production build1.63s passed;
existing >500kB chunk warnings remain. `git diff --check` passed. Backend contracts
were reused unchanged. M8 is locally qualified; remote CI/final delivery remain
M11 work. Detailed B/R rows qualify this increment; preserved matrices supply the
other route contracts and explicit limitations above.
