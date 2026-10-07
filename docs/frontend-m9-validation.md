# M9 — administration and entry route qualification

Status: active after M8 acceptance5a00f537. M0–M8 closed locally; M10–M11 pending.

## Ordered work and boundaries

1. Finish the preserved External Library sources increment (14 approved paths).
   Its pre-implementation E1–E27 matrix and seven historical REDs are preserved in
   the feature ownership record. Review, integrate, qualify and remove its old
   copied remote arrays/poller. Preserve exact-root confirmation, encrypted reusable
   connection ownership and durable scan tracking. No backend capability changes.
2. Reconcile the preserved accounts/permissions/Config/OIDC/RemoteStorage/backup
   run owners and auth/setup/public Share route contracts. Identify actual remaining
   aggregate backup, storage migration and entry-consumer ownership debt before
   modifying it. Qualify one coherent workflow with all consumers at a time.
3. Close M9 only after its administration, credential omission, role change, long
   Job recovery, destructive-command and public/private-navigation criteria pass.

Existing qualified checkpoints and complete matrices live in
[feature ownership validation](frontend-feature-ownership-validation.md).
Their existence is not blanket acceptance of M9. Preserve earlier results and
resolve actual gaps; do not claim a whole-source review from this document.
No M10 package/platform cleanup starts while M9 remains open.

The existing config/storage/account owners retain specific command contracts;
this milestone does not introduce universal CRUD, a new runtime or a framework.
Rollback each migrated owner and its consumers together. Existing security,
credential omission, storage ownership and destructive-operation fences remain
binding through every increment.

## First qualified increment: Settings Library Sources

The preserved14-path increment is integrated and qualified:116 unit/API/owner/
repository assertions; native exact-root reenrollment/write-back1/1; all frontend
format/lint/types. See E1–E27 and actual failed/corrected invocation records in the
linked ledger. Commands publish authoritative source DTOs, share connection/config
reads and keep accepted scans in TaskCenter. Local source/read copies and the
component scan poller are removed from Settings; Similarity delegates the same
source options. Refused Remove/Enroll confirmations retain visible failure.

The next bounded entry/source-consumer work is SetupFolder and UploadModal:
SetupFolder directly composes config/create/scan across awaits with no gesture
lifetime, stores the source DTO locally and can continue after navigation or a
session change. UploadModal copies config/source reads in an effect, requests
admin configuration for non-admins, catches errors to empty destinations and
silently falls back from a selected missing destination. Reconcile these actual
consumers before starting another M9 workflow. Existing accepted Model upload
execution and transfer/Job ownership from M7 must remain intact.

## First-folder workflow preflight (before tests)

Inspect complete SetupFolder and GettingStarted composition plus folder lifecycle
assertions. The next increment reuses canonical config/source commands, retaining
only an accepted source identity for scan retry. Disposal stops undispatched next
steps and local feedback; an already accepted scan remains owned by TaskCenter.
Keep the exact submitted folder, no implicit enablement on selection, partial/empty
scan recovery and no duplicate source on retry. Getter forms and initial onboarding
navigation remain the next independent entry-route reconciliation after this seam.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| F1 | stops first-folder dispatch after disposal | Edge | Held config GET then unmount | Caller read aborted; no config/source/scan write or local callback | Frontend unit | ✅ setup-folder.test.tsx |
| F2 | publishes a connected folder to source observers | Happy | POST full source DTO | Existing canonical observer shows exact new source | Frontend unit | ✅ setup-folder.test.tsx |
| F3 | suppresses completion after leaving first-folder setup | Edge | Accepted scan then component disposal before terminal | Task still tracked with its localized title descriptor; no old onIndexed/onBusyChange callback | Frontend unit | ✅ setup-folder.test.tsx |
| F4 | rejects first-folder commands from a retired session | Edge | Captured UI then identity change | No source/config writes under replacement credentials | Frontend unit | ✅ setup-folder.test.tsx |
| F5 | preserves first-folder recovery contracts | Happy/Error | Existing create/refused/partial/empty/retry cases | One source, exact input, deliberate retry and correct counts | Frontend unit | ✅ getting-started.test.tsx |

### First-folder qualification

Four new regressions failed against the old implementation (4/4,4.63s): continuing
writes after disposal, missing canonical publication, stale completion callback and
commands from retired credentials. SetupFolder now explicitly reads shared config/
source owners on submission, uses their concrete commands and retains only the
accepted source identity for scan retry. No source/config write happens on folder
selection. Typed create/scan receipts let this consumer use the same owner as Settings.
The Job waiter accepts the existing localized TaskText contract, preserving the
scan title while TaskCenter owns accepted work after disposal.

Initial affected gate105/4files12.33s passed. The type gate then caught the waiter's
old string-only title annotation; widened that annotation to its already-supported
TaskText input and asserted the retained descriptor. Final gate222/6files20.10s
passed: setup-folder,getting-started,settings-library-sources,external-libraries-panel,
task-center and suite-hygiene. Full frontend/app/UI/domain types,lint and format764files
passed. Original red/type outputs are retained with the final receipts.

Real onboarding (`playwright.onboarding.config.ts`,grep `reaches its first Model entirely`)
passed1/1:29.1s scenario,1.2m invocation,including first-account storage preparation,
first upload and actual mounted-folder discovery. Dedicated ports3337/4337 and
`/tmp/printstash-onboarding-4337` were used. No backend changes or performance claim.
This closes only this bounded workflow; M9 remains active and Upload's source
selection is next. Rollback the SetupFolder consumer and typed command receipts
as one increment; preserve the existing source owner and M7 accepted-work execution.

## Upload destination preflight (before tests)

Replace UploadModal's copied config/source effect with the established Query
owners. Retain the selected identity until an explicit selection/reset; a missing,
unbound or unreadable selected source blocks submission instead of retargeting it.
Only administrators read source administration catalogs. Catalog errors remain
recoverable; Vault uploads remain available when no source was selected. Preserve
M7's accepted upload lifetime and the existing file/collection/tag draft.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| U1 | avoids administrative source reads for members | Permission | Member opens Upload | No config/source GET | Frontend unit | ✅ uploading.test.tsx |
| U2 | requires destination review when a refresh deletes/unbinds it | Conflict | Source selected; catalog loses eligibility | File retained; no dispatch until explicit Vault/other source selection | Frontend unit | ✅ uploading.test.tsx |
| U3 | recovers a failed destination catalog | Error | Catalog503 then retry succeeds | Visible error/retry, source choices recovered, file retained | Frontend unit | ✅ uploading.test.tsx |
| U4 | follows canonical source updates while open | Happy | Shared source projection receives confirmed change | Upload options update without closing/reopening | Frontend unit | ✅ uploading.test.tsx |
| U5 | dispatches to the selected eligible source | Happy | Valid selected root and file | Actual artifact-upload request carries exact library identity | Frontend unit / real browser | ✅ uploading.test.tsx + real write-back |
| U6 | blocks a selected destination after denied/transient refresh | Permission/Error | Warm selected source then403/503 | No source name disclosed on denied read; no upload; explicit retry restores eligibility | Frontend unit | ✅ uploading.test.tsx |

### Upload destination qualification

Initial invocation24passed/6failed11.82s: five genuine new regressions plus one
new assertion used the wrong artifact-upload field (`library_id` rather than
`target_library_id`). Corrected that assertion against the actual typed boundary;
its old-code control passed1/1. No production change was needed for that existing
positive behavior. Historical silent fallback expectations were deliberately
replaced by the approved explicit-destination contract.

Affected gate101/4files16.14s passed; added warm403/503 controls, then final
115/6files17.10s passed (three UploadModal mirrors,GettingStarted,i18n coverage,
suite hygiene). App/UI/domain types,lint,format764files and build1.57s passed.
Existing >500kB bundle warning remains; this increment makes no speed/size claim.
Real exact-root reenrollment followed by external write-back passed1/1,9.5s
scenario48.5s invocation, against dedicated ports3327/4327 and isolated data root.

Removed UploadModal's effect-owned config/source copies and silent reset. The
catalog uses the shared options; only administrators subscribe to admin reads.
A selected unavailable destination retains its identity and files, hides denied
source details, blocks dispatch, and offers explicit retry/selection. Existing
Vault submissions and M7's accepted upload lifetime remain qualified. Rollback
this consumer and its observable destination tests together, retaining the
shared source owner. M9 remains active; aggregate backup ownership is next.

## Aggregate backup workflow: ordered preflight

