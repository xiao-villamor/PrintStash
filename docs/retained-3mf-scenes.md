# Retained 3MF scenes

A 3MF scene keeps each reached mesh resource's float64 vertices and triangle
indices separate from its instances. Each instance identifies a resource and an
affine placement. Repeated placements share source buffers instead of requiring
an immediate whole-scene mesh. Supported units are normalized to millimetres.

This preserves the [3MF capability policy](3mf-capabilities.md): unsupported
required extensions remain typed refusals. Original Artifact bytes and validated
embedded previews retain their independent lifetimes.

## Admission and consumer budgets

The bounded reader retains its hard ceiling of 2,000,000 faces. It validates both
reached source geometry and expanded placement counts, together with its existing
archive, XML, reference and transform controls. Retaining resources does not waive
these safety checks or authorize a later allocation.

Consumers then apply separate budgets:

- Basic bounds and triangle count use the retained scene without materializing a
  whole mesh. Source coordinates and placement arithmetic remain float64.
- Global topology resolution may materialize only within `load_face_budget`,
  which combines the configured triangle ceiling and the detected RAM budget.
- Fingerprints use the request's analysis triangle cap. A scene above the basic
  materialization or render cap can still produce a fingerprint when it fits this
  analysis budget. An over-budget fingerprint is refused before materialization.
- Full preview rendering checks its configured and RAM-derived triangle cap.
  Refusing this output preserves independently available metadata, fingerprints
  and a validated document preview.

When topology and fingerprint extraction both need placed geometry, they reuse
one materialization during the request. Its temporary mesh buffers and measurement topology caches are released
before direct scene rendering, including when fingerprints are disabled. A failed topology attempt does not trigger a
second materialization for the fingerprint or erase known bounds and count.

Measurement retains unique resource geometry and referenced-vertex indices;
placed-coordinate scratch is limited to chunks of 64,000 points. This is not a
renderer memory guarantee. Visual preparation can expand referenced positions
for every placement, center them in float64, and create float32 render positions.
Triangle indices enter the rasterizer in bounded chunks.

## Physical volume and topology

Closed, consistently oriented resources use the canonical signed polyhedral
integral. The kernel keeps component-local origins, batches of 4,096 facets and
accurate summation. Instance contributions scale by the absolute determinant of
the placement; reflected facets retain the renderer's corrected winding policy.
Signed resource contributions remain signed until their final sum, so a
negatively oriented inner shell subtracts a cavity instead of adding its volume.
A nonfinite or nonpositive final integral is a typed unavailable volume.
When source and placement scales compensate for each other, intermediate
underflow or overflow must not reject a finite placed volume. The ordinary
signed integral/product remains unchanged when representable. Lost source
range is recovered with exact power-of-two coordinate scaling; signed
logarithmic placement arithmetic avoids an overflowing intermediate determinant.

Open resources require whole-scene topology evaluation: separate parts may close
only after global welding, and coincident unwelded copies may remain invalid.
Resources are not repaired independently to infer a valid global solid. Within
the topology budget, the materialized mesh uses the existing canonical volume
classification. Beyond that budget, or after failed materialization, exact bounds
and count remain usable with `TOPOLOGY_NOT_EVALUATED` volume. Other volume faults
retain their specific typed causes without inventing a scalar estimate.

## Coverage describes separate facts

`SourceScanState.COMPLETE` records a completed source read. It does not imply a
whole mesh was materialized, topology was established, or a preview was rendered.
A scene-only result therefore retains `GeometryNotLoaded`; successful topology or
fingerprint materialization records `CompleteGeometry`. Preview coverage records
its own outcome: complete rendered output, document-supplied output, or no output.
A supplied image cannot certify geometry that was refused or never read.

See [derivative contracts](derivatives.md) for publication and retry behavior.

## Bounded measurement comparison

A diagnostic comparison used one closed icosphere resource (1,280 faces), with
1,64 and512 separated placements. Each mode ran twice in fresh processes,
alternating execution order. Timing covers measurement and, for the materialized
mode, its whole-scene allocation. Imports and source creation precede timing;
this is not an ingestion-throughput or rendering benchmark. Source digests,
array identities and physical results matched in all twelve observations.

