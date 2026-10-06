# Mesh resource evidence boundaries

Complete scene evidence retains unique source resources and placement identity; sampled sources cannot claim full resources. Detached analysis buffers must remain immutable and obey admitted native bounds. Requirements are described by the ingestion resource ownership design and the explicit complete/sample geometry contracts. These pure tests use actual NumPy/Trimesh source arrays; only the external native allocator failure is injected.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | refuses_untyped_preparation_identity | Error | Bad mesh/scene/geometry | TypeError | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_untyped_preparation_identity` |
| 2 | refuses_incoherent_resource_identity | Error | Duplicate resources/missing instance target | ValueError | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_incoherent_resource_identity` |
| 3 | refuses_whole_identity_for_placed_instances | Error | Repeated/translated placements claiming whole resource | ValueError | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_whole_identity_for_placed_instances` |
| 4 | refuses_invalid_detached_buffers | Error | Dtype/shape/mutable buffers | ValueError; source bytes unchanged | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_invalid_detached_buffers` |
| 5 | refuses_untyped_detached_identity | Error | Wrong scene/geometry | TypeError | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_untyped_detached_identity` |
| 6 | refuses_invalid_detach_budget | Error | Nonint/out of range cap | ValueError | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_invalid_detach_budget` |
| 7 | refuses_detach_above_face_budget | Error | Actual mesh with >100 faces | GeometryError geometry_work_limit | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_detach_above_face_budget` |
| 8 | refuses_detach_of_incomplete_evidence | Error | Sampled scene/BREP metadata | ValueError | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_refuses_detach_of_incomplete_evidence` |
| 9 | normalizes_native_materialization_failure | Error | External allocator raises OSError | GeometryError invalid_3mf; source bytes unchanged | Unit | ✅ `unit/modules/media/test_mesh_resources.py::TestResourceEvidenceBoundaries::test_normalizes_native_materialization_failure` |

## Validation and limits

Affected unit and integration files: **82 passed, 1 deprecation warning in 11.07s**, bounded wall 20.26s. The 27 new cases cover nine behaviours. Ruff check, format and whitespace checks pass; the import ordering fix after the focused run changed no test semantics. Existing actual 3MF resource graph and successful preparation/detachment tests remain.

All budgets and production code remain unchanged. No local full, coverage or Deep run was performed. The module baseline was 83.94%; final measured floor validation remains pending the single GitHub Deep run after all owner assertion PRs merge. The injected external allocator failure validates error normalization and source-buffer preservation; it does not simulate operating-system exhaustion or establish peak-RSS attribution.