The actual Settings backup surface still copies four remote catalogs plus config
and connection DTOs. `loadBackups` overwrites unsaved policy/retention on refresh,
turns discovery errors into empty lists, and leaves stale private rows after a
failed owned-catalog refresh. Its read lifetime is not attached to the view.
First migrate these reads and form drafts; then qualify exact-source destructive
commands, publication receipts and accepted backup Jobs in the same M9 workflow.
The existing backup-run owner remains canonical. No server-version conflict
contract is invented for the unversioned global configuration.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| B1 | distinguishes an unavailable backup catalog from empty storage | Error | Owned source GET503 | Error visible; no empty-storage claim | Frontend unit | ✅ settings-panel.test.tsx |
| B2 | recovers the owned backup catalog explicitly | Error | Failed read then Refresh succeeds | Catalog replaces failure; no command dispatched | Frontend unit | ✅ settings-panel.test.tsx |
| B3 | preserves an unsaved backup retention during refresh | Conflict | Draft14 while server30 | Refresh retains14 | Frontend unit | ✅ settings-panel.test.tsx |
| B4 | preserves an unsaved backup schedule during refresh | Conflict | Draft04:30 while server02:00 | Refresh retains04:30 | Frontend unit | ✅ settings-panel.test.tsx |
| B5 | blocks backup policy until configuration is available | Error | Config503 | Visible settings failure; no enabled save from fabricated defaults | Frontend unit | ✅ settings-panel.test.tsx |
| B6 | cancels an abandoned backup catalog read | Edge | Held GET then unmount | Native signal aborted | Frontend unit | ✅ settings-panel.test.tsx |
| B7 | hides denied backup rows after refresh | Permission | Warm rows then403 | Exact private source/confirmation hidden; no destructive dispatch | Frontend unit | ✅ settings-panel.test.tsx |
| B8 | distinguishes failed backup discovery from no candidates | Error | Discovery503 with owned catalog healthy | Explicit recovery, no false empty-storage claim | Frontend unit | ✅ settings-panel.test.tsx |
| B9 | preserves source-only connections when saving backup policy | Happy | Shared catalog includes library-only connection | Backup acknowledgment leaves unrelated source connection available | Frontend unit | ✅ settings-panel.test.tsx |

### Backup read/form checkpoint (workflow remains open)

Initial8/8 REDs included one incorrect schedule label; corrected it to the existing
Daily time (UTC),then that case separately failed on lost04:30 as intended.
After migration36passed/2failed: the Save policy button still missed the unavailable
configuration guard, and the denial test checked the exiting confirmation before
its close completed. Added the guard; awaited the actual read failure and dialog
removal. Full affected Settings/API/history gate215/4files51.97s passed.

Two subsequent controls (visible settings-read error and preserving a library-only
connection through a backup-policy ACK) passed2/2 4.94s. Types/lint,format765files,
repository/i18n12/2files3.87s passed. These invocations overlap; they are not a
unique aggregate coverage count. Existing jsdom download/navigation warnings remain.

Four independent canonical read projections replace the copied backup catalogs.
Signals cancel abandoned reads. Only missing404 discovery routes retain historical
empty support; supported-route failures remain recoverable errors. Drafts contain
edited policy fields rather than refreshed full DTOs. The existing destructive/
publication handlers still need migration; four explicitly marked local Query
update adapters must be removed in the next bounded backup command step before
M9 can close. Real backup/browser qualification follows that complete workflow.

## Backup command preflight (before tests)

Move exact-source command validation, cancellation and confirmed publication into
one backup owner. Preserve opaque locators, remote adoption hashes, provider delete
capabilities and durable accepted Jobs. Remove the four temporary catalog update
adapters and component-level receipt races. Keep policy writes explicit through
existing config/connection owners; they remain multiple server transactions, not
an invented atomic operation. Accepted successes publish independently; failed
intent remains reviewable. Local drafts and callbacks retire with their session.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| C1 | rejects a backup confirmation after its source changed | Conflict | Confirmation open; source/hash changes | No DELETE; review failure retained | Frontend unit | ✅ Settings/history mirrors |
| C2 | keeps a deleted backup absent after an older catalog response | Race | Held GET; confirmed DELETE; late old response | No resurrection; old GET canceled | Frontend unit | ✅ Settings/history mirrors |
| C3 | retires backup confirmations with the private session | Permission | Open confirmation then auth retirement | Old confirmation cannot dispatch | Frontend unit | ✅ Settings/history mirrors |
| C4 | retains an accepted backup without disposed-view feedback | Edge | Accepted Job, leave Settings, terminal | Task reaches terminal; no old toast/local follow-up | Frontend unit | ✅ Settings/history mirrors |
| C5 | publishes acknowledged backup policy to shared configuration | Happy | Retention/policy ACK normalized | Other config readers see acknowledged values | Frontend unit | ✅ Settings/history mirrors |
| C6 | retains partial policy failure without claiming full success | Error | Config accepted; connection refused | Accepted config visible; failed connection intent retained; explicit retry | Frontend unit | ✅ Settings/history mirrors |
| C7 | preserves exact-source backup lifecycle | Happy/Error | Existing create/upload/adopt/download/restore/delete/retry contracts | Exact locators, hashes, recovery and durable task outcomes retained | Unit/API/real browser | ✅ unit/API + real recovery |
| C8 | publishes an adopted backup before another listing | Happy | Full adoption DTO accepted | Owned catalog shows ACK; exact discovery candidate disappears; no redundant owned GET | Frontend unit | ✅ Settings/history mirrors |
| C9 | keeps one canonical history snapshot after invalidation | Happy | Backup command invalidates run history | History reuses the same key and fetches once; obsolete refresh counter removed | Frontend unit | ✅ backup-run-history.test.tsx |
| C10 | refreshes backup process views when returning | Recovery | Leave during server work; revisit with warm Query data | Owned catalog and run history read current server state without waiting for the global30s window | Frontend unit | ✅ Settings/history mirrors |

### Backup command qualification

Five initial REDs8.65s: an obsolete exact-source confirmation dispatched DELETE;
a GET remained uncancelled after deletion; auth retirement retained a confirmation;
a completed Job emitted a disposed-view toast; a normalized config ACK never
reached the shared projection. Tightened the delete race arrangement to await the
known deletion before releasing the held read; its cancellation assertion remained
RED4.80s. Separate partial-policy1RED5.85s and authoritative-adoption1RED5.69s
captured the remaining publication defects.

`settings-backup-commands.ts` now owns exact-source eligibility, cancellation,
confirmed publication and accepted manual-backup Jobs. Typed backup endpoints use
native caller signals and no legacy transport invalidation. Catalog/source refs,
reviewed archive identity, remote adoption digest and provider delete capability
are rechecked before dispatch; four local Query setter adapters are removed.
Delete refreshes independent discovery catalogs instead of removing matching keys
or basenames across unrelated providers. Adoption publishes the actual full DTO.
The existing run-history owner replaces the obsolete refresh counter; successful
retry asks only the owned-catalog observer to refresh, not policy/config catalogs.

Config/connection policy writes use their existing owners in explicit sequence.
Each accepted part publishes immediately, including when a later connection fails;
only acknowledged drafts clear. The backend still exposes multiple unversioned
transactions. This is not atomic multi-resource commit or external-editor conflict
detection. Exact-source command failures retain the confirmation for cancellation/
review, and callbacks/reload timers remain fenced to the originating view/session.

First command gate42passed/2failed18.81s: the authored review error needed its typed
message mapping, and a delete fixture kept serving the removed discovery file.
Mapped the specific code and made the fake reflect the actual deletion.44passed
17.81s, then the partial-policy increment45passed18.37s. A complete gate269passed/
2failed56.56s found two unrelated Trash scenarios whose earlier partial config had
implicitly meant unguarded storage. The new full config factory defaults verified;
those tests now explicitly request unguarded storage, preserving their original
assertions. Corrected full gate271/7files59.10s passed.

Two return-to-view regressions failed6.89s on the inherited30s freshness window.
Backup catalog/history options now read on return without a timer; both pass.
Latest Settings/history/owner/repository/i18n gate210/5files56.60s passed; affected
return/command controls11passed9.29s. Types passed. Lint required moving the auth
cleanup declaration after its setters and declaring the stable trash-retention
setter dependency; no suppression added. Final lint/format766files passed, and
15 affected lifetime/Trash assertions10.26s passed after that correction. Build
passed1.60s with the existing >500kB warning. Counts overlap across invocations.

Real `backup-recovery.spec.ts`:1/1,39.4s scenario1.3m invocation. The operator created
a backup, purged a real Model, restored it and downloaded matching Artifact bytes.
Dedicated ports3327/4327 and `/tmp/printstash-m9-backup-data`; no backend production
changes. No performance improvement claimed. M9 remains open: storage migration,
remaining Settings/notification and entry-route ownership must still be reconciled.


## Vault migration (M9 increment)

