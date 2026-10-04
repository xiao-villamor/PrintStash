# Mesh result contracts

Mesh admission, loading, measurements and embedded preview extraction each have a
single owner. See [the owner and temporary facade inventory](architecture/mesh-owners.md).
The shared contracts import data definitions without loading an execution owner.

## Independent coverage facts

`ThumbnailResult.coverage` records three different facts:

- `source_scan`: unread, complete or partial source reading.
- `geometry`: no prepared geometry, complete geometry or an explicitly sampled representation.
- `preview`: no image, a document-supplied image, complete representation or partial representation.

A complete source scan may coexist with sampled geometry or a partial preview.
An embedded image does not certify that geometry was read. The fallback scan flag
certifies only source reading; preview coverage also checks the retained triangle
count. Renderer-internal buffers do not certify prepared analysis geometry.

`PreparedMesh` retains a typed `Trimesh` and resource/placement scene. Complete and
sampled geometry are distinct cases; a sample cannot claim complete resources,
B-rep evidence or a whole-resource alias. Its derived `complete` property refers
only to prepared geometry. Successful fingerprint results require meaningful
records and coherent whole/component identities; partial results require a sampled
recipe and sampling cause. Error outcomes cannot carry successful records. New
worker causes are closed values; historical stored diagnostic text is unchanged.

The native codec requires all coverage fields and rejects unknown causes or
contradictory result facts. READY analysis requires complete geometry; PARTIAL
analysis requires sampled geometry with the same cause. Refused measurement and
a useful preview remain independent.

The persisted thumbnail `output_json.complete` field continues to describe the
preview. Mesh thumbnail recipe8 re-derives recipe7 outputs, whose old flag could
conflate a completed scan with all source triangles being represented. Existing
outputs remain visible until a replacement succeeds. Geometry values, numerical
fingerprints and public HTTP schemas are unchanged.

## Coverage matrix

