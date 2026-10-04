# Mesh measurement evidence

`MetadataRead.volume_measurement` is required. Its five fields are `state`,
`unit`, `method`, `value_mm3`, and `cause`; unknown tags, missing fields and
incompatible combinations are rejected. `unit` is always `mm3`. The compatible
`volume_mm3` field equals `value_mm3` exactly, including null; neither field is
rounded for persistence or transport.

| State | Method | Value | Cause |
|---|---|---|---|
| `measured` | `mesh_surface_integral` | Finite and positive | null |
| `unavailable` | `mesh_surface_integral` | null | `not_watertight`, `inconsistent_winding`, `non_positive_integral`, `nonfinite_integral`, or `measurement_failed` |
| `not_calculated` | null | null | `enrichment_pending`, `not_applicable`, `not_requested`, `geometry_unavailable`, or `topology_not_evaluated` |
| `legacy_unassessed` | null | Finite historical scalar, or null | null |

The metadata policy first requires a watertight, consistently wound surface.
STL's independent facet vertices may be welded in a measurement copy; winding
is not repaired. Signed integrals retain negative inner-shell contributions and
require a positive aggregate. They are not Boolean unions of overlapping solids
and do not establish absence of self-intersections. Optional similarity
fingerprints keep their own policy and algorithm identity.

The library integral uses component-local coordinates to avoid loss of precision
at large translations. Face connectivity labels identify each component origin;
referenced triangles are gathered in batches of at most 4,096 faces. Trimesh's
`mass_properties` evaluates signed batch integrals, and `math.fsum` combines
those values. Explicit zero center of mass avoids irrelevant centroid division
for zero-volume partial batches. A full expanded source triangle array is not
cached. The source mesh remains unchanged; only a mesh needing vertex welding
gets a whole measurement copy.

A complete bounded STL scan or preview can publish dimensions and face counts
without evaluating topology. Its volume is `not_calculated` with
`topology_not_evaluated`. An incomplete fallback preview cannot publish sampled
bounds/counts as source measurements. A thumbnail-only request retains
`not_requested`; refusing geometry retains `geometry_unavailable`. Native
outputs cannot contain legacy provenance or the durable row causes
`enrichment_pending` and `not_applicable`.

New mesh Metadata rows start pending. G-code and DXF volume is explicitly not
applicable. Scalar-only caller/archive metadata is accepted as
`legacy_unassessed`, including finite zero and negative historical scalars;
providing that old shape never certifies a measurement. Modern evidence must
match its compatible scalar, and internal database evidence columns are not
accepted as public input. Validation precedes Artifact publication.

Bounding dimensions are independently nullable, finite, nonnegative millimetres.
Zero and tiny finite dimensions are retained exactly. Nonfinite extents derived
from finite source coordinates cause an explicit invalid-source refusal rather
than an infinite JSON number or invented zero. Unexpected integral failures
retain independent dimensions/counts and log their original exception.

## Upgrade and export

The additive migration retains finite historical volume as unassessed. Old
nonfinite volume and nonfinite/negative dimensions become null, with repair
counts logged, before adding constraints. All other facts and Metadata identity
are preserved. Downgrade cannot reconstruct those unusable historical numbers.
SQLite uses four native column additions followed by one pinned table rebuild;
PostgreSQL uses the same generated constraint contract. New rows use pending
defaults after the historical transition.

Mesh metadata recipe 9 replaces recipe 8; G-code metadata recipe 2 replaces
recipe 1. Existing published facts remain visible until a successful
replacement. Failed regeneration does not erase those independent facts; its
terminal refusal is available through the derivative endpoint. Thumbnail and
fingerprint recipes do not change for this measurement correction.

JSON export and portable archives carry the required public variant. Portable
imports also retain old scalar-only archive compatibility. CSV appends
`volume_state`, `volume_unit`, `volume_method`, and `volume_cause` after all old
columns. These cells come from public evidence; scalar volume is retained in its
existing column without rounding. Models lacking Metadata keep blank cells.
Raw database restore migrates its staged database before adoption; database
transfer requires a source already at the current schema.