This increment consolidates migration reads and command receipts without changing
backend migration gates or adding automatic mutation retries. The existing report
render already checks `report.id === run.id`; a wrong-run report display is **not**
a confirmed defect. Migration DTOs have no comparable revision counter: cancellation
protects client read/receipt races, but backend plan/state checks remain authoritative.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| V1 | aborts migration startup when its view is disposed | Edge | Pending history GET, navigate away | Native request signal is aborted | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::aborts migration startup when its view is disposed` |
| V2 | exposes backup discovery failure without reporting an empty catalog | Error | Backup sources 503, recoverable migration exists | Visible read failure; recovery remains available | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::exposes backup discovery failure without reporting an empty catalog` |
| V3 | retires migration confirmations with their private session | Edge | Reviewed destructive confirmation, session ends | Dialog disappears; no POST from old intent | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::retires migration confirmations with their private session` |
| V4 | cancels an obsolete status read before publishing an acknowledged pause | Edge | Pending status GET races pause ACK | Abort signal; paused UI survives late response | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::cancels an obsolete status read before publishing an acknowledged pause` |
| V5 | keeps migration commands disabled after access is denied | Error | Warm data then 403 | Private run and destructive controls hidden | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::keeps migration commands disabled after access is denied` |
| V6 | requires renewed review after the confirmed migration changes | Edge | Open confirmation then changed state or plan | No destructive POST from obsolete confirmation | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::requires renewed review after the confirmed migration changes` |
| V7 | stops automatic status retries after a read failure | Error | Active migration polling returns 503 | Error visible; explicit refresh recovers | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::stops automatic status retries after a read failure` |
| V8 | preserves migration destination edits across catalog refresh | Edge | Draft destination then provider catalog refresh | User-entered values remain | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::preserves migration destination edits across catalog refresh` |
| V9.1 | starts the reviewed plan before copying | Happy | Reviewed plan digest | POST contains the reviewed digest | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::starts the reviewed plan before copying` |
| V9.2 | blocks an expired plan | Edge | Expired plan | Start disabled | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::blocks an expired plan` |
| V9.3 | retains the source throughout its grace period | Edge | Active migration before deadline | Source cleanup disabled | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::retains the source throughout its grace period` |
| V9.4 | keeps cleanup disabled without successful Full audit even after grace | Edge | Grace elapsed without passing full audit | Source cleanup disabled | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::keeps cleanup disabled without successful Full audit even after grace` |
| V9.5 | requires recovery before resuming a stopped copy | Edge | Recovery-required copying run | Recovery offered; resume absent | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::requires recovery before resuming a stopped copy` |
| V9.6 | rechecks state after an uncertain cutover response | Error | 409 cutover with ambiguous recovery | Recovery state replaces the prior ready projection | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::rechecks state after an uncertain cutover response` |
| V9.7 | shows completed source cleanup | Happy | Eligible source cleanup | Confirmed cleaned state visible | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::shows completed source cleanup` |
| V9.8 | discards only the confirmed candidate | Happy | Explicit candidate discard | Cleanup payload does not authorize source removal | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::discards only the confirmed candidate` |
| V9.9 | retains source indefinitely through an explicit action | Happy | Active retained source | Retain command preserves credentials | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::retains source indefinitely through an explicit action` |
| V9.10 | requires confirmation before removing credentials for manual cleanup | Edge | Manual cleanup requested | Only confirmation authorizes credential removal | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::requires confirmation before removing credentials for manual cleanup` |
| V9.11 | retries a reported recoverable object failure | Happy | Retryable failed objects | Single explicit retry request | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::retries a reported recoverable object failure` |
| V9.12 | shows the requested Full audit result | Happy | Full audit accepted | Persisted audit outcome visible | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::shows the requested Full audit result` |
| V9.13 | sends fresh candidate credentials without displaying them in the checked plan | Happy | Secret-bearing destination | Secrets sent once; checked plan does not render them | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::sends fresh candidate credentials without displaying them in the checked plan` |
| V9.14 | rejects bandwidth below the supported minimum before requesting a plan | Edge | Bandwidth below 1024 | No preflight POST | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::rejects bandwidth below the supported minimum before requesting a plan` |
| V9.15 | blocks destinations unavailable for Vault use | Edge | Provider unavailable for Vault | Preflight disabled | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::blocks destinations unavailable for Vault use` |
| V9.16 | surfaces report download failure without losing saved progress | Error | Report GET fails | Failure visible; run progress retained | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::surfaces report download failure without losing saved progress` |
| V10 | preserves Artifact bytes through restart and cutover | Happy | Real backend migration with online delta | Baseline and delta byte identity after verified cutover | Playwright | ✅ `frontend/tests/e2e-real/migration/vault-migration.spec.ts::verified migration resumes after restart with online delta Artifacts` |
| V11 | preserves the selected migration across history reorder | Edge | Current run, refreshed history starts with another run | Existing selected run remains displayed | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::preserves the selected migration across history reorder` |
| V12 | retains required provider defaults when editing a destination | Edge | Local provider supplies mandatory default root | Preflight includes default root plus edited paths | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::retains required provider defaults when editing a destination` |
| V13 | hides a migration when command recovery confirms revoked access | Error | Pause rejected with 403; status also denied | Private run and command controls disappear | Frontend unit | ✅ `frontend/src/components/__tests__/vault-migration-panel.test.tsx::hides a migration when command recovery confirms revoked access` |

Validation: the first three new lifetime/catalog tests failed against the previous
panel (3 failures, 4.11s). The first refactor pass had 62 passing tests and one
manual-refresh regression; history refresh now publishes its coherent snapshot
into run projections. The expanded tests then exposed a denied refresh retaining
private data; the owner now records that read through Query. Command recovery
also records denial in the run query (its regression failed before that change).

The real browser initially failed before preflight: the draft's first edit erased
the provider's required default `root`. A focused test reproduced that regression;
editing now starts from the complete provider defaults. An intermediate test then
incorrectly decoded the last GET's empty body; its assertion was corrected to read
the actual preflight POST. The selected-run refresh test also reproduced navigation
to a differently ordered history entry and now preserves the entry identity.

Final affected component/API/repository run: **83 tests across 4 files passed in
16.73s**. The failed-read test uses production focus-refresh defaults: a focused
regression exposed an extra automatic status retry, then explicit detail focus/
reconnect policy kept failed polling stopped until Refresh. These invocations
exercise overlapping tests; their counts must not be added together.

Real migration browser: **1/1 passed**, 49.7s scenario (1.5min invocation), including
API restart, explicit recovery/resume, verified cutover, full audit, report download
and equality of baseline/online-delta Artifact bytes. The initial browser failure
and trace are retained. Later denied-recovery/focus policy corrections were covered
by the component suite; this is not a claim of final-SHA browser/CI qualification.

App/UI/domain types, full frontend formatting (767 files), and production build
passed; build took 1.56s and retained the existing large-chunk warning. Lint caught
an unnecessary effect for entry selection; entry identity is now captured once
during rendering. The affected entry/history tests and lint were rerun after that
correction. No backend implementation changed and no performance improvement is
claimed. The active-storage configuration/inventory consumers still need their M9
integration review; this increment does not close the milestone.


## Current storage configuration ownership (M9 increment)

