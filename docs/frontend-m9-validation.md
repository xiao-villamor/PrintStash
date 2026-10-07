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
