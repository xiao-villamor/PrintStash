# Native geometric similarity

PrintStash executes the bounded geometric kernels and matching decisions in
Rust while retaining the existing Python coordinator and application-owned
durable records. Public search, fingerprint, and similarity contracts remain
unchanged.

## Ownership

| Concern | Owner |
| --- | --- |
| Voxel projection/fill, spherical-harmonic spectrum, DCT hashes, volumetric inertia, nearest-neighbour search, exact triangle equivalence, ICP refinement, and evidence decisions | Framework-free Rust `printstash-similarity-core` crate |
| Surface cleanup/PCA, deterministic sampling, alignment-hypothesis enumeration, checked-in SH projection basis, view rendering, and typed result assembly | Framework-independent `printstash-core` Python coordinator |
| Similarity scheduling, candidate retrieval, cache policy, and progress | Python application coordinator |
| `GeometryFingerprint`, `SimilarityRun`, `SimilarityCandidate`, durable jobs, and Artifact publication | Existing Python application transactions |

The PyO3 adapter decodes bounded contiguous numeric buffers into typed `Mesh`,
`Point`, `Face`, `SurfaceTree`, `Transform`, and verification values. The core
crate returns typed results and stable errors; Python conversion and exception
mapping remain in the adapter. Queue types, SQLAlchemy sessions, ORM objects,
HTTP objects, and storage handles do not enter the core. A Python callable
running on a native thread is not counted as native compute.

`nalgebra` 0.35.0 supplies the symmetric eigensolver and SVD used by inertia
and rigid ICP. It is pinned through `Cargo.lock`, uses Apache-2.0, supports the
repository toolchain, and replaces a private linear-algebra implementation.
The existing Rayon pool remains shared. The bounded-volume hierarchy lives in
the reusable core crate; this milestone adds no runtime, executor, database,
queue, or thread pool.

## Preserved contracts

- Mesh admission ceilings, finite-value validation, voxel dimensions, surface
  and interior filling, and the two-million crossing work ceiling retain their
  stable failure codes.
- SH shell/order layout, checked-in projection basis, six view hashes, D2
  sampling, and descriptor availability reasons remain versioned.
- Registration preserves deterministic seeds, proper/improper PCA hypotheses,
  exact incidence proof, mirror ambiguity, sampled distance provenance, and the
  established maximum of 5,000 evaluation points.
- ICP still refines four hypotheses per parity for eight bounded iterations.
  Rust uses `nalgebra` SVD and refuses invalid or excessive inputs before work.
- Evidence classes, confidence, scale, mirror state, topology checks, voxel IoU,
  and exact-equivalence requirements remain unchanged. Retrieval evidence never
  authorizes deletion without independent verification.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | fills a closed voxel shell | Happy | Watertight cube | Interior and surface cells are occupied | Rust | ✅ `rust/similarity-core/src/descriptors.rs::tests::fills_the_center_of_a_closed_cube` |
| 2 | bounds voxel work | Error | Repeated crossing-heavy faces | Stable resource-limit error before unbounded work | Rust | ✅ `rust/similarity-core/src/descriptors.rs::tests::rejects_excessive_projected_intersections` |
| 3 | classifies exact mirrored scale | Happy | Exact reflected geometry and scale 2 | `rescaled_mirrored` at confidence 1 | Rust | ✅ `rust/similarity-core/src/verification.rs::tests::exact_reflection_keeps_the_mirror_class` |
| 4 | preserves descriptor corpus | Happy/Error | Calibrated, ambiguous, flat, malformed, and over-budget surfaces | Existing SH, view, inertia, availability, and limit results | Core | ✅ `packages/printstash-core/tests/mesh/similarity/test_descriptors.py` |
| 5 | preserves voxel corpus | Happy/Error | Closed/open surfaces and work-limit fixtures | Existing grids, fill state, and stable errors | Core | ✅ `packages/printstash-core/tests/mesh/similarity/test_voxel.py` |
| 6 | preserves exact geometry proof | Happy/Error | Reordered, translated, scaled, mirrored, and colliding inputs | Only a bijective incidence-preserving mapping proves equality | Core | ✅ `packages/printstash-core/tests/mesh/similarity/test_geometry.py` |
| 7 | preserves proximity and ICP | Happy/Error | Triangle surfaces, rigid hypotheses, invalid buffers, and work ceilings | Existing distances, transforms, convergence, and controlled failures | Core | ✅ `packages/printstash-core/tests/mesh/similarity/test_proximity.py` |
| 8 | preserves matching decisions | Happy/Edge | Labeled identical, rescaled, mirrored, remeshed, repaired, near, partial, and unrelated meshes | Existing evidence class, confidence, ambiguity, transform, and provenance | Core | ✅ `packages/printstash-core/tests/mesh/similarity/test_verification.py` |
| 9 | maps native decision failures | Error | Native decision rejects nonfinite or invalid metrics | Public core raises the stable `GeometryError` code | Core | ✅ `packages/printstash-core/tests/mesh/similarity/test_verification.py::TestVerifyMeshes::test_translates_native_decision_errors` |
| 10 | preserves application integration | Happy/Edge | Real fingerprint and similarity processing records | Existing records, cache behavior, and typed results | Integration | ✅ `tests/integration/modules/media/geometry_analysis`, `tests/integration/modules/media/test_fingerprints.py`, `tests/integration/modules/similarity/processing`, `tests/integration/modules/similarity/test_verification_cache.py` |
| 11 | compares committed similarity implementations | Performance | Fingerprints plus identical, scale/mirror, retessellation, and near-shape verification | Source hashes and correctness catalog match before timing | Repo | ✅ `tests/repo/test_bench_similarity.py` |

The milestone gate runs the direct Rust core suite, binding tests, complete core
coverage, application integration, packaging, containers, and exact-diff security
review. Controlled performance comparisons remain deferred until the implementation
milestones are complete.

## Performance protocol

Protocol `geometric-similarity-v1` runs the same high-level core API from the
new commit against both the immediate parent and M00 release images. It measures
fingerprint/descriptor generation and four verification classes: identical,
rescaled mirrored, retessellated, and similar shape. Inputs are deterministic,
hashed numeric arrays.

One warm-up precedes seven alternating pairs under the 2 CPU/2 GiB and 4 CPU/4
GiB profiles, extended to fourteen when noisy. Before timing is accepted, the
harness compares source hashes, retrieval keys, surface/D2/shape descriptors,
evidence class, confidence, exact/mirror state, transform, sampled distances,
voxel IoU, and provenance. It reports fingerprint and verification p50/p95/max,
wall time, process CPU, sampled RSS, and container peak memory. The standard 5%
elapsed/latency and 10% CPU/memory review thresholds apply after correctness
passes.