The active-storage card still copies full configuration/provider responses and
reloads them after writes. This increment reuses existing configuration/provider
owners, keeps credential drafts local and preserves explicit root enrollment.
The configuration endpoint has no conditional revision contract; detecting an
observed background change does not claim atomic cross-client conflict protection.
Root enrollment currently accepts a role, not a reviewed path. A frontend snapshot
can reject a change already observed in its catalog; the backend contract limitation
must remain explicit rather than claiming filesystem identity fencing.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| SC1 | cancels an abandoned storage configuration read | Edge | Pending config GET, view unmounts | Request signal aborted | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::cancels an abandoned storage configuration read` |
| SC2 | exposes failed configuration reads with explicit recovery | Error | Config 503 | Error/retry visible; no enabled save with unknown config | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::exposes failed configuration reads with explicit recovery` |
| SC3 | hides private storage configuration after denial | Error | Warm config then 403 | Private paths and save controls hidden | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::hides private storage configuration after denial` |
| SC4 | publishes the normalized storage receipt to configuration observers | Happy | Successful normalized PUT | Shared configuration contains receipt; no redundant GET | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::publishes the normalized storage receipt to configuration observers` |
| SC5 | retains a storage draft during background configuration refresh | Edge | Unsaved paths then GET refresh | Draft remains, changed base requires review | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::retains a storage draft during background configuration refresh` |
| SC6 | preserves a newer credential draft after an older save finishes | Edge | Edit again while PUT pending | New secret draft remains unsaved | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::preserves a newer credential draft after an older save finishes` |
| SC7 | retires root enrollment review with its session | Edge | Enrollment confirmation then logout | Confirmation disappears; no POST | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::retires root enrollment review with its session` |
| SC8 | refuses enrollment after the reviewed root changes | Edge | Config projection changes root during confirmation | No enrollment POST | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::refuses enrollment after the reviewed root changes` |
| SC9 | updates current storage after a confirmed migration cutover | Happy | Cutover receipt activates new destination | Existing config observer rereads active paths | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::updates current storage after a confirmed migration cutover` |
| SC10 | keeps private configuration unreadable without an administrator | Error | No administrator session | No private config request or editable control | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::keeps private configuration unreadable without an administrator` |
| SC11 | discards a conflicting storage draft before renewed review | Edge | Background configuration changed while editing | Explicit discard reveals current fields; subsequent save uses them | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::discards a conflicting storage draft before renewed review` |
| SC12 | permits retry after refused root enrollment | Error | Enrollment rejected; next explicit attempt succeeds | Confirmation remains usable; successful receipt shown | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::permits retry after refused root enrollment` |
| SC13 | hides storage after enrollment access is revoked | Error | Enrollment 403 followed by denied config read | Private location and review disappear | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx::hides storage after enrollment access is revoked` |

Validation: initial ownership selection reported 9 failures; 8 reproduced the old
behavior and the newer-credential test first had an overly exact accessible label.
After correcting that arrangement, it independently failed against the committed
old card because the newer credential was replaced. The first implementation run
had 63 passing/5 failing tests: loading-status timing, a lost field-validation
message, two anonymous fixtures pretending an admin-only config endpoint was
public, and that label arrangement. The validation message was preserved; the
anonymous fixtures now assert the real private-read contract. The existing
server-refusal and credential-omission assertions still pass.

The cutover/current-storage integration failed before targeted configuration
invalidation. Separate regressions covered retry after refused enrollment and
hiding private locations after enrollment permission revocation. Final affected
selection: **155 tests across 7 files passed in 17.10s**. The real migration scenario
now additionally asserts the new data/thumbnail paths in the current-storage
summary: **1/1 passed**, 47.0s scenario (1.5min invocation).

App/UI/domain types and build passed (1.65s; existing large-chunk warning). Lint
rejected a conditional empty-object spread; equivalent explicit construction passed.
Full formatting caught the preceding migration increment's final entry-selection
formatting change; it was corrected rather than suppressing the check. Final
formatting covers 768 files. All failed invocations are retained. No performance
claim is made from request-count assertions or smaller components.

This is **frontend ownership qualification, not complete concurrent-edit
qualification**. The approved plan requires an atomic backend precondition for
editable aggregates. Configuration still has no such contract, and role-only root
enrollment cannot prove the path reviewed by the client. These remain required M9
work before milestone closure; frontend fingerprints are not a substitute. The
inventory/cache consumers and remaining administration/entry workflows are also
still open.


## Configuration transaction prerequisite (M9, locally qualified)

The conditional-edit contract requires a single atomic write. The existing PUT
commits derivative controls, flags, schedule and currency independently before
persisting the remaining settings. This increment removes those intermediate
commits; it does not yet claim version-conflict protection. Runtime publication
and derivative wake-up hints must follow a successful commit. Rollback is a
revert of this increment; no schema or public payload changes are involved.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| CT1 | rejects the entire configuration patch when its final database write fails | Error | Mixed derivative, flag, schedule, currency and OIDC patch, with/without a typed provider; database rejects the final field | Every previously stored field and effective runtime setting remains unchanged | Integration | ✅ `integration/api/v1/test_config.py::TestConfigurationTransaction::test_rejects_the_entire_patch_when_the_final_database_write_fails` |
| CT2 | publishes a successfully committed mixed configuration patch | Happy | Mixed controls, flags, schedule, currency and runtime settings | Response and subsequent GET expose all accepted fields | Integration | ✅ `integration/api/v1/test_config.py::TestConfigurationTransaction::test_publishes_a_successfully_committed_mixed_patch` |
| CT3 | clears runtime overrides in a mixed configuration patch | Edge | Existing string and integer overrides; empty string and minus one with a flag change | DB overrides cleared; GET returns configured defaults | Integration | ✅ `integration/api/v1/test_config.py::TestConfigurationTransaction::test_clears_runtime_overrides_in_a_mixed_patch` |
| CT4 | retains standalone derivative policy command behavior | Happy | Direct policy command without caller-owned transaction | Fresh session reads the accepted producer control | Integration | ✅ `integration/modules/derivatives/test_policy.py::TestResolution::test_independent_sessions_read_the_saved_policy` |
| CT5 | PostgreSQL rejects the whole mixed patch on a failed final write | Error | Real PostgreSQL rejects the OIDC field after other controls were staged | All persisted fields remain at the original values | Integration | ✅ `integration/postgres/test_config_transactions.py::TestConfigurationTransaction::test_rejects_the_whole_patch_after_a_failed_final_write` |
| CT6 | PostgreSQL persists the accepted mixed patch | Happy | Real PostgreSQL accepts controls, flags, schedule, currency and runtime settings | Fresh session reads every accepted value | Integration | ✅ `integration/postgres/test_config_transactions.py::TestConfigurationTransaction::test_persists_the_accepted_mixed_patch` |

Validation (2026-10-07):

- Initial regression: **1 failed, 2 passed** in 29.73 s. A final database
  rejection left earlier flag, schedule, currency and derivative edits saved.
- First fix: **3 passed** in 3.84 s. Affected-owner selection (config API,
  derivative policy, backup schedule, runtime configuration/overlay/storage):
  **239 passed, 1 failed** in 18.45 s. The additional typed-provider fixture
  attempted a forbidden namespace change and received 409 before the injected
  failure. It now starts with an existing WebDAV configuration and changes its
  credential. Corrected regression selection: **4 passed** in 4.06 s.
- PostgreSQL transaction cases: **2 passed** in 39.94 s, using the repository's
  container fixture. No migration or public API schema changed.
- Ruff check passed for all six touched Python files. Formatting was applied to
  the changed blocks; unrelated Python 3.14 exception-format changes were reverted.
- Targeted Pyright reported **3 errors** in unchanged `runtime_config.py` lines
  120 and 803 (`File.sha256.is_not` and the integer fallback conversion).
  Those exact expressions also exist at the pre-increment commit. This is not
  a green type gate; resolution remains part of final validation.
- No complete suite, frontend build or browser suite was run for this backend-only
  prerequisite. Full delivery gates remain due at M11. No performance claim.

This removes partial database commits. It does **not** yet provide conditional
edit versions, reviewed-root enforcement, or complete M9 acceptance.


## Durable configuration edit version (M9, locally qualified)

Before wiring conditional HTTP writes, persist a version for the editable vault
configuration aggregate. Its scope is the fields accepted by the configuration
PUT, including legacy storage projections and typed provider credentials. Direct
SQL, ORM and legacy writes must advance it in the same transaction. Backup-run
bookkeeping, setup markers and unrelated feature settings must not create false
conflicts. The database-history epoch already rotated during restore will pair
with this version in the subsequent HTTP contract; the storage-root installation
identity remains unchanged.

This is an additive schema step, not complete conflict protection. Generate the
migration from the model, install the same immutable trigger contract on upgrades
and fresh databases, and preserve existing configuration on downgrade/upgrade.
The HTTP/client rollout follows this prerequisite; no frontend claim is enabled
by this step alone.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| CV1 | new configuration starts with a positive version | Happy | Fresh SQLite/PostgreSQL singleton | Stored vault edit version is one | Integration | ✅ `integration/db/test_config_edit_contracts.py::TestConfigurationEditVersion::test_new_configuration_starts_with_a_positive_version` |
| CV2 | legacy edits advance the configuration version | Happy | Each editable configuration field changes through SQL without a version | Fresh read has a greater version | Integration | ✅ `integration/db/test_config_edit_contracts.py::TestConfigurationEditVersion::test_legacy_edits_advance_the_configuration_version` |
| CV3 | operational bookkeeping preserves the editing base | Edge | Backup attempt time, setup marker, unrelated notification setting change | Stored vault edit version unchanged | Integration | ✅ `integration/db/test_config_edit_contracts.py::TestConfigurationEditVersion::test_operational_bookkeeping_preserves_the_editing_base` |
| CV4 | rollback preserves the previous editing base | Error | Editable field changed then transaction rolled back | Field and version both unchanged | Integration | ✅ `integration/db/test_config_edit_contracts.py::TestConfigurationEditVersion::test_rollback_preserves_the_previous_editing_base` |
| CV5 | an explicit version advance is not counted twice | Edge | Conditional writer advances version with edited fields | Stored version equals the supplied advance | Integration | ✅ `integration/db/test_config_edit_contracts.py::TestConfigurationEditVersion::test_an_explicit_version_advance_is_not_counted_twice` |
| CV6 | no-op writes preserve the editing base | Edge | SQL writes the existing currency again | Stored version unchanged | Integration | ✅ `integration/db/test_config_edit_contracts.py::TestConfigurationEditVersion::test_no_op_writes_preserve_the_editing_base` |
| CV7 | upgrade preserves existing configuration | Happy | Previous-head database with stored settings | New version initialized; settings preserved; legacy write advances it | Integration | ✅ `integration/db/migrations/test_config_edit_version.py::TestConfigEditMigration::test_upgrade_preserves_existing_configuration` |
| CV8 | downgrade and reupgrade preserve existing settings | Edge | New schema with edited settings, round trip to previous head | Settings preserved; contract works after reupgrade | Integration | ✅ `integration/db/migrations/test_config_edit_version.py::TestConfigEditMigration::test_round_trip_preserves_existing_settings` |
| CV9 | operators can render the upgrade without a database connection | Happy | Offline SQL for the previous-to-new range on SQLite/PostgreSQL | Output includes the additive column and installed trigger | Integration | ✅ `integration/db/migrations/test_config_edit_version.py::TestOfflineConfigEditMigration::test_renders_the_upgrade_without_a_database_connection` |

Validation (2026-10-07):

- Initial SQLite regression failed because the durable version column did not
  exist: **1 failed, 93 deselected** in 3.21 s.
- Database contract and migration selection: **96 passed, 2 setup errors** in
  108.09 s. All **94** fresh-schema contract cases passed on SQLite/PostgreSQL,
  including each of the 42 edited columns; both SQLite migration cases passed.
  PostgreSQL setup attempted to replay the historical baseline, which references
  `models` before that table exists. The corrected fixture follows the existing
  PostgreSQL migration-test pattern: supported fresh installation, downgrade to
  the previous revision, then exercise this upgrade with historical data.
- Only the two corrected PostgreSQL migration cases were repeated:
  **2 passed, 2 deselected** in 53.26 s.
- Offline upgrade rendering plus the preceding compound-write rollback cases:
  **6 passed** in 4.67 s.
- Selected schema/factory guards (`test_models_versus_chain`, migration patterns,
  database parity, schema DDL and `TestBuildSystemConfig`): **170 passed** in
  186.33 s. No additional schema migration is emitted by autogenerate.
- Ruff check passed for all seven touched Python files; DB source format check
  passed. The new trigger module passes targeted Pyright (**0 errors**).
  An explicitly broadened model-file check reports six existing SQLModel
  `__tablename__` assignment diagnostics; this model file is outside the
  configured Pyright include list, and its table-name declarations are unchanged.
- The generated migration contains exactly the new non-null version column with
  a server default. Trigger installation/removal was then added, as Alembic does
  not generate non-table objects. Existing migrations were not edited.
- A direct comparison with the configuration request schema found no missing or
  extra editable columns after mapping its typed provider payload to stored
  configuration/secret projections. No public API or frontend wire type changed.

HTTP preconditions, coherent editing-base reads, concurrent-request regression
coverage, restore-incarnation checks and frontend conflict recovery remain the
next portion of M9. This prerequisite does not close the milestone. No full
backend/frontend suite, browser suite, build or performance benchmark was rerun.


## Atomic configuration edit claim (M9, domain operation qualified)

The persisted version now needs a domain-owned compare-and-advance operation.
A conditional claim compares the database-history epoch and vault version in
one UPDATE, then rechecks the actor's current administrator/session authority.
The caller retains commit/rollback ownership, so rejected edits and downstream
validation failures consume no version. Unconditional legacy callers remain
explicitly unprotected against overwrites. The HTTP/read/client integration is
still pending; this increment must not be described as complete conflict UI.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| CC1 | only one editor can commit from a shared base | Edge | Two real concurrent writers with the same epoch/version on SQLite/PostgreSQL | One committed value; one edit_conflict; positive advanced version | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_only_one_editor_can_commit_from_a_shared_base` |
| CC2 | rollback preserves an unused editing base | Error | Claim and field edit followed by rollback | Previous field and version remain usable | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_rollback_preserves_an_unused_editing_base` |
| CC3 | a restored database rejects a prior incarnation | Error | History epoch rotates while integer version stays equal | Old base rejected without a version advance | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_a_restored_database_rejects_a_prior_incarnation` |
| CC4 | a legacy writer invalidates an earlier conditional base | Edge | Unconditional committed write before conditional save | Conditional claim rejected; legacy value retained | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_a_legacy_writer_invalidates_an_earlier_conditional_base` |
| CC5 | revoked administrator authority rejects the edit | Error | Actor disabled, demoted, deleted, or session version rotated since authentication | Permission denied; configuration and version unchanged after rollback | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_revoked_administrator_authority_rejects_the_edit` |
| CC6 | a current administrator can commit an explicit edit | Happy | Current epoch/version and administrator | Accepted value and greater version persist | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_a_current_administrator_can_commit_an_explicit_edit` |
| CC7 | explicit legacy commands can save without a base | Happy | Current administrator deliberately supplies no base | Value persists with an advanced version; no conflict protection claimed | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_explicit_legacy_commands_can_save_without_a_base` |
| CC8 | a non-singleton configuration cannot be edited through this contract | Error | Persisted row with id other than one | Invalid-target error; singleton value and version unchanged | Integration | ✅ `integration/modules/administration/test_config_edits.py::TestClaim::test_rejects_a_non_singleton_target` |

