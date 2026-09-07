# Backend modularization validation

This refactor preserves persisted schemas, storage keys, endpoints and archive
formats. The shared operation is implemented in OSS. Cloud adoption is deferred by request;
its required adapter and validation are described in `architecture/cloud-adoption.md`.

Paths below are relative to `backend/tests`, unless prefixed `core`.
A checked row means its named assertions ran successfully; it does not replace
completion of the full regression and coverage gates below.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `TestSharedRevisionContract.test_shared_business_contract` — promote survivor | Happy | Delete recommended G-code | Highest live G-code version becomes recommended through the OSS adapter | Integration | ✅ OSS contract examples |
| 2 | Same contract — preserve recommendation | Edge | Delete unmarked Revision | Existing recommendation survives | Integration | ✅ |
| 3 | Same contract — clear selected thumbnail | Edge | Thumbnail belongs to deleted Revision | Both thumbnail pointers cleared | Integration | ✅ |
| 4 | Same contract — preserve another thumbnail | Edge | Thumbnail belongs to another Artifact | Pointer and path retained | Integration | ✅ |
| 5 | Same contract — final G-code | Edge | Delete the only live G-code | No remaining recommendation | Integration | ✅ |
| 6 | Same contract — unsupported Artifact | Error | Mesh id | Stable error and no soft deletion | Integration | ✅ |
| 7 | Same contract — unavailable Revision | Error | Trashed Revision | Not-found error and unchanged state | Integration | ✅ |
| 8 | `integration/modules/library/test_revisions.py::TestRemoveRevision` | Error | Trashed Model, foreign Model's File, actor without edit permission | No mutation through the non-HTTP operation | Integration | ✅ |
| 9 | `TestRevisionTransaction.test_inactive_actor_cannot_invoke_the_command` | Error | Inactive OSS actor | Permission denial and live File retained | Integration | ✅ |
| 10 | `TestRevisionTransaction.test_failed_commit_rolls_back_the_whole_revision_change` | Error | Commit raises | File, recommendation, thumbnail and source tombstone roll back together | Integration | ✅ |
| 11 | `TestRevisionTransaction.test_linked_revision_records_a_source_tombstone` | Edge | Linked-source Revision | Tombstone and deletion committed together | Integration | ✅ |
| 12 | `integration/modules/library/test_commands.py::TestUpdateModel` | Error | Unauthorized worker or failure while assigning new tags | Permission denied; failed mutation retains original name and creates no orphan tag | Integration | ✅ |
| 13 | `integration/modules/printing/test_dispatch.py::TestDispatchAuthority` | Error | Inactive actor calls dispatch directly | No provider/storage access and no PrintJob | Integration | ✅ |
| 14 | `integration/api/v1/printers` | Happy / Error / Edge | Send, compatibility rejection, provider and business errors | Existing HTTP outcomes and persisted job transitions | Integration | ✅ 220-test focused run includes dispatch and model listing |
| 15 | `integration/modules/ingestion/test_background.py` and `api/v1/ingest/test_import_progress.py` | Happy / Error / Edge | Direct URL, ZIP, selection, download/inspection failures | Correct progress, review manifest and failed-job outcomes | Integration | ✅ 86 passed after handler extraction |
| 16 | `repo/test_architecture.py::TestImports` | Error | Deferred cycle, private symbol/file, qualified alias, type-only dependency | Forbidden edges rejected; type-only cycles and local private implementation allowed | Repo | ✅ 60 architecture, shared-core boundary and OpenAPI tests passed |
| 17 | `repo/test_forbidden_imports.py` | Error | Shared core source tree | No framework, ORM or product imports | Repo | ✅ retained boundary |
| 18 | `repo/test_openapi_contract.py` | Happy | Generated OpenAPI | Same recorded public schema | Repo | ✅ included in focused runtime and ingestion runs |
| 19 | `integration/db/migrations/test_models_versus_chain.py` and schema parity tests | Edge | Split table declarations | Same metadata, constraints and migration chain | Integration | ✅ schema and migration checks in the broad and infrastructure runs |
| 20 | `integration/modules/library/model_views/test_model_views_n_plus_one.py` | Edge | Different page sizes | Query count remains independent of Model count | Integration | ✅ extraction regression suite |
| 21 | `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py` | Error | Lost commit acknowledgement | Published blob retained for reconciliation | Integration | ✅ broad regression run |
| 22 | `integration/modules/backups/backup` | Error / Edge | Corrupt archive/journal, changed provider identity, uncertain publication/restore | Refusal or reconciliation preserves exact ownership and recovery evidence | Integration | ✅ broad regression and all 144 infrastructure tests; additional boundary cases below |
| 23 | `unit/runtime/test_realtime.py` and `integration/bootstrap/test_lifecycle.py` | Error / Edge | Slow/dead sink, startup or maintenance recovery | Healthy sinks proceed; dead sinks dropped; dependencies close correctly | Unit / Integration | ✅ 111 runtime/bootstrap tests passed |
| 24 | `repo/test_architecture.py::TestArchitecture.test_storage_contracts_do_not_initialize_adapters` | Error | Import storage contracts in a fresh interpreter | Local/S3 adapters and runtime binding are not imported | Repo | ✅ failed with the old façade and passes after its removal |
| 25 | `TestRevisionConcurrency.test_sqlite_deletions_serialize_before_choosing_the_survivor` | Edge | Two independent sessions delete the recommended Revision and its replacement concurrently | Both deletions complete; final survivor remains recommended | Integration / SQLite WAL | ✅ failed before the writer reservation; passes after it |
| 26 | `TestRevisionConcurrency.test_a_preloaded_session_refreshes_recommendations_after_another_commit` | Edge | ORM session cached an old recommendation before another transaction commits | New deletion observes current recommendation and promotes the remaining Revision | Integration / SQLite WAL | ✅ failed with the cached snapshot; passes with explicit refresh |
| 27 | `integration/modules/backups/backup/test_downloads.py::TestArchiveOwnership.test_rejects_unverifiable_receipts` | Error / Edge | Foreign provider or absent remote identity | Ownership refused | Integration | ✅ |
| 28 | `integration/modules/backups/backup/test_downloads.py::TestArchiveOwnership.test_rejects_changed_remote_object` | Error / Edge | Size, ETag or create-token changed | Changed object cannot authorize a download | Integration | ✅ |
| 29 | `integration/modules/backups/backup/test_downloads.py::TestArchiveOwnership.test_remote_head_failure_does_not_authorize_download` | Error / Edge | Remote HEAD fails | Ownership remains unverified | Integration | ✅ |
| 30 | `integration/modules/backups/backup/test_downloads.py::TestArchiveOwnership.test_rejects_a_changed_destination` | Error / Edge | Missing target, foreign namespace or outside key | No source is accepted | Integration | ✅ |
| 31 | `integration/modules/backups/backup/test_downloads.py::TestArchiveOwnership.test_snapshot_without_bucket_uses_the_selected_namespace` | Error / Edge | Selected source supplies namespace | Conditional HEAD validates the exact selected receipt | Integration | ✅ |
| 32 | `integration/modules/backups/backup/test_downloads.py::TestVerifiedDownload.test_partial_or_replaced_body_is_never_published` | Error / Edge | Truncated body, changed digest or replacement after read | No cache publication; body closed and temporary bytes removed | Integration | ✅ |
| 33 | `integration/modules/backups/backup/test_downloads.py::TestVerifiedDownload.test_cache_reuse_requires_its_own_ownership` | Error / Edge | Previously downloaded owned cache | Exact cached bytes reused | Integration | ✅ |
| 34 | `integration/modules/backups/backup/test_downloads.py::TestVerifiedDownload.test_an_existing_cache_cannot_be_adopted_implicitly` | Error / Edge | Foreign bytes, no receipt or wrong receipt hash | Cache rejected and existing bytes preserved | Integration | ✅ |
| 35 | `integration/modules/backups/backup/test_downloads.py::TestVerifiedDownload.test_missing_local_archive_has_no_remote_fallback` | Error / Edge | Local source no longer exists | Not-found error without remote discovery | Integration | ✅ |
| 36 | `integration/modules/backups/backup/test_archive_format.py::TestRestoreManifest.test_refuses_invalid_structure` | Error / Edge | Missing/directory manifest, invalid JSON or malformed fields | Stable rejection before destination writes | Integration | ✅ |
| 37 | `integration/modules/backups/backup/test_archive_format.py::TestRestoreManifest.test_archive_identity_must_match_current_storage` | Error / Edge | Manifest names another provider | Storage namespace mismatch | Integration | ✅ |
| 38 | `integration/modules/backups/backup/test_archive_format.py::TestRestoreManifest.test_legacy_key_map_ignores_non_entries` | Error / Edge | Legacy map contains malformed entries | Only valid member-to-key mapping retained | Integration | ✅ |
| 39 | `integration/modules/backups/backup/test_restore_blobs.py::TestCollisionPreflight.test_rejects_conflicts_before_publication` | Error / Edge | Duplicate key, changed bytes or wrong intent | Staged bytes and existing destination preserved | Integration | ✅ |
| 40 | `integration/modules/backups/backup/test_catalogue.py::TestS3Catalogue.test_omits_unverifiable_archives` | Error / Edge | Incomplete proof, changed object or unreadable manifest | Archive omitted; opened stream closed | Integration | ✅ |
| 41 | `integration/modules/backups/backup/test_catalogue.py::TestS3Catalogue.test_listing_outage_does_not_invent_sources` | Error / Edge | S3 listing outage | Empty result, no invented source | Integration | ✅ |
| 42 | `integration/modules/backups/backup/test_adoption.py::TestS3Adoption.test_existing_receipt_cannot_be_adopted_again` | Error / Edge | Committed or pending receipt | Adoption refused; receipt state unchanged | Integration | ✅ |
| 43 | `integration/modules/backups/backup/test_adoption.py::TestS3Adoption.test_stale_selection_does_not_update_legacy_receipt` | Error / Edge | Operator selected another source | Legacy receipt retains its missing digest | Integration | ✅ |
| 44 | `integration/modules/backups/backup/test_adoption.py::TestS3Adoption.test_refuses_non_archive_locators` | Error / Edge | Empty, outside or non-archive locator | Invalid-key error | Integration | ✅ |
| 45 | `integration/modules/backups/backup/test_adoption.py::TestS3Adoption.test_missing_target_is_explicit` | Error / Edge | S3 target unavailable | Explicit unavailable error | Integration | ✅ |
| 46 | `integration/modules/backups/backup/test_adoption.py::TestS3Adoption.test_operator_digest_must_match_the_download` | Error / Edge | Valid archive differs from operator-selected digest | No adoption; stream and temporary download cleaned up | Integration | ✅ |
| 47 | `integration/modules/backups/backup/test_adoption.py::TestLocalAdoption.test_rejects_ambiguous_or_outside_names` | Error / Edge | Empty, traversing or unrelated filename | Invalid-filename error | Integration | ✅ |
| 48 | `integration/modules/backups/backup/test_adoption.py::TestLocalAdoption.test_missing_selected_archive_is_not_adopted` | Error / Edge | Selected archive missing | Not-found error | Integration | ✅ |
| 49 | `integration/modules/backups/backup/test_opendal.py::TestVerifiedOpenDalDownload.test_reuses_only_a_verified_owned_cache` | Error / Edge | Exact owned cache exists | Same path and bytes reused | Integration | ✅ |
| 50 | `integration/modules/backups/backup/test_opendal.py::TestVerifiedOpenDalDownload.test_preexisting_cache_is_not_silently_adopted` | Error / Edge | Changed bytes or absent/mismatched cache receipt | Cache rejected without changing its contents | Integration | ✅ |
| 51 | `integration/modules/backups/backup/test_opendal.py::TestVerifiedOpenDalDownload.test_failed_download_leaves_no_new_owned_bytes` | Error / Edge | Disconnected destination, changed object or interrupted read | Committed receipt preserved; temporary download removed | Integration | ✅ |
| 52 | `integration/modules/backups/backup/test_restore.py::TestRecoveryDestination.test_refuses_recovery_without_current_destination_proof` | Error / Edge | Unknown provider identity or legacy journal against remote storage | Journal and database unchanged; maintenance retained | Integration | ✅ |
| 53 | `unit/modules/backups/backup/test_targets.py::TestRemoteIdentity.test_response_must_match_every_selected_identity_component` | Error / Edge | Wrong size, ETag or version | Exact identity disagreement rejected | Unit | ✅ |
| 54 | `unit/modules/backups/backup/test_targets.py::TestRemoteIdentity.test_version_does_not_hide_an_etag_change` | Error / Edge | Same version, different ETag | ETag disagreement rejected | Unit | ✅ |
| 55 | `unit/modules/backups/backup/test_targets.py::TestRemoteIdentity.test_config_changing_through_every_retry_cannot_build_a_target` | Error / Edge | Every configuration snapshot changes | No mixed target constructed | Unit | ✅ |
| 56 | `unit/modules/backups/backup/test_targets.py::TestRemoteIdentity.test_config_settling_after_an_update_uses_the_whole_new_snapshot` | Error / Edge | Update stabilizes on a new configuration | Complete new tuple selected | Unit | ✅ |


