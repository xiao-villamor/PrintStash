# M1 session and transport qualification

Prerequisite M0 is closed at `9f1d6bd7`. M1 alone is active. This checkpoint
reconciles the previously implemented transport foundation with the integrated
source; it does not advance the later feature milestones.

## Callback lifetime regression plan (before tests)

`artifact-upload.ts::controlledOptions` registers an active upload before invoking
`onSession`. Both creation and resume enter their cleanup `try/finally` only after
that callback and the outer session assertion. A synchronous callback exception
or session retirement can therefore leave an upload reported as active even
though the operation has rejected. The intended outcome is release of the local
controller on every exit; a durable resumable upload remains a server concern.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | releases the transfer when its session callback retires authentication | Edge | New upload or resume; onSession retires auth | AbortError, inactive public transfer status, no plan request | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::releases the transfer when its session callback retires authentication` |
| 2 | releases the transfer when its session callback fails | Error | New upload or resume; onSession throws | Original error, inactive public transfer status, no plan request | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::releases the transfer when its session callback fails` |
| 3 | allows the session callback to pause its transfer | Happy | New upload or resume; onSession requests pause | Pause succeeds, AbortError, inactive transfer, no plan request | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::allows the session callback to pause its transfer` |

Production scope: `frontend/src/lib/artifact-upload.ts`; its existing test mirror;
this validation record. No new controller abstraction, API endpoint or persistence
format. Keep controller registration before the callback, put callback delivery
inside the existing cleanup boundary, and retain first-party callers' behavior.

## Collection query cancellation plan (before tests)

Cancelling an obsolete Query must abort its HTTP read. The current collection
children, path lookup, id lookup, search and README query functions do not consume
the Query signal, although the shared transport supports it. Session retirement
already fences these reads; this gap wastes obsolete work rather than proving a
cross-session publication leak. Preserve endpoint parameters, result shapes and
pagination keys. Pass the same signal through explicit refresh reads too.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 4 | aborts the obsolete collection HTTP read | Edge | Real query hook and endpoint; held HTTP response; cancel Query; children/path/id/search/README variants | The native HTTP signal becomes aborted | Frontend unit | ✅ `src/lib/__tests__/queries.test.tsx::aborts the obsolete collection HTTP read: $label` |

Scope: optional transport options on the five taxonomy reads, their Query owners,
and the existing collection refresh call site. This does not change collection
behavior, introduce a cache or advance the navigation milestone. Existing query
and endpoint tests retain result, input, continuation and disabled-read controls.

## Qualification results

The callback cases ran before the cleanup change: four failures (retired auth or
throwing callback, each for creation/resume) and two passing pause controls. The
five native cancellation cases then failed before signal propagation. After both
changes, **143/143 tests in five files passed** (3.85 s): `queries`, `taxonomy`,
`artifact-upload`, `session-transport`, and `api/request`. Existing query tests
still assert pagination results and disabled reads. The affected Library refresh,
continuation and authorization selection passed **16/16** (12.75 s; 161 unrelated
cases not selected). This is dependency regression coverage, not M5 acceptance.

The preceding integrated transport/API/auth/asset/dependency/hygiene run passed
**783 tests and failed one** (48 files, 18.76 s). The sole failure is the already
pending M5 `tests/e2e/library-snapshot.spec.ts`: its three tests are outside a
`describe`. It is preserved for M5; this checkpoint does not claim that the global
hygiene gate passes. New M1 tests live inside their existing or new describes.

Real-backend authentication passed **3/3** (56.5 s, Chromium, no retries): real
form sign-in, rejected wrong password, API-key login followed by revocation.
This run preceded only the collection signal plumbing; no auth source changed
since it. App, UI and domain typechecks passed, as did application lint with
warnings denied. `pnpm format:check` passed all 745 matched files.
No full coverage, full backend run, final CI or performance improvement is claimed.

## Transport ownership and remaining migration boundary

`session-transport.ts::withSessionRequest` owns an operation's authentication
incarnation and caller cancellation through response consumption. `auth-store`
retires that incarnation on session replacement, including a new login for the
same account. `request.ts::requestApi` scopes headers, body, errors and mutation
acknowledgements; `getJson` always requests `cache: no-store`. There is no JSON TTL
map or shared pending-request map in that transport. Query owns shared JSON
freshness; imperative commands may still read the endpoint without owning a
second transport cache. This is not a claim that all component remote state has
already moved to Query: that is explicitly M7–M9 work.

The existing [transport matrix](frontend-state-validation.md) rows 1–34 supplies
header/body, same-account replacement, stale 401, streaming bytes, XHR progress,
current 401, download publication and acknowledged-mutation contracts. The current
integrated run above exercises their owning files. The new four rows here cover
the gaps found while qualifying M1. The master matrix now links M1 rows 28–31 to
actual assertions, without accepting later feature rows early.

Direct HTTP review: the app's native `fetch` and `XMLHttpRequest` call sites are
confined to `lib/api/request.ts` and `lib/artifact-upload.ts` (signed storage PUT).
The latter runs under the upload/session scope through the receipt. Binary/text
loaders enter the scoped transport; `components/stl-viewer.tsx::Mesh` uses the
blob URL produced by the protected loader. `api/search.ts` owns an interaction
deadline and passes cancellation into the common transport. The UI/domain
packages have no application HTTP owner. Socket lifecycle is M7, asset admission
and leases are M6, and neither is claimed closed by this transport checkpoint.

The extension is a separate client: `browser-extension/core.ts::fetchVault` owns
Vault transport and target/credential rules; `capture-transport.ts` scopes staged
capture; `popup.ts` connects transport to the connection operation lifetime.
It does not import the web Query cache. Its relevant qualification is preserved
in [the extension record](extension-disconnect-permission-validation.md): 272
unit tests in 13 files and the three target builds. `git diff 1bfdc041 HEAD --
browser-extension` is empty at this checkpoint; unchanged builds were not rerun.
Its full platform/deployment qualification remains M10.

### Explicit imperative readers to preserve during the cutover

These inspected consumers explain why removing the redundant transport cache
must not also remove direct endpoint access. Their feature ownership is reviewed
in the named later milestone, not migrated in M1:

| Consumer / symbols | Current purpose | Next owner / milestone |
|---|---|---|
| `lib/auth-provider.tsx` identity bootstrap/login/refresh | Verify an identity before publishing session state | Auth lifecycle, preserved in M1; entry-route contracts in M9 |
| `lib/artifact-upload.ts` status/plan | Drive resumable transfer, then publish receipts | Upload command; durable progress presentation in M7 |
| `components/archive-review.tsx` `getJobStatus` effect | Read the archive manifest | Work/ingest Query in M7 |
| `pages/inbox-detail.tsx` `searchCollections` | Resolve editable import destination during a command | Inbox workflow in M7; existing session/route fences retained |
| `components/model-tags-dialog.tsx`, `multipart-model-browser.tsx` conflict review | Explicit latest-version read before reviewing a draft | Library mutation owner in M4 |
| `components/model-detail/revisions-tab.tsx`, `print-history-section.tsx`, `share-dialog.tsx` | Detail subresource reads | Detail contracts in M8 |
| `pages/multipart-builds.tsx` composition and selected Model effects | Resolve display/selected revision metadata | Builds owner in M8 |
| `pages/getting-started.tsx` `discoverLibraryLocations` | Discover user-selectable locations | Entry workflow in M9 |
| `components/maintenance-panel.tsx` list/detail/timer reads | Audit status, findings, backup verification | Administration workflow in M9 |
| `components/audit-schedule-panel.tsx` policy/history effect | Display schedule and prior runs | Administration workflow in M9 |

The endpoint inventory below is a static call inventory, not a claim of full
manual review of each feature. It names all 110 `getJson` calls across 36 endpoint
files in the integrated app. Feature Queries and imperative readers use these
same endpoints. No dependency, generated file or build artifact was included.

| File (under `frontend/`) | JSON read symbols |
|---|---|
| `src/components/slicer-open-button.tsx` | `openInSlicer` |
| `src/lib/api/artifact-cache.ts` | `artifactCacheApi.read`, `artifactCacheApi.reset` |
| `src/lib/api/artifact-uploads.ts` | `getArtifactUpload`, `getArtifactUploadPlan` |
| `src/lib/api/auth.ts` | `getAuthProviders`, `getMe`, `listAdminUsers`, `listApiKeys` |
| `src/lib/api/backup.ts` | `listBackupRuns`, `listBackupSources`, `listBackups`, `listUnownedLocalBackups`, `listUnownedRemoteBackups`, `listUnownedS3Backups` |
| `src/lib/api/captions.ts` | `getCaption` |
| `src/lib/api/config.ts` | `getHealthDetails`, `getLatestRelease`, `getSetupStatus`, `getStorageProviders`, `getVaultConfig` |
| `src/lib/api/documents.ts` | `getDocument`, `listDocuments` |
| `src/lib/api/filaments.ts` | `listFilamentProfiles` |
| `src/lib/api/fleet.ts` | `getFleetSummary`, `listFleetQueue`, `listMaintenanceLog`, `listMaintenanceWindows` |
| `src/lib/api/gc.ts` | `getActiveGcPlan` |
| `src/lib/api/inbox.ts` | `getPendingImport`, `listPendingImports` |
| `src/lib/api/jobs.ts` | `getJobStatus`, `listJobs`, `listWorkJobs` |
| `src/lib/api/libraries.ts` | `discoverLibraryLocations`, `listExternalLibraries` |
| `src/lib/api/library-browse.ts` | `getLibraryRevision`, `getLibraryThumbnails`, `listLibraryPage` |
| `src/lib/api/maintenance.ts` | `getLatestVaultAudit`, `getVaultAudit`, `listAuditPolicies`, `listVaultAudits` |
| `src/lib/api/models.ts` | `getArtifactOutcomes`, `getModel`, `getModelFacets`, `getModelPrintJobs`, `getModelPrinterFiles`, `getVaultStats`, `listModelPage`, `listModels`, `listOutlinerModels`, `listTrash` |
| `src/lib/api/multipart-builds.ts` | `getMultipartBuild`, `listMultipartBuilds` |
| `src/lib/api/multipart-models.ts` | `getMultipartModel`, `listMultipartModelCandidates`, `listMultipartModels` |
| `src/lib/api/notifications.ts` | `getNotificationsSettings`, `listNotificationDeliveries` |
| `src/lib/api/outliner.ts` | `listOutlinerCollections`, `listOutlinerEntries`, `searchOutliner` |
| `src/lib/api/printer-profiles.ts` | `listPrinterProfiles` |
| `src/lib/api/printers.ts` | `getDashboard`, `getMoonrakerConfig`, `getPrinter`, `getPrinterDiagnostics`, `getPrinterMaterialState`, `getPrinterStatus`, `listPrinterFiles`, `listPrinterJobs`, `listPrinterPermissions`, `listPrinters` |
| `src/lib/api/provenance.ts` | `getModelProvenance`, `getModelSourceCover` |
| `src/lib/api/provider-connections.ts` | `listBrowserDevices`, `listProviderConnections` |
| `src/lib/api/saved-views.ts` | `listSavedViews` |
| `src/lib/api/search.ts` | `getSearchSettings`, `getSearchStatus`, `listInferenceModels`, `listSearchGenerations` |
| `src/lib/api/share.ts` | `listModelShares` |
| `src/lib/api/similarity.ts` | `getSimilarityCandidate`, `getSimilarityRun`, `getSimilarityStatus`, `listSimilarityCandidates`, `listSimilarityRuns` |
| `src/lib/api/spoolman.ts` | `getSpoolmanStatus`, `listSpools` |
| `src/lib/api/statistics.ts` | `getPrintStatistics` |
| `src/lib/api/storage-connections.ts` | `listStorageConnections` |
| `src/lib/api/storage-inventory.ts` | `getCollectionStorage`, `getModelStorage`, `getStorageCapacityActivity`, `getStorageCleanupOpportunities`, `getStorageInventory` |
| `src/lib/api/taxonomy.ts` | `getCollectionReadme`, `listCollectionChildren`, `listCollectionPermissions`, `listTags`, `lookupCollection`, `lookupCollectionById`, `searchCollections` |
| `src/lib/api/vault-migration.ts` | `getVaultMigration`, `getVaultMigrationReport`, `listVaultMigrations` |
| `src/lib/api/work.ts` | `getWorkOverview`, `listDerivatives` |

### Compatibility invalidation: named callers and removal deadline

**Delete by M10, before M11 acceptance.** The temporary dependency exception in
`scripts/dependency-boundaries.ts::MIGRATION_EXCEPTIONS` permits only
`request.ts → query-client.ts` via `queryClient` and `invalidateQueriesForPath`.
`invalidateApiCache` is an invalidation adapter, not a GET cache. Its mutation
wrappers are `sendJson`, `sendForm`, `sendAction`, `sendFormWithProgress` and
`requestMutation`; their 149 call sites in 27 files are named below. `fresh` is a
no-op compatibility option with the same deadline. Each later feature owner must
replace these invalidation dependencies before the adapter and exception are
removed. Reintroducing a transport TTL/dedup cache is not a rollback strategy.

| File (under `frontend/`) | Caller symbols |
|---|---|
| `src/lib/api/artifact-cache.ts` | `artifactCacheApi.clear`, `artifactCacheApi.reset`, `artifactCacheApi.save` |
| `src/lib/api/backup.ts` | `adoptLocalBackup`, `adoptRemoteBackup`, `adoptS3Backup`, `createBackup`, `deleteBackup`, `restoreBackup`, `uploadBackup` |
| `src/lib/api/config.ts` | `enrollStorageRoot`, `prepareSetupStorage` |
| `src/lib/api/documents.ts` | `createDocument`, `deleteDocument`, `updateDocument`, `uploadDocument`, `uploadDocumentImage` |
| `src/lib/api/filaments.ts` | `createFilamentProfile`, `deleteFilamentProfile`, `updateFilamentProfile` |
| `src/lib/api/fleet.ts` | `checkFleetCompatibility`, `createFleetBatch`, `decideFleetOperatorGate`, `deleteFleetJob`, `enqueueFleetJob`, `resolveFleetJob`, `retryFleetJob`, `updateFleetJob` |
| `src/lib/api/gc.ts` | `abortGcPlan`, `approveGcPlan`, `createGcPlan`, `finalizeGcPlan` |
| `src/lib/api/inbox.ts` | `batchPendingImports`, `capturePendingImport`, `dismissPendingImport`, `importPendingImport`, `retryPendingImport`, `updatePendingImport` |
| `src/lib/api/jobs.ts` | `discardJobStaging` |
| `src/lib/api/libraries.ts` | `createExternalLibrary`, `deleteExternalLibrary`, `enrollExternalLibraryRoot`, `scanExternalLibrary`, `scanExternalLibraryPath`, `updateExternalLibrary` |
| `src/lib/api/maintenance.ts` | `cancelVaultAudit`, `ignoreAuditFinding`, `repairAuditFinding`, `saveAuditPolicy`, `skipAuditSlot`, `startVaultAudit`, `verifyBackup` |
| `src/lib/api/models.ts` | `addGcodeRevision`, `batchDeleteModels`, `batchMoveModels`, `batchSetRevisionLabels`, `batchTagModels`, `createManualPrintJob`, `deleteFileRevision`, `deleteModel`, `importLibraryArchive`, `importPrintJobsFromPrinter`, `ingestModel`, `ingestOrca`, `ingestUrl`, `inspectArchive`, `purgeExpiredTrash`, `purgeModel`, `replaceFileTags`, `restoreModel`, `restoreSourceFile`, `selectArchiveEntries`, `selectCollectionMembers`, `selectModelFiles`, `starModel`, `trashSourceFile`, `unstarModel`, `updateFileRevision`, `updateModel` |
| `src/lib/api/multipart-builds.ts` | `archiveMultipartBuild`, `confirmBuildResult`, `createMultipartBuild`, `duplicateMultipartBuild`, `queueBuildPart`, `selectBuildRevision` |
| `src/lib/api/multipart-models.ts` | `createMultipartModel`, `deleteMultipartModel`, `starMultipartModel`, `unstarMultipartModel` |
| `src/lib/api/notifications.ts` | `createNotificationChannel`, `deleteNotificationChannel`, `setNotificationsEnabled`, `testNotificationChannel`, `updateNotificationChannel` |
| `src/lib/api/printer-profiles.ts` | `createPrinterProfile`, `deletePrinterProfile`, `updatePrinterProfile` |
| `src/lib/api/printers.ts` | `createPrinter`, `deletePrinter`, `emergencyStopPrinter`, `homePrinter`, `printerControl`, `sendToPrinter`, `setPrinterTemperature`, `updatePrinter`, `updatePrinterManualMaterialState` |
| `src/lib/api/provenance.ts` | `deleteModelSourceCover`, `patchModelProvenance`, `putModelSourceCover` |
| `src/lib/api/provider-connections.ts` | `authorizeMyMiniFactory`, `connectCults`, `createBrowserPairing`, `disconnectProvider`, `renameBrowserDevice`, `revokeBrowserDevice` |
| `src/lib/api/saved-views.ts` | `createSavedView`, `deleteSavedView`, `updateSavedView` |
| `src/lib/api/share.ts` | `createModelShare`, `revokeShare` |
| `src/lib/api/spoolman.ts` | `syncSpoolmanFilaments`, `testSpoolman`, `updateSpoolman` |
| `src/lib/api/storage-inventory.ts` | `cleanupStorageCache`, `cleanupStorageStaging`, `sampleStorageInventory` |
| `src/lib/api/system.ts` | `restartPrintStash` |
| `src/lib/api/taxonomy.ts` | `createCollection`, `createTag`, `deleteCollection`, `deleteTag`, `moveCollection`, `renameCollection`, `replaceCollectionTags`, `setCollectionReadme`, `uploadCollectionImage` |
| `src/lib/api/vault-migration.ts` | `auditVaultMigration`, `cleanupVaultMigration`, `cutoverVaultMigration`, `pauseVaultMigration`, `preflightVaultMigration`, `recoverVaultMigration`, `resumeVaultMigration`, `retainVaultMigration`, `retryVaultMigration`, `startVaultMigration` |
| `src/lib/api/work.ts` | `regenerateDerivatives`, `retryDerivative` |

## M1 acceptance

M0 is closed; M1's own acceptance requirements are qualified by the matrices and
runs above. This is a local milestone closure, not final delivery. M2 is next.
The unrelated pending M5 hygiene failure remains a required fix before its own
qualification and before final CI. Raw run logs and the static inventories are
retained locally under `reports/frontend-implementation/m1-closure-2026-10-07`.

Rollback is a coherent transport change: keep the session fences and the callback
cleanup. A specific signal propagation issue can be reverted at its endpoint and
Query consumer together; do not reintroduce the redundant JSON cache. Removal of
compatibility wrappers waits for their named callers, with the hard M10 deadline.
