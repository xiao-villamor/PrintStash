# Mesh pipeline regression acceptance — #259

The gate runs the shipped full image, its real HTTP endpoints, SQLite, filesystem
and DBOS engine in native Linux containers capped at 1 GiB and 4 GiB, with swap
disabled. Deep CI covers amd64 and arm64 independently. Host-side factories
generate adversarial input; neither the image nor CI downloads issue attachments.

The matrix lists implemented assertions. Container rows require a successful
resource report on the final integration SHA; a test existing is not evidence
that a particular CI run passed. The containment, outcomes and staging changes
supply the companion tests referenced here.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | preserves supported mesh results (Gate.run) | Happy | STL, OBJ, 3MF, native STEP/STP; DXF original-only | Supported geometry/viewer output; original bytes preserved | Production container | ✅ scripts/mesh_resource_gate.py::Gate.run |
| 2 | preserves 3MF placement (TestThreeMFResources) | Edge | Nested, repeated, mirrored instances | Exact transformed geometry | Integration | ✅ TestThreeMFResources.test_applies_nested_mirror_in_physical_units; TestProductionResources.test_preserves_cross_document_resource_identity |
| 3 | rejects excessive expansion (test_refuses_exponential_component_expansion) | Error | Small package expands above face budget | scene_resource_limit | Integration/container | ✅ TestThreeMFResources.test_refuses_exponential_component_expansion |
| 4 | rejects malformed packages (TestMalformedPackages/TestProductionResources) | Error | Cycles, invalid XML, missing parts, bombs | Stable failure codes | Integration/container | ✅ TestMalformedPackages.test_contains_malformed_package; TestThreeMFResources.test_refuses_cyclic_resource_graph |
| 5 | stops sudden allocation (Gate.burst) | Error | 8 GiB allocation, 128 MiB worker AS | Resource exit; API healthy; OOM count unchanged | Production container | ✅ scripts/mesh_resource_gate.py::Gate.burst |
| 6 | bounds aggregate allocation (test_counts_descendants_against_the_admitted_budget) | Edge | Nested child memory and concurrent uploads | Tree refusal; cgroup OOM count unchanged | Integration/container | ✅ TestWorkerBootstrap.test_counts_descendants_against_the_admitted_budget; Gate.run |
| 7 | preserves changing admission (TestRenderAdmission) | Edge | Active work during concurrency change | New admission waits for existing work | Unit | ✅ TestRenderAdmission.test_config_change_waits_for_old_admissions_to_settle |
| 8 | terminates worker trees (TestWorkerBootstrap/TestAbandonedTemporaryOutputs/TestMeshCancellation/Gate.cancel_native) | Error | Cancellation, immediate retry, parent death, timeout, successful orphan | Descendants dead; owned temporary output removed; following work ready | Integration/unit/container | ✅ TestMeshCancellation.test_withdrawal_stops_in_flight_native_work; Gate.cancel_native |
| 9 | reports geometry refusal (TestDeriveMesh) | Error | Resource refusal with embedded image | Metadata terminal failure | Integration | ✅ TestDeriveMesh.test_refused_geometry_is_terminal_with_an_embedded_preview |
| 10 | retains embedded preview (TestDeriveMesh) | Happy | Same input | Thumbnail ready | Integration | ✅ TestDeriveMesh.test_embedded_preview_survives_refused_geometry |
| 11 | suppresses unchanged failures (TestTerminalMeshFailure/Gate.run) | Edge | Watcher, scans, nudges, restart | Attempts and timestamps unchanged | Integration/E2E/container | ✅ TestTerminalMeshFailure.test_unchanged_mesh_failure_survives_source_refresh; Gate.run |
| 12 | exhausts timeouts (TestOutcomes) | Error | Repeated timeout | Stops at configured maximum | Integration | ✅ TestOutcomes.test_timeouts_stop_at_the_configured_attempt_limit |
| 13 | permits deliberate reprocessing (TestWithdrawAndRetry/TestPending/TestTerminalMeshFailure) | Edge | Retry, new recipe, changed bytes | New work eligible | Integration | ✅ TestWithdrawAndRetry.test_a_retry_forgets_every_unsuccessful_attempt; TestPending.test_a_recipe_bump_makes_every_artifact_pending_again; TestTerminalMeshFailure.test_changed_mesh_bytes_become_eligible_again |
| 14 | preserves downloads (Gate.case/TestMeshFailureRecovery) | Error | Failed geometry in local/S3 vault, mounted folder, Nextcloud/OpenSSH/S3 source | SHA-256 equals original | E2E/container | ✅ TestMeshFailureRecovery.test_original_download_survives_geometry_failure; TestRefusedRemoteMesh.test_original_download_survives_geometry_failure; Gate.case |
| 15 | preserves slicer handoff (Gate.case/TestMeshFailureRecovery) | Error | Failed geometry in local/S3 vault, mounted folder, Nextcloud/OpenSSH/S3 source | Signed download equals original | E2E/container | ✅ TestMeshFailureRecovery.test_signed_slicer_download_survives_geometry_failure; TestRefusedRemoteMesh.test_signed_slicer_download_survives_geometry_failure; Gate.case |
| 16 | retains failed input (TestStagingCleanup/Gate.run) | Error | Uncommitted failed ingest | Lease/input retained until discard/expiry | Integration/container | ✅ TestStagingCleanup.test_reconciliation_retains_failed_retry_input |
| 17 | releases committed staging (TestStagingCleanup/Gate.case) | Happy | Committed import/recovery | Staging absent, no residual leases | Integration/container | ✅ TestStagingCleanup.test_reconciliation_releases_completed_upload_before_expiry |
| 18 | preserves uncertain ownership (TestStagingCleanup) | Error | Replacement path | Replacement survives, lease charged | Integration | ✅ TestStagingCleanup.test_release_preserves_a_replacement_file |
| 19 | serializes discard/retry (TestStagingDiscard/TestStagingCleanup) | Edge | Concurrent database writers | Coherent ownership outcome | SQLite/PostgreSQL integration | ✅ TestStagingCleanup.test_discard_serializes_against_retry; TestStagingDiscard.test_discard_serializes_against_retry |
| 20 | denies another user (TestDiscardStaging) | Error | Different owner | 404, no deletion | Integration API | ✅ TestDiscardStaging.test_other_user_cannot_discard |
| 21 | exposes safe recovery (zip-upload.spec.ts) | Happy | Failed ZIP inspection | Confirmation, discard, capacity released | Playwright real | ✅ zip-upload.spec.ts: reclaims the retained input of a failed ZIP preparation |
| 22 | avoids accumulation (Gate.run) | Edge | Twelve mixed files after native warm-up | Current RSS delta and trend bounded | Production container | ✅ scripts/mesh_resource_gate.py::Gate.run |
| 23 | continues after bad input (Gate.run/TestMeshFailureRecovery) | Error | Bad file followed by healthy one | Following metadata ready | E2E/container | ✅ TestMeshFailureRecovery.test_healthy_artifact_finishes_after_a_bad_file; Gate.run |
| 24 | rejects mesh bypasses (TestMeshBoundaries) | Error | Native submodules, aliases, relative imports, wildcard imports, raw callable references | Repository gate rejects bypass; isolation and shared types remain allowed | Repository | ✅ TestMeshBoundaries.test_rejects_mesh_bypasses |

