# Shared visual preparation

A visual pass prepares referenced geometry once and reuses it for its preview
and orthographic views. The shared core owns this representation and the
rasterizer. The application facade injects chunk budgets, logging and the raster
callback; embedding orchestration owns loading, admission and output selection.
No parser, storage, ORM or job engine dependency is introduced into core.

## Ownership and memory

`prepare_mesh_render` and `prepare_scene_render` return `PreparedRender` with
owned relative float32 positions, int64 welded position identities and float64
angle-weighted smooth normals. Source arrays remain untouched. Cached arrays and
emitted triangle chunks reject ordinary writes, and source mutations after
preparation do not change the prepared representation.

Scene preparation copies each unique resource's faces once and retains placement
ordering, offsets and reflected winding. It does not allocate a whole placed
float64 mesh or all placed faces. Expanded referenced positions, welded
identities and smooth normals are still held whole: this is not a constant-memory
renderer. Triangle temporaries and face traversal remain bounded by the configured
chunk size. Reuse is scoped to one pass, without a persistent cache or global
mutable state.

Each camera recomputes projection, culling and shading. Only camera-independent
welding and smooth normal accumulation are reused. Existing raster work budgets,
crease treatment, silhouette recovery, supersampling and vignette remain intact.

## Pixel and encoding contracts

`render_prepared_pixels` returns immutable final RGBA bytes with positive integer
dimensions and exact byte length. `RenderedPixels.rgb` requires an explicit
`RGBBackground`: `WHITE` preserves Pillow's white alpha composition for embedding
inputs; `IGNORE_ALPHA` preserves the grayscale conversion used by view hashes.
No PNG serialization or decoding is required by either consumer.

`render_prepared_thumbnail` uses the same pixel kernel and existing PNG/WebP
encoding settings. Original mesh/scene byte entry points prepare once and
delegate. The visual preview keeps WebP normalization, decoding and bicubic
resizing, including existing encoder identities. Point inputs are unchanged.
Missing render output remains an explicit refusal; missing decoded preview RGB
raises instead of supplying empty bytes.

## Compatibility evidence

The fixtures were captured from commit
`59e91d25172ca53c47f26b1ec773992defddc57a` before this refactor, using Python 3.12.3,
NumPy 2.5.2 and Pillow 12.3.0. The core fixture records source geometry/digests and
nine views of tetrahedron, box, thin plate and sphere, with exact PNG, RGBA,
white RGB, alpha-ignored RGB and grayscale hashes. Four independent descriptor
literals cover tetrahedron and cube with canonical and ambiguous frames. The
application fixture pins source bytes, recipe identity, preview RGB and six RGB
view hashes from the complete legacy visual pass.

These are historical references; do not regenerate them from the new renderer.
Preserved pixels and descriptor bytes require no recipe bump or backfill. Tests
also exercise equivalent exports, retained/reflected scene parity, immutable
snapshots, codec refusal and the real supervised native worker lifecycle.

## Performance observations

A bounded comparison against the archived legacy source completed 12 fresh-child
samples in 26.30 seconds, two samples per mode/input, with alternating execution
order and one BLAS/OMP thread. It measures six 128 px RGB views, excluding imports
and source construction. Tracemalloc is enabled in both modes. All paired source
digests and six RGB output digests match exactly.

| Input | Faces | Legacy median (ms) | Prepared median (ms) | Tracked peak legacy/prepared (MiB) |
|---|---:|---:|---:|---:|
| Tetrahedron | 4 | 271.20 | 161.86 | 8.89/8.33 |
| Sphere | 1280 | 695.87 | 544.05 | 25.08/24.52 |
| Dense sphere | 5120 | 913.63 | 442.10 | 31.75/31.27 |

