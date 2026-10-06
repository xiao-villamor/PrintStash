# Import resolver boundary contracts

Capture privacy and explicit user selection are specified in `docs/vault-maintenance-and-capture.md` and the capture skill reference. Untrusted provider shapes may not substitute file identities or persist secret transport data. These pure app-boundary tests supply provider responses at the transport/GraphQL seam; they do not replace selection validators or parser logic.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | refuses_invalid_selected_file | Error | Invalid file/id/url | Typed constructor refuses | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_invalid_selected_file` |
| 2 | refuses_invalid_selected_archive | Error | Non-tuple/empty/invalid/duplicate/files/url | Typed archive constructor refuses | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_invalid_selected_archive` |
| 3 | refuses_invalid_selection_identity | Error | Empty/duplicate/emptyID selection | Selection mismatch | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_invalid_selection_identity` |
| 4 | refuses_malformed_selected_response | Error | Wrong files shape/member/ID | Selection mismatch | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_malformed_selected_response` |
| 5 | refuses_missing_selected_link | Error | Empty/nontext downloadURL | Resolution failure | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_missing_selected_link` |
| 6 | refuses_incomplete_selected_response | Error | One of two selectedIDs omitted | Selection mismatch | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_incomplete_selected_response` |
| 7 | refuses_malformed_download_envelope | Error | Wrong nested provider JSON types | Resolution failure | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_malformed_download_envelope` |
| 8 | refuses_unsupported_selected_provider | Error | Unsupported host | Selection unsupported | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_refuses_unsupported_selected_provider` |
| 9 | redacts_unexpected_selected_source_failure | Error | Provider parse exception with token | Stable error; no secrets in log | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_redacts_unexpected_selected_source_failure` |
| 10 | rejects_invalid_capture_metadata | Error | Malformed Printables metadata | Capture invalid stable error | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_rejects_invalid_capture_metadata` |
| 11 | redacts_invalid_graphql_response | Error | HTTP500/nonJSON response | Stable error; no provider body in logs | Unit | ✅ `unit/modules/ingestion/test_import_resolvers.py::TestSelectionBoundaries::test_redacts_invalid_graphql_response` |

## Validation and limits

The affected unit and integration resolver files pass **103 tests, 1 deprecation warning in 5.44s**, bounded wall 9.59s. There are 32 new cases across eleven behaviours; existing successful selection and provider identity assertions remain. Ruff check/format and whitespace checks pass. Provider I/O responses are supplied through the current transport/GraphQL seam; parsing, selection validation, error normalization and redaction execute normally. HTTP error responses are asserted closed.

No production code, dependency, floor or debt entry changed. No local full/coverage/Deep run was performed. The baseline module measurement was 87.13%; the final measured floor remains pending the single GitHub Deep run after all owner assertions merge. These assertions do not establish global qualification or cleanup of pooled client sockets.
