# Shared bounded STL reader

Measurements, full mesh loading, retained fallback samples and the two-pass
streaming preview use `stl_reader.iter_stl_blocks`. Consumers choose what to
retain or render; they share the interpretation of source facets, limits and
source identity. No successful completion is reported from a partially validated
source.

## Source validation

An exact binary STL has an 84-byte header followed by its declared 50-byte facet
records. A binary header can begin with `solid`; record length still identifies
it. Trailing or truncated records are refused. Binary stored normals are ignored
because geometry normals are derived from vertices. Nonfinite vertex coordinates
are refused.

ASCII vertices remain float64. Blank lines and comments are accepted; a complete
facet boundary at EOF is accepted without `endsolid`. Incomplete facets,
nonfinite or overflowing coordinates, malformed normal tokens, and content after
`endsolid` are refused. Byte, facet, line, line-length and deadline budgets apply
to the entire validated source, including facets outside a retained sample.

Every read is pinned by device, inode, size, modification time and change time.
Opening, EOF, later passes and final result publication verify that identity.
Replacing a source is refused even if its size and modification time are
restored. A preview worker that detects a change after rendering writes no
successful completion manifest; the parent accepts output only from a successful
worker with a valid manifest.

## Separate consumers and coverage

| Consumer | Retained representation | Completion contract |
| --- | --- | --- |
| Measurements | Exact source bounds and facet count | Every source facet validated to stable EOF; topology remains unassessed |
| Full mesh loading | Admitted float64 facet arrays | Binary materializes after its header probe; ASCII scans for allocation size then materializes under the same snapshot and deadline |
| Fallback sample | Deterministic bounded subset | `STLSample.source_complete` certifies source validation; `complete` certifies every source facet was retained |
| Sampled thumbnail | Bounded facets and image buffers | Source coverage is independent of sample/raster coverage; `STLThumbnailResult.complete` additionally requires full retention and remaining raster budget |
| Streaming preview | Bounded blocks and image buffers | Full bounds pass precedes rasterization of all facets; candidate budgets and the source snapshot must complete before publication |

The strict `read_stl_sample` API preserves invalid-source, resource-limit and
source-changed refusals. Legacy sample/preview adapters keep their established
`None` refusal result. Neither a complete read nor a complete preview certifies
watertight topology or calculable physical volume. Thumbnail outcomes translate
these facts into explicit source, representation and preview coverage.

## Sampling and framing

Retained facet indices use fixed vectorized priorities, preserve source order,
and include the first and last facet when the cap permits. The subset is
independent of reader block size and binary/ASCII encoding. Validation still
reads the full bounded source rather than seeking selected binary records.
Sampling keeps at most its retention budget plus one reader block as facet
candidates; it does not allocate a complete mesh.

Camera bounds use every validated source facet, including a small remote
component, rather than sampled percentiles. Translation is removed in float64
before visual projection. Streaming converts bounded screen coordinates and
scaled depth to float32. Facet degeneracy checks remain invariant under rigid
transforms. Existing camera, material, transparency and raster-candidate controls
remain in force: holes stay transparent and a sampled or raster-limited preview
is reported as partial. Temporary analysis geometry is released before fallback
rendering; image memory remains bounded by output dimensions.

## Refresh and cost