HTTP boundary completion retains these additional contracts:

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 57 | `TestOperationErrorResponse.test_preserves_the_error_response` | Error | Every operation failure kind | Existing status and detail; no unsolicited retry header | Integration | ✅ 557-test HTTP boundary run |
| 58 | `TestOperationErrorResponse.test_exposes_the_retry_delay` | Error | Busy operation with two-second retry delay | HTTP 429 with unchanged Retry-After | Integration | ✅ 557-test HTTP boundary run |
| 59 | `TestArchitecture.test_rejects_transport_imports_in_product_operations` | Error | Deferred FastAPI, response, HTTP exception or API import | Architectural violation reported | Repo | ✅ 557-test HTTP boundary run |
| 60 | Existing Inbox, sharing, RBAC, multipart and toolpath cases | Error / Edge | Direct operation rejection or route invocation | Transport-free business failure; HTTP callers retain existing public outcomes | Integration | ✅ 557-test HTTP boundary run |
| 61 | Existing auth and setup cases | Happy / Error | Cookie sessions, setup ticket/origin and first-owner locking | Existing authentication and first-ownership behavior after moving HTTP adapters | Integration | ✅ 557-test HTTP boundary run |
| 62 | `TestFirstOwnerConcurrency.test_two_sqlite_api_processes_create_exactly_one_owner` | Edge | Two prepared browser sessions claim an empty installation simultaneously | One 201, one 409, exactly one superuser | Integration / two processes | ✅ five concurrency tests pass after registering the production HTTP handler |
| 63 | `TestManufacturingConcurrency.test_concurrent_conflicting_confirmations_have_one_winner` | Edge | Concurrent output confirmations | One operation wins; conflicting result is rejected | Integration / SQLite and PostgreSQL | ✅ SQLite concurrency tests pass; PostgreSQL included in the infrastructure lane |
| 64 | `TestConcurrentReservations.test_parallel_queue_requests_reserve_each_unit_once` | Edge | Two concurrent queue commands | One reservation succeeds; planned units are counted once | Integration / SQLite and PostgreSQL | ✅ SQLite concurrency tests pass; PostgreSQL included in the infrastructure lane |
| 65 | `repo/test_architecture.py::TestImports.test_rejects_an_operations_unexported_dependency` | Error | HTTP reaches an operation's imported dependency directly, through an alias or through a deferred import | Architectural violation identifies the dependency and its consumer | Repo | ✅ four variants in the 23-test architecture/OpenAPI run |
| 66 | `repo/test_architecture.py::TestImports.test_allows_an_explicit_public_error_contract` | Happy | Operation explicitly exports its error contract in `__all__` | Public error contract remains accessible to HTTP | Repo | ✅ |
| 67 | `repo/test_architecture.py::TestImports.test_allows_an_operation_defined_by_its_owner` | Happy | HTTP calls the owner's own operation | Operation is accepted as public | Repo | ✅ |
| 68 | `integration/modules/ingestion/test_import_resolvers.py::TestConnectedManifest.test_rejects_noncanonical_provider_identity` | Error | Provider identity URL includes query, fragment, credentials or an insecure scheme | No capture manifest; stable contract-change error | Integration | ✅ four URL variants pass |
| 69 | `integration/modules/administration/test_setup_bootstrap.py::TestLockInstallation.test_excludes_a_competing_postgres_setup_transaction` | Edge | One installation transaction holds the PostgreSQL advisory lock | A peer receives PostgreSQL's lock-not-available error instead of entering setup | Integration / PostgreSQL | ✅ |
| 70 | `integration/modules/administration/test_setup_bootstrap.py::TestLockInstallation.test_rejects_postgres_setup_after_configuration_commits` | Edge | A peer cached unconfigured state before the first transaction committed configuration | New lock acquisition refreshes state and rejects setup as already configured | Integration / PostgreSQL | ✅ |

