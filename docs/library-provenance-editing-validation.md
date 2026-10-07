# Versioned Source editing — M4

M3 is closed. This is a bounded M4 increment, not closure of M4.

## Observed gap and contract

Before these increments, `SourceTab` loaded provenance and covers into separate
effect-owned state. `api/provenance.ts` sent override/cover writes without the
conditional editing contract; applying a source title/description also omitted
the Model version. The backend supported conditional claims, but provenance
reads exposed no editing base. Guessing a version from a later unrelated Model GET could pair old
fields with a newer editing permission and silently overwrite a concurrent edit.

The backend increment extends the existing provenance projection with its Model edit version and each
source's nullable private cover metadata. Read the edit version before composing
the projection and verify it again before returning. A changed version returns
`409 edit_snapshot_changed`; no mixed editing snapshot is published. The GET ETag
uses that exact receipt version. Override acknowledgements use the same receipt.
This provides one coherent source-editing input without a generic repository or
an extra cache. Existing source-cover endpoints remain compatible.

The frontend cutover below now consumes that contract. Source has one Query
owner, conditional writes and explicit conflict recovery. Multipart auxiliary
recovery, the Library move command's remaining unversioned `updateModel` call,
and physical-restore editing-token identity remain M4 work. No complete
first-party conditional-edit guarantee is claimed yet.

## Backend contract matrix

| #   | Behaviour (test name)                                          | Category | Precondition / input                                  | Observable outcome asserted                                        | Tier                   | Status                                                                                                                    |
| --- | -------------------------------------------------------------- | -------- | ----------------------------------------------------- | ------------------------------------------------------------------ | ---------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 1   | returns the Model editing base with provenance                 | Happy    | Model with captured source                            | Required edit_version matches Model and GET ETag                   | Integration            | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_returns_model_editing_base`               |
| 2   | returns an editing base without sources                        | Edge     | Model has no capture                                  | Valid edit_version, empty sources, matching ETag                   | Integration            | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_returns_editing_base_without_sources`     |
| 3   | reports an absent source cover explicitly                      | Edge     | Source without a cover                                | Required cover field is null                                       | Integration            | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_reports_absent_cover`                     |
| 4   | includes only private cover metadata                           | Happy    | Uploaded source cover                                 | Same public metadata as cover route; no storage key or raw bytes   | Integration            | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_includes_only_private_cover_metadata`     |
| 5   | acknowledges the override editing version                      | Happy    | Conditional override from displayed version           | Changed field and advanced edit_version match acknowledgement ETag | Integration            | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_acknowledges_override_editing_version`    |
| 6   | rejects a changed editing snapshot                             | Error    | Real writer advances version during projection        | 409 edit_snapshot_changed; no source payload                       | Integration            | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_rejects_changed_editing_snapshot`         |
| 7   | refuses an absent Model editing snapshot                       | Error    | Projection requested for missing Model                | Explicit model_not_found, no fabricated version                    | Integration            | ✅ `integration/modules/library/model_views/test_detail.py::TestProvenanceDetail::test_refuses_absent_model`              |
| 8   | scopes cover metadata to its Model                             | Error    | Two Models each own a source cover                    | Projection contains only requested Model source/cover identities   | Integration            | ✅ `integration/modules/library/model_views/test_detail.py::TestProvenanceDetail::test_scopes_cover_metadata_to_model`    |
| 9   | keeps source projection query count fixed                      | Edge     | One source grows to ten with covers                   | All sources returned with the same SELECT count                    | Integration            | ✅ `integration/modules/library/model_views/test_detail.py::TestProvenanceDetail::test_keeps_query_count_fixed`           |
| 10  | reads a versioned source on PostgreSQL                         | Happy    | Real PostgreSQL Model, source and cover               | Editing version and matching cover metadata returned               | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestProvenanceSnapshot::test_reads_versioned_source`                     |
| 11  | rejects a concurrent PostgreSQL edit during source composition | Error    | Separate writer commits while provenance read is open | edit_snapshot_changed instead of a mixed snapshot                  | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestProvenanceSnapshot::test_rejects_concurrent_edit_during_composition` |

Production ownership stays in `modules/library/model_views/detail.py`; schema
and route composition expose the receipt. No new migration or portable capture
manifest change is needed. Existing permission/privacy/validation cases remain
applicable. Rollback removes the additive projection fields and its consumer
cutover together; no persisted data needs conversion.

## Actual validation