Shared STL validation introduced mesh metadata recipe11, thumbnail recipe10
and geometry interpretation `geometry-v6-sh5f4577c4`. Subsequent retained-scene
eligibility changes and [current recipe identities](derivatives.md#retained-3mf-measurements-and-previews)
are recorded with the derivative contracts. Viewer STL remains recipe2. Mathematical descriptors, the SH basis and
verifier
calibration are unchanged; historical evidence and user review decisions remain
retained. [3MF capabilities](3mf-capabilities.md) and independent embedded-preview
publication continue to apply to 3MF sources.

Complete source validation can read more bytes than selective record seeking.
This is a correctness change with bounded work, not a claim of improved runtime.
Pass reuse and retained scene consumers are separate implementation decisions.

## Behaviour coverage

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | test_accepts_a_coordinate_a_float32_can_hold | Edge | zero/positive/negative/float32 max | valid_stl_value True | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestValidValue::test_accepts_a_coordinate_a_float32_can_hold` |
| 2 | test_rejects_a_coordinate_a_float32_cannot_hold | Error | NaN/infinity/above float32 range | valid_stl_value False | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestValidValue::test_rejects_a_coordinate_a_float32_cannot_hold` |
| 3 | test_recognises_a_binary_stl_by_its_exact_length | Happy | exact header plus declared records | Exact count and source byte size | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestSourceIsBinary::test_recognises_a_binary_stl_by_its_exact_length` |
| 4 | test_rejects_a_file_too_short_to_hold_a_header | Error | short binary header | No binary-header eligibility | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestSourceIsBinary::test_rejects_a_file_too_short_to_hold_a_header` |
| 5 | test_rejects_a_declared_count_the_file_length_contradicts | Error | header count/byte length disagree | No binary-header eligibility | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestSourceIsBinary::test_rejects_a_declared_count_the_file_length_contradicts` |
| 6 | test_rejects_a_declared_count_of_zero | Error | zero-facet binary declaration | No binary-header eligibility | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestSourceIsBinary::test_rejects_a_declared_count_of_zero` |
| 7 | test_treats_ascii_as_not_binary | Edge | ASCII source | No binary-header eligibility | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestSourceIsBinary::test_treats_ascii_as_not_binary` |
| 8 | test_reports_a_file_that_is_not_there | Edge | missing file | No binary-header eligibility | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestSourceIsBinary::test_reports_a_file_that_is_not_there` |
| 9 | test_reads_every_triangle | Happy | binary two facets | Exact triangle count2 | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_reads_every_triangle` |
| 10 | test_reports_the_bounding_box_it_saw | Happy | two facets with known extrema | Bounds min(0,0,0),max(1,1,1) | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_reports_the_bounding_box_it_saw` |
| 11 | test_hands_each_chunk_to_the_caller | Edge | 20 facets, chunk cap8 | Block counts[8,8,4] | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_hands_each_chunk_to_the_caller` |
| 12 | test_refuses_a_file_that_is_not_exactly_a_binary_stl | Error | short source forced binary | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_refuses_a_file_that_is_not_exactly_a_binary_stl` |
| 13 | test_refuses_more_triangles_than_the_budget_allows | Error | count exceeds max_triangles | STLBudgetExceeded | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_refuses_more_triangles_than_the_budget_allows` |
| 14 | test_refuses_a_source_larger_than_the_budget_allows | Error | source exceeds max_source_bytes | STLBudgetExceeded | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_refuses_a_source_larger_than_the_budget_allows` |
| 15 | test_refuses_a_coordinate_that_is_not_finite | Error | infinite vertex | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_refuses_a_coordinate_that_is_not_finite` |
| 16 | test_refuses_to_read_past_the_deadline | Error | expired deadline | STLBudgetExceeded | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadBinary::test_refuses_to_read_past_the_deadline` |
| 17 | test_reads_a_number | Happy | valid numeric token | Exact parsed value | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestParseFloat::test_reads_a_number` |
| 18 | test_refuses_something_that_is_not_a_number | Error | nonnumeric token | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestParseFloat::test_refuses_something_that_is_not_a_number` |
| 19 | test_refuses_a_number_a_float32_cannot_hold | Error | numeric token beyond range | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestParseFloat::test_refuses_a_number_a_float32_cannot_hold` |
| 20 | test_reads_repeated_factory_facets | Happy | ASCII factory one/two complete facets | Exact count and source bounds | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_reads_repeated_factory_facets` |
| 21 | test_reads_every_facet | Happy | ASCII two facets | Exact triangle count2 | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_reads_every_facet` |
| 22 | test_reports_the_bounding_box_it_saw | Happy | two facets with known extrema | Bounds min(0,0,0),max(1,1,1) | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_reports_the_bounding_box_it_saw` |
| 23 | test_ignores_lines_that_carry_no_geometry | Edge | blank/comment lines | One facet preserved | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_ignores_lines_that_carry_no_geometry` |
| 24 | test_accepts_a_file_that_ends_without_endsolid | Edge | EOF exactly at completed facet | One complete facet accepted | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_accepts_a_file_that_ends_without_endsolid` |
| 25 | test_refuses_a_file_that_ends_mid_facet | Error | truncated ASCII facet | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_a_file_that_ends_mid_facet` |
| 26 | test_refuses_a_file_with_no_facets_at_all | Error | empty solid | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_a_file_with_no_facets_at_all` |
| 27 | test_refuses_content_after_endsolid | Error | facet after endsolid | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_content_after_endsolid` |
| 28 | test_refuses_a_malformed_facet | Error | invalid normal/loop/vertex/endloop/endfacet grammar | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_a_malformed_facet` |
| 29 | test_refuses_an_unknown_keyword | Error | unknown ASCII token | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_an_unknown_keyword` |
| 30 | test_refuses_a_line_longer_than_the_budget_allows | Error | ASCII line beyond max_line_bytes | Typed source refusal | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_a_line_longer_than_the_budget_allows` |
| 31 | test_refuses_more_lines_than_the_budget_allows | Error | line count exceeds max_lines | STLBudgetExceeded | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_more_lines_than_the_budget_allows` |
| 32 | test_refuses_more_bytes_than_the_budget_allows | Error | ASCII bytes exceed max_source_bytes | STLBudgetExceeded | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_more_bytes_than_the_budget_allows` |
| 33 | test_refuses_more_triangles_than_the_budget_allows | Error | count exceeds max_triangles | STLBudgetExceeded | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_more_triangles_than_the_budget_allows` |
| 34 | test_refuses_bytes_that_are_not_ascii | Error | non-ASCII source bytes | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_bytes_that_are_not_ascii` |
| 35 | test_refuses_a_coordinate_that_is_not_a_number | Error | nonnumeric vertex | InvalidSTL | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadAscii::test_refuses_a_coordinate_that_is_not_a_number` |
| 36 | test_preserves_ascii_precision | Edge | ASCII near1e9 with small extents | Exact finite float64 source bounds | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestScanSTL::test_preserves_ascii_precision` |
| 37 | test_accepts_ascii_at_budget | Edge | each ASCII budget exactly met | Exact count2 and total source bytes | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestScanSTL::test_accepts_ascii_at_budget` |
| 38 | test_rejects_ascii_above_budget | Error | each configured source budget exceeded | STLBudgetExceeded | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestScanSTL::test_rejects_ascii_above_budget` |
| 39 | test_rejects_ascii_line_above_budget | Error | line length exceeds ceiling | InvalidSTL with line-budget diagnosis | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestScanSTL::test_rejects_ascii_line_above_budget` |
| 40 | test_rejects_source_change_mid_scan | Error | source changed after initial chunk | STLSourceChanged | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestScanSTL::test_rejects_source_change_mid_scan` |
| 41 | test_scan_is_independent_of_chunk_size | Edge | same source, different chunk sizes | Equal complete measurements | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestScanSTL::test_scan_is_independent_of_chunk_size` |
| 42 | test_accepts_binary_header_starting_solid | Edge | exact binary header begins solid | Complete count preserved | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestScanSTL::test_accepts_binary_header_starting_solid` |
| 43 | test_rejects_invalid_integer_budget | Error | zero/negative/bool/float in each integer limit | ValueError positive-integer contract | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadLimits::test_rejects_invalid_integer_budget` |
| 44 | test_rejects_chunk_above_hard_cap | Error | chunk size8193 | ValueError hard cap | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadLimits::test_rejects_chunk_above_hard_cap` |
| 45 | test_rejects_nonfinite_deadline | Error | NaN/positive or negative infinity | ValueError finite deadline | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestReadLimits::test_rejects_nonfinite_deadline` |
| 46 | test_deadline_must_hold_when_final_block_is_consumed | Error | deadline expires after final yielded block | STLBudgetExceeded before successful EOF | Unit | ✅ `unit/modules/media/test_stl_reader.py::TestCompletionBudget::test_deadline_must_hold_when_final_block_is_consumed` |
| 47 | parses STL variants consistently | Happy | Binary, solid-prefixed binary and ASCII source | Measurement/sample/load/streaming consumers agree on exact count and bounds | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_parses_stl_variants_consistently` |
| 48 | ignores binary stored normals | Edge | Malformed/nonfinite stored normals with valid vertices | Geometry and previews remain determined by source vertices | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_binary_stored_normals_do_not_change_geometry_or_preview` |
| 49 | test_nonfinite_binary_positions_are_refused | Error | nonfinite vertex outside retained sample | Scanner/adapter refuse, optional sample/preview unavailable | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_nonfinite_binary_positions_are_refused` |
| 50 | test_ascii_stored_normals_remain_finite_syntax | Error | ASCII normal NaN/Inf | Scanner/adapter refuse, optional sample unavailable | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_ascii_stored_normals_remain_finite_syntax` |
| 51 | test_rejects_malformed_stl_before_materialization | Error | truncation/count mismatch/trailing/nonfinite/invalidASCII/nonASCII | Public loader GeometryError invalid_source | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_rejects_malformed_stl_before_materialization` |
| 52 | test_declines_malformed_stl_in_optional_samples | Error | same malformed source corpus | Scan InvalidSTL; optional sample produces no geometry | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_declines_malformed_stl_in_optional_samples` |
| 53 | certifies full source coverage for partial samples | Edge | Source exceeds retained cap | Complete source read is distinguished from partial representation | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_certifies_source_completion_for_partial_samples` |
| 54 | test_preserves_ascii_materialization_precision | Edge | coordinates1e9 plus1/16 grid | Exact float64 sampled and loaded triangles | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLConsumers::test_preserves_ascii_materialization_precision` |
| 55 | test_accepts_stl_ascii_at_limit | Edge | triangle/byte/line/line-length limits exact | Complete source bytes and triangle count preserved | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLBudgets::test_accepts_stl_ascii_at_limit` |
| 56 | test_rejects_stl_ascii_above_limit | Error | each ASCII limit exceeded | Typed scan budget refusal; optional sample unavailable | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLBudgets::test_rejects_stl_ascii_above_limit` |
| 57 | reads every binary byte for small samples | Edge | Small retained budget on larger binary source | Complete validated read covers unretained records | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLBudgets::test_reads_every_binary_source_byte_even_with_small_sample` |
| 58 | test_materializes_with_bounded_complete_read_passes | Happy | binary/ASCII source through materializer | Actual read passes match canonical bounded policy | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLBudgets::test_materializes_with_bounded_complete_read_passes` |
| 59 | keeps sample independent of block size | Edge | Same source/retention budget with different reader blocks | Same ordered retained facet subset | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLBudgets::test_keeps_sample_independent_of_chunk` |
| 60 | test_holds_source_snapshot_across_passes | Error | replacement preserves size and mtime | STLSourceChanged via inode identity | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLSourceSnapshots::test_holds_source_snapshot_across_passes` |
| 61 | refuses post-EOF source mutation | Error | Source replaced after reader EOF before consumer completion | Sampler rejects changed identity rather than certifying completion | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLCompletionSnapshots::test_refuses_change_after_validated_eof_before_completion` |
| 62 | test_preserves_scan_bytes_on_read_failure | Error | injected read failure after partial bytes across3consumers | OSError, actual positive source-read cost, unchanged original bytes | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSTLReadFailures::test_preserves_scan_bytes_on_read_failure` |
| 63 | preserves canonical refusal category | Error | Invalid source, budget excess or changed identity | Strict sampler retains the typed refusal | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestCanonicalSTLRefusals::test_retains_canonical_refusal_category` |
| 64 | test_preserves_measured_partial_fingerprint | Edge | Sphere1280facets retained100; versioned v6 fixture | Exact retained/D2 hashes and numerical descriptors preserved, scan COMPLETE/fingerprint PARTIAL | Integration | ✅ `integration/modules/media/test_stl_reader.py::TestSampleFingerprintRecipe::test_preserves_measured_partial_fingerprint` |
| 65 | test_retains_canonical_refusal_category | Error | invalid/budget/changed source across consumers | Exact closed InvalidSTL.reason or adapter GeometryError code | Integration | ✅ `integration/modules/media/mesh_loading/test_stl_outcomes.py::TestSTLLoading::test_retains_canonical_refusal_category` |
| 66 | test_reports_unavailable_source | Error | missing STL file | GeometryError source_unavailable | Integration | ✅ `integration/modules/media/mesh_loading/test_stl_outcomes.py::TestSTLLoading::test_reports_unavailable_source` |
| 67 | test_load_mesh_returns_none_for_unsupported_loaded_type | Edge | Generic OBJ loader fixture on current owner seam | Preserves PointCloud refusal or exact typed loader call/returned mesh | Unit | ✅ `unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_returns_none_for_unsupported_loaded_type` |
| 68 | test_load_mesh_uses_typed_loader_without_processing | Edge | Generic OBJ loader fixture on current owner seam | Preserves PointCloud refusal or exact typed loader call/returned mesh | Unit | ✅ `unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_uses_typed_loader_without_processing` |
| 69 | uses the canonical material colour | Happy | Canonical material override | Opaque pixels use the configured material colour | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_uses_the_canonical_material_colour` |
| 70 | stl fallback uniformly caps sample to 100k | Edge | 101 source facets; retention cap10 | Retains10 facets while reporting101 parsed facets and all source bytes | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_uniformly_caps_sample_to_100k` |
| 71 | stl fallback dense fixture has a coherent silhouette | Happy | Dense surface beyond retention cap | Bounded sample forms a coherent silhouette | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_dense_fixture_has_a_coherent_silhouette` |
| 72 | stl fallback microfacets keep connected surface coverage | Happy | Subpixel microfaceted surface | Preview retains connected surface coverage | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_microfacets_keep_connected_surface_coverage` |
| 73 | stl fallback work budget is observable | Edge | Bounded retention/raster instrumentation | Retained facets and raster work stay within their budgets | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_work_budget_is_observable` |
| 74 | stl fallback global candidate budget for large facets | Edge | Large projected facets consume candidate budget | Work stays bounded and preview is explicitly partial | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_global_candidate_budget_for_large_facets` |
| 75 | all retained facets are partial when raster budget is exhausted | Edge | Every facet retained; raster budget exhausted | Source is complete but preview remains partial | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_all_retained_facets_are_partial_when_raster_budget_is_exhausted` |
| 76 | keeps the hole open when rasterising an annulus | Happy | Annular geometry | Centre remains transparent | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_keeps_the_hole_open_when_rasterising_an_annulus` |
| 77 | stl fallback refuses nonfinite facets | Error | Nonfinite binary vertex coordinates | No preview is accepted | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_refuses_nonfinite_facets` |
| 78 | sampler validates more records than it retains | Edge | More source records than retention slots | Reports every parsed record and complete scanned-byte count | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_sampler_validates_more_records_than_it_retains` |
| 79 | binary helpers reject a file shorter than the header | Error | Binary file shorter than header | Sampler refuses input | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_binary_helpers_reject_a_file_shorter_than_the_header` |
| 80 | binary helpers reject a truncated facet record | Error | Truncated binary facet | Sampler refuses input | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_binary_helpers_reject_a_truncated_facet_record` |
| 81 | stl fallback binary helpers fail closed on io errors | Error | Binary source read fails | Preview/sample adapters refuse instead of publishing geometry | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_binary_helpers_fail_closed_on_io_errors` |
| 82 | binary sampler is nothing when a record is short | Error | Short binary record | No sample returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_binary_sampler_is_nothing_when_a_record_is_short` |
| 83 | binary sampler is nothing when the file cannot be opened | Error | Binary source cannot be opened | No sample returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_binary_sampler_is_nothing_when_the_file_cannot_be_opened` |
| 84 | stl fallback ascii helpers fail closed on io errors | Error | ASCII source read fails | Legacy adapters return refusal | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_ascii_helpers_fail_closed_on_io_errors` |
| 85 | ascii samples from a partly invalid source are refused | Error | Valid ASCII facets mixed with malformed content | Entire sample is refused | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_ascii_samples_from_a_partly_invalid_source_are_refused` |
| 86 | ascii samples are nothing when no facet parses | Error | ASCII source with no valid complete facet | No sample returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_ascii_samples_are_nothing_when_no_facet_parses` |
| 87 | sample budget must be positive | Error | Invalid retention budget | Invalid sample budget raises | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_sample_budget_must_be_positive` |
| 88 | refuses a frame size outside the supported range | Error | Frame dimensions outside supported bounds | No preview returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_refuses_a_frame_size_outside_the_supported_range` |
| 89 | refuses a sample whose coordinates are not finite | Error | Nonfinite sampled coordinates | No preview returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_refuses_a_sample_whose_coordinates_are_not_finite` |
| 90 | refuses a sample whose bounds are a single extreme point | Error | Bounds collapse at an extreme coordinate | No invalid visual frame returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_refuses_a_sample_whose_bounds_are_a_single_extreme_point` |
| 91 | refuses a sample whose bounds overflow float32 | Error | Bounds beyond supported coordinate range | No overflowing preview returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_refuses_a_sample_whose_bounds_overflow_float32` |
| 92 | refuses to render when view selection fails | Error | Camera selection fails | No preview returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_refuses_to_render_when_view_selection_fails` |
| 93 | stl fallback returns none when optional render dependencies are missing | Error | Optional rendering dependencies unavailable | No preview returned | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_stl_fallback_returns_none_when_optional_render_dependencies_are_missing` |
| 94 | incomplete annular sample still preserves hole | Edge | Incomplete retained annular sample | Hole remains open | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_incomplete_annular_sample_still_preserves_hole` |
| 95 | renders a dense microfaceted annulus as one connected ring | Happy | Dense microfaceted annulus | Preview remains a connected ring | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_renders_a_dense_microfaceted_annulus_as_one_connected_ring` |
| 96 | refuses an oversized ascii line | Error | ASCII line exceeds limit | Source refused | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_refuses_an_oversized_ascii_line` |
| 97 | caps retained ascii facets without stopping validation | Edge | ASCII source larger than retained subset | Retains cap while validating/reporting every source facet | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_caps_retained_ascii_facets_without_stopping_validation` |
| 98 | ascii fallback refuses source above byte budget | Error | ASCII source exceeds byte budget | Source refused | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_ascii_fallback_refuses_source_above_byte_budget` |
| 99 | ascii pending vertices at eof are refused | Error | Pending ASCII facet vertices at EOF | No complete preview accepted | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_ascii_pending_vertices_at_eof_are_refused` |
| 100 | ascii fallback rejects float32 overflow | Error | ASCII coordinate exceeds supported range | No overflowing geometry accepted | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestRenderStlThumbnail::test_ascii_fallback_rejects_float32_overflow` |
| 101 | hostile ascii is refused | Error | Hostile malformed ASCII | No sample accepted | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestPartialSampling::test_hostile_ascii_is_refused` |
| 102 | bounds sampling working set | Edge | Retention17; reader block31; source120 | Candidate facet working set never exceeds retention plus one block | Unit | ✅ `unit/modules/media/test_stl_fallback.py::TestSampleWorkingSet::test_bounds_sampling_working_set` |
| 103 | allows work before the deadline | Happy | Deadline remains in future | Work admitted | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestCheckDeadline::test_allows_work_before_the_deadline` |
| 104 | refuses work after the deadline | Error | Deadline expired | Budget refusal raised | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestCheckDeadline::test_refuses_work_after_the_deadline` |
| 105 | reads a binary file as binary | Happy | Exact binary STL | Pass reports parsed facet count | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestReadPass::test_reads_a_binary_file_as_binary` |
| 106 | reads anything else as ascii | Happy | ASCII STL | Pass reports parsed facet count | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestReadPass::test_reads_anything_else_as_ascii` |
| 107 | frames the exact source bounds | Happy | Exact source bounds with remote coordinates | Frame uses complete source extrema | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestFrame::test_frames_the_exact_source_bounds` |
| 108 | keeps camera coordinates in float64 | Edge | Large translated float64 bounds | Camera computation preserves relative precision | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestFrame::test_keeps_camera_coordinates_in_float64` |
| 109 | writes the manifest | Happy | Completed manifest payload | Exact manifest JSON published | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestWriteManifest::test_writes_the_manifest` |
| 110 | leaves no partial file behind | Edge | Atomic manifest publication | No partial manifest remains | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestWriteManifest::test_leaves_no_partial_file_behind` |
| 111 | refuses a parent pid below one | Error | Nonpositive expected parent PID | Launcher initialization refused | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestApplyWorkerLimits::test_refuses_a_parent_pid_below_one` |
| 112 | refuses a parent that is not the launcher | Error | Parent differs from expected launcher | Launcher initialization refused | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestApplyWorkerLimits::test_refuses_a_parent_that_is_not_the_launcher` |
| 113 | renders a binary stl with a manifest beside it | Happy | Renderable binary source | Success with complete manifest and exact count | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_renders_a_binary_stl_with_a_manifest_beside_it` |
| 114 | renders an ascii stl | Happy | Renderable ASCII source | Success | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_renders_an_ascii_stl` |
| 115 | writes the image it rendered | Happy | Renderable source | Published image has valid PNG signature | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_writes_the_image_it_rendered` |
| 116 | uses the canonical material colour | Happy | Canonical material profile | Observed opaque colour follows profile | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_uses_the_canonical_material_colour` |
| 117 | refuses a budget the parent should never send | Error | Invalid hard limits/dimensions/deadline/launcher arguments | Exit2 distinguishes invalid invocation | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_refuses_a_budget_the_parent_should_never_send` |
| 118 | refuses a parent that is not the launcher | Error | Actual parent differs from launcher | Exit3; no accepted output | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_refuses_a_parent_that_is_not_the_launcher` |
| 119 | reports a source that is not there | Error | Missing source | Exit3 | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_reports_a_source_that_is_not_there` |
| 120 | reports a source larger than its budget | Error | Source exceeds byte budget | Exit3 | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_reports_a_source_larger_than_its_budget` |
| 121 | reports a file it cannot parse | Error | Unparseable source | Exit3 | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_reports_a_file_it_cannot_parse` |
| 122 | reports a source that changes between the two passes | Error | Source changes between complete-bounds and raster passes | Exit3; changed source not accepted | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_reports_a_source_that_changes_between_the_two_passes` |
| 123 | refuses replaced source with restored file metadata | Error | Source replacement preserves size and mtime | Exit3 and no completion manifest | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_refuses_replaced_source_with_restored_file_metadata` |
| 124 | refuses source replaced after render before manifest | Error | Source replaced after rendering; size/mtime preserved | Exit3 and no completion manifest for binary/ASCII | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_refuses_source_replaced_after_render_before_manifest` |
| 125 | reports an unexpected failure distinctly | Error | Unexpected worker exception | Distinct exit4 | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_reports_an_unexpected_failure_distinctly` |
| 126 | success publishes a complete manifest | Happy | Complete stable source and bounded rasterization | Manifest certifies count, scanned bytes and complete status | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestMain::test_success_publishes_a_complete_manifest` |
| 127 | retains valid streamed triangle | Happy | Valid oblique streamed triangle | Facet retained as nondegenerate | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestNondegenerateTriangles::test_retains_valid_streamed_triangle` |
| 128 | rejects degenerate streamed triangle | Error | Repeated/collinear triangle vertices | Facet rejected as degenerate | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestNondegenerateTriangles::test_rejects_degenerate_streamed_triangle` |
| 129 | is invariant to rigid transform | Edge | Rigid transform of valid facet | Degeneracy decision invariant | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestNondegenerateTriangles::test_is_invariant_to_rigid_transform` |
| 130 | rejects nonfinite streamed triangle | Error | Nonfinite streamed coordinates | Facet rejected | Unit | ✅ `unit/modules/media/test_stl_preview_worker.py::TestNondegenerateTriangles::test_rejects_nonfinite_streamed_triangle` |
| 131 | test_keeps_global_bounds_when_remote_facet_is_not_retained | Edge | Two-facet source; retained sample1; streamer unavailable | Preview useful/PARTIAL; source scan COMPLETE; dimensions/count cover far omitted facet; typed topology-not-evaluated volume preserved | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestFallbackMeasurements::test_keeps_global_bounds_when_remote_facet_is_not_retained` |
| 132 | test_bounded_fallback_cannot_certify_materialized_geometry | Edge | Complete source scan but bounded retained/raster preview | Preview PARTIAL independent source COMPLETE; no materialized geometry claim | Unit | ✅ `unit/modules/media/test_thumbnail_engine.py::TestPreviewCoverage::test_bounded_fallback_cannot_certify_materialized_geometry` |
| 133 | test_preserves_the_shared_reader_refusal | Error | Strict sample raises invalid source, resource limit, source changed or source unavailable | FAILED fingerprint retains exact closed cause/no records; no image, scan or geometry claim; volume remains NOT_REQUESTED | Unit | ✅ `unit/modules/media/test_thumbnail_engine.py::TestSampledFingerprintRefusals::test_preserves_the_shared_reader_refusal` |
| 134 | test_preserves_the_shared_loader_refusal | Error | Strict full loader raises the four source refusal causes | FAILED fingerprint retains exact closed cause/no records; no image, scan or geometry claim; unrequested volume preserved | Unit | ✅ `unit/modules/media/test_thumbnail_engine.py::TestSharedReaderLoadRefusals::test_preserves_the_shared_loader_refusal` |
| 135 | test_refuses_thumbnail_when_source_validation_exceeds_budget | Error | ASCII source exceeds strict validation-byte limit before EOF | No retained preview is published and no complete/partial source certificate emitted | Unit | ✅ `unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_refuses_thumbnail_when_source_validation_exceeds_budget` |
| 136 | test_measures_no_geometry_from_a_file_it_could_not_load | Error | Shared-reader validation byte limit prevents complete source proof | Count and extents remain unknown | Unit | ✅ `unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_measures_no_geometry_from_a_file_it_could_not_load` |
| 137 | test_recomputes_fingerprints_from_the_previous_stl_sample_recipe | Edge | Old v5/v6 READY/PARTIAL/FAILED receipt exists for same source | New canonical-version claim uses distinct row and preserves historical receipt | Integration | ✅ `integration/modules/similarity/test_fingerprints.py::TestFingerprintLeases::test_recomputes_fingerprints_from_the_previous_stl_sample_recipe` |
| 138 | test_rederives_stl_outputs_from_the_previous_reader_recipe | Edge | Previous metadata10/11 or thumbnail9/10 READY or terminal FAILED receipt; other kind current | SQL work source discovers Artifact for current metadata/thumbnail recipes | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_rederives_stl_outputs_from_the_previous_reader_recipe` |
| 139 | test_preserves_native_hull_descriptor | Edge | Isolated STL full fingerprint under new reader and current receipt | Native hull output retained and receipt equals canonical current ALGORITHM_VERSION | Integration | ✅ `integration/modules/media/test_mesh_isolation.py::TestGeometryMeasurements::test_preserves_native_hull_descriptor` |
| 140 | test_required_capability_replaces_old_parser_receipts | Edge | Real unknown-required 3MF; historical v4 ready receipt and embedded PNG | Current geometry/thumbnail recipes refuse geometry independently of useful preview; current canonical unsupported result; original bytes preserved | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_required_capability_replaces_old_parser_receipts` |
| 141 | test_verification_preserves_the_shared_reader_refusal | Error | Oversized-STL verification strict sample raises invalid source/resource limit/source changed/source unavailable | Public verify_paths raises the exact closed GeometryError cause; no sentinel invalid_geometry conversion | Integration | ✅ `integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestSharedSTLSampleRefusals::test_verification_preserves_the_shared_reader_refusal` |
| 142 | test_over_cap_mesh_upload_has_a_visible_thumbnail | Edge | Dense binarySTL uploaded through real API beyond full-render cap | READY derivative and decodable bounded image with visible connected geometry | E2E | ✅ `e2e/test_ingest.py::TestMetadata::test_over_cap_mesh_upload_has_a_visible_thumbnail` |
| 143 | test_refuses_malformed_mesh | Error | Malformed full-load STL passed to public verification | Typed invalid_source cause without usable geometry | Integration | ✅ `integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestVerifyPaths::test_refuses_malformed_mesh` |
| 144 | test_a_geometry_failure_keeps_its_code | Error | Actual malformed STL enters supervised pair verification | GeometryError retains strict INVALID_SOURCE literal at caller | Integration | ✅ `integration/modules/media/test_verification_isolation.py::TestVerifyPaths::test_a_geometry_failure_keeps_its_code` |
| 145 | test_reports_a_geometry_failure_as_a_coded_frame | Error | Actual malformed STL enters worker main and encoded reply | Parent decoder raises strict INVALID_SOURCE; worker status remains0 | Integration | ✅ `integration/modules/media/test_verification_worker.py::TestMain::test_reports_a_geometry_failure_as_a_coded_frame` |
| 146 | test_reuses_loaded_mesh_for_descriptors | Happy | Actual subdivided STL64faces with metadata/thumbnail/fingerprint requested | Exact geometry, useful preview, READY whole/component descriptors; source I/O observes only one complete parsing pass | Integration | ✅ `integration/modules/media/test_fingerprints.py::TestFingerprintExtraction::test_reuses_loaded_mesh_for_descriptors` |
| 147 | test_complete_source_with_partial_preview_preserves_unknown_volume | Edge | Real1×2×3cube STL12facets; loader refused; streamer unavailable; fallback retains4facets after full source scan | Useful PARTIAL preview; source COMPLETE; GeometryNotLoaded; GeometryReady exact extents1/2/3 and count12; scalar volumeNone with NOT_CALCULATED/TOPOLOGY_NOT_EVALUATED | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestBoundedVolumeAuthority::test_complete_source_with_partial_preview_preserves_unknown_volume` |
