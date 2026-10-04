# Mesh owners

Mesh operations have one owner each. Callers use these destination modules directly;
none imports the compatibility facade or thumbnail/analysis orchestration.

| Responsibility | Owner | Public operations |
| --- | --- | --- |
| Format routing, source estimates, loader ceilings and reclamation | `app.modules.media.mesh_policy` | `render_admission`, `render_jobs_limit`, `canonical_suffix`, `estimate_triangle_count`, `ram_triangle_cap`, `load_face_budget`, `exceeds_cap`, `reclaim_memory` |
| Host capacity and native process memory facts | `app.modules.media.native_process` | `memory_limit_bytes`, `native_capacity`, `native_memory_budget_bytes`, `process_rss_bytes`, `process_tree_rss_bytes` |
| Weighted source claims and loader memory costs | `app.modules.media.native_budget` | `MeshSource`, `estimate_sources`, `request`, `face_capacity` |
| Shared descriptor-owned admission | `app.runtime.native_admission`, `app.runtime.native_runtime` | `LocalResourcePool`, `NativePermit`, `Resources`, `admit`, `current_permit`, `inherit` |
| Bounded source preparation | `app.modules.media.source_preparation`, `app.runtime.preparation_runtime` | Batch reservations, separate prepared-byte and transfer-slot pools |
| Shared warm-model residency | `app.runtime.inference_resources` | `reserve`, `capacity`, `launch_resources`, `has_pressure` |
| Materialized source loading, isolated STEP conversion and STL export | `app.modules.media.mesh_loading` | `load_mesh`, `load_step_mesh`, `to_stl_bytes` |
| Materialized source dimensions and signed volume evidence | `app.modules.media.mesh_measurements` | `geometry_from_mesh`, `signed_mesh_integral` |
| Measurements from retained 3MF resources and placements | `app.modules.media.scene_measurements` | `measure_scene`, `SceneMeasurements`, `VolumeTopologyRequired` |
| Bounded embedded 3MF preview extraction | `app.modules.media.mesh_previews` | `extract_embedded_3mf_thumbnail` |
| Typed request, measurement and thumbnail results | `app.modules.media.mesh_contracts` | Shared data contracts, `MeshMeasurements.unavailable` |
| Bounded 3MF source parsing and reachable resource ownership | `app.modules.media.three_mf_scene` | `read_scene` |
| Validated retained scenes and explicit materialization | `app.modules.media.mesh_resources` | `PreparedScene`, `materialize_scene`, `load_3mf`, `prepare_loaded_mesh` |
| Application rendering settings and logging | `app.modules.media.mesh_render` | `render_mesh_thumbnail`, `render_scene_thumbnail` |
| Relative positions and repeatable face chunks for rendering | `printstash_core.mesh.render_geometry` | `prepare_mesh`, `prepare_scene` |
| Software shading and rasterization | `printstash_core.mesh.rasterizer` | `render_mesh_thumbnail`, `render_scene_thumbnail` |

Bootstrap binds the shared admission, source-preparation and model-residency
ledgers explicitly. `mesh_policy` projects the inherited allowance into loader
ceilings; it owns no process-local semaphore or cached host-memory ceiling.
Loading remains lazy and preserves scene placements. Measurements preserve source
precision and winding evidence. Embedded preview extraction keeps its archive and
image limits. New consumers enter the bounded isolation seams; only execution
owners call materialization, measurement or rendering primitives directly.

## Retained 3MF scenes

`three_mf_scene.read_scene` admits reachable source resources and placed counts
before allocating their arrays. `PreparedScene` retains those resources and their
instance transforms. It has no whole mesh and does not certify materialized
geometry coverage. `materialize_scene` is the explicit boundary that creates a
`PreparedMesh` with complete materialized geometry.

`scene_measurements.measure_scene` measures referenced vertices in bounded affine
chunks and evaluates each unique resource's closed topology once. It reuses the
component-local signed integral from `mesh_measurements`, preserving negative
cavity contributions until the placed aggregate is classified. Volume failures
retain independently obtained dimensions and triangle counts. Open resource
boundaries yield the internal `VolumeTopologyRequired` variant; the engine can
request one budgeted global materialization or publish volume as not calculated
with `topology_not_evaluated`. Public volume evidence remains the canonical core
`VolumeMeasurement` union.

The engine materializes only when global topology or admitted fingerprint analysis
requires it, and releases that mesh before rendering the retained scene. Rendering
uses `render_geometry` to prepare relative float32 positions and repeatable bounded
face chunks; it still retains O(expanded referenced vertices) position and normal
arrays. It does not provide zero-copy or GPU instancing.

## Compatibility facade inventory

`app.modules.media.mesh_processing` is a temporary import facade. Its operation
aliases delegate to the owners above; its legacy `extract_geometry` wrapper projects
`MeshMeasurements.geometry`. It owns no mutable policy or native algorithms.

| Fixed consumer | Retained operation | Retirement condition |
| --- | --- | --- |
| `backend/tests/unit/modules/media/mesh_processing/test_entry_points.py` | `extract_geometry` | Remove these compatibility assertions with the facade |

There are no production consumers. The repo guard
`TestMeshFacadeInventory.test_only_fixed_legacy_consumers_import_the_facade` rejects
any new consumer. This inventory may shrink, and new code must use a destination
owner. The facade and its remaining tests are removed together after compatibility
retirement; they must not become an alternative implementation.

## Verification

The owner suites in `backend/tests/unit/modules/media/{mesh_policy,mesh_loading,
mesh_measurements,mesh_previews}/` retain the original policy, parser, measurement
and archive assertions at their actual owners. Engine integration assertions from
those suites live in `thumbnail_engine/test_processing.py`; the real-file guards
and reachability checks remain in
`backend/tests/integration/modules/media/test_mesh_processing.py`.

`backend/tests/repo/test_mesh_boundaries.py` also enforces one-way primitive
imports, canonical data-contract imports, safe isolation entry points and the
fixed facade inventory.

Retained measurement and instance behavior is documented in
[retained 3MF scenes](../retained-3mf-scenes.md).

Result coverage, codec invariants and the complete behavior matrix are documented
in [mesh result contracts](../mesh-contracts.md).