Validation (2026-10-07):

- Initial selected case: **1 failed, 17 deselected** in 24.56 s because the new
  domain operation did not exist. This was missing-contract evidence, not a
  reproduction through the still-unconditional HTTP endpoint.
- The first complete selection was deliberately interrupted while still running:
  each SQLite case rebuilt the full schema. Its output, including a pytest
  temporary-directory teardown `KeyError` after interruption, was retained; it
  is not counted as a completed test run.
- With one schema per dialect and per-case row cleanup, the complete final
  selection passed: **22 passed** in 50.37 s. Setup took 24.61 s for SQLite and
  21.57 s for PostgreSQL; subsequent reported setup costs were at most 0.16 s.
  This is test-preparation evidence, not an application-performance improvement.
- The concurrency case uses two real sessions and a barrier, not mocked writes.
  The incarnation case rotates the authority epoch to simulate restore; the
  actual backup-restore workflow is not rerun or claimed by this selection.
- Ruff check/format and targeted Pyright for `config_edits.py` passed.
  No full suite, schema gate, frontend build or browser suite was repeated.

At this checkpoint `config_edits.claim` had no production caller. The following
HTTP increment integrates it; client conflict recovery remains pending. M9 remains
the only active milestone.


## Configuration HTTP editing contract (M9 API increment)

Integrate the existing database claim with the actual configuration GET/PUT. The
editing projection must use one persisted row snapshot and deployment defaults,
rather than combine its version with a delayed process overlay. Capture a write's
receipt before its commit releases the row; a later writer cannot replace that
receipt. Preserve the explicit additive `conditional-v1` compatibility contract.
The frontend's header/base/conflict migration follows this API increment.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| CH1 | returns an ETag matching the configuration editing base | Happy | Authorized configuration GET | Header matches epoch/version in body | Integration | ✅ |
| CH2 | reads committed editable values before runtime publication | Edge | Stored OIDC value differs from process overlay | GET returns stored value with its current version | Integration | ✅ |
| CH3 | clears an override without returning the retired runtime value | Edge | Stored override cleared while process overlay is old | GET returns deployment default with current version | Integration | ✅ |
| CH4 | commits an edit from the reviewed base | Happy | Valid If-Match and conditional-v1 | Accepted value; advanced version; matching receipt ETag | Integration | ✅ |
| CH5 | rejects an outdated compound patch | Error | Another writer saved after the draft base | 412 edit_conflict; no part of losing patch persisted | Integration | ✅ |
| CH6 | requires the opted-in editing base | Error | conditional-v1 without If-Match | 428; configuration unchanged | Integration | ✅ |
| CH7 | keeps explicit legacy write compatibility | Edge | No conditional headers | Write accepted; version advances | Integration | ✅ |
| CH8 | rejects malformed or unsupported preconditions | Error | Weak/wildcard/wrong aggregate/invalid ETag, zero/negative/oversized versions, oversized digit input or unknown contract | 412 or 400; configuration unchanged | Integration | ✅ |
| CH9 | rejects a base from a retired database history | Error | Authority epoch rotated after GET | 412; configuration unchanged | Integration | ✅ |
| CH10 | returns the committed command's own receipt | Edge | Another writer commits currency or OIDC immediately after this command | PUT reports its accepted value/base; subsequent GET and runtime OIDC report later writer | Integration | ✅ |
| CH11a | TestGetConfig.test_rejects_an_unauthenticated_caller | Error | Anonymous GET | 401; no configuration disclosed | Integration | ✅ |
| CH11b | TestGetConfig.test_rejects_a_non_superuser | Error | Member GET | 403; no configuration disclosed | Integration | ✅ |
| CH12a | TestUpdateConfig.test_rejects_an_unauthenticated_caller | Error | Anonymous PUT | 401; no configuration mutation | Integration | ✅ |
| CH12b | TestUpdateConfig.test_rejects_a_non_superuser | Error | Member PUT | 403; no configuration mutation | Integration | ✅ |
| CH13 | publishes an explicitly cleared runtime override | Edge | Successful API clear of an OIDC override | Runtime value returns to deployment default after commit | Integration | ✅ |

Validation:

- Initial HTTP regression selection: **14 failed in 5.92 s** before the change.
  An intermediate S3 accessor syntax error prevented collection; it was corrected.
- First combined selection: **76 passed, 1 failed in 14.31 s**. An unrelated edit
  incorrectly revalidated a legacy SFTP provider. Field-scoped runtime publication
  fixes that regression; its focused selection passed **19 tests in 6.90 s**.
- HTTP editing, existing configuration API, library conditional-edit consumers and
  effective runtime reads: **119 passed in 18.84 s**. This includes a real second
  database write immediately after commit and verifies the original receipt.
- A subsequent oversized-version regression reproduced a 500 (**1 failed in
  4.62 s**). Signed 64-bit bounds are checked before integer conversion/database
  binding. Malformed-header selection: **9 passed, 11 deselected in 5.03 s**.
- PostgreSQL compound configuration transactions: **2 passed in 40.67 s**.
  Existing schema/claim gates were not repeated; this increment changes no schema.
- OpenAPI snapshot regeneration: **1 passed in 6.01 s**; reviewed delta is the
  required editing-base fields, optional conditional headers and GET description.
- Frontend configuration API: **20 passed in 2.45 s**. App and workspace package
  typechecks passed after making the editing base required in the DTO/factory.
- Targeted backend Pyright reports the same three existing diagnostics in
  `runtime_config.py` (`File.sha256.is_not` and two `int(object)` diagnostics).
  This check is not green; no new diagnostics were reported in the other five
  changed production modules.

Affected Python Ruff checks, schema formatting, frontend DTO/factory formatting
and lint, and `git diff --check` passed.

These results qualify this API increment, not M9 acceptance or final delivery.
Frontend conditional headers, draft-base ownership and explicit conflict recovery
remain pending. No full suite or browser suite was repeated for this increment.


## Conditional configuration client transport (M9 increment)