| Placements | Retained median time | Materialized median time | Retained tracked peak | Materialized tracked peak |
|---|---|---|---|---|
| 1 | 9.66 ms | 12.40 ms | 0.695 MiB | 0.705 MiB |
| 64 | 32.33 ms | 239.94 ms | 0.716 MiB | 21.618 MiB |
| 512 | 242.45 ms | 1778.73 ms | 0.861 MiB | 172.848 MiB |

The tracked peak is allocation memory observed by `tracemalloc`, not total RSS.
At512 placements, process high-water RSS medians were164.1 MiB for retained and
340.2 MiB for materialized measurement, including the runtime baseline. Memory
stays near unique-resource cost while placed scratch remains bounded. The final
comparison completed in28.23s after the compensated-scale/lifetime corrections.
An earlier run recorded17.99ms versus12.37ms for one placement; this final run
recorded9.66ms versus12.40ms, so small timings do not establish a general speedup.
These synthetic samples support the allocation contract, not a production
latency target. Python3.12.3 used one BLAS/OpenMP thread.

## Validation

Focused core checks:60 passed. Affected backend checks:152 passed with two
initial fixture/architecture failures; the exact lifetime correction passed
separately and all53 architecture guard cases passed after registering the
new primitive. The initial failure logs remain available for diagnosis. Independent review
added six compensated-scale regressions, reproduced red before correction;
all54 related scene/integral/precision checks passed after correction.
CI exposed six former coupled-budget consumer expectations. The corrected
contracts retain metadata above preview caps; terminal-refusal fixtures exceed
the unchanged hard2048-instance limit. All six focused checks passed in37.32s.
The extended lifetime test reproduced retained measurement caches without
fingerprints; both lifetime cases passed after releasing those caches.
The current configured backend type check and Ruff checks passed.

