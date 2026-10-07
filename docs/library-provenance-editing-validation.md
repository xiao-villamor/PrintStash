# Versioned Source editing — M4

M3 is closed. This is a bounded M4 increment, not closure of M4.

## Observed gap and contract

`SourceTab` loads provenance and covers into separate effect-owned state.
`api/provenance.ts` sends override/cover writes without the conditional editing
contract; applying a source title/description also omits the Model version.
The backend already supports conditional claims, but provenance reads expose no
editing base. Guessing a version from a later unrelated Model GET could pair old
fields with a newer editing permission and silently overwrite a concurrent edit.

Extend the existing provenance projection with its Model edit version and each
source's nullable private cover metadata. Read the edit version before composing
the projection and verify it again before returning. A changed version returns
`409 edit_snapshot_changed`; no mixed editing snapshot is published. The GET ETag
uses that exact receipt version. Override acknowledgements use the same receipt.
This provides one coherent source-editing input without a generic repository or
an extra cache. Existing source-cover endpoints remain compatible.

This increment prepares the backend contract. Next, the Source feature Query
owner and frozen local drafts will consume it, send conditional writes, preserve
drafts/files on conflict or unknown outcome and require explicit review. Multipart
auxiliary recovery and physical-restore editing-token identity remain M4 work.
No complete first-party conditional-edit guarantee is claimed yet.

## Coverage matrix (before implementation)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | returns the Model editing base with provenance | Happy | Model with captured source | Required edit_version matches Model and GET ETag | Integration | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_returns_model_editing_base` |
| 2 | returns an editing base without sources | Edge | Model has no capture | Valid edit_version, empty sources, matching ETag | Integration | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_returns_editing_base_without_sources` |
| 3 | reports an absent source cover explicitly | Edge | Source without a cover | Required cover field is null | Integration | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_reports_absent_cover` |
| 4 | includes only private cover metadata | Happy | Uploaded source cover | Same public metadata as cover route; no storage key or raw bytes | Integration | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_includes_only_private_cover_metadata` |
| 5 | acknowledges the override editing version | Happy | Conditional override from displayed version | Changed field and advanced edit_version match acknowledgement ETag | Integration | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_acknowledges_override_editing_version` |
| 6 | rejects a changed editing snapshot | Error | Real writer advances version during projection | 409 edit_snapshot_changed; no source payload | Integration | ✅ `integration/api/v1/models/test_provenance.py::TestVersionedProvenance::test_rejects_changed_editing_snapshot` |
| 7 | refuses an absent Model editing snapshot | Error | Projection requested for missing Model | Explicit model_not_found, no fabricated version | Integration | ✅ `integration/modules/library/model_views/test_detail.py::TestProvenanceDetail::test_refuses_absent_model` |
| 8 | scopes cover metadata to its Model | Error | Two Models each own a source cover | Projection contains only requested Model source/cover identities | Integration | ✅ `integration/modules/library/model_views/test_detail.py::TestProvenanceDetail::test_scopes_cover_metadata_to_model` |
| 9 | keeps source projection query count fixed | Edge | One source grows to ten with covers | All sources returned with the same SELECT count | Integration | ✅ `integration/modules/library/model_views/test_detail.py::TestProvenanceDetail::test_keeps_query_count_fixed` |
| 10 | reads a versioned source on PostgreSQL | Happy | Real PostgreSQL Model, source and cover | Editing version and matching cover metadata returned | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestProvenanceSnapshot::test_reads_versioned_source` |
| 11 | rejects a concurrent PostgreSQL edit during source composition | Error | Separate writer commits while provenance read is open | edit_snapshot_changed instead of a mixed snapshot | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestProvenanceSnapshot::test_rejects_concurrent_edit_during_composition` |

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

The Source UI still uses its previous effect-owned reads and unconditioned
writers. Its Query/draft migration is the next M4 increment; this checkpoint does
not claim that the visible editing workflow is now protected. Final full gates
and latest-commit CI remain outstanding for the complete implementation.