The configuration transport accepts an explicit draft base during the additive
rollout. The existing secret-safe command owner forwards that base, validates
injected receipts, and refuses to replace a newer configuration projection with a
late acknowledgement. Unmigrated forms remain explicitly unprotected until the
M9 cutover; an optional base is temporary compatibility, not a completed rollout.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| CF1 | sends the reviewed configuration base | Happy | Conditional update with captured epoch/version | Exact If-Match and opt-in headers; accepted receipt | Unit | ✅ |
| CF2 | rejects an invalid configuration base before dispatch | Error | Malformed epoch, nonpositive or unsafe version | No request; invalid-base error | Unit | ✅ |
| CF3 | rejects an invalid conditional configuration receipt | Error | Same/older version, changed history, malformed base | No false accepted result | Unit | ✅ |
| CF4 | forwards a captured configuration base to an injected writer | Happy | Secret-bearing command with base | Writer receives captured base and signal; no MutationCache secrets | Unit | ✅ |
| CF5 | retains a newer observed configuration after a late receipt | Race | Cache reaches version 3 before version 2 ACK | Visible/cache version 3 retained | Unit | ✅ |
| CF6 | rejects a nonadvancing injected configuration receipt | Error | Injected writer returns original version | Error state; original cache preserved | Unit | ✅ |
| CF7 | propagates a conflict without retrying the write | Error | Server returns 412 | Error state; exactly one PUT; original projection preserved | Unit | ✅ |


## OIDC conditional form cutover (M9 increment)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| OC1 | keeps the first-edit base across a background refresh | Race | Draft starts at v1; read publishes v2 | Save still sends v1 | Unit | ✅ |
| OC2 | requires explicit review after a configuration conflict | Error | v1 PUT rejected; latest v2 authorized | Draft/secret retained; no blind retry; reviewed v2 save explicit | Unit | ✅ |
| OC3 | requires explicit review after a configuration conflict (503/network cases) | Error | PUT returns 503 or fetch rejects | No success claim; fresh read before explicit action | Unit | ✅ |
| OC4 | keeps saving blocked when latest configuration cannot be read | Error | Conflict followed by failed review read | Draft retained; no second PUT | Unit | ✅ |
| OC5 | discards the local secret only when adopting the reviewed version | Happy | User explicitly adopts fresh values | Secret cleared; reviewed values shown; no PUT | Unit | ✅ |
| OC6 | requires another review after a second conflict | Race | Revised save rejected again | Old review removed; no blind third write | Unit | ✅ |
| OC7 | retires a pending conflict review on session change | Auth | Logout while review GET pending | No retired values/secret shown; no new write | Unit | ✅ |
| OC8 | detects a competing SSO edit in two browser tabs | Integration | Both drafts share base; second tab saves first | First receives 412; keeps draft; explicit review/save persists it | Real browser | ✅ |

Validation and limits:

- Client regressions initially **12 failed / 28 passed in 3.66 s**; transport and
  shared owner then passed **40 tests in 2.58 s**.
- Initial OIDC regression selection: **4 failed in 6.70 s**. After the change,
  **24 passed / 1 failed in 8.31 s** because an existing fake acknowledgement did
  not advance its version. The fake now mirrors the actual write contract.
- Final transport, shared owner and OIDC component selection: **69 passed across
  3 files in 6.62 s**, including network loss, repeat conflict, adoption and session
  retirement. This is behavioral evidence, not a performance benchmark.
- Actual backend/browser: **2 passed in 58.8 s** (two-tab conflicting edits and
  the existing SSO persistence/secret/login flow), using isolated ports/data.
- Frontend typecheck (app and workspace packages), lint, format (768 files) and
  production build passed. Initial lint rejected two conditional empty-object
  spreads; corrected to explicit optional properties. Build retains its existing
  >500 kB chunk warning. No bundle-performance improvement is claimed.
- All other configuration forms still need first-edit base ownership and explicit
  conflict recovery. The shared client's temporary optional base must be removed
  at that cutover. Mock-API configuration fixtures still need a complete contract
  audit; real-browser qualification above does not validate those fixtures.
- M9 remains active; M10/M11 and final delivery gates have not been advanced.

After preserving omission of the compatibility base in injected-writer options,
the final owner selection passed **11 tests in 2.70 s**, and typecheck passed again.
Remaining configuration writers include Settings (including the direct trash
retention write), StorageConfigCard, ExternalLibrariesPanel and SetupFolder.


## Library source toggle configuration cutover (M9 increment)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| LT1 | sends the observed configuration base when enabling sources | Happy | Off configuration observed, explicit toggle | Conditional base forwarded to writer | Unit | ✅ |
| LT2 | reviews a rejected source toggle before retrying | Error | Conflict or unconfirmed write, then authorized fresh config | No blind retry; original intent explicitly sent with reviewed base | Unit | ✅ |
| LT3 | retains a blocked source toggle when review fails | Error | Latest config GET fails | No revised-save action or second write | Unit | ✅ |
| LT4 | adopts the reviewed source setting without another write | Happy | Operator accepts latest setting | Toggle reflects remote value; no second command | Unit | ✅ |
| LT5 | revises a conflicting source activation in the browser | Integration | Another config command commits before toggle | 412; explicit review; accepted activation | Real browser | ✅ |
| LT6 | hides source review after access is denied | Auth | Review GET returns 403 | Private toggle/review removed; no new write | Unit | ✅ |
| LT7 | retires a pending source review on logout | Auth | Logout before latest read completes | No retired review or revised-save action | Unit | ✅ |

Validation:

- Before production changes: **5 failed / 58 deselected in 8.09 s**.
- Full affected component file: **62 passed / 1 failed in 9.46 s**. The new
  adoption assertion matched both the feature switch and an individual source
  switch. Selectors now name the feature switch explicitly.
- Corrected toggle cases: **5 passed / 58 deselected in 3.57 s**. Added review
  permission/session cases: **2 passed / 63 deselected in 2.92 s**. The rest of
  the component file was not rerun after a selector-only test correction.
- Real backend/browser conditional activation: **1 passed in 39.8 s** including
  process startup. Another configuration command invalidates the visible base;
  activation receives 412, then explicit review/revised save succeeds.
- Frontend typecheck, lint, format (768 files), production build and diff checks
  passed. Build retains the existing >500 kB chunk warning. No full test suite or
  backend schema gate was repeated. These times describe test execution, not
  application performance.

This completes the source-feature toggle's conditional client path. SetupFolder,
StorageConfigCard and Settings (including direct trash retention) remain to
migrate; the transport's optional base still marks that unfinished cutover.


## First-folder conditional activation (M9 increment)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| FF1 | enables sources against the read configuration base | Happy | Feature disabled; explicit folder submit | Exact conditional header before source creation | Unit | ✅ |
| FF2 | blocks source creation until a conflicting activation is reviewed | Error | Activation receives 412 | Folder retained; no source POST until explicit reviewed retry | Unit | ✅ |
| FF3 | blocks source creation until a conflicting activation is reviewed (503 case) | Error | Lost activation response; fresh read says enabled | No repeated activation; source creation follows explicit choice | Unit | ✅ |
| FF4 | keeps a failed first-folder review blocked | Error | Review read fails or is forbidden | No revised action or source POST | Unit | ✅ |
| FF5 | retires a first-folder review with its session | Auth | Logout during review read | Local paths/review removed; no later command | Unit | ✅ |
| FF6 | reaches its first Model entirely through browser controls | Integration | Real setup, upload and mounted source | Conditional activation accepted and scanned Model visible | Real browser | ✅ |
| FF7 | stops connection when sources cannot be enabled | Error | Getting-started parent receives activation 503 | Folder error shown; source creation stopped; explicit review required | Unit | ✅ |

Validation:

- First-folder regressions before the change: **7 failed / 3 passed in 9.28 s**.
  Updated component selection: **10 passed in 4.53 s**. An assertion-only lint
  correction was checked with the two activation cases: **2 passed in 3.20 s**.
- Getting-started parent: **22 passed / 2 failed in 12.73 s**. A test-edit selector
  used an incomplete exact error message; another still expected one alert after
  the explicit review was added. The corrected affected cases passed **2 tests in
  4.09 s**. The source-creation failure still permits ordinary correction/retry;
  only uncertain configuration activation requires the review step.
- Real onboarding: **2 passed in 1.2 minutes**, including the existing responsive
  setup check and the browser-only first Model/folder lifecycle. The latter now
  asserts a successful conditional configuration PUT before observing the scanned
  Model. No extra full-browser suite was run.
- Typecheck, corrected lint, format (768 files), build and diff checks passed.
  The existing large-chunk build warning remains. No performance claim follows
  from these test timings.

SetupFolder now retains its name/path through activation conflicts, uses the
explicitly reviewed configuration for retry, and avoids a duplicate enable write
when a fresh review confirms activation already succeeded. Session retirement
removes the local folder form and prevents delayed review completion from acting.
StorageConfigCard and Settings remain the configuration-cutover consumers; their
migration is required before removing the optional-base compatibility path.