The delivery rows are expanded in [mesh-original-delivery.md](mesh-original-delivery.md).
The companion matrices identify the mirrored owner files:
[containment](mesh-containment.md), [outcomes](mesh-outcomes.md),
[staging](staging-recovery.md) and [cancellation](mesh-cancellation.md).

## Running the production gate

From backend/, build the same image used in production, then run:

```sh
docker build --build-arg PRINTSTASH_VARIANT=full -t printstash:mesh-resources .
uv run --frozen python scripts/mesh_resource_gate.py \
  --image printstash:mesh-resources --memory-gib 1 --report /tmp/mesh-1g.json
uv run --frozen python scripts/mesh_resource_gate.py \
  --image printstash:mesh-resources --memory-gib 4 --report /tmp/mesh-4g.json
```

For private acceptance of the two reported files, put their actual 3MF packages
in a local directory and add --acceptance /path/to/directory. Exactly two .3mf
files are required. At 4 GiB their metadata must be ready; at 1 GiB controlled
terminal refusal is allowed. Never commit those attachments or depend on
GitHub downloads in CI.

The JSON artifact records original hashes, wall time, actual job attempts,
derivative attempts/reasons/duration/peak RSS, cgroup peak/current memory,
OOM-kill count, cancellation of an observed active native Job, warmed current
parent RSS samples, and cleanup outcome.
Derivative attempts can equal the configured maximum after a terminal refusal;
actual job attempts distinguish that exhaustion marker from executions.
The artifact is written on failure too, along with container logs. Missing
reports fail artifact publication. A following healthy input must finish.

Merge each independently validated PR progressively. After the final merge,
require CI and Deep CI on that exact main SHA before considering a release;
this gate does not authorize publication.