## Gate evidence

- The complete backend regression suite on `c94be69` passed in CI on Python 3.11
  and 3.13: 8,663 general tests plus 144 PostgreSQL/storage tests in each runtime.
  The initial Python 3.11 coverage gate identified the provider-identity and
  directly measured PostgreSQL setup cases added in rows 68–70. Production code
  did not change while closing those gaps.
- Shared core: 1,431 tests and all five branch-coverage floor checks passed;
  its Ruff and strict Pyright checks passed. CI also validated Python 3.11/3.13
  with lowest/highest dependency resolution.
- Final HTTP/import regression: 1,250 tests passed after the last route import
  changes. Additional resolver, source, API and repository checks: 2,328 passed;
  the WebDAV E2E case passed when launched through `uv` so its executable was on
  PATH. Four mounted-folder discovery/permission tests passed.
- The two new PostgreSQL setup tests passed on their final assertions. The
  unchanged SQLite two-process test hit its existing worker-result timeout during
  a heavily loaded mixed-resource run, then passed alone in 96.88 seconds with
  the original timeout and assertions. It also passed in both complete CI runs.
- A long-running local general pass finished with 8,654 passing tests and three
  failures from workers that had loaded the previous fake HTTP-error adapters.
  The corrected five concurrency cases passed, followed by both complete CI
  regression passes above. No test was skipped or weakened to hide these results.
- Final combined local branch coverage: **93.97%**. All ten coverage
  floor checks pass, retaining the 90% aggregate/module floors and the existing
  capped debt list. Measurements from before the ten route changes were removed
  and replaced with fresh execution data from their final regression tests.
- Backend Ruff, configured Pyright, dependency declarations and ratcheted
  formatting pass. Architecture checks report no recorded cycles, private
  dependencies, legacy service imports or HTTP transport imports in operations.
  OpenAPI, metadata/migration parity and query-budget assertions pass.
- CI also passed the critical capabilities, frontend, browser extension,
  real-backend browser workflows and Docker images for amd64/arm64.
- Cloud implementation and execution remain deferred to
  `architecture/cloud-adoption.md`.
