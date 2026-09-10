# Similar Models — first geometry-core increment

Implementation tracking for [#154](https://github.com/xiao-villamor/PrintStash/issues/154#issuecomment-5622781316).
This is part of stage 1, not completion of T0 or the feature. The pure array
operation performs no I/O and cannot change Model identity or stored Artifacts.

Remaining stage 1 work: calibrated SH basis, view descriptors, convex-hull and
volumetric inertia descriptors, Components/resource instances, bounded file
loading and oversized-STL sampling, persistence/migrations, shared leases,
ingest integration, resumable runs, API and Maintenance UI. Stages 2–5 remain
pending, including verification, review, STEP and #166 integration. The full
feature e2e (ingest → review → Family with preserved Revisions) remains missing
until those operations exist. This branch is not ready for feature release.

## Coverage matrix

These rows refine the core portion of S005–S015 and S045 in the linked plan;
no other plan row is claimed complete. All tests below live in
`backend/packages/printstash-core/tests/mesh/similarity/test_fingerprint.py`.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| C01 | reports_physical_surface_metrics | Happy | Closed scalene tetrahedron | Area, volume, Euler and counts agree with analytic values | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_reports_physical_surface_metrics` |
| C02 | preserves_input_arrays | Happy | Read-only vertex/face arrays | Source arrays remain byte-identical | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_preserves_input_arrays` |
| C03 | stabilizes_reexport_order | Happy | Triangle soup, permuted indices, cyclic face order, float32 | Same eight retrieval keys | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_stabilizes_reexport_order` |
| C04 | stabilizes_rigid_transforms | Happy | Proper rotations plus translations | Same retrieval keys | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_stabilizes_rigid_transforms` |
| C05 | retains_physical_scale | Edge | 0.5x, 2x, 25.4x | Physical keys differ; normalized keys agree | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_retains_physical_scale` |
| C06 | separates_chiral_reflection | Edge | Scalene tetrahedron reflected in X | No shared canonical key | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_separates_chiral_reflection` |
| C07 | distinguishes_winding_from_reflection | Edge | All faces reversed | Only winding-insensitive keys agree | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_distinguishes_winding_from_reflection` |
| C08 | cleans_analysis_copy | Edge | Duplicate vertices/faces, degenerate faces, unused vertex | Cleaned fingerprint equals original | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_cleans_analysis_copy` |
| C09 | reports_ambiguous_frame | Edge | Cube with repeated PCA eigenvalues | Canonical keys absent with explicit ambiguity | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_reports_ambiguous_frame` |
| C10 | omits_unreliable_volume | Edge | Open, inconsistently wound or zero-volume surface | Volume and ratio absent with reason | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_omits_unreliable_volume` |
| C11 | reports_degenerate_surface | Edge | Empty mesh, collapsed or collinear triangles | Stable degenerate_surface error | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_reports_degenerate_surface` |
| C12 | rejects_nonfinite_geometry | Error | NaN, positive/negative infinity | Stable nonfinite_geometry error | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_rejects_nonfinite_geometry` |
| C13 | rejects_invalid_indices | Error | Negative, out-of-range or non-integer indices | Stable invalid_faces error | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_rejects_invalid_indices` |
| C14 | rejects_invalid_array_shapes | Error | Non-triangular arrays, non-numeric vertices | Stable invalid_vertices/invalid_faces error | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_rejects_invalid_array_shapes` |
| C15 | enforces_mesh_budget | Error | Vertices/faces one over limit | Stable resource_limit error | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_enforces_mesh_budget` |
| C16 | rejects_invalid_budget | Error | Zero, negative, boolean or excessive limits | Stable invalid_budget error | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_rejects_invalid_budget` |
| C17 | rejects_numeric_overflow | Error | Finite coordinates beyond representable metrics | Stable numeric_range error | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_rejects_numeric_overflow` |
| C18 | computes_normalized_d2 | Happy | Closed tetrahedron | 64 nonnegative bins sum to one; positive physical mean | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_computes_normalized_d2` |
| C19 | repeats_content_seed | Happy | Reordered, translated geometry | Same content seed and D2 histogram | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_repeats_content_seed` |
| C20 | normalizes_d2_scale | Edge | Uniform scales | Same D2 bins; physical mean scales | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_normalizes_d2_scale` |
| C21 | weights_d2_by_surface_area | Edge | 81 triangles on the smallest tetrahedron face | D2 stays within fixed distribution tolerance | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_weights_d2_by_surface_area` |
| C22 | records_unavailable_descriptors | Edge | First core increment | SH, view, hull and volumetric inertia explicitly unavailable | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_records_unavailable_descriptors` |
| C23 | pins_algorithm_golden | Happy | Fixed synthetic geometry | Versioned keys and D2 digest equal committed golden | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_pins_algorithm_golden` |
| C24 | keeps_numpy_optional_at_import | Edge | Interpreter blocks NumPy import | Public contract imports without mesh runtime | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_keeps_numpy_optional_at_import` |
| C25 | accepts_mesh_at_budget | Edge | Vertices/faces exactly at configured limit | Fingerprint returned | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_accepts_mesh_at_budget` |
| C26 | stabilizes_surface_pca_after_retessellation | Happy | One face retessellated to 81 triangles | Surface covariance eigenvalue ratios stay equal | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_stabilizes_surface_pca_after_retessellation` |
| C27 | recovers_across_base_grid_boundary | Edge | Fixed 0.0004-coordinate perturbation | Half-cell grid retrieves pair split by base grid | Unit | ✅ `mesh/similarity/test_fingerprint.py::TestFingerprintMesh::test_recovers_across_base_grid_boundary` |

## Validation

- `cd backend/packages/printstash-core && ./scripts/test.sh coverage -q`:
  1,503 passed, five coverage-gate tests passed; aggregate branch-aware coverage
  98.91%; new similarity module 100% statements/branches. Floors unchanged.
- Python 3.11.15 / NumPy 1.26.4: all 54 geometry cases passed against the same
  golden used by Python 3.14.6 / NumPy 2.5.2.
- Autonomous package Ruff lint/format and strict Pyright: passed.
- Backend Ruff lint: passed. Repository test-hygiene suite: 2,544 passed.
- Application full-stack/e2e suites were not run: this increment adds a pure,
  currently unconnected core operation. No API, schema or frontend code changes.
- Security diff scan and the complete feature e2e remain gates before the feature
  PR is marked ready. This initial branch remains implementation in progress.