## Scoped cost observations

These are fresh-process measurements of the geometry-extraction phase, not
end-to-end throughput or a performance gate. This is a small sample on a shared
host with uncontrolled scheduling. Each case has three observations;
imports and synthetic source creation precede the timed phase, and process peak
RSS includes them. The baseline is the production evaluator at commit
`7ce82e1b61ef38895c3ad99e9c415f8dc6173c09`. All candidates receive the same
synthetic source arrays and preserve them. The rejected candidate invoked the
library once per connected component; the selected candidate uses bounded
facet batches.

| Source | Production median ms / max RSS MiB | Per-component median ms / max RSS MiB | Bounded batches median ms / max RSS MiB |
|---|---|---|---|
| One 81,920-face icosphere | 145.841 / 209.61 | 210.747 / 215.24 | 210.672 / 192.21 |
| 1,000 tetrahedra, 4,000 faces | 6.562 / 162.14 | 357.582 / 162.07 | 9.106 / 163.04 |
| 10,000 tetrahedra, 40,000 faces | 84.224 / 182.45 | 3,729.807 / 176.96 | 91.829 / 173.46 |

The additional connectivity work costs about 45% in the single large-surface
observation. Batching avoids the severe Python overhead of one library call per
small solid and bounds triangle scratch to a batch. One box of dimensions
1×2×3 at a translation of 10¹⁵ mm measures 5.333333333333333 in production and
6 in both corrected candidates. Two instances at opposite translations measure
10.666666666666666 in production and 12 in the corrected candidates. These
analytic checks distinguish precision recovery from merely faster execution.

## Coverage matrix