## Storage form conditional editing (M9 increment)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| SF1 | retains a storage draft during background configuration refresh | Race | Draft from v1; new provider values at v2 | Frozen draft fields and v1 header; server conflict surfaced | Unit | ✅ |
| SF2 | reviews a storage conflict before revised save | Error | 412 or uncertain 503; fresh sanitized v2 | Draft retained; reviewed values shown; explicit v2 retry merges only deliberate overrides | Unit | ✅ |
| SF3 | preserves a newer credential draft after an older save finishes | Race | Credential changed during v1 save | New credential remains; next save uses accepted v2 base | Unit | ✅ |
| SF4 | adopts the reviewed storage configuration without writing | Happy | Explicit adoption after conflict | Local override discarded; no second PUT | Unit | ✅ |
| SF5 | retires a pending storage review on logout | Auth | Session ends while review read pending | No retired review or credential displayed | Unit | ✅ |
| SF6 | configures WebDAV through restart with safe GC preview | Integration | Credential draft followed by competing config write | 412, explicit review/retry200, continued real WebDAV lifecycle | Real browser | ✅ |
| SF7 | blocks storage retry when latest configuration cannot be read | Error/Auth | Review GET returns 503 or 403 | No revised save; denied review hidden; no second PUT | Unit | ✅ |

Validation:

- Initial focused regressions: **5 failed in 9.29 s**. The affected component file
  passed **49 tests in 7.75 s** after replacing the fingerprint check with the
  backend editing contract and freezing the initial provider values.
- A stronger revised-save assertion exposed an attempt to resend untouched old
  fields: **2 failed / 1 passed in 3.87 s**. Explicit reviewed retries now merge
  only intentional field overrides onto the reviewed provider configuration.
  Revised-save/newer-credential cases passed **3 tests in 3.96 s**.
- Permission/transient review failures: **2 passed in 3.89 s**. The strengthened
  background-refresh assertion verifies the v1 request header and frozen untouched
  fields: **1 passed in 3.21 s**. A previous three-case run occurred before the
  intended test edit applied; it supplied no new regression evidence.
- Real WebDAV/browser lifecycle: **1 passed in 1.1 minutes**, including startup,
  process restart, credential conflict/review/revised save, upload and safe GC
  behavior. The actual test body took 34.9 s. Other provider/browser suites were
  not repeated.
- Typecheck initially rejected an untranslated label; using the existing `None`
  message corrected it. App/package typechecks, lint, format (768 files), build
  and diff checks passed. Existing large-chunk build warnings remain. Test timings
  are not application-performance measurements.

The JSON storage fingerprint is removed. Each local draft owns an editing base,
initial sanitized provider snapshot and deliberate overrides. Newer credentials
typed during an accepted save remain local and advance to that receipt's base.
Review shows sanitized provider fields and credential-presence indicators only.
Settings remains the final configuration PUT consumer to migrate. Root enrollment
still requires its separate server-side reviewed-root contract; this increment
qualifies storage configuration editing, not that filesystem command or M9 closure.


## Immediate Settings preferences (M9 increment)

Auto-mark, currency and thumbnail width share one concrete configuration-command
owner. It owns the pending preference, conditional base, authorized review and
explicit revised save; the page owns labels and notices. This removes their
competing choice/busy state pairs without generalizing backup or trash forms.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| SP1 | reviews a conflicting display currency before saving again | Error | v1 currency command receives 412; fresh v2 | Intended currency retained; explicit reviewed v2 save | Unit | ✅ |
| SP2 | reads current preferences before retrying an uncertain save | Error | Currency PUT receives 503 | No automatic repeat; fresh review before action | Unit | ✅ |
| SP3a | saves the display currency | Happy | Select currency | Correct configuration payload dispatched | Unit | ✅ |
| SP3b | saves the model image width | Happy | Select image width | Correct configuration payload dispatched | Unit | ✅ |
| SP3c | remembers the known-good choice | Happy | Toggle auto-mark | Accepted choice displayed; control reenabled | Unit | ✅ |
| SP4a | puts the currency back when the server refuses | Auth | Currency write receives 403 | Previous value displayed | Unit | ✅ |
| SP4b | puts the width back when the server refuses | Auth | Width write receives 403 | Previous value displayed | Unit | ✅ |
| SP5 | retires a pending preference review on logout | Auth | Session changes during review GET | No retired review or subsequent PUT | Unit | ✅ |
| SP6 | adopts a reviewed preference without another write | Happy | Operator chooses current server value | Current value displayed; no repeat PUT | Unit | ✅ |
| SP7 | blocks preference retry after a failed review | Error/Auth | Review GET fails or is forbidden | No revised write | Unit | ✅ |
| SP8 | change display currency persists | Integration | Browser selects currency through Settings | Accepted setting survives page reload | Real browser | ✅ |

Validation:

- Initial conflict regressions: **2 failed in 7.03 s**; first affected preference
  selection passed **13 tests in 8.96 s**.
- Expanded selection: **16 passed / 1 failed in 13.85 s**. Adoption passed alone
  (**1 in 5.71 s**) but failed again in the same selection (**16 passed / 1 failed
  in 14.80 s**). New tests now explicitly wait for the initial configuration read
  to enable the select before interacting. The same affected selection passed
  **17 tests in 13.45 s** after that readiness correction. Failure logs are retained;
  this is not a claim that the entire test suite is free of flakes.
- Normalized receipts/read recovery and known-good choice: **4 passed in 6.04 s**.
- Existing real-browser currency persistence flow: **1 passed in 54.3 s** including
  isolated backend setup. The browser test proves persistence; conflict, adoption,
  denied review and session retirement are asserted in the component selection.
- Typecheck initially identified an overly broad numeric thumbnail-width intent;
  the owner now uses the public DTO's closed width type. App/package types, lint,
  format (769 files), production build and diff checks passed. Existing large-chunk
  build warnings remain. No full test suite was run.

`settings-preferences.ts` owns only the three immediate preferences, their captured
base and explicit recovery. The page's three choice/busy pairs are removed;
thumbnail regeneration remains a separate operation. Backup retention/policy and
trash retention still require their own draft-base migration before the client
can make configuration preconditions mandatory. M9 remains active.

The strengthened known-good assertion waits for the accepted command to reenable
the toggle, then checks its value: **1 passed in 5.18 s**.


## Settings retention draft ownership (M9 increment)

Backup and trash retention keep a local text/base pair from the first edit and
reuse the scalar Settings configuration command/review owner. Trash listing no
longer fetches or copies configuration. Explicit revised save uses the currently
edited valid retention value against the authorized reviewed base.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| RT1 | keeps a retention draft across configuration refresh | Edge | First edit v1; config refetch v2 | Typed days retained; PUT still v1 | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::keeps a retention draft across configuration refresh` |
| RT2 | reviews a retention conflict before saving revised days | Error | 412; fresh v2; operator revises days | No blind retry; explicit new value with v2 | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::reviews a retention conflict before saving revised days` |
| RT3 | refreshes trash without replacing retention input | Edge | Local days edited; Refresh trash | Draft retained; no duplicate configuration GET | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::refreshes trash without replacing retention input` |
| RT4 | publishes acknowledged backup policy to shared configuration | Happy | Server normalizes retention | Shared DTO and field show accepted value | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::publishes acknowledged backup policy to shared configuration` |
| RT5 | saves the retention window | Happy | Admin submits valid trash days | Conditional write accepted | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::saves the retention window` |
| RT6 | rejects an empty trash retention draft | Edge | Cleared days field | Save disabled; no coercion to zero | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::rejects an empty trash retention draft` |
| RT7 | keeps retention editing unavailable to members | Error | Member opens Trash | No private configuration read; no retention write | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::keeps retention editing unavailable to members` |
| RT8 | retries a failed trash configuration read | Error | Initial configuration GET 503 | Explicit retry enables retention editor | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::retries a failed trash configuration read` |

| RT9 | expired GC preview is non-destructive without an independent backup | Happy | Real backend; retention saved before preview | Safe GC flow completes after retention save | Playwright | ✅ `tests/e2e-real/settings.spec.ts::expired GC preview is non-destructive without an independent backup` |

Focused validation: initial regression selection **6 failed** before implementation;
retention/GC consumers **18 passed in 12.52 s**. The added permission/recovery
selection found **1 passed, 1 failed** (missing retry); after the fix the combined
selection was **20 passed in 13.90 s**, 165 unrelated cases deselected. Typecheck
identified two stale references and an unsupported test query option, all corrected.
Lint then identified a displaced client directive; restoring it to the first line
resolved the error. Types, lint, format (769 files) and production build passed;
existing large-chunk warnings remain.
No full suite was run for this increment.

