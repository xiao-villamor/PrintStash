# Geometry outcomes acceptance matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | encodes independent geometry refusal | Error | Refusal and successful image | Typed refusal survives reply | Unit | ✅ TestGeometryOutcome.test_refusal_survives_a_successful_preview_reply |
| 2 | rejects incomplete replies | Error | Missing geometry outcome | Worker failure, no fake success | Unit | ✅ TestGeometryOutcome.test_missing_geometry_outcome_is_not_a_successful_reply |
| 3 | refuses geometry with an embedded image | Error | Expanded 3MF above budget | Terminal metadata failure | Integration | ✅ TestDeriveMesh.test_refused_geometry_is_terminal_with_an_embedded_preview |
| 4 | retains the embedded image | Happy | Same resource refusal | Thumbnail ready | Integration | ✅ TestDeriveMesh.test_embedded_preview_survives_refused_geometry |
| 5 | exposes malformed source failure | Error | Broken 3MF package | Metadata failed, no unknown-only success | Integration | ✅ TestDeriveMesh.test_malformed_mesh_does_not_publish_successful_unknown_metadata |
| 6 | retains legitimate unknown volume | Edge | Open STL surface | Ready geometry, volume null | Integration | ✅ TestGeometryMeasurements.test_open_mesh_keeps_unknown_volume_without_refusing_geometry |
| 7 | preserves closed STL volume | Happy | Facet vertices are independent | Ready solid volume after welding | Integration | ✅ TestGeometryMeasurements.test_closed_stl_retains_its_solid_volume |
| 8 | stops transient attempts | Error | Repeated timeouts | Configured maximum, backoff exhausted, metrics retained | Integration | ✅ TestOutcomes.test_timeouts_stop_at_the_configured_attempt_limit |
| 9 | suppresses unchanged source failures | Edge | Watcher/periodic refresh, mtime changed | Attempts and timestamp unchanged | Integration | ✅ TestTerminalMeshFailure.test_unchanged_mesh_failure_survives_source_refresh |
| 10 | allows changed source content | Edge | Broken 3MF replaced by valid bytes | Metadata ready | Integration | ✅ TestTerminalMeshFailure.test_changed_mesh_bytes_become_eligible_again |
| 11 | suppresses unchanged failures | Edge | Repeated missing-output nudges | Attempts and timestamp unchanged | E2E | ✅ TestMeshFailureRecovery.test_unchanged_terminal_failure_survives_reconciler_nudges |
| 12 | allows explicit retry | Edge | Terminal derivative | Failed attempts reset | Integration | ✅ TestWithdrawAndRetry.test_a_retry_forgets_every_unsuccessful_attempt |
| 13 | allows new recipes | Edge | Previous recipe | Bounded source offers new work | Integration | ✅ TestPending.test_a_recipe_bump_makes_every_artifact_pending_again |
| 14 | preserves originals | Error | Malformed 3MF derivative | Download SHA-256 identical | E2E | ✅ TestMeshFailureRecovery.test_original_download_survives_geometry_failure |
| 15 | preserves slicer handoff | Error | Malformed 3MF derivative | Signed download identical | E2E | ✅ TestMeshFailureRecovery.test_signed_slicer_download_survives_geometry_failure |
| 16 | continues after failure | Error | Broken package followed by healthy STL | Following metadata ready | E2E | ✅ TestMeshFailureRecovery.test_a_bad_file_does_not_block_the_next_healthy_artifact |

Tests live in the mirrored media and derivatives owners and tests/e2e/test_ingest.py.
The production-container restart and watcher acceptance coverage belongs to the
permanent resource gate.
