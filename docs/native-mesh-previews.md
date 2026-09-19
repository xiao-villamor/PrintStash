# Native mesh preparation and previews

PrintStash prepares and renders STL and 3MF previews in Rust while retaining the
existing Python import coordinator and durable state. The public HTTP, metadata,
thumbnail, and job contracts are unchanged.

## Ownership

| Concern | Owner |
| --- | --- |
| Binary/ASCII STL scanning, source fencing, framing, depth rendering, shading, and encoding | Rust `printstash-render-core` |
| 3MF ZIP inflation and XML mesh parsing | Rust binding using `zip` and `quick-xml` |
| 3MF component-graph policy and transform ordering | `printstash_core.mesh.threemf` |
| 3MF resource buffers, transform application, preparation, rendering, and encoding | Rust native resource/scene handles plus `printstash-render-core` |
| Preview selection, resource admission, fallback policy, and progress | Python media coordinator |
| Durable jobs, Artifact publication, and thumbnail generations | Existing Python application transactions |

The PyO3 `NativeStlSource` is a translation-only wrapper over the one
framework-neutral `printstash-render-core::stl_source` implementation. It no
longer contains a second parser, source-identity fence, reservoir, or depth
renderer.

Ordinary and dense STL previews use the path-based isolated renderer first. A
thumbnail-only STL request does not allocate a trimesh object or copy complete
vertex/face buffers through PyO3. Exact geometry and fingerprint requests may
still load the admitted mesh for their separate computations; preview rendering
does not copy those buffers back into Rust. If the path renderer refuses a
source, the existing bounded full renderer remains available before the sampled
fallback.

For 3MF, native archive reads return opaque mesh-resource handles. Python parses
only the bounded XML shell and resolves the component graph. It returns resource
handles and 4x4 transforms to `NativeScenePreview`; Rust checks counts and finite
affine transforms, applies units/placements, prepares one owned scene, and
renders it. The existing numeric buffers are exposed only to the geometry and
similarity compatibility path. Preview buffers do not make an additional
Python-to-Rust round trip. Scenes above the established two-million-face preview
ceiling retain the existing bounded fallback behavior.

No dependency or thread pool was added. The implementation reuses the pinned
`zip`, `quick-xml`, `image`, `fast_image_resize`, and Rayon-backed native stack.

## Preserved contracts

- Binary and ASCII STL bounds, triangle limits, line limits, deadlines, source
  replacement detection, complete/sampled flags, and fallback ordering remain
  bounded.
- 3MF stored, Deflate, Bzip2, and LZMA members; units; build placement;
  components; repeated instances; external model parts; and package-path limits
  retain their existing behavior.
- Preview dimensions, canonical camera, supersampling, material, flat-mesh
  framing, output formats, embedded-preview preference, and failure categories
  remain unchanged.
- The native 3MF scene produces byte-identical PNG output for the committed
  multipart benchmark fixture. STL uses the already-approved complete streaming
  renderer; the comparison protocol requires equal geometry and bounded pixel
  error against the parent renderer.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | delegates the binding to the ASCII-capable core source | Happy | Complete ASCII facet | Count, bytes, bounds, and framing sample match | Binding | ✅ `rust/tests/test_stl_pipeline.py::TestNativeStlSource::test_binding_uses_the_core_ascii_source` |
| 2 | preserves binary STL differential behavior | Happy | Binary source and reference worker | Bounds, sample, depth image, budgets, and source fencing match | Binding | ✅ `rust/tests/test_stl_pipeline.py` |
| 3 | renders ordinary STL from its path | Happy | Admitted STL also loaded for geometry | Path renderer wins without mesh-buffer rendering | Unit | ✅ `test_thumbnail_engine.py::TestThumbnailEngine::test_stl_path_renderer_avoids_copying_loaded_mesh_buffers` |
| 4 | skips trimesh for thumbnail-only STL | Edge | Thumbnail repair without geometry/fingerprint | No full mesh allocation; complete path preview returned | Unit | ✅ `test_thumbnail_engine.py::TestThumbnailEngine::test_thumbnail_only_stl_does_not_allocate_a_trimesh` |
| 5 | retains full-render fallback | Error | Native STL path render refuses input | Existing bounded full renderer returns a preview | Unit | ✅ `test_thumbnail_engine.py::TestThumbnailEngine::test_stl_path_failure_retains_the_full_renderer_fallback` |
| 6 | keeps 3MF resources native through preview | Happy | Repeated placed object | Native scene renders without calling the byte-buffer renderer | Core | ✅ `test_threemf.py::TestLoadScene::test_renders_repeated_instances_from_native_resource_handles` |
| 7 | preserves 3MF scene semantics | Happy/Edge | Units, transforms, nested/repeated/external components, four compression methods | Flattened geometry and placements match the established loader | Core | ✅ `packages/printstash-core/tests/mesh/test_threemf.py` |
| 8 | carries the native scene through application flattening | Integration | Real 3MF through `_load_mesh` | Final mesh retains an owned native preview handle | Integration | ✅ `tests/integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_streams_3mf_without_the_legacy_loader` |
| 9 | compares deterministic preview workloads | Performance | Binary/ASCII STL, multipart/embedded 3MF | Source hashes, geometry, dimensions, completeness, and approved pixels match before timing | Repo | ✅ `tests/repo/test_bench_mesh_preview.py` |

Focused evidence on the implementation revision: 32 STL binding tests, 12
standalone render-core tests, 53 3MF core tests, 10 media integration tests, 10
thumbnail-selection tests, and 28 benchmark/matrix tests pass. Full application,
native coverage, packaging, containers, security, and controlled comparisons
remain required on the milestone PR revision.

## Performance protocol

Protocol `mesh-preview-v1` runs committed release images against the immediate
parent and M00 baseline under 2 CPU/2 GiB and 4 CPU/4 GiB profiles. It covers
small and dense binary STL, ASCII STL, multipart 3MF, and embedded 3MF. One
warm-up precedes seven alternating pairs, extended to fourteen when noisy.

The report records source hashes, geometry, completeness, strategy, encoded
hash/size, a bounded 32x24 RGB comparison probe, latency p50/p95/max, CPU, RSS,
and container peak memory. Comparison requires exact source hashes, geometry,
dimensions, completion, and failures. It permits a mean probe difference of at
most 24/255 for the intentional switch from the full STL renderer to the
existing complete streaming renderer. The standard 5% elapsed and 10%
CPU/memory review thresholds apply after correctness passes.