The selected real-backend browser flow passed **1/1 in 54.6 s**, including isolated
server startup (test body **15.3 s**). It exercises retention save and the existing
non-destructive GC preview. Concurrent-edit behavior is asserted in the component
regressions above, not inferred from this browser test.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| RT10 | saves backup retention from the backup section | Happy | Admin submits 14 days | Accepted server value shown after save | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::saves backup retention from the backup section` |

The affected existing scalar-preference, backup-retention and Trash-list consumers
passed **28 tests in 16.54 s** (157 unrelated cases deselected). The backup save
fixture now returns an advancing edit receipt and asserts the accepted field.
This increment does not migrate compound backup policy, Trash/GC list ownership,
or the remaining M9 surfaces. M9 remains active.

## Compound backup policy editing and first-party config cutover (M9 increment)

The config aggregate owns its captured policy snapshot and deliberate field changes.
A config conflict or uncertain receipt must stop destination writes until explicit
review. Independent destination receipts remain published after partial failure;
only deliberately changed destination fields are submitted. Connection-version
contracts remain a separate incomplete boundary, not covered by the config ETag.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| BP1 | keeps the original policy snapshot during refresh | Edge | First edit v1; background v2 changes untouched fields | PUT retains v1 snapshot and precondition | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::keeps the original policy snapshot during refresh` |
| BP2 | reviews a failed policy save before destination writes | Error | Config returns 412 or 503; review v2 | No destination writes before explicit revised save | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::reviews a failed policy save before destination writes` |
| BP3 | adopts a reviewed policy without writing destinations | Happy | Config conflict; authorized review | Server policy replaces draft; no further writes | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::adopts a reviewed policy without writing destinations` |
| BP4 | preserves confirmed destination receipts after partial failure | Error | First destination ACK; second fails | Confirmed value retained; retry only remaining destination | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::preserves confirmed destination receipts after partial failure` |
| BP5 | blocks policy retry after an unavailable review | Error | Review returns 403 or 503 | No revised-save action or destination write | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::blocks policy retry after an unavailable review` |
| BP6 | retires a pending policy review on logout | Edge | Logout while review read pending | No old review or destination write after response | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::retires a pending policy review on logout` |
| BP7 | saves the complete automatic-backup policy | Happy | Admin changes policy and one destination flag | Conditional config ACK then deliberate destination patch | Frontend unit | ✅ `src/components/__tests__/settings-panel.test.tsx::saves the complete automatic-backup policy` |
| BP8 | configure automatic backups with independent destinations | Happy | Real server; explicit schedule and remote flag edits | Saved config and destination values read back | Playwright | ✅ `tests/e2e-real/settings.spec.ts::configure automatic backups with independent destinations` |
| BP9 | sends the reviewed configuration base | Happy | First-party config writer | Mandatory conditional headers; advancing receipt | Frontend unit | ✅ `src/lib/api/__tests__/config.test.ts::sends the reviewed configuration base` |
| BP10 | rejects an invalid configuration base before dispatch | Error | Invalid epoch or version | No network write | Frontend unit | ✅ `src/lib/api/__tests__/config.test.ts::rejects an invalid configuration base before dispatch` |
| BP11 | rejects an invalid conditional configuration receipt | Error | Stale or foreign receipt | No confirmation | Frontend unit | ✅ `src/lib/api/__tests__/config.test.ts::rejects an invalid conditional configuration receipt` |
| BP12 | rejects a stale configuration write in the browser fake | Error | Two edits using one base | Second write 412; first value retained | Playwright | ✅ `tests/e2e/settings.spec.ts::rejects a stale configuration write in the browser fake` |
| BP13 | persists a conditional preference in the browser fake | Happy | UI changes currency then reloads | Advancing receipt and persisted selection | Playwright | ✅ `tests/e2e/settings.spec.ts::persists a conditional preference in the browser fake` |

Validation to date: the eight new component cases failed before implementation
(**8 failed in 15.97 s**). After the owner migration, **13 affected cases passed
in 13.92 s**. Mandatory configuration transport/owner tests: **40 passed in 3.70 s**;
legacy library-client fixture updated to the conditional contract: **15 passed in
3.48 s**. Typecheck caught that old call and its fixture's JSON type; both corrected.
Types (app and packages), lint, format (770 files) and production build pass.

The two mock-browser regressions passed **2/2 in 9.7 s**. The fake now uses the
shared complete configuration factory, advances edit versions, rejects stale
preconditions and persists patches; dedicated credential write values are not echoed in its
responses. Existing source-toggle fixture changes advance the same version.

The first selected real-browser run stopped during fixture creation (**422**):
backup-selection flags belong to PATCH, not connection creation. The fixture now
creates then configures the destination through the documented endpoint. The
corrected selected flow passed **1/1 in 47.8 s**, including startup (body **7.9 s**).
The existing source-enabled upload flow also passed **1/1 in 5.1 s** (body **2.4 s**).
No full test suite was run.

All production `updateVaultConfig` callers now carry a captured base; the transport
and Query command require it in their public types and validate it at runtime.
Temporary optional-base/legacy-publication branches are removed. The backend's
legacy compatibility is unchanged and is explicitly outside conditional protection.
M9 still includes the reviewed-root contract, remaining administrative query owners
and independent editable-aggregate contracts. M10 and M11 have not been advanced.

## Reviewed local-root enrollment (M9 increment verified)

The first-party confirmation sends the exact reviewed path. The backend captures
its active local root, compares that path before identity/marker writes, and uses
that same captured root for enrollment. Legacy requests without a reviewed path
retain the old role-only behavior for compatibility; the first-party transport
requires the path. This does not prove filesystem identity after a mount/symlink
replacement; existing enrollment/marker checks retain that responsibility.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| RE1 | rejects an unobserved root change | Error | Data/thumb root changes after review | 409; no identity write; neither path receives a marker | Integration | ✅ `integration/api/v1/test_config.py::TestStorageRootEnrollment::test_rejects_an_unobserved_root_change` |
| RE2 | superuser can enroll an existing markerless root | Happy | Matching reviewed path or legacy request | Exact root marker created | Integration | ✅ `integration/api/v1/test_config.py::TestStorageRootEnrollment::test_superuser_can_enroll_an_existing_markerless_root` |
| RE3 | rejects member root enrollment | Error | Member confirms current root | 403; no marker | Integration | ✅ `integration/api/v1/test_config.py::TestStorageRootEnrollment::test_rejects_member_root_enrollment` |
| RE4 | rejects an empty reviewed path | Edge | Empty explicit path | 422; no marker | Integration | ✅ `integration/api/v1/test_config.py::TestStorageRootEnrollment::test_rejects_an_empty_reviewed_path` |
| RE5 | POSTs an explicit confirmation for the selected root role | Happy | Reviewed role and path | Both sent with confirmation | Frontend unit | ✅ `src/lib/api/__tests__/config.test.ts::enrollStorageRoot::POSTs an explicit confirmation for the selected root role` |
| RE6 | offers explicit enrollment for a missing legacy marker | Happy | Operator confirms displayed path | Request carries displayed path | Frontend unit | ✅ `src/components/__tests__/storage-config-card.test.tsx::offers explicit enrollment for a missing legacy marker` |
| RE7 | dismisses enrollment after a server-side root change | Error | Server returns storage_review_changed | Confirmation closed; changed-root message; no retry | Frontend unit | ✅ `src/components/__tests__/storage-config-card.test.tsx::dismisses enrollment after a server-side root change` |
| RE8 | rejects a different path to the same root | Error | Reviewed symlink alias differs from configured path | 409; no marker | Integration | ✅ `integration/api/v1/test_config.py::TestStorageRootEnrollment::test_rejects_a_different_path_to_the_same_root` |
| RE9 | rejects enrollment for a nonlocal backend | Error | S3 active; reviewed local path supplied | 409 storage_backend_not_local; no marker | Integration | ✅ `integration/api/v1/test_config.py::TestStorageRootEnrollment::test_rejects_enrollment_for_a_nonlocal_backend` |
| RE10 | requires an explicit confirmation | Error | Reviewed path without confirmation | 400 storage_root_confirmation_required | Integration | ✅ `integration/api/v1/test_config.py::TestStorageRootEnrollment::test_requires_an_explicit_confirmation` |

Focused verification:

- Backend enrollment selection: **9 passed**, 61 deselected in **5.52 s**.
- Complete affected config API and StorageConfigCard Vitest files: **81 passed** in **9.68 s**.
- OpenAPI snapshot update test: **1 passed** in **5.75 s**; inspected diff adds only nullable optional `expected_path` with a nonempty-string constraint.
- Frontend typecheck (app/UI/domain), scoped oxlint/oxfmt, backend scoped Ruff, and scoped backend Pyright (0 errors/warnings) passed.

Preserved red runs: backend **3 failed / 4 passed** in **6.53 s**, because
`expected_path` was forbidden; UI **2 failed / 5 passed / 45 skipped** in **5.74 s**,
because the request omitted the path and the new dismissal assertion ran during
the existing exit animation. The latter now waits for observable dialog removal;
no production dialog behavior changed. Logs are retained locally under
`/tmp/printstash-m9-root-{red,ui-red,green,ui-green,openapi,types,backend-types}.log`.
No full suite, coverage gate, browser server or build was run for this bounded
contract regression. M9 remains open pending its reopened M7/M8 prerequisites
and remaining administration work.


## Provider accounts checkpoint after M8 acceptance

M8 prerequisites are locally accepted at `ea144c7c`. Provider/device read ownership
and command lifetime are implemented in the next bounded M9 increment. Focused
validation: 26 tests passed; format, lint and types passed. The detailed matrix
and remaining browser-name contract are in `frontend-m9-provider-validation.md`.
M9 remains open; this checkpoint does not accept all administration workflows.

The subsequent browser-name backend increment adds conditional-v1 support,
monotonic versions and a pairing-incarnation editing history. Its API, migration
and independent-session tests are recorded in the provider matrix. The frontend
cutover remains required; M9 has not been closed.

The provider/browser frontend cutover is now locally accepted: captured editing
bases, explicit review/adoption/revised saves, session retirement, extension
adapter compatibility and a real two-editor browser flow. See the complete matrix
in `frontend-m9-provider-validation.md`. Next M9 seam: notifications; subsequent
remote source/connection edit contracts and remaining Settings reads stay open.