Rows name individual test functions; parameter values exercise the listed
boundaries. A covered row identifies an assertion, not a claim that a broad
coverage or global suite has run. Exact execution results accompany the PR.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | test_preserves_small_positive_volume | Edge | 1e-9 mm3 | Exact positive value retained | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeMeasured::test_preserves_small_positive_volume` |
| 2 | test_rejects_invalid_measured_volume | Error | Null, bool, strings, nonpositive, nonfinite, huge integer | Predictable ValueError | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeMeasured::test_rejects_invalid_measured_volume` |
| 3 | test_rejects_non_enum_unavailable_cause | Error | Free string cause | Constructor rejects invalid cause | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeUnavailable::test_rejects_non_enum_unavailable_cause` |
| 4 | test_rejects_non_enum_not_calculated_cause | Error | Free string cause | Constructor rejects invalid cause | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeNotCalculated::test_rejects_non_enum_not_calculated_cause` |
| 5 | test_preserves_legacy_unassessed_scalar | Edge | Null, zero, negative, tiny, positive finite | Original scalar retained without assessment | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeLegacyUnassessed::test_preserves_legacy_unassessed_scalar` |
| 6 | test_preserves_each_volume_wire_variant | Happy | All four valid variants | Exact five-field encode/decode equality | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeWire::test_preserves_each_volume_wire_variant` |
| 7 | test_rejects_unknown_volume_wire_variant | Error | Unknown tag | Decoder rejects unknown state | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeWire::test_rejects_unknown_volume_wire_variant` |
| 8 | test_rejects_invalid_volume_wire_shape | Error | Missing/extra fields or incompatible values | Strict decoder rejects malformed variant | Unit | ✅ `packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeWire::test_rejects_invalid_volume_wire_shape` |
| 9 | test_preserves_public_volume_evidence | Happy | Measured, unavailable, not calculated, legacy zero/negative/null | Wire equality and compatible scalar equality | Unit | ✅ `unit/schemas/test_mesh_measurements.py::TestPublicVolumeEvidence::test_preserves_public_volume_evidence` |
| 10 | test_requires_every_volume_variant_field | Error | Each required field absent | ValidationError | Unit | ✅ `unit/schemas/test_mesh_measurements.py::TestPublicVolumeEvidence::test_requires_every_volume_variant_field` |
| 11 | test_rejects_incompatible_or_nonfinite_public_volume | Error | Unknown tags, incompatible method/cause, invalid numeric values | ValidationError | Unit | ✅ `unit/schemas/test_mesh_measurements.py::TestPublicVolumeEvidence::test_rejects_incompatible_or_nonfinite_public_volume` |
| 12 | test_metadata_api_requires_volume_evidence | Error | Scalar without required public variant | ValidationError | Unit | ✅ `unit/schemas/test_mesh_measurements.py::TestPublicVolumeEvidence::test_metadata_api_requires_volume_evidence` |
| 13 | test_metadata_api_rejects_scalar_disagreement | Error | Null, bool, nonfinite or mismatched scalar | ValidationError | Unit | ✅ `unit/schemas/test_mesh_measurements.py::TestPublicVolumeEvidence::test_metadata_api_rejects_scalar_disagreement` |
| 14 | test_rejects_nonphysical_public_dimensions | Error | Three axes; nonfinite, negative, bool, string, overflowing integer | ValidationError | Unit | ✅ `unit/schemas/test_mesh_measurements.py::TestPublicDimensionEvidence::test_rejects_nonphysical_public_dimensions` |
| 15 | test_preserves_nullable_nonnegative_dimension_precision | Edge | Null, zero, 1e-9 | Exact public dimension | Unit | ✅ `unit/schemas/test_mesh_measurements.py::TestPublicDimensionEvidence::test_preserves_nullable_nonnegative_dimension_precision` |
| 16 | test_preserves_measurement_precision | Edge | Cube edges .001, .123456789, 123.456789 | Dimensions and analytic volume at strict relative tolerance | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_preserves_measurement_precision` |
| 17 | test_refuses_nonfinite_volume | Error | Library returns Inf/NaN | Unknown scalar volume | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_refuses_nonfinite_volume` |
| 18 | test_geometry_from_mesh_handles_non_watertight_volume_error | Error | Open surface | Dimensions survive unavailable integral | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_geometry_from_mesh_handles_non_watertight_volume_error` |
| 19 | test_reports_inconsistent_winding_volume_evidence | Error | Box with reversed facet | Unavailable/inconsistent_winding | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_reports_inconsistent_winding_volume_evidence` |
| 20 | test_reports_open_surface_volume_evidence | Error | Box missing facet | Unavailable/not_watertight | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_reports_open_surface_volume_evidence` |
| 21 | test_reports_negative_integral_volume_evidence | Error | Globally reversed closed box | Unavailable/non_positive_integral | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_reports_negative_integral_volume_evidence` |
| 22 | test_reports_nonfinite_integral_volume_evidence | Error | Library returns Inf/NaN | Unavailable/nonfinite_integral | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_reports_nonfinite_integral_volume_evidence` |
| 23 | test_reports_zero_integral_volume_evidence | Error | Library returns zero | Unavailable/non_positive_integral | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_reports_zero_integral_volume_evidence` |
| 24 | test_reports_small_measured_volume_evidence | Edge | Cube edge .001 | Measured 1e-9 without rounding | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_reports_small_measured_volume_evidence` |
| 25 | test_reports_missing_geometry_volume_evidence | Edge | No mesh | Not calculated/geometry_unavailable | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_reports_missing_geometry_volume_evidence` |
| 26 | test_unexpected_volume_failure_retains_independent_measurements | Error | Actual library seam raises RuntimeError | Dimensions/count retained, typed cause, original diagnostic, source unchanged | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestGeometryFromMesh::test_unexpected_volume_failure_retains_independent_measurements` |
| 27 | test_preserves_signed_integrals_across_facet_batch_boundaries | Edge | 64 translated tetrahedra including ten reversed; batches 5/12/17 | Signed analytic volume44, count256, immutable source | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestVolumeIntegralBatching::test_preserves_signed_integrals_across_facet_batch_boundaries` |
| 28 | test_measures_closed_source_without_whole_mesh_geometry_copies | Edge | Closed source forbids mesh copy/full triangles array | Correct analytic volume44 | Unit | ✅ `unit/modules/media/mesh_processing/test_load_mesh.py::TestVolumeIntegralBatching::test_measures_closed_source_without_whole_mesh_geometry_copies` |
| 29 | test_measures_stl_without_thumbnail | Happy | ASCII/binary STL above full-load facet cap | Complete global dimensions/count, no render stage | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestMetadataOnly::test_measures_stl_without_thumbnail` |
| 30 | test_leaves_streamed_volume_unknown | Edge | Complete bounded metadata-only scan | Not calculated/topology_not_evaluated | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestMetadataOnly::test_leaves_streamed_volume_unknown` |
| 31 | test_thumbnail_only_preview_preserves_unrequested_volume | Edge | Streaming/fallback without geometry request | Preview plus NotRequested geometry/volume | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestVolumeRequestOwnership::test_thumbnail_only_preview_preserves_unrequested_volume` |
| 32 | test_refuses_nonfinite_extents_from_finite_source_coordinates | Error | Actual ASCII STL and3MF finite vertices whose extent overflows | Typed invalid-source refusal; no infinite JSON measurements | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestMeasurementNumericalValidity::test_refuses_nonfinite_extents_from_finite_source_coordinates` |
| 33 | test_preserves_integral_for_large_translated_3mf | Edge | 3MF box at0 and±1e15 | Analytic volume6 and exact1/2/3 extents | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestVolumeTranslationPrecision::test_preserves_integral_for_large_translated_3mf` |
| 34 | test_retains_negative_orientation_after_translation | Error | 3MF reflected surface at±1e15 | Nonpositive-integral refusal, source unchanged | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestVolumeTranslationPrecision::test_retains_negative_orientation_after_translation` |
| 35 | test_preserves_far_separated_3mf_instance_integrals | Edge | Two actual3MF instances at opposite1e15 | Measured12, expanded count24 | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestDisconnectedVolumePrecision::test_preserves_far_separated_3mf_instance_integrals` |
| 36 | test_retains_negative_inner_shell_contribution | Edge | Translated outer/negative inner shell | Measured signed volume5 | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestDisconnectedVolumePrecision::test_retains_negative_inner_shell_contribution` |
| 37 | test_complete_bounded_preview_exposes_unassessed_topology | Happy | Complete streaming/fallback previews | Exact dimensions/count, topology not evaluated | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestBoundedVolumeAuthority::test_complete_bounded_preview_exposes_unassessed_topology` |
| 38 | test_partial_fallback_cannot_publish_sampled_measurements | Error | Actual bounded partial fallback | Usable incomplete image, refused geometry, no sampled metadata | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestBoundedVolumeAuthority::test_partial_fallback_cannot_publish_sampled_measurements` |
| 39 | test_rejects_nonphysical_incoming_dimensions_before_publication | Error | Three dimensions withInf/-Inf/negative/bool | No publication/File row; staged source retained | Integration | ✅ `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestIncomingDimensionEvidence::test_rejects_nonphysical_incoming_dimensions_before_publication` |
| 40 | test_rejects_invalid_volume_before_artifact_side_effects | Error | Internal DB columns, malformed variant, invalid scalar, mismatch | No publication/version allocation/File row | Integration | ✅ `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestIncomingVolumeEvidence::test_rejects_invalid_volume_before_artifact_side_effects` |
| 41 | test_persists_explicit_volume_evidence | Happy | Modern typed evidence | Exact persisted variant | Integration | ✅ `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestIncomingVolumeEvidence::test_persists_explicit_volume_evidence` |
| 42 | test_preserves_scalar_only_payload_as_unassessed | Edge | Old scalar-only payload | Original finite/null scalar remains legacy | Integration | ✅ `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestIncomingVolumeEvidence::test_preserves_scalar_only_payload_as_unassessed` |
| 43 | test_new_mesh_metadata_starts_pending | Happy | New mesh Metadata | Pending cause, null scalar/method | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumePersistence::test_new_mesh_metadata_starts_pending` |
| 44 | test_persists_unavailable_volume_without_fingerprint | Error | Real inconsistent STL, optional fingerprint disabled | Unavailable winding cause persisted | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumePersistence::test_persists_unavailable_volume_without_fingerprint` |
| 45 | test_rejects_missing_integral_method | Error | Raw SQL measured/unavailable with NULL method | IntegrityError | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumeDatabaseConstraint::test_rejects_missing_integral_method` |
| 46 | test_rejects_invalid_cross_column_volume_evidence | Error | Raw SQL contradictory state/value/method/causes | IntegrityError | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumeDatabaseConstraint::test_rejects_invalid_cross_column_volume_evidence` |
| 47 | test_rejects_internal_volume_column_overrides | Error | Four internal column overrides | Fixture rejects ambiguous normalization | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumeMetadataFactory::test_rejects_internal_volume_column_overrides` |
| 48 | test_refreshes_gcode_metadata_from_scalar_recipe_without_losing_slicer_facts | Edge | Old ready G-code recipe1 andlegacy scalar | Recipe2 ready, not applicable volume, slicer facts retained | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumeRecipeRefresh::test_refreshes_gcode_metadata_from_scalar_recipe_without_losing_slicer_facts` |
| 49 | test_discovers_mesh_metadata_from_previous_evidence_recipe | Edge | Old ready mesh recipe8 | Current recipe9 work discovered | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumeRecipeRefresh::test_discovers_mesh_metadata_from_previous_evidence_recipe` |
| 50 | test_rejects_nonphysical_persisted_dimensions | Error | Three axes raw SQL Inf/-Inf/negative | IntegrityError | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestDimensionDatabaseConstraint::test_rejects_nonphysical_persisted_dimensions` |
| 51 | test_upgrade_repairs_unrepresentable_legacy_scalar_without_losing_other_facts | Error | Predecessor DB with±Inf volume | Null legacy volume, same IDs/slicer facts, repair count | Integration | ✅ `integration/db/migrations/test_mesh_volume_evidence.py::TestHistoricalNonfiniteVolume::test_upgrade_repairs_unrepresentable_legacy_scalar_without_losing_other_facts` |
| 52 | test_upgrade_retains_historical_volume_without_certification | Edge | Null/zero/negative/tiny/positive old scalar | Exact legacy scalar, schema equivalence | Integration | ✅ `integration/db/migrations/test_mesh_volume_evidence.py::TestVolumeUpgrade::test_upgrade_retains_historical_volume_without_certification` |
| 53 | test_upgrade_switches_new_rows_to_pending_without_changing_old_rows | Happy | Old row followed by new insert | Historical legacy, future pending, preserved keys/index | Integration | ✅ `integration/db/migrations/test_mesh_volume_evidence.py::TestVolumeUpgrade::test_upgrade_switches_new_rows_to_pending_without_changing_old_rows` |
| 54 | test_roundtrip_preserves_historical_measurements | Edge | Upgrade/downgrade/reupgrade | Tiny scalar and source facts retained, schema equivalence | Integration | ✅ `integration/db/migrations/test_mesh_volume_evidence.py::TestVolumeUpgrade::test_roundtrip_preserves_historical_measurements` |
| 55 | test_offline_sql_preserves_repair_contract | Edge | Offline SQLite rendering | One pinned rebuild, literal repairs/defaults, no fabricated counts | Integration | ✅ `integration/db/migrations/test_mesh_volume_evidence.py::TestVolumeOfflineMigration::test_offline_sql_preserves_repair_contract` |
| 56 | test_upgrade_retires_invalid_dimension_facts | Error | Three axes withInf/-Inf/negative historical facts | Null affected fact only, same IDs/slicer facts, repair count | Integration | ✅ `integration/db/migrations/test_mesh_volume_evidence.py::TestHistoricalDimensionRepair::test_upgrade_retires_invalid_dimension_facts` |
| 57 | test_upgrade_preserves_finite_dimension_precision | Edge | Three axes with0 and1e-9 | Exact original dimensions | Integration | ✅ `integration/db/migrations/test_mesh_volume_evidence.py::TestHistoricalDimensionRepair::test_upgrade_preserves_finite_dimension_precision` |
| 58 | test_upgrade_retires_unrepresentable_measurement_facts | Error | Released PostgreSQL withInf/-Inf/NaN andnegative bbox | Null unusable facts, tiny axis/facts retained, schema equivalence | Integration | ✅ `integration/postgres/test_contracts.py::TestMeshMeasurementContract::test_upgrade_retires_unrepresentable_measurement_facts` |
| 59 | test_rejects_integral_evidence_without_method | Error | Measured/unavailable raw SQL withNULL method | PostgreSQL IntegrityError | Integration | ✅ `integration/postgres/test_contracts.py::TestMeshMeasurementContract::test_rejects_integral_evidence_without_method` |
| 60 | test_rejects_nonfinite_measurement_facts | Error | Each dimension/legacy scalar withInf/-Inf/NaN | PostgreSQL IntegrityError | Integration | ✅ `integration/postgres/test_contracts.py::TestMeshMeasurementContract::test_rejects_nonfinite_measurement_facts` |
| 61 | test_portable_archive_retains_public_volume_evidence | Happy | Real ZIP modern measured/unavailable/legacy variants | Exact public evidence import; no internal columns | Integration | ✅ `integration/modules/ingestion/test_library_transfer.py::TestPortableVolumeEvidence::test_portable_archive_retains_public_volume_evidence` |
| 62 | test_imports_scalar_only_archive_without_certifying_volume | Edge | Old ZIP scalar-only null/zero/negative/tiny/positive | Legacy variant retained after actual import | Integration | ✅ `integration/modules/ingestion/test_library_transfer.py::TestPortableVolumeEvidence::test_imports_scalar_only_archive_without_certifying_volume` |
| 63 | test_export_preserves_volume_evidence | Happy | Measured/unavailable/not calculated/legacy0/null/noMetadata | CSV appended evidence andJSON retain exact facts | Integration | ✅ `integration/modules/library/model_views/test_export_payload.py::TestExportVolumeEvidence::test_export_preserves_volume_evidence` |
| 64 | test_ingestion_publishes_volume_evidence_without_fingerprints | Happy | Real3MF tiny/winding uploads; fingerprints disabled | API requiredvariant, SQLite evidence, currentrecipe ready, no fingerprints | E2E | ✅ `e2e/test_mesh_measurements.py::TestMeshMeasurementEvidence::test_ingestion_publishes_volume_evidence_without_fingerprints` |
| 65 | preserves published volume after mesh metadata cancellation | Error | Current metadata measured6e-9; native result2.0 returns after durable cancellation | Outcome empty; measured6e-9 remains; metadata derivative CANCELLED | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestAttemptPublication::test_cancelled_mesh_metadata_preserves_published_volume` |
| 66 | preserves replacement volume after mesh metadata supersession | Error | Fresh attempt publishes measured1e-9 or unavailable/not_watertight before stale2.0 arrives | Stale outcome empty; exact replacement evidence preserved; current metadata derivative READY | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestAttemptPublication::test_superseded_mesh_metadata_preserves_replacement_volume` |
| 67 | preserves published volume when mesh source changes | Error | ArtifactSource digest changes after native computation before publish | Stale outcome empty; previously measured6e-9 unchanged | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestAttemptPublication::test_replaced_mesh_source_preserves_published_volume` |
| 68 | replaces rounded small measurements with assessed volume | Edge | Real small3MF plus old bbox/volume zeros and recipe4 | SQLite bbox0.001/0.002/0.003 and assessed measured6e-9 remain unrounded | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_replaces_rounded_small_measurements` |
| 69 | replaces an unassessed winding volume with its cause | Error | Real STL one reversed face; scalar-only historical500 | SQLite volume null; unavailable/inconsistent_winding replaces unassessed scalar | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_replaces_stale_inconsistent_winding_volume` |
| 70 | returns pending volume evidence | Happy | default metadata | scalar null; exact not_calculated/enrichment_pending five-field evidence | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::returns pending volume evidence` |
| 71 | derives the scalar from measured evidence | Happy | explicit measured value 100 | volume_mm3 equals measured value | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::derives the scalar from measured evidence` |
| 72 | accepts an exactly matching measured scalar | Happy | scalar 100 plus measured 100 | matching fields retained | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::accepts an exactly matching measured scalar` |
| 73 | preserves unavailable volume causes | Happy | all five unavailable causes | integral method, null scalar and value, original cause | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::preserves unavailable volume causes: %s` |
| 74 | preserves not calculated volume causes | Happy | all five not calculated causes | null method, scalar and value, original cause | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::preserves not calculated volume causes: %s` |
| 75 | preserves historical scalar as unassessed | Edge | null, zero, positive, negative scalar override | legacy_unassessed with matching scalar and null method/cause | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::preserves historical scalar as unassessed: $label` |
| 76 | preserves explicit legacy evidence | Edge | explicit legacy zero | zero retained without certification | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::preserves explicit legacy evidence` |
| 77 | does not share default volume evidence | Edge | two default builds; mutate first evidence | second remains enrichment_pending | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::does not share default volume evidence` |
| 78 | copies caller volume evidence | Edge | mutate evidence after build | metadata preserves original evidence | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::copies caller volume evidence` |
| 79 | preserves unrelated metadata overrides | Edge | triangle count and material override | both unrelated fields retained | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::preserves unrelated metadata overrides` |
| 80 | rejects conflicting volume scalars | Error | measured, unavailable, not_calculated, legacy with mismatched scalar | throws coherence error | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::rejects conflicting volume scalars: $state` |
| 81 | rejects invalid measured volumes | Error | zero, negative, NaN, positive/negative Infinity | throws positive finite measurement error | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::rejects invalid measured volumes: $label` |
| 82 | rejects nonfinite historical scalars | Error | NaN, positive/negative Infinity | throws finite legacy error | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::rejects nonfinite historical scalars: $label` |
| 83 | rejects nonfinite explicit legacy evidence | Error | legacy NaN, positive/negative Infinity | throws finite legacy error | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aMetadata::rejects nonfinite explicit legacy evidence: $label` |
| 84 | test_rejects_volume_assessment_when_geometry_was_not_requested | Error | Not requested geometry with assessment causes | Constructor rejects incompatible request evidence | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeEvidence::test_rejects_volume_assessment_when_geometry_was_not_requested` |
| 85 | test_rejects_measured_volume_when_geometry_was_not_requested | Error | Not requested geometry with measured volume | Constructor rejects measured evidence | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeEvidence::test_rejects_measured_volume_when_geometry_was_not_requested` |
| 86 | test_rejects_invalid_native_volume_wire | Error | Legacy output, bool, unknown cause, scalar mismatch | Typed malformed-worker refusal | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeEvidence::test_rejects_invalid_native_volume_wire` |
| 87 | test_rejects_geometry_not_requested_with_measured_wire_volume | Error | Forged contradictory frame | Typed malformed-worker refusal | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeEvidence::test_rejects_geometry_not_requested_with_measured_wire_volume` |
| 88 | test_rejects_native_reply_without_volume_evidence | Error | Required volume field missing | Typed malformed-worker refusal | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeEvidence::test_rejects_native_reply_without_volume_evidence` |
| 89 | test_rejects_assessed_volume_from_refused_geometry | Error | Refused geometry with measured/unavailable volume | Constructor rejects contradictory evidence | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeOwnership::test_rejects_assessed_volume_from_refused_geometry` |
| 90 | test_rejects_durable_row_causes_in_native_output | Error | Pending/not applicable row causes | Constructor rejects non-native causes | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeOwnership::test_rejects_durable_row_causes_in_native_output` |
| 91 | test_rejects_assessed_volume_from_refused_native_frame | Error | Forged refused/measured frame | Typed malformed-worker refusal | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeVolumeOwnership::test_rejects_assessed_volume_from_refused_native_frame` |
| 92 | test_rejects_nonphysical_native_dimensions | Error | Nonfinite, negative, bool, string, overflowing dimension | Typed malformed-worker refusal | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeDimensionEvidence::test_rejects_nonphysical_native_dimensions` |
| 93 | test_csv_keeps_absent_metadata_distinct_from_volume_evidence | Edge | Model without Metadata | Blank appended CSV cells; empty JSON metadata | Integration | ✅ `integration/modules/library/model_views/test_export_payload.py::TestExportVolumeEvidence::test_csv_keeps_absent_metadata_distinct_from_volume_evidence` |
| 94 | test_openapi_contract | Happy | Intended required volume variant and nonnegative dimensions | Exact reviewed OpenAPI snapshot | Repo | ✅ `repo/test_openapi_contract.py::TestOpenapi::test_openapi_contract` |
| 95 | test_preserves_geometry_refusal_with_preview | Error | Refused geometry, independent preview | Preview retained with not-calculated/geometry_unavailable volume | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_preserves_geometry_refusal_with_preview` |
| 96 | test_retains_returned_stage_statistics | Happy | Thumbnail-only native result | NotRequested volume with original phase evidence | Unit | ✅ `unit/scripts/test_bench_thumbnails.py::TestBenchmarkFile::test_retains_returned_stage_statistics` |
| 97 | test_force_rebuild_refreshes_existing_mesh_thumbnail | Happy | Real admin rebuild and replacement preview | Replacement persisted with coherent not-requested volume | Integration | ✅ `integration/api/v1/ingest/test_ingest_api.py::TestIngestModel::test_force_rebuild_refreshes_existing_mesh_thumbnail` |
| 98 | test_publishes_requested_volume_evidence | Happy | Four explicit volume variants | Production projection retains exact wire and scalar | Repo | ✅ `repo/test_factories.py::TestBuildMetadata::test_publishes_requested_volume_evidence` |
| 99 | test_full_renderer_is_reported_as_the_selected_strategy | Happy | Full mesh path with typed MeshMeasurements and topology-not-evaluated volume | PNG, full strategy, no failure, exact geometry | Unit | ✅ `unit/modules/media/test_thumbnail_engine.py::TestThumbnailEngine::test_full_renderer_is_reported_as_the_selected_strategy` |
| 100 | test_post_load_memory_budget_is_enforced | Error | Loaded mesh exceeds RAM facet cap; typed MeshMeasurements stub | Renderer forbidden, no image, none strategy, resource-limit refusal | Unit | ✅ `unit/modules/media/test_thumbnail_engine.py::TestThumbnailEngine::test_post_load_memory_budget_is_enforced` |
| 101 | test_refusal_survives_a_successful_preview_reply | Edge | GeometryRefused with uncalculated/geometry-unavailable volume and successful preview | Encode/decode preserves geometry refusal | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestGeometryOutcome::test_refusal_survives_a_successful_preview_reply` |
| 102 | rejects invalid volume measurement in scalar accessor | Error | None, string measured, integer 1 bypass public typing | TypeError invalid volume measurement | Unit | ✅ backend/packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeValue::test_rejects_invalid_volume_measurement |
| 103 | rejects invalid volume measurement in encoder | Error | None, string measured, integer 1 bypass public typing | TypeError invalid volume measurement | Unit | ✅ backend/packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeWire::test_rejects_invalid_volume_measurement |