The first six new integration cases failed before production edits (4.77 s):
missing editing versions/cover metadata and a mixed read accepted as HTTP200.
The complete provenance API file then passed **57/57** (13.23 s). An intermediate
run encountered an incomplete local patch because an editing-script assertion
stopped after the schema change; it is retained separately, not counted as a
second product regression or hidden in the final pass.

Projection identity/query-count checks and the OpenAPI snapshot passed **4/4**
(5.54 s). Real PostgreSQL passed **2/2** (42.85 s), including a separately committed
concurrent writer. The existing backend capture/import/recapture lifecycle passed
**1/1** (4.58 s). Frontend Source/component and API tests passed **23/23** (5.31 s).
The TypeScript DTO and existing fixtures now describe the additive fields.

Backend lint and Pyright passed. Frontend app/UI/domain type checks, affected
lint and formatting passed. The regenerated OpenAPI diff was inspected: only
required positive `ModelProvenanceRead.edit_version` and required nullable
`ProvenanceSourceRead.cover` were added. Portable capture schema_version remains
2 and no persisted schema changed. Existing cover endpoints retain their payloads.

These backend results qualified commit `d4637e13`; at that checkpoint the Source
UI still used effect-owned reads and unconditioned writes. The following cutover
replaces them. Final full gates and latest-commit CI remain outstanding for the
complete implementation.

## Source frontend cutover — assessed behaviour matrix

This follows the qualified projection above. The Source feature owns its Query
record and confirmed receipts; fields retain only local draft text and the version
at which editing began. A failed or ambiguous write requires a fresh, coherent
Model/Source review before an explicit retry. No M5 work is included.

