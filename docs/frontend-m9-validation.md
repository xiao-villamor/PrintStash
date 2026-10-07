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