Tracked peaks exclude NumPy/native allocations not seen by tracemalloc. Child
RSS high-water marks include the imported runtime and constructed source; they
were 150–176 MiB in this run. The host was shared, and static checks could overlap.
Cold codec initialization is included where the legacy path needs it. This small
exploratory sample is not a production latency or ingestion SLA. The measured
work is lower for these inputs; it does not establish an ingestion-wide speedup.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | test_snapshots_source_geometry | Edge | Source vertices/faces modified after preparation | Prepared coordinates/chunks retain snapshot | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareMeshRender::test_snapshots_source_geometry` |
| 2 | test_rejects_cached_array_mutation | Error | Write positions/position IDs/smooth normals | Read-only arrays reject writes | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareMeshRender::test_rejects_cached_array_mutation` |
| 3 | test_retains_source_buffers_unchanged | Happy | Actual tetra source | Source bytes unchanged; expected dtype/count cache | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareMeshRender::test_retains_source_buffers_unchanged` |
| 4 | test_rejects_invalid_normal_preparation_chunk | Error | Chunk0/-1/bool/float | Explicit invalid_render_chunk error | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareMeshRender::test_rejects_invalid_normal_preparation_chunk` |
| 5 | test_returns_no_preparation_for_empty_source | Edge | None source | No prepared representation | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareMeshRender::test_returns_no_preparation_for_empty_source` |
| 6 | test_snapshots_unique_resources_for_repeated_placements | Happy | 64placements/reflection then mutateoriginal | Placed indices/positions stable; preparation preserves original bytes | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareSceneRender::test_snapshots_unique_resources_for_repeated_placements` |
| 7 | test_preserves_prepared_indices_after_chunk_mutation | Error | Write emitted chunk indices | Read-only rejection; repeated traversal unchanged | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareSceneRender::test_preserves_prepared_indices_after_chunk_mutation` |
| 8 | test_refuses_scene_budget_before_cached_preparation | Error | 2049placements | scene_resource_limit before cached output allocation | Unit | ✅ `packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareSceneRender::test_refuses_scene_budget_before_cached_preparation` |
| 9 | test_preserves_declared_rgb_background | Happy | Transparent/partial/opaque RGBA;ignorealpha/white | Exact Pillow RGB conversion and immutable RGBA | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderedPixels::test_preserves_declared_rgb_background` |
| 10 | test_rejects_invalid_pixel_contract | Error | Dimensions0/bool/float/negative;short/trailing/mutableRGBA | Constructor rejects invalid pixel contract | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderedPixels::test_rejects_invalid_pixel_contract` |
| 11 | test_rejects_unknown_background | Error | Unclosed string policy | TypeError before RGB conversion | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderedPixels::test_rejects_unknown_background` |
| 12 | test_preserves_existing_view_pixels | Happy | Front/side/reversed orthographicviews | Direct RGBA matches bytewrapper decoded pixels | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_preserves_existing_view_pixels` |
| 13 | test_preserves_cached_geometry_across_views | Edge | Front/side/frontagain with one preparation | Repeated view same and all cached buffers untouched | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_preserves_cached_geometry_across_views` |
| 14 | test_renders_without_image_codec_roundtrip | Happy | Image save/open forbidden afterreference capture | Correct RGBA produced without imagecodec | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_renders_without_image_codec_roundtrip` |
| 15 | test_reuses_normal_preparation_during_render | Happy | Bincount normalaccumulation forbidden afterpreparation | Correct view consumes cachednormal preparation | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_reuses_normal_preparation_during_render` |
| 16 | test_preserves_scene_pixels_across_face_chunks | Edge | Reflected placements;chunks1/5/1000 | Direct RGBA identical to encoded retainedscene output | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_preserves_scene_pixels_across_face_chunks` |
| 17 | test_preserves_silhouette_recovery | Edge | Inverted flatplate | Same silhouette recovery pixels | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_preserves_silhouette_recovery` |
| 18 | test_refuses_invalid_camera | Error | Nonorthogonal camera | No pixels result | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_refuses_invalid_camera` |
| 19 | test_preserves_renderer_failure_result | Error | Actual rasterboundary raises | No pixels result insteadofpropagation | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_preserves_renderer_failure_result` |
| 20 | test_preserves_existing_encoded_bytes | Happy | PNG/WEBP withsamecamera/dimensions/chunk | Exact legacy encoded bytes | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedThumbnail::test_preserves_existing_encoded_bytes` |
| 21 | preserves exact legacy descriptor bytes | Happy | Tetra/cube canonical and ambiguous frames; raw source digests and frozen legacy JSON at59e91d | Exactly48bytes equal four independent literals; frozenJSON/provenance/source digests match | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_preserves_legacy_view_hash_golden` |
| 22 | reuses one preparation for six views | Happy | Real tetra surface; actual preparation/render functions wrapped for observation | One preparation shared by six renders; exact view ordering64x64/matte;48bytes preserved | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_reuses_preparation_for_six_views` |
| 23 | keeps source arrays immutable | Edge | Canonical/ambiguous tetra; vertices/faces/frame read-only | Source values unchanged; complete48byte result | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_keeps_view_source_arrays_unchanged` |
| 24 | avoids an image codec roundtrip | Edge | Real raster outputs; Image.open/save refuse calls | Complete48byte legacy-equivalent descriptor without encoding or decoding PNG | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_hashes_actual_pixels_without_codec_roundtrip` |
| 25 | ignores alpha for legacy grayscale | Edge | Real six-view outputs; observed public RGB conversion policy | All six request IGNORE_ALPHA; exact48byte legacy golden preserved | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_preserves_legacy_grayscale_alpha_policy` |
| 26 | reports explicit missing render | Error | Prepared pixel renderer returnsNone | GeometryError view_render_unavailable from public descriptor seam | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_renderer_failure_has_explicit_reason` |
| 27 | keeps equivalent exports invariant | Edge | Reordered/scaled tetra exports | Equal six-view hashes and distinct per-view information | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_view_hashes_survive_equivalent_exports` |
| 28 | retains DCT encoding | Happy | 64x64orthogonal edges | Distinct8byteperview hashes | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_dct_distinguishes_orthogonal_edges` |
| 29 | refuses invalid DCT input | Error | Wrongshape/nonfinite image | GeometryError invalid_view_image | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_invalid_view_is_not_hashed` |
| 30 | refuses missing preparation | Error | Public preparation factory returns None | Exact GeometryError.code=view_render_unavailable | Unit | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py::TestViews::test_missing_preparation_has_explicit_reason` |
| 31 | test_loads_the_mesh_once_for_a_complete_visual_pass | Happy | Complete tetrahedron STL, multiview32 | One load and one preparation across preview plus6frames; source file, vertices and faces unchanged | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_loads_the_mesh_once_for_a_complete_visual_pass` |
| 32 | test_reuses_component_preparation_for_embedding_views | Happy | Complete STL resource component1 | One preparation for6RGBframes; source resource buffers and file unchanged | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_reuses_component_preparation_for_embedding_views` |
| 33 | test_refuses_decoded_thumbnail_without_rgb | Error | Valid text EmbeddingInput returned at image decoder boundary | GeometryError embedding_view_failed, no empty RGB fallback | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_refuses_decoded_thumbnail_without_rgb` |
| 34 | test_preserves_legacy_encoded_thumbnail | Happy | Real tetrahedron, PNG/WEBP params | Exact encoded bytes equal and original vertex/face bytes unchanged | Integration | ✅ `tests/integration/modules/media/test_mesh_render.py::TestPreparedMeshRender::test_preserves_legacy_encoded_thumbnail` |
| 35 | test_preserves_legacy_white_rgb | Happy | Real tetrahedron, matte32 canonical frame | Exact RGB bytes equal; app face chunk size/logger/raster callback injected | Integration | ✅ `tests/integration/modules/media/test_mesh_render.py::TestPreparedMeshRender::test_preserves_legacy_white_rgb` |
| 36 | test_preserves_retained_scene_preview | Happy | 16 shared tetrahedron placements | Exact encoded bytes equal; resources and transforms unchanged | Integration | ✅ `tests/integration/modules/media/test_mesh_render.py::TestPreparedMeshRender::test_preserves_retained_scene_preview` |
| 37 | test_ignores_unreferenced_vertices_in_visual_inputs | Edge | 3MF original versus unused far coordinate; thumbnail/multiview | Same preview and frame RGB bytes | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_ignores_unreferenced_vertices_in_visual_inputs` |
| 38 | test_refuses_partial_render_embeddings | Error | Over-budget sampled STL | embedding_requires_complete_geometry | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_refuses_partial_render_embeddings` |
| 39 | test_refuses_invalid_render_dimensions | Error | Size31/513 | invalid_view_budget | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_refuses_invalid_render_dimensions` |
| 40 | test_bounds_a_3mf_by_the_callers_cap_before_composing_the_scene | Happy | Instanced3MF over caller cap | scene_resource_limit before compose allocation | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestVerifyPaths::test_bounds_a_3mf_by_the_callers_cap_before_composing_the_scene` |
| 41 | test_preserves_preview_pixels | Happy | Real repository Benchy STL | Existing independently stored silhouette/displayed pixels match | Integration | ✅ `tests/integration/modules/media/test_mesh_render.py::TestMeshRender::test_preserves_preview_pixels` |
| 42 | test_preserves_scene_preview_pixels | Happy | 64 shared placements | Exact RGBA arrays equal | Integration | ✅ `tests/integration/modules/media/test_mesh_render.py::TestMeshRender::test_preserves_scene_preview_pixels` |
| 43 | test_preserves_frozen_visual_input_hashes | Happy | Tetrahedron STL and exact clip/v1 test recipe captured at HEAD59e91d before production changes | Source SHA and recipe view_identity match captured input; thumbnail and six canonical RGB SHA256 match frozen legacy values; source unchanged | Integration | ✅ `tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_preserves_frozen_visual_input_hashes` |
| 44 | test_rejects_prepared_render_bypasses | Error | Twelve raw prepared render entry points, direct imports/module aliases/callbacks | Exact raw target rejected for all36routes | Unit | ✅ `tests/repo/test_mesh_boundaries.py::TestMeshBoundaries::test_rejects_prepared_render_bypasses` |
| 45 | test_completes_one_isolated_multiview_pass | Happy | Real STL source; bounded native child | Six complete32pxRGB views, previewRGB, exited process, no temporary residue | Integration | ✅ `tests/integration/modules/media/test_visual_render.py::TestVisualRender::test_completes_one_isolated_multiview_pass` |
| 46 | test_renders_step_without_database_access_in_the_child | Happy | Real STEP box; bounded native child | Six complete views and exited child without database access | Integration | ✅ `tests/integration/modules/media/test_visual_render.py::TestVisualRender::test_renders_step_without_database_access_in_the_child` |
| 47 | test_kills_the_child_at_the_callers_deadline | Error | 10ms caller deadline | Typed inference_timeout and exited child | Integration | ✅ `tests/integration/modules/media/test_visual_render.py::TestVisualRender::test_kills_the_child_at_the_callers_deadline` |
| 48 | test_kills_the_child_at_the_shared_memory_ceiling | Error | Observed treeRSS above shared ceiling | Typed embedding_worker_oom and exited child | Integration | ✅ `tests/integration/modules/media/test_visual_render.py::TestVisualRender::test_kills_the_child_at_the_shared_memory_ceiling` |
| 49 | test_rejects_incomplete_or_untrusted_worker_output | Error | Empty/malformed/incomplete/private-error native replies | Typed sanitized output refusal | Integration | ✅ `tests/integration/modules/media/test_visual_render.py::TestVisualRender::test_rejects_incomplete_or_untrusted_worker_output` |
| 50 | test_cancellation_reaps_the_worker_tree | Error | Cancel running child tree | OperationCancelled; all child/descendant PIDs gone | Integration | ✅ `tests/integration/modules/media/test_visual_render.py::TestVisualRender::test_cancellation_reaps_the_worker_tree` |