| #   | Behaviour (test name)                                     | Category | Precondition / input                              | Observable outcome asserted                                | Tier          | Status                                                                                                                                                                              |
| --- | --------------------------------------------------------- | -------- | ------------------------------------------------- | ---------------------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F1  | sends conditional override edits                          | Happy    | Displayed version, override or clear              | PATCH includes exact If-Match and opt-in header            | Frontend unit | ✅ `src/lib/api/__tests__/provenance.test.ts::sends conditional $label edits`                                                                                                       |
| F2  | sends conditional cover changes                           | Happy    | Cover upload or deletion from displayed version   | PUT/DELETE headers identify frozen base                    | Frontend unit | ✅ `src/lib/api/__tests__/provenance.test.ts::sends conditional cover $label`                                                                                                       |
| F3  | preserves the acknowledged cover version                  | Edge     | Server version advances by more than one          | Receipt contains exact ETag version                        | Frontend unit | ✅ `src/lib/api/__tests__/provenance.test.ts::sends conditional cover $label (exact version 9 from base 4)`                                                                         |
| F4  | rejects an unusable acknowledgement                       | Error    | Missing, wrong-entity, stale or malformed version | Promise rejects; no invented successful receipt            | Frontend unit | ✅ `src/lib/api/__tests__/provenance.test.ts::rejects a $label cover acknowledgement; rejects an unusable override acknowledgement; rejects cover metadata for another source`      |
| F5  | cancels a retired Source read                             | Edge     | Caller aborts pending GET                         | Transport signal aborts; no returned payload               | Frontend unit | ✅ `src/lib/api/__tests__/provenance.test.ts::cancels a retired Source read`                                                                                                        |
| F6  | reads source covers from the shared snapshot              | Happy    | Provenance with cover metadata                    | Cover controls reflect DTO; no secondary metadata GET      | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::reads source covers from the shared snapshot`                                                                        |
| F7  | distinguishes a failed read from empty provenance         | Error    | Initial GET fails then succeeds                   | Error and Retry; successful retry renders source           | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::distinguishes a failed read from empty provenance`                                                                   |
| F8  | freezes the version when a field draft opens              | Edge     | Background refresh during editing                 | Save sends original version with retained draft            | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::freezes the version when a field draft opens`                                                                        |
| F9  | publishes a confirmed Source receipt ahead of a late read | Edge     | Older GET resolves after PATCH                    | Confirmed value remains visible                            | Frontend unit | ✅ `src/features/library/__tests__/provenance.test.tsx::publishes a confirmed Source receipt ahead of a late read`                                                                  |
| F10 | blocks automatic retry after an uncertain write           | Error    | 412, network error or 5xx                         | Draft retained; no repeat write before review              | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::retries the retained draft after $label review (412, 503, lost response)`                                            |
| F11 | retries the retained draft against a reviewed version     | Happy    | Fresh matching Model/Source review                | Explicit retry sends retained command and reviewed base    | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::retries the retained draft after $label review`                                                                      |
| F12 | adopts the reviewed Source without another write          | Happy    | Conflict followed by review                       | Draft closes; latest value visible; no extra PATCH         | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::adopts the reviewed Source without another write`                                                                    |
| F13 | refuses an incoherent review                              | Edge     | Model and provenance versions differ              | Retry stays unavailable; draft preserved                   | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::refuses an incoherent review`                                                                                        |
| F14 | preserves a draft when review fails                       | Error    | Review GET 503                                    | No retry-write affordance until successful review          | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::preserves a draft when review fails`                                                                                 |
| F15 | hides private Source data after denied review             | Error    | Review 401, 403 or 404                            | Captured values and write actions disappear                | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::hides private Source data after denied review`                                                                       |
| F16 | refuses retry after edit permission is revoked            | Error    | Fresh review role view                            | Latest values readable; retry disabled                     | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::refuses retry after edit permission is revoked`                                                                      |
| F17 | suppresses duplicate submissions                          | Edge     | Double action while write held                    | Exactly one request                                        | Frontend unit | ✅ `src/features/library/__tests__/provenance.test.tsx::suppresses duplicate submissions`                                                                                           |
| F18 | retires old-session Source effects                        | Edge     | Session changes during write or review            | No new-session cache publication or success feedback       | Frontend unit | ✅ `src/features/library/__tests__/provenance.test.tsx::ignores a Source acknowledgement after $label; retires an old-session Source review`                                        |
| F19 | retains the selected cover for explicit retry             | Error    | Cover PUT conflict then review                    | Same File retried with reviewed version                    | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::retains the selected cover for explicit retry`                                                                       |
| F20 | applies captured Model fields conditionally               | Happy    | Use source title or description                   | Model PATCH uses Source base; acknowledged Model published | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::applies captured Model $label conditionally; reviews the current Model value before replacing it with captured text` |
| F21 | reviews a repeated conflict                               | Edge     | Another writer changes after review               | Second 412 requires another fresh review                   | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::reviews a repeated conflict`                                                                                         |
| F22 | edits captured metadata with real conflict recovery       | Happy    | Two editors against real backend                  | Conflict review and explicit retry persist selected draft  | Playwright    | ✅ `tests/e2e-real/provenance.spec.ts::edits captured metadata with real conflict recovery`                                                                                         |

| F23 | retires a draft when changing Model identity | Edge | Detail changes Model while write pending | New Model data cannot receive old command feedback | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::retires a draft when changing Model identity` |
| F24 | preserves an editable draft after validation rejection | Error | Write returns 422 | Draft retained and Save enabled; no invented success | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::preserves an editable draft after validation rejection` |
| F25 | hides cached Source data after a denied refresh | Error | Warm source followed by 403 GET | Cached private values disappear | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::hides cached Source data after a denied refresh` |
| F26 | freezes cover confirmation versions | Edge | Background refresh while replace/delete confirmation open | Confirmed request uses original version | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::freezes the cover $label confirmation version` |
| F27 | refreshes Source on return after a departed editor writes | Edge | Write started, editor unmounted before acknowledgement | Snapshot remains stale for next reader | Frontend unit | ✅ `src/features/library/__tests__/provenance.test.tsx::refreshes Source on return after a departed editor writes` |

| F28 | requests the reviewed cover version | Edge | Another editor replaces the cover | Review requests fresh image bytes; adopted cover reuses that version | Frontend unit | ✅ `src/components/model-detail/__tests__/source-tab.test.tsx::requests the reviewed cover version` |
| F29 | exposes the Source feature interface to its UI | Happy | Source component imports its feature owner | Dependency graph permits public owner; private imports still rejected | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::enforces the production repository graph` |

| F30 | refuses an invalid editing base before sending | Error | Non-positive or unsafe caller version | No HTTP write and explicit rejection | Frontend unit | ✅ `src/lib/api/__tests__/provenance.test.ts::refuses invalid editing base before sending` |
| F31 | rejects a malformed Source read version | Error | Missing or invalid edit_version in GET | Error instead of an editable snapshot | Frontend unit | ✅ `src/lib/api/__tests__/provenance.test.ts::rejects malformed Source read version` |

