# Artifact delivery validation

The acceptance scenarios below cover issue #101. Focused results: 61 API/unit
cases, 55 provider/unit/E2E cases, 17 frontend request cases and the reviewed
OpenAPI contract passed. The real Chromium native-download proof passed one
test (19.7 seconds overall), including exact saved bytes, Unicode filename,
provider CORS and zero API body bytes. Full-suite and coverage checks remain
in the integration queue; focused results do not imply those gates passed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | revalidates_authenticated_original_privately | Happy | Authenticated original | Private revalidation header | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_revalidates_authenticated_original_privately` |
| 2 | uses_the_original_digest_as_validator | Happy | Original bytes | ETag equals original SHA-256 | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_uses_the_original_digest_as_validator` |
| 3 | returns_no_body_for_matching_validator | Edge | Matching weak ETag | 304 with empty body | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_returns_no_body_for_matching_validator` |
| 4 | prioritizes_etag_over_modification_date | Edge | Different ETag, future date | Original bytes returned | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_prioritizes_etag_over_modification_date` |
| 5 | evaluates_modification_date | Edge | Unchanged date | 304 response | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_evaluates_modification_date` |
| 6 | authorizes_before_conditional_response | Error | Anonymous request, wildcard ETag | 401 before validation | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_authorizes_before_conditional_response` |
| 7 | serves_exact_local_range | Happy | Local bytes=2-7 | 206 with exact segment | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_serves_exact_local_range` |
| 8 | ignores_range_when_if_range_does_not_match | Edge | Stale If-Range | 200 with full original | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_ignores_range_when_if_range_does_not_match` |
| 9 | removes_obsolete_delivery_route | Error | Both retired route suffixes | 404 response | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_removes_obsolete_delivery_route` |
| 10 | denies_revoked_conditional_access | Error | Revoked share with matching ETag | 404 before validation | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_denies_revoked_conditional_access` |
| 11 | keeps_shared_original_noncacheable | Happy | Public share download | private, no-store | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_keeps_shared_original_noncacheable` |
| 12 | keeps_slicer_original_noncacheable | Happy | Slicer capability download | private, no-store | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_keeps_slicer_original_noncacheable` |
| 13 | revalidates_authenticated_thumbnail_privately | Happy | Authenticated thumbnail | Private revalidation header | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_revalidates_authenticated_thumbnail_privately` |
| 14 | uses_a_separate_converted_validator | Edge | OBJ converted to STL | Different representation ETag | Integration | ✅ `integration/api/v1/files/test_delivery.py::TestAuthorizedDelivery::test_uses_a_separate_converted_validator` |
| 15 | offloads_the_original | Happy | HTTPS S3 with CORS | Exact original bytes from signed GET | Contract | ✅ `contract/modules/storage/test_artifact_delivery.py::TestBrowserDelivery::test_offloads_the_original` |
| 16 | preserves_the_download_filename | Happy | Unicode display name | RFC 5987 response filename | Contract | ✅ `contract/modules/storage/test_artifact_delivery.py::TestBrowserDelivery::test_preserves_the_download_filename` |
| 17 | falls_back_without_cors | Error | No bucket CORS policy | No browser target | Contract | ✅ `contract/modules/storage/test_artifact_delivery.py::TestBrowserDelivery::test_falls_back_without_cors` |
| 18 | serves_selected_range | Happy | Remote original, Range | 206 with exact segment | Contract | ✅ `contract/modules/storage/test_artifact_delivery.py::TestBrowserDelivery::test_serves_selected_range` |
| 19 | compares_if_range_to_the_original_digest | Edge | Range with matching SHA validator | 206 despite distinct provider ETag | Contract | ✅ `contract/modules/storage/test_artifact_delivery.py::TestBrowserDelivery::test_compares_if_range_to_the_original_digest` |
| 20 | serves_full_original_for_stale_if_range | Edge | Remote Range with stale validator | 200 full original | Contract | ✅ `contract/modules/storage/test_artifact_delivery.py::TestBrowserDelivery::test_serves_full_original_for_stale_if_range` |
| 21 | accepts_an_object_scoped_target | Happy | HTTPS, matching object/origin, 60 seconds | Target accepted | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestSafeBrowserDownload::test_accepts_an_object_scoped_target` |
| 22 | refuses_an_unsafe_url | Error | HTTP, userinfo, fragment, control, relative, missing host | Target rejected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestSafeBrowserDownload::test_refuses_an_unsafe_url` |
| 23 | refuses_invalid_expiry | Error | Expired, zero, over 60 seconds | Target rejected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestSafeBrowserDownload::test_refuses_invalid_expiry` |
| 24 | refuses_a_different_object | Error | Object identity differs | Target rejected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestSafeBrowserDownload::test_refuses_a_different_object` |
| 25 | refuses_unproven_cors | Error | Browser origin lacks proof | Target rejected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestSafeBrowserDownload::test_refuses_unproven_cors` |
| 26 | refuses_required_headers | Error | Target needs extra authorization header | Target rejected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestSafeBrowserDownload::test_refuses_required_headers` |
| 27 | redacts_target_representation | Error | Secret query and object key | Neither exposed by repr | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestSafeBrowserDownload::test_redacts_target_representation` |
| 28 | matches_an_original_validator | Edge | Weak ETag, list, wildcard | Conditional match | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestNotModified::test_matches_an_original_validator` |
| 29 | ignores_invalid_date | Error | Malformed HTTP date | Full response selected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestNotModified::test_ignores_invalid_date` |
| 30 | selects_the_requested_bytes | Edge | Closed, open, suffix, clipped ranges | Expected inclusive bounds | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestByteRange::test_selects_the_requested_bytes` |
| 31 | rejects_unsatisfiable_range | Error | Reversed, past end, zero suffix, oversized integer | Unsatisfiable error | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestByteRange::test_rejects_unsatisfiable_range` |
| 32 | ignores_unsupported_range | Edge | Malformed or multipart range | Full response selected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestByteRange::test_ignores_unsupported_range` |
| 33 | weak_validator_cannot_select_a_partial_response | Edge | Weak If-Range | Full response selected | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestRangeMatches::test_weak_validator_cannot_select_a_partial_response` |
| 34 | matches_modification_time | Edge | Exact modification date | Range allowed | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestRangeMatches::test_matches_modification_time` |
| 35 | encodes_unicode_display_name | Happy | Non-ASCII filename | UTF-8 filename parameter | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestContentDisposition::test_encodes_unicode_display_name` |
| 36 | removes_path_header_injection | Error | Traversal and CRLF filename | Safe display name | Unit | ✅ `unit/modules/storage/test_artifact_delivery.py::TestContentDisposition::test_removes_path_header_injection` |
| 37 | falls_back_when_cors_policy_is_unreadable | Error | Denied GetBucketCors | API fallback | Unit | ✅ `unit/modules/storage/storage_backend/test_s3.py::TestBrowserDownload::test_falls_back_when_cors_policy_is_unreadable` |
| 38 | refuses_a_later_permissive_cors_rule | Error | Earlier matching rule does not expose filename | API fallback | Unit | ✅ `unit/modules/storage/storage_backend/test_s3.py::TestBrowserDownload::test_refuses_a_later_permissive_cors_rule` |
| 39 | falls_back_when_the_signer_fails | Error | Signer exception with sensitive diagnostics | Fallback without secret logging | Unit | ✅ `unit/modules/storage/storage_backend/test_s3.py::TestBrowserDownload::test_falls_back_when_the_signer_fails` |
| 40 | closes_an_unstarted_body | Error | Consumer closes before first read | Provider body closed | Unit | ✅ `unit/modules/storage/storage_backend/test_s3.py::TestStreamRange::test_closes_an_unstarted_body` |
| 41 | rejects_a_truncated_body | Error | Provider body shorter than advertised | Operation error and closed body | Unit | ✅ `unit/modules/storage/storage_backend/test_s3.py::TestStreamRange::test_rejects_a_truncated_body` |
| 42 | offloads_authorized_download | Happy | Canonical authenticated API + HTTPS S3 | 307 with empty API body; exact provider bytes | E2E | ✅ `e2e/test_artifact_delivery.py::TestArtifactDelivery::test_offloads_authorized_download` |
| 43 | retries a failed browser delivery through the API | Error | Fetch raises TypeError | One retry using original API and proxy header | Frontend unit | ✅ `src/lib/api/__tests__/request.test.ts::retries a failed browser delivery through the API` |
| 44 | does not retry a cancelled read | Edge | Aborted signal | Cancellation propagated | Frontend unit | ✅ `src/lib/api/__tests__/request.test.ts::does not retry a cancelled read` |
| 45 | does not retry an authorization denial | Error | 403 API response | Denial propagated once | Frontend unit | ✅ `src/lib/api/__tests__/request.test.ts::does not retry an authorization denial` |
| 46 | preserves cancellation while revalidating protected text | Edge | Text read with signal | Signal and no-cache mode retained | Frontend unit | ✅ `src/lib/api/__tests__/request.test.ts::preserves cancellation while revalidating protected text` |
| 47 | downloads native S3 bytes through the authenticated frontend helper | Happy | Actual browser, authenticated API, HTTPS S3 with CORS, Unicode name | Exact bytes and Unicode filename; no provider app credentials or referrer; sole API response is private/no-store 307 with zero emitted body bytes | Playwright | ✅ `frontend/tests/e2e-real/delivery/native-download.spec.ts::downloads native S3 bytes through the authenticated frontend helper` |

The browser run used the actual API/TLS provider fixture started once before a
coordinated stable-network window, then reused it through the same delivery
config. An isolated offline dependency installation resolved a changing shared
Playwright installation before the passing run. Backend fixture lint, launcher
syntax validation, frontend test lint and TypeScript all passed.