Each row identifies an assertion against an observable requirement. Rows include
retained policy/parser/measurement/archive behavior after moving tests to their
actual owners. All 119 legacy test functions are retained. Matrix coverage does
not claim that every row ran locally: affected unit/consumer selections and real
native parity checks were run; normal PR CI supplies the global merge gate.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Host RAM detection | Happy | Linux host | Positive or absent | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestDetectMemoryLimitBytes::test_detect_memory_limit_is_positive_on_linux |
| 2 | Cgroup v2 limit | Happy | 2 GiB ceiling | At most 2 GiB | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestDetectMemoryLimitBytes::test_detect_memory_limit_reads_cgroup_v2_value |
| 3 | Cgroup v1 limit | Happy | 1 GiB ceiling | At most 1 GiB | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestDetectMemoryLimitBytes::test_detect_memory_limit_reads_cgroup_v1_value |
| 4 | Reclamation | Happy | No loaded mesh | Returns safely | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestReclaimMemory::test_reclaim_memory_is_safe_to_call |
| 5 | Binary STL estimate | Happy | 1,234 facets | 1,234 triangles | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_binary_stl_triangle_count_is_exact |
| 6 | ASCII STL estimate | Happy | Text facets | Bytes divided by 250 | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_an_ascii_stl_is_estimated_from_its_text_density |
| 7 | 3MF XML estimate | Happy | Uncompressed model | Bytes divided by 70 | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_3mf_triangle_count_from_uncompressed_xml |
| 8 | PLY estimate | Happy | Face header | 1,234,567 faces | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_ply_face_count_from_header |
| 9 | OBJ estimate | Happy | Face directives | 300 triangles | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_obj_triangle_count_from_face_directives |
| 10 | Checkpoint release | Edge | Waiting admission | Both threads finish | Unit | ✅ unit/modules/media/mesh_policy/test_admission.py::TestRenderAdmission::test_release_progresses_during_waiter_checkpoint |
| 11 | Limit change | Edge | Active old gate | Waits until drain | Unit | ✅ unit/modules/media/mesh_policy/test_admission.py::TestRenderAdmission::test_config_change_waits_for_old_admissions_to_settle |
| 12 | Safety share | Edge | Estimates disabled | 256 MiB per job | Unit | ✅ unit/modules/media/mesh_policy/test_admission.py::TestRenderAdmission::test_disabling_estimates_keeps_divided_safety_budget |
| 13 | Fallback share | Edge | Four concurrent jobs | 256 MiB per job | Unit | ✅ unit/modules/media/mesh_policy/test_admission.py::TestRenderAdmission::test_native_fallback_is_divided_by_concurrency |
| 14 | Cgroup fallback | Edge | Unusable membership | Uses 8 GiB host RAM | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestDetectMemoryLimitBytes::test_ignores_unusable_cgroup_membership |
| 15 | Nested ceiling | Edge | Nested cgroup limits | Effective minimum | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestDetectMemoryLimitBytes::test_detects_effective_nested_cgroup_limit |
| 16 | Unlimited cgroup | Edge | Unlimited v2 value | Positive or absent | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestDetectMemoryLimitBytes::test_detect_memory_limit_ignores_unlimited_cgroup_v2 |
| 17 | Cached ceiling | Edge | Pinned RAM cache | Cap exists | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestRamTriangleCap::test_ram_triangle_cap_uses_cached_memory_limit |
| 18 | Format RAM costs | Edge | STL versus 3MF | 3MF cap is lower | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestRamTriangleCap::test_the_ram_cap_scales_per_format |
| 19 | Concurrent RAM share | Edge | One versus two jobs | Cap halves | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestRamTriangleCap::test_ram_cap_divides_budget_by_max_render_jobs |
| 20 | Disabled RAM cap | Edge | Zero fraction | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestRamTriangleCap::test_ram_cap_disabled_when_fraction_zero |
| 21 | Minimum concurrency | Edge | Zero/negative jobs | One job | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestRenderJobsLimit::test_render_jobs_limit_floors_at_one |
| 22 | Unknown size proof | Edge | No triangle/byte proof | Refuses loading | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestExceedsCap::test_unknown_estimate_without_a_size_proof_uses_the_bounded_path |
| 23 | Proven byte budget | Edge | Unknown triangles | Admits loading | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestExceedsCap::test_unknown_estimate_is_safe_when_the_byte_budget_is_proven |
| 24 | Native budget ceiling | Edge | Large detected RAM | At most 2 GiB | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestNativeMemoryBudget::test_is_capped_at_two_gibibytes |
| 25 | Native budget default | Edge | RAM undetectable | 1 GiB | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestNativeMemoryBudget::test_an_undetectable_budget_falls_back_to_one_gibibyte |
| 26 | Trailing STL bytes | Edge | Extra bytes | No underestimate | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_binary_stl_with_trailing_bytes_is_not_underestimated |
| 27 | Binary solid header | Edge | Header says solid | No ASCII misread | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_binary_stl_header_starting_with_solid_is_not_misread_as_ascii |
| 28 | 3MF missing model | Edge | Other ZIP payload | Total bytes / 70 | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_3mf_without_model_part_falls_back_to_total_uncompressed_size |
| 29 | OBJ polygon estimate | Edge | N-gon faces | Conservative 20 | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_obj_ngon_faces_count_conservatively |
| 30 | Cancelled waiter | Error | Cancellation raised | No admission leak | Unit | ✅ unit/modules/media/mesh_policy/test_admission.py::TestRenderAdmission::test_cancelled_waiter_releases_without_admission |
| 31 | RAM detection failure | Error | Unreadable sources | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestDetectMemoryLimitBytes::test_detect_memory_limit_survives_unreadable_sources |
| 32 | Missing RAM ceiling | Error | Detection fails | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestRamTriangleCap::test_ram_triangle_cap_none_when_detection_fails |
| 33 | Invalid concurrency | Error | Nonnumeric config | One job | Unit | ✅ unit/modules/media/mesh_policy/test_budgets.py::TestRenderJobsLimit::test_render_jobs_limit_falls_back_to_one_on_bad_config |
| 34 | PLY missing faces | Error | No face element | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_ply_without_face_element_returns_none |
| 35 | PLY missing terminator | Error | No end_header | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_ply_header_without_end_header_returns_none |
| 36 | Invalid PLY count | Error | Nonnumeric faces | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_ply_face_count_non_integer_returns_none |
| 37 | OBJ without faces | Error | No face directives | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_obj_without_faces_returns_none |
| 38 | Unsupported estimate | Error | Unknown suffix | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_estimator_returns_none_for_unrecognised_suffix |
| 39 | Corrupt ZIP estimate | Error | Invalid 3MF ZIP | Returns None | Unit | ✅ unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_estimator_returns_none_for_corrupt_3mf |
| 40 | STL pass-through | Happy | Raw STL source | Identical bytes | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_passes_through_raw_stl |
| 41 | Scene STL export | Happy | Placed scene | Valid STL bytes | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_bakes_the_scene_transforms_into_the_geometry |
| 42 | Instanced export | Happy | 3MF within budget | 120 exported faces | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_converts_an_instanced_3mf_inside_the_budget |
| 43 | Non-STL export | Happy | OBJ source | STL bytes | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_converts_non_stl_mesh |
| 44 | STL loading | Happy | Real STL | Nonempty mesh | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_returns_trimesh_for_real_stl |
| 45 | STEP loading | Happy | Real STEP fixture | Mesh and PNG | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_renders_real_step_fixture |
| 46 | Scene flattening | Happy | Two geometries | 24 mesh faces | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_flattens_scene_with_multiple_geometries |
| 47 | Single scene part | Happy | One geometry | 12 mesh faces | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_scene_with_single_geometry_returns_it_directly |
| 48 | Typed loading | Happy | Known source | Unprocessed mesh | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_uses_typed_loader_without_processing |
| 49 | Export admission | Edge | Over-cap source | No STL bytes | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_refuses_over_cap_mesh |
| 50 | Placement export cap | Edge | Expanded 3MF over cap | No unbounded load | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_refuses_a_3mf_whose_placements_exceed_the_budget |
| 51 | Empty scene | Edge | No mesh geometry | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_scene_with_no_trimesh_geometry_returns_none |
| 52 | Unreadable 3MF export | Error | Cannot open archive | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_fails_closed_on_a_3mf_it_cannot_open |
| 53 | STL read failure | Error | Read raises | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_read_failure_returns_none |
| 54 | Unloadable export | Error | Load returns None | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_returns_none_when_mesh_fails_to_load |
| 55 | Export failure | Error | Export raises | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_returns_none_on_export_failure |
| 56 | Unknown format | Error | Unsupported suffix | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_returns_none_for_unrecognised_extension |
| 57 | Flattening failure | Error | Scene dump raises | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_declines_a_scene_it_cannot_flatten |
| 58 | Concatenation failure | Error | Join raises | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_declines_a_scene_whose_parts_will_not_concatenate |
| 59 | Non-mesh join | Error | OBJ/STEP variants | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_declines_a_non_mesh_concatenation_result |
| 60 | Unsupported geometry | Error | Point cloud result | Returns None | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_returns_none_for_unsupported_loaded_type |
| 61 | STEP RAM limit | Error | Child exceeds RSS | Child killed; None | Unit | ✅ unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadStepMeshIsolated::test_step_tessellation_is_killed_when_child_exceeds_rss_budget |
| 62 | Source precision | Edge | Small/large edges | Dimensions preserved | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_preserves_measurement_precision |
| 63 | Tiny volume | Edge | Small closed source | 1e-9 mm³ measured | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_small_measured_volume_evidence |
| 64 | Missing geometry | Edge | No mesh | Geometry unavailable | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_missing_geometry_volume_evidence |
| 65 | Signed batch sums | Edge | Mixed components | 44 mm³; 256 facets | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestVolumeIntegralBatching::test_preserves_signed_integrals_across_facet_batch_boundaries |
| 66 | Bounded measurement | Edge | Copies forbidden | 44 mm³ measured | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestVolumeIntegralBatching::test_measures_closed_source_without_whole_mesh_geometry_copies |
| 67 | Nonfinite volume | Error | NaN/Inf integral | No volume scalar | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_refuses_nonfinite_volume |
| 68 | Volume-only failure | Error | Open mesh | Dimensions survive | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_geometry_from_mesh_handles_non_watertight_volume_error |
| 69 | Winding evidence | Error | Mixed winding | Inconsistent winding | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_inconsistent_winding_volume_evidence |
| 70 | Open-surface evidence | Error | Open source | Not watertight | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_open_surface_volume_evidence |
| 71 | Negative integral | Error | Reversed orientation | Nonpositive integral | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_negative_integral_volume_evidence |
| 72 | Nonfinite evidence | Error | NaN/Inf integral | Nonfinite integral | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_nonfinite_integral_volume_evidence |
| 73 | Zero integral | Error | Zero signed sum | Nonpositive integral | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_zero_integral_volume_evidence |
| 74 | Independent measures | Error | Kernel raises | Dimensions survive | Unit | ✅ unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_unexpected_volume_failure_retains_independent_measurements |
| 75 | Canonical preview | Happy | Competing PNGs | Canonical selected | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_prefers_the_canonical_thumbnail_over_a_larger_plate_image |
| 76 | Known preview paths | Happy | Three known folders | PNG extracted | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_accepts_known_thumbnail_dirs |
| 77 | Preview dimensions | Happy | Configured size | 320 × 240 image | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_webp_conversion_honours_configured_model_preview_size |
| 78 | Declared image cap | Edge | Oversized ZIP member | No member read | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_rejects_thumbnail_declared_over_limit_without_reading_member |
| 79 | Missing PNG | Edge | Archive without PNG | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_returns_none_when_no_png_present |
| 80 | Compressed geometry | Edge | Stored valid preview | Preview retained | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_compressed_geometry_does_not_reject_a_stored_preview |
| 81 | Expanded archive cap | Edge | Over total budget | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_rejects_archive_over_the_expanded_size_budget |
| 82 | Compression ratio cap | Edge | Highly compressed PNG | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_rejects_thumbnail_over_the_compression_ratio_budget |
| 83 | No preview candidates | Edge | 3MF without preview | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_entry_points.py::TestExtractEmbedded3mfThumbnail::test_extract_embedded_3mf_thumbnail_no_candidates_returns_none |
| 84 | Invalid candidate | Error | Valid smaller PNG | Valid PNG survives | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_valid_embedded_preview_survives_larger_invalid_candidates |
| 85 | Unknown preview path | Error | PNG elsewhere | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_ignores_png_outside_thumbnail_dirs |
| 86 | Invalid stored image | Error | Oversized PNG | ValueError | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_invalid_thumbnail_is_never_returned_as_raw_storage_payload |
| 87 | Wrong archive suffix | Error | Non-3MF path | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_rejects_non_3mf_suffix |
| 88 | Corrupt preview ZIP | Error | Invalid archive | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_returns_none_for_corrupt_archive |
| 89 | Unsafe member path | Error | Unsafe ZIP names | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_rejects_unsafe_member_names |
| 90 | Ambiguous preview | Error | Generic alternatives | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestExtractEmbedded3mfThumbnail::test_rejects_ambiguous_generic_previews |
| 91 | Invalid PNG signature | Error | Wrong magic bytes | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestPngMagic::test_rejects_data_without_png_magic |
| 92 | Corrupt extraction | Error | Invalid 3MF ZIP | Returns None | Unit | ✅ unit/modules/media/mesh_previews/test_entry_points.py::TestExtractEmbedded3mfThumbnail::test_extract_embedded_3mf_thumbnail_survives_corrupt_archive |
| 93 | Full mesh preview | Happy | 500 admitted facets | PNG and face count | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_under_cap_mesh_renders_normally |
| 94 | Progress reporting | Happy | Admitted mesh | Three phase labels | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_analyze_mesh_reports_progress_labels |
| 95 | Embedded priority | Happy | Valid 3MF preview | Embedded image wins | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_valid_embedded_3mf_preview_precedes_mesh_render |
| 96 | Loaded reclamation | Happy | Loaded mesh | Reclaims once | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_loaded_mesh_triggers_memory_reclaim |
| 97 | Real mesh render | Happy | Actual STL | Valid PNG | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestRenderThumbnail::test_render_thumbnail_real_mesh_renders_png |
| 98 | Truncated fallback | Edge | ASCII scan cutoff | Partial scan/preview | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_reports_an_incomplete_fallback_thumbnail_as_incomplete |
| 99 | Load admission | Edge | Over-cap source | No geometry/image | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_over_cap_mesh_is_never_loaded |
| 100 | Streaming preview | Edge | 1,001 STL facets | Complete scan/preview | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_over_cap_valid_stl_uses_streaming_thumbnail_fallback |
| 101 | Over-cap preview | Edge | 3MF with PNG | Image; no geometry | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_over_cap_3mf_still_gets_embedded_preview |
| 102 | Enabled large preview | Edge | Large 3MF; flag on | Embedded image | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_large_3mf_uses_embedded_preview_when_flag_on |
| 103 | Disabled large preview | Edge | Large 3MF; flag off | No geometry/image | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_large_3mf_skips_embedded_preview_when_flag_off |
| 104 | Byte admission | Edge | Oversized source | No geometry/image | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_oversize_file_is_never_loaded |
| 105 | Oversized 3MF preview | Edge | Large ZIP with PNG | Image; no geometry | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_oversize_3mf_still_gets_embedded_preview |
| 106 | Disabled byte cap | Edge | Zero byte limit | 42k facets; PNG | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_size_guard_disabled_when_zero |
| 107 | Post-load ceiling | Edge | Estimate misses cap | Geometry; no image | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_post_load_backstop_skips_render_when_estimate_missed |
| 108 | Skipped reclamation | Edge | Loading refused | No reclamation | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_skipped_mesh_does_not_reclaim |
| 109 | Repair fallback | Edge | Over-cap 3MF | Embedded image | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestRenderThumbnail::test_render_thumbnail_falls_back_to_the_embedded_image_over_the_cap |
| 110 | Disabled repair fallback | Edge | Over cap; flag off | Returns None | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestRenderThumbnail::test_render_thumbnail_over_cap_with_embedded_fallback_disabled_returns_none |
| 111 | Concurrent rendering | Edge | Eight jobs; limit two | Peak at most two | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestRenderSemaphore::test_render_semaphore_caps_concurrent_renders |
| 112 | Unloaded measurements | Error | Truncated source | No dimensions/count | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_measures_no_geometry_from_a_file_it_could_not_load |
| 113 | Render failure | Error | 3MF render fails | Embedded image | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestRenderThumbnail::test_render_thumbnail_falls_back_to_embedded_when_render_fails |
| 114 | No usable image | Error | Render/preview absent | Returns None | Unit | ✅ unit/modules/media/thumbnail_engine/test_processing.py::TestRenderThumbnail::test_render_thumbnail_is_none_when_nothing_can_be_rendered |
| 115 | Facade geometry | Happy | Loaded STL | Geometry; reclaimed | Unit | ✅ unit/modules/media/mesh_processing/test_entry_points.py::TestExtractGeometry::test_extract_geometry_loads_the_mesh_exactly_once |
| 116 | Facade static cap | Edge | Over-cap STL | No face count | Unit | ✅ unit/modules/media/mesh_processing/test_entry_points.py::TestExtractGeometry::test_extract_geometry_respects_cap |
| 117 | Facade PLY cap | Edge | Over-cap PLY | No face count | Unit | ✅ unit/modules/media/mesh_processing/test_entry_points.py::TestExtractGeometry::test_over_cap_ply_skips_load |
| 118 | Facade RAM cap | Edge | 700k facets; 2 GiB | No loading | Unit | ✅ unit/modules/media/mesh_processing/test_entry_points.py::TestExtractGeometry::test_ram_cap_skips_mesh_a_big_host_would_render |
| 119 | Facade static ceiling | Edge | Huge host RAM | No loading | Unit | ✅ unit/modules/media/mesh_processing/test_entry_points.py::TestExtractGeometry::test_static_cap_still_applies_on_a_huge_ram_host |
| 120 | Facade placement cap | Edge | Expanded 3MF over cap | No unbounded load | Unit | ✅ unit/modules/media/mesh_processing/test_entry_points.py::TestExtractGeometry::test_extract_geometry_refuses_a_3mf_whose_placements_exceed_the_budget |
| 121 | Dense real render | Happy | Admitted real mesh | Geometry and PNG | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_real_dense_mesh_renders |
| 122 | Buffer release | Happy | Real mesh analysis | No retained mesh | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_analysis_retains_no_mesh_afterwards |
| 123 | Real triangle cap | Edge | Dense STL over cap | Streaming PNG | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_real_over_triangle_mesh_uses_streaming_fallback |
| 124 | Real byte cap | Edge | Real oversized STL | Streaming PNG | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_real_oversize_file_uses_streaming_fallback |
| 125 | Instanced cap preview | Edge | Expanded 3MF above render budget + PNG | Embedded; exact measures retained | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_instanced_3mf_preserves_measurements_with_embedded_preview |
| 126 | Instanced cap refusal | Edge | Expanded 3MF above render budget; no PNG | Preview resource limit; exact measures retained | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_instanced_3mf_preserves_measurements_when_render_is_refused |
| 127 | Corpus RAM budget | Edge | Real mesh corpus | Within RSS budget | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_real_corpus_processes_within_memory_budget |
| 128 | Compressed mesh bomb | Error | Tiny ZIP; huge XML | Preview; no load | Integration | ✅ integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_real_compression_bomb_3mf_is_not_decompressed |
| 129 | Safe entry points | Happy | Isolation/contracts | No violations | Unit | ✅ repo/test_mesh_boundaries.py::TestMeshBoundaries::test_allows_safe_consumers |
| 130 | Canonical contracts | Happy | Internal consumers | Data-owner imports | Unit | ✅ repo/test_mesh_boundaries.py::TestPrimitiveDependencies::test_internal_consumers_import_contracts_from_the_data_owner |
| 131 | Data-owner acceptance | Happy | Contract imports | No violations | Unit | ✅ repo/test_mesh_boundaries.py::TestPrimitiveDependencies::test_accepts_data_contract_imports |
| 132 | Passive contracts | Happy | Fresh process import | No execution owners | Unit | ✅ repo/test_mesh_boundaries.py::TestPrimitiveDependencies::test_contract_import_does_not_load_execution_owners |
| 133 | Isolation enforcement | Error | Production imports | No bypasses | Unit | ✅ repo/test_mesh_boundaries.py::TestMeshBoundaries::test_new_consumers_cannot_bypass_mesh_isolation |
| 134 | Bypass detection | Error | Unsafe import forms | Violations detected | Unit | ✅ repo/test_mesh_boundaries.py::TestMeshBoundaries::test_rejects_mesh_bypasses |
| 135 | Reverse dependencies | Error | Primitive imports | No orchestrators | Unit | ✅ repo/test_mesh_boundaries.py::TestPrimitiveDependencies::test_primitive_owners_do_not_import_orchestrators |
| 136 | Orchestrator detection | Error | Direct/relative imports | Violations detected | Unit | ✅ repo/test_mesh_boundaries.py::TestPrimitiveDependencies::test_rejects_orchestrator_imports |
| 137 | Facade inventory | Error | All static consumers | Fixed test only | Unit | ✅ repo/test_mesh_boundaries.py::TestMeshFacadeInventory::test_only_fixed_legacy_consumers_import_the_facade |
| 138 | Facade responsibility | Error | Facade AST | No policy/native code | Unit | ✅ repo/test_mesh_boundaries.py::TestMeshFacadeInventory::test_facade_holds_no_mutable_policy_or_native_implementation |
| 139 | test_rejects_a_state_outside_the_enum | Error | Strings, unknown/null/bool/container states | Typed state rejection | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_a_state_outside_the_enum` |
| 140 | test_accepts_valid_terminal_outcome | Happy | READY/partial sampled/failed/unsupported coherent records and causes | Exact terminal state, records and closed cause retained | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_accepts_valid_terminal_outcome` |
| 141 | test_rejects_failure_cause_outside_the_enum | Error | Unknown/known raw strings, bool/int/containers | TypeError rejects non-enum cause | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_failure_cause_outside_the_enum` |
| 142 | test_rejects_success_without_records | Error | READY or PARTIAL with no records | ValueError refuses successful empty result | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_success_without_records` |
| 143 | test_rejects_records_on_failure | Error | FAILED/UNSUPPORTED with successful record | ValueError rejects contradictory terminal result | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_records_on_failure` |
| 144 | test_rejects_failure_without_cause | Error | FAILED/UNSUPPORTED lacks cause | ValueError requires explicit failure cause | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_failure_without_cause` |
| 145 | test_rejects_ready_with_failure_cause | Error | READY record plus refusal cause | ValueError rejects cause on successful result | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_ready_with_failure_cause` |
| 146 | test_rejects_partial_without_sampling_cause | Error | Partial sample cause absent or invalid_source | ValueError requires sampled source reason | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_partial_without_sampling_cause` |
| 147 | test_rejects_partial_with_complete_recipe | Error | Sample claims complete geometry | ValueError rejects complete topology from partial source | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_partial_with_complete_recipe` |
| 148 | test_rejects_duplicate_component_indices | Error | Duplicate component index after whole record | ValueError rejects ambiguous component identity | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintResult::test_rejects_duplicate_component_indices` |
| 149 | test_rejects_invalid_record_identity | Error | Negative/bool index; zero/negative/bool count | ValueError rejects invalid numeric identity | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintRecord::test_rejects_invalid_record_identity` |
| 150 | test_rejects_empty_descriptor_values | Error | Empty descriptor dictionary | ValueError refuses record without evidence | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintRecord::test_rejects_empty_descriptor_values` |
| 151 | test_rejects_component_instance_count_mismatch | Error | Component count2 but one placement | ValueError refuses inconsistent placement count | Unit | ✅ `unit/modules/media/test_fingerprints.py::TestFingerprintRecord::test_rejects_component_instance_count_mismatch` |
| 152 | test_preserves_complete_resource_identity | Happy | Actual Trimesh box prepared once | Same mesh, CompleteGeometry and sole resource alias | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestPreparedMesh::test_preserves_complete_resource_identity` |
| 153 | test_retains_explicit_sampled_geometry | Edge | Actual sample with empty scene | SampledGeometry identity, derived prepared completeness and exact reason | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestPreparedMesh::test_retains_explicit_sampled_geometry` |
| 154 | test_rejects_sample_with_resource_claims | Error | Sampled representation claims complete resource scene | ValueError rejects contradictory sampled scene | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestPreparedMesh::test_rejects_sample_with_resource_claims` |
| 155 | test_rejects_complete_geometry_without_resources | Error | Complete representation empty scene | ValueError rejects missing complete resource facts | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestPreparedMesh::test_rejects_complete_geometry_without_resources` |
| 156 | test_rejects_unknown_whole_resource_identity | Error | Whole alias absent from complete resources | ValueError rejects invalid whole-resource reuse | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestPreparedMesh::test_rejects_unknown_whole_resource_identity` |
| 157 | test_preserves_source_arrays | Happy | Actual Trimesh vertices/faces | Exact source arrays unchanged by preparation | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestPreparedMesh::test_preserves_source_arrays` |
| 158 | test_rejects_non_sampling_reason | Error | Invalid source enum, raw string or null | Closed sample variant rejects incompatible cause | Unit | ✅ `unit/modules/media/test_mesh_facts.py::TestSampledGeometry::test_rejects_non_sampling_reason` |
| 159 | test_round_trips_independent_facts | Edge | Unread/no-output; document preview; full mesh; scanner-only; complete scan/partial preview; complete scan/sampled analysis; partial scan/sample | Coverage variants preserved exactly | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestMeshCoverage::test_round_trips_independent_facts` |
| 160 | test_rejects_full_geometry_without_complete_scan | Error | Full geometry plus unread/partial scan | ValueError instead of contradictory full geometry | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestMeshCoverage::test_rejects_full_geometry_without_complete_scan` |
| 161 | test_rejects_sampled_geometry_without_scan | Error | Sample representation with unread scan | ValueError | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestMeshCoverage::test_rejects_sampled_geometry_without_scan` |
| 162 | test_rejects_untyped_facts | Error | Raw string in source/geometry/preview field | TypeError | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestMeshCoverage::test_rejects_untyped_facts` |
| 163 | test_requires_each_fact | Error | Each required coverage wire field absent | ValueError; no fallback | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestDecodeCoverage::test_requires_each_fact` |
| 164 | test_rejects_unknown_enum_fact | Error | Unknown/empty/null/bool/number scan or preview | ValueError | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestDecodeCoverage::test_rejects_unknown_enum_fact` |
| 165 | test_rejects_unknown_geometry_fact | Error | Missing/unknown/extra geometry tag; sample cause absent | ValueError | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestDecodeCoverage::test_rejects_unknown_geometry_fact` |
| 166 | test_rejects_non_sampling_cause | Error | Sample tagged invalid_source | ValueError | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestDecodeCoverage::test_rejects_non_sampling_cause` |
| 167 | test_complete_scan_survives_partial_preview_transport | Edge | Complete/full geometry with partial image | Scan/geometry/preview retained independently | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestCoverageReply::test_complete_scan_survives_partial_preview_transport` |
| 168 | test_sampled_geometry_survives_complete_scan_transport | Edge | Complete scan with sampled geometry and no image | Coverage survives frame | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestCoverageReply::test_sampled_geometry_survives_complete_scan_transport` |
| 169 | test_rejects_invalid_native_coverage | Error | Null coverage; unknown scan/preview/geometry/sampling cause | WORKER_FAILED | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestCoverageReply::test_rejects_invalid_native_coverage` |
| 170 | test_rejects_missing_native_coverage | Error | Coverage key removed from native header | WORKER_FAILED | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestCoverageReply::test_rejects_missing_native_coverage` |
| 171 | test_rejects_unknown_fingerprint_cause | Error | Unknown/empty/bool/number native analysis cause | WORKER_FAILED | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestCoverageReply::test_rejects_unknown_fingerprint_cause` |
| 172 | test_preserves_known_worker_cause | Happy | ERR1 resource_limit | GeometryError with exact code | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestRaiseReportedError::test_preserves_known_worker_cause` |
| 173 | test_rejects_unrecognized_worker_cause | Error | ERR1 unknown/empty/non-ASCII | WORKER_FAILED | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestRaiseReportedError::test_rejects_unrecognized_worker_cause` |
| 174 | test_rejects_preview_fact_inconsistent_with_image | Error | Absent image/complete preview; image/no preview | ValueError | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativePreviewOwnership::test_rejects_preview_fact_inconsistent_with_image` |
| 175 | test_rejects_document_provenance_inconsistent_with_strategy | Error | Embedded/complete; full/document supplied | ValueError | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativePreviewOwnership::test_rejects_document_provenance_inconsistent_with_strategy` |
| 176 | test_rejects_untyped_coverage | Error | String coverage passed to result | TypeError | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativePreviewOwnership::test_rejects_untyped_coverage` |
| 177 | test_retains_known_native_failure_literal | Happy | GeometryError resource_limit | Exact ERR1 bytes | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestEncodeError::test_retains_known_native_failure_literal` |
| 178 | test_rejects_unrecognized_native_failure_literal | Error | Invented GeometryError identifier | ValueError | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestEncodeError::test_rejects_unrecognized_native_failure_literal` |
| 179 | test_bounded_fallback_cannot_certify_materialized_geometry | Edge | Fallback complete source scan; 4 sampled faces out of 12 | COMPLETE scan; NOT_LOADED representation; PARTIAL preview | Unit | ✅ `unit/modules/media/test_thumbnail_engine.py::TestPreviewCoverage::test_bounded_fallback_cannot_certify_materialized_geometry` |
| 180 | test_embedded_preview_does_not_certify_unread_source | Edge | Real 3MF embedded image; no geometry requested | Unread source; NOT_LOADED geometry; DOCUMENT_SUPPLIED preview | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestCoverage::test_embedded_preview_does_not_certify_unread_source` |
| 181 | test_complete_source_scan_survives_partial_preview | Edge | Real full cube load; forced renderer/streamer no output; bounded real fallback | COMPLETE scan/full representation; PARTIAL preview; count12 | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestCoverage::test_complete_source_scan_survives_partial_preview` |
| 182 | test_complete_source_scan_survives_sampled_analysis | Edge | Real 320-face sphere; render cap1; metadata plus analysis, no preview | COMPLETE scanner result; sampled analysis; no image; count320 | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestCoverage::test_complete_source_scan_survives_sampled_analysis` |
| 183 | test_analysis_only_preserves_complete_source_scan | Edge | Same sphere, analysis only; sampler reaches source boundary | COMPLETE scan even without metadata; sampled analysis; empty measurements | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestCoverage::test_analysis_only_preserves_complete_source_scan` |
| 184 | test_measures_stl_without_thumbnail | Happy | Binary/ASCII over cap | Scanner COMPLETE, materialization NOT_LOADED, preview NOT_PRODUCED; exact measures | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestMetadataOnly::test_measures_stl_without_thumbnail` |
| 185 | test_analysis_budget_preserves_measurements_without_render | Edge | Retained 3MF scene plus refused optional analysis | COMPLETE source, geometry not materialized; no image; measurements preserved | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestThumbnailEngine::test_analysis_budget_preserves_measurements_without_render` |
| 186 | test_refusal_survives_a_successful_preview_reply | Edge | Geometry refusal plus useful preview | Refusal survives transport separately from preview | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestGeometryOutcome::test_refusal_survives_a_successful_preview_reply` |
| 187 | test_round_trips_each_fingerprint_state_as_a_wire_string | Happy | READY/PARTIAL with records; FAILED/UNSUPPORTED with closed cause | Literal terminal state and typed decode retained | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestReplyFrame::test_round_trips_each_fingerprint_state_as_a_wire_string` |
| 188 | test_rejects_successful_analysis_with_wrong_representation | Error | READY with unloaded/sample geometry; PARTIAL with full/unloaded geometry | Constructor rejects impossible successful analysis | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeFingerprintOwnership::test_rejects_successful_analysis_with_wrong_representation` |
| 189 | test_rejects_partial_cause_disagreeing_with_sample | Error | Valid PARTIAL record; sample reason differs from analysis reason | Constructor ValueError | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeFingerprintOwnership::test_rejects_partial_cause_disagreeing_with_sample` |
| 190 | test_rejects_forged_successful_analysis_representation | Error | Native frame contains valid analysis records with wrong representation | WORKER_FAILED | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeFingerprintOwnership::test_rejects_forged_successful_analysis_representation` |
| 191 | test_rejects_forged_partial_cause_disagreeing_with_sample | Error | Valid sampled native frame; analysis cause tampered | WORKER_FAILED | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestNativeFingerprintOwnership::test_rejects_forged_partial_cause_disagreeing_with_sample` |
| 192 | test_matches_the_in_process_engine | Happy | Real cube through native worker and direct engine | Image, geometry, strategy, explicit coverage, failure and fingerprint parity | Integration | ✅ `integration/modules/media/test_mesh_isolation.py::TestGenerate::test_matches_the_in_process_engine` |
| 193 | Publishes preview completeness independently of source scan | Edge | Complete source scan with complete, partial or document-supplied preview | Ready persisted thumbnail has the preview-specific complete flag and existing physical bytes | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_publishes_preview_completeness_independently_of_source_scan` |
| 194 | Rediscovers previews with ambiguous completeness | Edge | Ready mesh thumbnail at recipe7 with old fallback complete flag | Durable work source requests the current thumbnail recipe | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_rederives_previews_with_ambiguous_completeness` |
| 195 | Retains refusal stage statistics | Error | No image, no produced preview, failed load phase | Benchmark retains measured refusal phase and reason | Unit | ✅ `unit/scripts/test_bench_thumbnails.py::TestBenchmarkFile::test_retains_refusal_stage_statistics` |
| 196 | Rejects missing refusal cause | Error | No image with no failure cause | Benchmark classifies unexpected failure with explicit reason | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_rejects_missing_refusal_cause` |
| 197 | Retries incomplete analysis only when requested | Edge | Failed, genuinely sampled partial or unsupported result | Lease retry policy preserves terminal states and only explicit retry claims them | Integration | ✅ `integration/modules/similarity/test_fingerprints.py::TestFingerprintLeases::test_retries_incomplete_analysis_only_when_requested` |
| 198 | Failed extraction retains the original Artifact | Error | Typed invalid geometry extraction | Failure cause persisted while Artifact bytes and identity remain intact | Integration | ✅ `integration/modules/similarity/test_ingestion.py::TestIngestDerivative::test_persists_failed_derivative_without_starting_run` |
| 199 | Missing optional providers preserve manual library work | Edge | Similarity or inference package unavailable | Routes, annotations and post-ingestion extension remain optional | Integration | ✅ `integration/bootstrap/test_optional_features.py::TestOptionalFeatures::test_absent_feature_preserves_manual_library_work` |
| 200 | Isolated STL conversion matches direct conversion | Happy | OBJ box through real disposable worker and direct owner | Identical STL bytes | Integration | ✅ `integration/modules/media/test_stl_isolation.py::TestToStlBytes::test_matches_the_in_process_conversion` |
| 201 | Preserves native hull descriptor | Happy | Actual isolated fingerprint job | Valid native fingerprint evidence survives reply | Integration | ✅ `integration/modules/media/test_mesh_isolation.py::TestGeometryMeasurements::test_preserves_native_hull_descriptor` |
| 202 | Preserves transformed component measurements | Happy | Placed 3MF components | Physical resource and whole-object fingerprint values match placement | Integration | ✅ `integration/modules/media/test_fingerprints.py::TestFingerprintExtraction::test_preserves_transformed_component_measurements` |
| 203 | Reuses loaded geometry for fingerprint descriptors | Happy | Real source parsed once | Whole/component fingerprint values survive without an extra source load | Integration | ✅ `integration/modules/media/test_fingerprints.py::TestFingerprintExtraction::test_reuses_loaded_mesh_for_descriptors` |
| 204 | Persists each outcome as a text state | Edge | Valid READY, PARTIAL, FAILED or UNSUPPORTED result with its required records or cause | Returned state and persisted state are plain strings matching the result state | Integration | ✅ `integration/modules/similarity/test_fingerprints.py::TestFingerprintPublication::test_persists_each_outcome_as_a_text_state` |
| 205 | test_cancelled_mesh_metadata_preserves_published_volume | Error | Published6e-9 measured volume; cancellation before stale reply | No publication; published evidence unchanged; derivative CANCELLED | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestAttemptPublication::test_cancelled_mesh_metadata_preserves_published_volume` |
| 206 | test_superseded_mesh_metadata_preserves_replacement_volume | Error | New successful attempt stores unavailable NOT_WATERTIGHT or measured1e-9 volume; old reply arrives | Replacement evidence unchanged; current derivative READY | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestAttemptPublication::test_superseded_mesh_metadata_preserves_replacement_volume` |
| 207 | test_replaced_mesh_source_preserves_published_volume | Error | Source sha changes before stale measurement reply | No publication; existing measured volume unchanged | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestAttemptPublication::test_replaced_mesh_source_preserves_published_volume` |
| 208 | test_releases_analysis_buffers_before_streaming[True] | Edge | Sampled fingerprint followed by real isolated streamer | All PreparedMesh/Trimesh/vertex/face weakrefs dead before streamer; one reclaim; useful image and PARTIAL fingerprint preserved | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestThumbnailEngine::test_releases_analysis_buffers_before_streaming[True]` |