| F32 | preserves existing Source browser flows | Happy | Detail layout and cover replacement; captured metadata override/restore | Existing route contracts pass against versioned mock API | Playwright | ✅ `tests/e2e/model-detail.spec.ts::Source tab displays and replaces a private representative cover; Source tab keeps metadata readable at the minimum details-panel width; tests/e2e/pending-imports.spec.ts::completes the URL capture review lifecycle with partial results; tests/e2e/uploads.spec.ts::URL capture is reviewable, reports a partial result, and restores a source override` |

| F33 | cancels the other review read after failure | Error | One review endpoint fails while the other is pending | Outstanding request aborted; explicit review remains available | Frontend unit | ✅ `src/features/library/__tests__/provenance.test.tsx::cancels the other review read after failure` |

Existing URL-safety, locale, optional-cover, field-label and file-validation
contracts remain in the Source component mirror. Their qualification must be
retained in the cutover. Source cover bytes keep their existing authenticated
asset owner; metadata no longer has a separate effect-owned fetch.

## Frontend ownership and qualification

`features/library/provenance.ts` owns the Source Query, command receipts and
review lifecycle. `source-tab.tsx` owns field text, frozen editing snapshots and
cover confirmation UI. A review reads Model permissions/current target values and
Source data, requires matching versions, and never submits a write automatically.
A failed review cancels its other outstanding read. Confirmed writes cancel older
Source reads and cannot downgrade a newer receipt; retired sessions and unmounted
editors cannot publish feedback. Starting a write makes the Source read stale so
a later visit revalidates even if the editor departed before the acknowledgement.

The component's remote-state effects and separate cover-metadata GET are removed.
Cover bytes keep the authenticated asset owner; their cache identity includes the
cover metadata version so a reviewed replacement cannot reuse the old image.
Source override/cover API clients now require an editing version and publish
through their feature owner instead of the transport invalidation adapter. Model
field application supplies a version to the existing Model client; that client's
remaining compatibility path is not declared removed here. The shared browser
fake now models the new required DTO fields and rejects missing/stale Source
preconditions rather than returning permissive successes.

Qualification retained both failures and passing runs:

- Initial Source/API regression run: **17 failed / 23 passed**, before production
  changes. The first cutover passed **40/40**.
- Expanded tests exposed a departed-editor stale read and a Model-identity lifetime
  gap. A warm-cache denial assertion initially read before Query notified React;
  waiting for the observable render corrected that test, not production policy.
- A cover-review regression reproduced reuse of the old asset; a separate failed-
  review regression reproduced the uncancelled sibling request. Both were fixed
  at their owners and passed their focused checks.
- Integrated qualification: **219/219**, seven files, 21.74 s, including Source,
  Model detail, dependency boundaries and localization. A subsequent assertion
  strengthens the delayed-read case to check the confirmed title as well as its
  version; the complete owner mirror then passed **8/8** (3.38 s).
- Real backend Chromium: **1/1**, 8.4 s (42.9 s including servers). Actual capture,
  import, competing conditional PATCH, 412, explicit review/retry and persisted
  title were asserted. The initial run used a button selector for an ARIA tab and
  timed out; its page snapshot is retained. The selector was corrected without
  changing the product or increasing timeouts.
- Mock API Chromium: **3/3**, 19.9 s (cover, narrow layout and existing trash
  control); retained capture/override/restore entry flows **2/2**, 15.3 s.

The new feature required registration as a public Library entry point; the first
integrated boundary check caught that omission before it was corrected. Typecheck
also caught an omitted cover type import; the corrected app/UI/domain typecheck
passed. Full frontend lint and formatting passed (748 files); the subsequently
strengthened owner assertion also passed scoped lint/format. Three suite-hygiene
checks passed (1.41 s); the existing M5 top-level browser-test defect remains
explicitly deferred, so no complete hygiene pass is claimed. Qualification used
the active integration worktree, including preserved later checkpoints; the
Source commit includes only its owned changes and the Model detail fixture's
required version, not the queued M5/M7 edits. No improvement in startup or decoded-image latency is claimed. Removing the
secondary metadata read is asserted behaviour, not a comparable performance
measurement. Full implementation build/CI and final evidence reconciliation remain
M11, after the intervening milestones have actually closed.

Review coverage for this increment: complete manual review of the new Source
feature owner, Source component, provenance endpoint client, their mirrors and
real-browser spec; targeted review of transport, asset leasing, Model publication,
Query keys, factories, mock routes and public-entry enforcement. This does not
close the repository-wide manual-review gaps tracked for M8–M11. Rollback reverts
the Source consumer/API/owner cutover together and retains the additive backend
projection; it restores the documented unprotected editing behaviour, so it is
not a claim of equivalent correctness.