The completed matrix below records101 distinct test functions; parameterized
cases were verified against the passing test results. It includes the real
ingestion flow, recipe backfill and historical descriptor regressions.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | test_materializes_64_placements_without_concatenation | Happy | 64placements, ordinary or reflected scale | Exact placed vertices/faces; output owns arrays; original vertices/faces/transforms unchanged; no stacked partial buffers | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_materializes_64_placements_without_concatenation` |
| 2 | test_rejects_expanded_budget_before_output_allocation | Error | 64placements exceed face or vertex cap | scene_resource_limit before output allocation | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_expanded_budget_before_output_allocation` |
| 3 | test_accepts_exact_materialization_budget | Edge | Counts exactly256faces/vertices | Exact shapes and final placement offsets accepted | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_accepts_exact_materialization_budget` |
| 4 | test_rejects_invalid_materialization_budget | Error | Zero, bool, float or over-ceiling budgets | Closed invalid_scene_budget refusal | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_invalid_materialization_budget` |
| 5 | test_rejects_empty_scene | Error | No resources/instances | Closed empty_scene refusal | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_empty_scene` |
| 6 | test_rejects_invalid_resource_identity | Error | Duplicate or empty resource IDs | duplicate_resource refusal | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_invalid_resource_identity` |
| 7 | test_rejects_missing_resource | Error | Placement points to absent ID | missing_resource refusal | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_missing_resource` |
| 8 | test_ignores_unreferenced_invalid_arrays | Edge | Unused resource has invalid arrays | Only reached valid geometry contributes to exact output | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_ignores_unreferenced_invalid_arrays` |
| 9 | test_rejects_invalid_source_before_output_allocation | Error | Nonfinite points, bad/negative/uint64 indices or empty faces | Exact closed source refusal before output allocation | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_invalid_source_before_output_allocation` |
| 10 | test_rejects_invalid_placement_before_output_allocation | Error | Bad shape/nonfinite/singular transform | Exact invalid/degenerate transform refusal before output allocation | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_invalid_placement_before_output_allocation` |
| 11 | test_rejects_oversized_scene_before_output_allocation | Error | 4097resources or2049instances | scene_resource_limit before output allocation | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_oversized_scene_before_output_allocation` |
| 12 | test_normalizes_valid_source_dtypes | Edge | float32/int32 or int64/uint64 valid resource arrays | Exact expected coordinates/indices normalized to float64/int64 | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_normalizes_valid_source_dtypes` |
| 13 | test_rejects_transformed_numeric_overflow | Error | Finite huge scale overflows placed coordinates | Closed numeric_range refusal | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_components.py::TestComposeScene::test_rejects_transformed_numeric_overflow` |
| 14 | test_yields_repeatable_chunks_in_placement_order | Happy | Identity+reflected placements; chunk1/5/20 | Exact relative float32 vertices, reordered faces, repeated iterator order and immutable source arrays | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareScene::test_yields_repeatable_chunks_in_placement_order` |
| 15 | test_rejects_invalid_chunk_size | Error | Zero/negative/bool/float chunk | invalid_render_chunk refusal | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareScene::test_rejects_invalid_chunk_size` |
| 16 | test_rejects_source_limits_before_render_allocation | Error | Expanded faces/vertices or instance ceiling exceeded | scene_resource_limit before render output allocation | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareScene::test_rejects_source_limits_before_render_allocation` |
| 17 | test_rejects_relative_render_numeric_overflow | Error | Finite huge placement scale cannot fit relative float32 | numeric_range refusal | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareScene::test_rejects_relative_render_numeric_overflow` |
| 18 | test_preserves_placed_mesh_pixels_without_materialization | Happy | 64placements including reflections, overlaps and crossing face chunks | Exact legacy pixel equality without source materialization; source arrays/transforms unchanged | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderSceneThumbnail::test_preserves_placed_mesh_pixels_without_materialization` |
| 19 | test_preserves_scene_pixels_after_large_translation | Edge | Scene shifted by positive/negative1e9 | Exact pixels retained after centering in float64 | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderSceneThumbnail::test_preserves_scene_pixels_after_large_translation` |
| 20 | test_ignores_unused_scene_coordinates | Edge | Source vertices include far unreferenced coordinate | Exact regular-scene pixels | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderSceneThumbnail::test_ignores_unused_scene_coordinates` |
| 21 | test_keeps_silhouette_fallback_for_scene | Edge | Inverted plate winding | Exact legacy fallback silhouette pixels | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderSceneThumbnail::test_keeps_silhouette_fallback_for_scene` |
| 22 | test_returns_nothing_for_empty_scene | Error | No resources/instances | Public scene thumbnail returns None | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderSceneThumbnail::test_returns_nothing_for_empty_scene` |
| 23 | test_remaps_referenced_vertices_in_face_chunks | Edge | Legacy mesh has unused coordinates and multiple chunk sizes | Referenced faces/frame pixel behavior retained | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_remaps_referenced_vertices_in_face_chunks` |
| 24 | test_preserves_camera_rendering_after_translation | Edge | Legacy explicit camera and translated source | Camera pixels remain translation invariant | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_preserves_camera_rendering_after_translation` |
| 25 | test_preserves_source_coordinates | Happy | Legacy structural mesh input | Source coordinates remain unchanged | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_preserves_source_coordinates` |
| 26 | test_renders_the_same_pixels_at_every_face_chunk_size | Edge | Legacy mesh rendered with different face chunk sizes | Exact pixels retained across chunk boundaries | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_renders_the_same_pixels_at_every_face_chunk_size` |
| 27 | test_can_encode_the_canonical_webp_without_a_png_intermediate | Happy | Legacy canonical WebP request | Direct WebP encoding retained | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_can_encode_the_canonical_webp_without_a_png_intermediate` |
| 28 | test_returns_nothing_for_a_mesh_whose_faces_are_none | Error | Legacy structural mesh lacks faces | Public mesh thumbnail returns None | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_returns_nothing_for_a_mesh_whose_faces_are_none` |
| 29 | test_measures_64_placements_from_unique_source | Happy | 64 placements of one tetrahedron | Analytical dimensions/count/volume and source bytes unchanged | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_measures_64_placements_from_unique_source` |
| 30 | test_preserves_affine_signed_integral | Happy | reflection/nonuniform scale/shear | Physical bbox and signed integral match materialized owner | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_affine_signed_integral` |
| 31 | test_preserves_additive_closed_overlap_volume | Edge | coincident/overlapping closed resources | Additive legacy closed index-domain integral retained | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_additive_closed_overlap_volume` |
| 32 | test_preserves_adjacent_closed_solid_volume | Edge | two adjoining closed cubes | Volume16 and dimensions match whole scene | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_adjacent_closed_solid_volume` |
| 33 | test_preserves_inverted_surface_volume_refusal | Error | reversed closed source | NON_POSITIVE_INTEGRAL with useful dimensions/count | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_inverted_surface_volume_refusal` |
| 34 | test_refuses_volume_for_inconsistent_closed_winding | Error | one flipped face | INCONSISTENT_WINDING with useful dimensions/count | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_refuses_volume_for_inconsistent_closed_winding` |
| 35 | test_preserves_finite_measurements_when_volume_overflows | Error | finite dimensions with overflowing determinant | Dimensions/count preserved; NONFINITE_INTEGRAL | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_finite_measurements_when_volume_overflows` |
| 36 | test_requires_whole_topology_for_closing_halves | Edge | two open halves close after weld | Explicit topology requirement with useful dimensions/count | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_requires_whole_topology_for_closing_halves` |
| 37 | test_requires_whole_topology_for_coincident_unwelded_copies | Edge | duplicated facet vertices | Explicit global topology requirement | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_requires_whole_topology_for_coincident_unwelded_copies` |
| 38 | test_ignores_unused_source_coordinates | Edge | unreferenced distant vertex | Referenced measures unchanged | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_ignores_unused_source_coordinates` |
| 39 | test_preserves_physical_measurements_at_large_placement | Edge | large affine translation | Original physical measures preserved | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_physical_measurements_at_large_placement` |
| 40 | test_preserves_disconnected_resource_precision | Edge | closed cubes at opposite1e15 offsets | Signed volume12 with source unchanged | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_disconnected_resource_precision` |
| 41 | test_preserves_signed_cavity_contribution | Edge | positive outer and negative inner resource | Signed shell aggregate56 | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_signed_cavity_contribution` |
| 42 | test_preserves_dimensions_when_volume_kernel_fails | Error | library integral exception | MEASUREMENT_FAILED with bbox/count retained | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_dimensions_when_volume_kernel_fails` |
| 43 | test_rejects_nonfinite_placed_dimensions | Error | finite source whose global span overflows | Typed numeric_range refusal | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_rejects_nonfinite_placed_dimensions` |
| 44 | test_requires_topology_resolution_before_projection | Error | VolumeTopologyRequired | No public projection until explicit resolution | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_requires_topology_resolution_before_projection` |
| 45 | test_rejects_invalid_triangle_count | Error | zero/negative/bool/noninteger/overcap count | Constructor rejects invalid count | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestSceneMeasurements::test_rejects_invalid_triangle_count` |
| 46 | test_rejects_unknown_volume_outcome | Error | unknown string outcome | Constructor rejects noncanonical union | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestSceneMeasurements::test_rejects_unknown_volume_outcome` |
| 47 | test_rejects_invalid_bounds | Error | length/nonfinite/inverted/overflow/bool bounds | Constructor rejects invalid bounds | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestSceneMeasurements::test_rejects_invalid_bounds` |
| 48 | test_preserves_measurements_across_point_chunks | Edge | chunk1/3/4 | Analytical tetrahedron measures unchanged across final partial chunks | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_measurements_across_point_chunks` |
| 49 | test_preserves_unique_arrays_for_repeated_instances | Happy | 64 instances sharing one resource | Placed count768, exact source buffer identities, no whole mesh | Unit | ✅ `tests/unit/modules/media/test_mesh_resources.py::TestPreparedScene::test_preserves_unique_arrays_for_repeated_instances` |
| 50 | test_rejects_invalid_resource_identity | Error | empty scene/missing resource | Typed empty_scene or missing_resource rejection | Unit | ✅ `tests/unit/modules/media/test_mesh_resources.py::TestPreparedScene::test_rejects_invalid_resource_identity` |
| 51 | test_rejects_expanded_face_budget_before_materialization | Error | 1008faces ×2048instances exceeds2Mfaces | scene_resource_limit before placed buffers | Unit | ✅ `tests/unit/modules/media/test_mesh_resources.py::TestPreparedScene::test_rejects_expanded_face_budget_before_materialization` |
| 52 | test_rejects_unknown_scene_type | Error | string scene | TypeError at PreparedScene boundary | Unit | ✅ `tests/unit/modules/media/test_mesh_resources.py::TestPreparedScene::test_rejects_unknown_scene_type` |
| 53 | test_preserves_complete_source_identity | Happy | one identity instance | CompleteGeometry and source resource alias preserved | Unit | ✅ `tests/unit/modules/media/test_mesh_resources.py::TestMaterializeScene::test_preserves_complete_source_identity` |
| 54 | test_preserves_reflected_source_arrays | Happy | reflection and nonuniformscale | Correct winding and volume144 with source arrays unchanged | Unit | ✅ `tests/unit/modules/media/test_mesh_resources.py::TestMaterializeScene::test_preserves_reflected_source_arrays` |
| 55 | test_preserves_negative_closed_integral | Edge | reversed box | Signed -6 kept; whole measurement remains NON_POSITIVE_INTEGRAL | Unit | ✅ `tests/unit/modules/media/mesh_measurements/test_signed_integral.py::TestSignedMeshIntegral::test_preserves_negative_closed_integral` |
| 56 | test_preserves_zero_signed_aggregate | Edge | opposite signed closed cubes | Signed zero kept; whole measurement unavailable | Unit | ✅ `tests/unit/modules/media/mesh_measurements/test_signed_integral.py::TestSignedMeshIntegral::test_preserves_zero_signed_aggregate` |
| 57 | test_preserves_large_offset_component_precision | Edge | disconnected opposite1e15 boxes | Signed integral12 and measured12 | Unit | ✅ `tests/unit/modules/media/mesh_measurements/test_signed_integral.py::TestSignedMeshIntegral::test_preserves_large_offset_component_precision` |
| 58 | test_refuses_nonfinite_library_integral | Error | mass_properties NaN/Inf | NONFINITE_INTEGRAL preserved | Unit | ✅ `tests/unit/modules/media/mesh_measurements/test_signed_integral.py::TestSignedMeshIntegral::test_refuses_nonfinite_library_integral` |
| 59 | test_preserves_source_buffers | Edge | icosphere with multiple integral batches | Finite signed integral; source buffers unchanged | Unit | ✅ `tests/unit/modules/media/mesh_measurements/test_signed_integral.py::TestSignedMeshIntegral::test_preserves_source_buffers` |
| 60 | test_preserves_signed_integral_across_facet_batches | Edge | 1025closed boxes crossing4096 boundary | 6150 integral with each scratch batch≤4096faces | Unit | ✅ `tests/unit/modules/media/mesh_measurements/test_signed_integral.py::TestSignedMeshIntegral::test_preserves_signed_integral_across_facet_batches` |
| 61 | test_preserves_dimensions_when_volume_allocation_fails | Error | Volume integral raises MemoryError | Exact bbox/count retained with MEASUREMENT_FAILED; P02 whole-mesh propagation unchanged | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_dimensions_when_volume_allocation_fails` |
| 62 | Retain basic metadata and requested preview without flattening | Happy | 64 placements; geometry requested; optional thumbnail/fingerprint combinations | Exact bbox/count/volume; complete source scan, no materialized geometry; requested preview produced; over-cap optional FP refused before allocation | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_preserves_basic_outputs_without_source_materialization` |
| 63 | Fingerprint has an independent materialization budget | Edge | 256 placed faces; basic/render budget100; FP budget300; no document image | Metadata remains ready, FP READY with records and one materialization; preview refuses RESOURCE_LIMIT | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestThumbnailEngine::test_analysis_budget_is_independent_of_scene_render_budget` |
| 64 | Materialize an admitted fingerprint once | Happy | Closed single resource; metadata, FP and preview requested | One materialization; typed measured volume1000; FP READY and image | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_materializes_eligible_fingerprint_once` |
| 65 | Resolve global topology and reuse it for optional FP | Edge | Two open halves close only after whole-scene welding; FP off/on | One materialization for both consumers; exact bbox/count and VolumeMeasured1000; materialized coverage; optional FP READY | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_preserves_volume_when_halves_close_after_whole_scene_welding` |
| 66 | Preserve retained facts above topology budget | Edge | 256 faces in open halves; topology budget100; optional FP cap100 | No materialization; exact bbox/count retained; volume TOPOLOGY_NOT_EVALUATED; complete scan and geometry not loaded; optional FP budget refusal | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_preserves_scene_facts_above_topology_budget` |
| 67 | Refuse volume for coincident unwelded copies | Error | Two overlapping copies whose resources use unshared facet vertices | Metadata remains ready with8 faces; typed NOT_WATERTIGHT, no scalar volume | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_refuses_volume_for_coincident_unwelded_copies` |
| 68 | Refuse FP before allocating its over-budget scene | Error | 256 faces; FP cap100; preview requested | FP FAILED/GEOMETRY_WORK_LIMIT, no records or materialization; metadata ready and image preserved | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_refuses_optional_fingerprint_before_source_materialization` |
| 69 | Release temporary topology/FP buffers before rendering | Edge | Basic metadata with FP disabled/enabled and direct preview; Trimesh lifetimes observed | All temporary meshes dead before rendering and at return; optional FP outcome, volume and image preserved | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_releases_materialized_fingerprint_before_scene_render` |
| 70 | Refuse placement numeric overflow without crashing | Error | Finite transform entries overflow placed coordinates; FP off/on | Geometry INVALID_SOURCE with no count; source syntax scan complete and no materialized geometry; requested FP fails NUMERIC_RANGE | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_refuses_transformed_numeric_overflow_without_worker_crash` |
| 71 | Keep document image after placement overflow | Error | Numeric overflow plus a valid embedded PNG | Geometry INVALID_SOURCE and FP FAILED/NUMERIC_RANGE; embedded image succeeds independently with no thumbnail failure | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_keeps_embedded_preview_when_measurements_exceed_numeric_range` |
| 72 | Keep known facts when topology allocation fails | Error | Open halves; materializer raises invalid_3mf; FP and preview requested | One attempt; bbox/count remain ready; volume TOPOLOGY_NOT_EVALUATED; FP INVALID_3MF; scene image still produced | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_preserves_known_geometry_when_topology_materialization_fails` |
| 73 | Refuse nonfinite extent from finite placements | Error | Opposite finite translations produce infinite combined extent | Geometry INVALID_SOURCE with no published bbox; FP FAILED/NUMERIC_RANGE | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestRetainedThreeMFScene::test_refuses_nonfinite_extent_from_finite_placements` |
| 74 | Scene adapter preserves the existing preview pixels | Happy | Real repeated3MF scene rendered directly and through composed legacy mesh | PNG pixels exactly equal at160x120 | Integration | ✅ `tests/integration/modules/media/test_mesh_render.py::TestMeshRender::test_preserves_scene_preview_pixels` |
| 75 | Legacy mesh adapter retains its preview golden | Happy | Existing real Benchy fixture | Existing golden image pixels unchanged | Integration | ✅ `tests/integration/modules/media/test_mesh_render.py::TestMeshRender::test_preserves_preview_pixels` |
| 76 | Retain unsupported-required capability refusal | Error | Required unsupported extension, with/without embedded image | Geometry refuses; FP UNSUPPORTED with exact cause and no records; source bytes retained; document image survives independently | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestUnsupported3MFCapability::test_refuses_required_capability_without_losing_document_preview` |
| 77 | Retain large translation integral precision | Edge | Closed3MF placements at large offsets | Same exact physical volume for translated geometry | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestVolumeTranslationPrecision::test_preserves_integral_for_large_translated_3mf` |
| 78 | Retain signed negative orientation | Edge | Negatively oriented resource at large offsets | Typed unavailable volume with its existing nonpositive-integral cause | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestVolumeTranslationPrecision::test_retains_negative_orientation_after_translation` |
| 79 | Retain far-separated resource integrals | Edge | Two closed placements far apart | Physical volume sums local integrals without losing translation precision | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestDisconnectedVolumePrecision::test_preserves_far_separated_3mf_instance_integrals` |
| 80 | Retain negative cavity contribution | Edge | Closed outer and negatively oriented inner shells | Physical volume subtracts the inner shell contribution | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestDisconnectedVolumePrecision::test_retains_negative_inner_shell_contribution` |
| 81 | Retain shared STL reader pass bound | Edge | Over-cap STL metadata plus isolated preview | Exactly two source passes for measured preview | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestSTLReadCost::test_preview_with_geometry_reads_two_source_passes` |
| 82 | Document preview does not certify an unread source | Edge | Thumbnail-only repair with embedded3MF image | Document-supplied preview, source not scanned and geometry not loaded | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestCoverage::test_embedded_preview_does_not_certify_unread_source` |
| 83 | test_new_consumers_cannot_bypass_mesh_isolation | Error | Actual production modules including registered scene_measurements primitive | Every consumer still uses isolated seams; native primitive remains explicitly owned | Unit | ✅ `tests/repo/test_mesh_boundaries.py::TestMeshBoundaries::test_new_consumers_cannot_bypass_mesh_isolation` |
| 84 | test_rejects_retained_scene_bypasses | Error | Six raw scene/materialization/integral/render seams imported directly, called via module alias or referenced as callbacks | Exact raw target is reported for all18routes, including public core mesh export | Unit | ✅ `tests/repo/test_mesh_boundaries.py::TestMeshBoundaries::test_rejects_retained_scene_bypasses` |
| 85 | test_primitive_owners_do_not_import_orchestrators | Error | All primitive modules including scene_measurements | No primitive acquires engine/analysis/facade dependency | Unit | ✅ `tests/repo/test_mesh_boundaries.py::TestPrimitiveDependencies::test_primitive_owners_do_not_import_orchestrators` |
| 86 | test_allows_safe_consumers | Happy | Isolation generator plus pure result/request/error imports | Safe consumer produces zero violations | Unit | ✅ `tests/repo/test_mesh_boundaries.py::TestMeshBoundaries::test_allows_safe_consumers` |
| 87 | Measures retained instances through real ingestion | Happy | 64 repeated placements, preview, similarity off, materialization cap100 | Upload completes with exact bbox/count256/volume64000, useful preview and no fingerprints | E2E | ✅ `tests/e2e/test_mesh_measurements.py::TestRetainedSceneMeasurementEvidence::test_ingestion_measures_instances_beyond_materialization_budget` |
| 88 | Preserves independent physical measurement evidence | Happy | STL and3MF ingestion, fingerprint disabled | Physical dimensions/count/typed measured volume and current derivative receipt persisted | E2E | ✅ `tests/e2e/test_mesh_measurements.py::TestMeshMeasurementEvidence::test_ingestion_publishes_volume_evidence_without_fingerprints` |
| 89 | Replaces previous required-capability receipts | Error | Old measurement/fingerprint evidence, required unsupported extension | Typed refusal withdraws prior scalar evidence, keeps preview and original bytes; current version recorded | Integration | ✅ `tests/integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_required_capability_replaces_old_parser_receipts` |
| 90 | Discovers previous metadata/thumbnail recipes for backfill | Edge | Metadata10/11 or thumbnail9/10, ready/failed | Only outdated output scheduled, companion current receipt preserved | Integration | ✅ `tests/integration/modules/derivatives/test_source.py::TestPending::test_rederives_stl_outputs_from_the_previous_reader_recipe` |
| 91 | Retries previous eligibility receipts | Edge | Fingerprintv5/v6, ready/partial/failed | New version claim created and old receipt unchanged | Integration | ✅ `tests/integration/modules/similarity/test_fingerprints.py::TestFingerprintLeases::test_recomputes_fingerprints_from_the_previous_stl_sample_recipe` |
| 92 | Filters current similarity evidence by canonical version | Edge | Previousv4/v6 or current version | Previous versions historical; only current evidence eligible | Integration | ✅ `tests/integration/modules/similarity/candidates/test_candidates.py::TestInterpretationVersion::test_version_controls_current_evidence` |
| 93 | Preserves historical STL descriptor values | Edge | Historicalv6 sample golden and current eligibility receipt | Source/sample/D2/descriptor numeric hashes unchanged; result records current canonical version | Integration | ✅ `tests/integration/modules/media/test_stl_reader.py::TestSampleFingerprintRecipe::test_preserves_measured_partial_fingerprint` |
| 94 | test_preserves_compensated_affine_volume | Edge | Tetrahedron source/placement scales:1e-100×1e110;1e100×1e-110;1e-110×1e120;1e110×1e-100 | VolumeMeasured equals independent1000×(source_scale×placement_scale)^3 with zero absolute tolerance; all geometry matches actual materialized owner; source vertices/faces/transform unchanged | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_compensated_affine_volume` |
| 95 | test_preserves_compensated_negative_orientation | Edge | Reversed tetrahedron source/placement scales:1e-100×1e110;1e-110×1e120 | NON_POSITIVE_INTEGRAL matches actual materialized owner; analytical bbox/count retained; source vertices/faces/transform unchanged | Unit | ✅ `tests/unit/modules/media/test_scene_measurements.py::TestMeasureScene::test_preserves_compensated_negative_orientation` |
| 96 | test_repeated_3mf_preserves_measurements_when_worker_refuses_preview | Edge | Actual supervised400tetra placements; render cap1000faces | GeometryReady count1600/bbox2005×20×30/VolumeMeasured400000; complete source and geometry not loaded; absent previewRESOURCE_LIMIT; normal supervised exit | Integration | ✅ `tests/integration/modules/media/test_mesh_isolation.py::TestGenerate::test_repeated_3mf_preserves_measurements_when_worker_refuses_preview` |
| 97 | test_instanced_3mf_preserves_measurements_with_embedded_preview | Edge | 20assembly placements; cap50; actual embeddedPNG | Exact count80/bbox10×20×30/VolumeMeasured20000; complete source and geometry not loaded; embedded image unchanged with no failure | Integration | ✅ `tests/integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_instanced_3mf_preserves_measurements_with_embedded_preview` |
| 98 | test_instanced_3mf_preserves_measurements_when_render_is_refused | Edge | 20assembly placements; cap50; no embeddedPNG | Same exact full metadata; complete source and geometry not loaded; absent previewRESOURCE_LIMIT | Integration | ✅ `tests/integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_instanced_3mf_preserves_measurements_when_render_is_refused` |
| 99 | test_analysis_budget_preserves_measurements_without_render | Edge | 320face real sphere; fingerprint cap100; no render | GeometryReady and exact source measures with VolumeMeasured; geometry not loaded; fingerprintFAILED/GEOMETRY_WORK_LIMIT/empty records | Integration | ✅ `tests/integration/modules/media/test_thumbnail_engine.py::TestThumbnailEngine::test_analysis_budget_preserves_measurements_without_render` |
| 100 | Hard scene refusal preserves embedded preview | Error | 2049 placements of one tetrahedron exceed the unchanged 2048-instance admission limit; soft render cap remains 100; original PNG embedded | Metadata RESOURCE_LIMIT terminal at max attempts/current recipe; thumbnail READY/current recipe/embedded; stored source and embedded PNG bytes unchanged | Integration | ✅ `tests/integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_refused_geometry_is_terminal_with_an_embedded_preview` |
| 101 | Reconciler cannot retry unchanged hard scene refusal | Edge | Same 2049-placement source with original PNG embedded; soft render cap remains 100 | Metadata terminal RESOURCE_LIMIT/max attempts/current recipe; updated_at and attempts unchanged; preview READY/current recipe/embedded and stable bytes; original ZIP and embedded PNG unchanged | E2E | ✅ `tests/e2e/test_ingest.py::TestMeshFailureRecovery::test_unchanged_terminal_failure_survives_reconciler_nudges` |
