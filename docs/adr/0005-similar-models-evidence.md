# Similar Models fingerprints retrieve evidence without defining identity

Status: Accepted; the standalone geometry, review, composition, STEP and local
inference workflows are implemented. Remaining acceptance work is
tracked in the [coverage matrix](0005-similar-models-coverage.md) and the
[implementation plan for #154](https://github.com/xiao-villamor/PrintStash/issues/154#issuecomment-5622781316).

Source-byte SHA-256 remains Model identity. Similar Models computes disposable,
versioned evidence from analysis copies; it never rewrites an Artifact or
transfers a Revision. A matching geometric key starts independent verification.
Quantized coordinates can collide or straddle both grids, so keys alone cannot
produce `identical_geometry`, confidence 1.0, or permission to group content.

The geometric core lives in `printstash_core.mesh.similarity`. It accepts loaded
triangle arrays and uses NumPy through the existing optional mesh extra. File
parsing, unit conversion, storage leases, authorization and shared compute
admission stay with product adapters. This keeps geometry available to lite.
The revised independent plan includes a minimal local inference owner, provider
contract and durable Space/Generation/vector store. A later AI Search platform
can adopt these contracts and rows; Similar Models does not wait for that issue.

The first recipe cleans coincident vertices and duplicate/degenerate triangles
on a copy, centers on surface area, and computes exact surface covariance. Vertex
PCA would overweight densely tessellated patches. Sorted, separated eigenvalues
fix axis permutations; four proper sign choices cover the remaining orientation
ambiguity. Repeated eigenvalues suppress canonical keys rather than choosing an
arbitrary symmetric frame. A winding-insensitive triangle representation still
preserves geometric chirality; reflection is not a proper rotation.

Each winding policy has base and half-cell grids at 1e-4 of the PCA bounding-box
diagonal. Physical keys retain the diagonal at six significant digits; normalized
keys omit it. This tolerates common export precision without accidentally making
both groups scale invariant. Neither rounding policy is an equivalence contract.
Both must be calibrated on the complete corpus before classification ships.

D2 uses 8,192 area-weighted point pairs, a PCG64 seed derived from normalized
geometry, and 64 mean-normalized distance bins. The final bin includes distances
at or above four times the mean. Stable face/corner order makes reordered exports
repeatable. D2 is a sampled distribution, not an exact surface-distance metric.
The recipe and NumPy version accompany the result. A committed synthetic golden
pins the recipe; changed output requires inspection and an algorithm version
decision, never automatic golden replacement.

The application recipe is `geometry-v2-sh5f4577c4`. Complete meshes include
hull/fill ratios, volumetric inertia where defined, deterministic views and the
frozen spherical-harmonic projection. Open, inconsistent or oversized geometry
retains explicit unavailable measurements. Oversized STL sampling is always
partial and cannot provide exact keys or exact-equivalence confirmation. STEP
uses an isolated OCP process in the full profile, with millimetre conversion and
a fixed tessellation recipe; lite reports the capability as unavailable.

Independent verification uses full triangle correspondence for exact classes.
Surface registration and sampled distances retain seeds, counts and tolerances;
sampled Hausdorff is not a bound on every point of the continuous surface.
Voxel overlap uses the recorded orthographic parity-fill recipe. Intersecting
shells and non-manifold inputs need human review. Confidence orders evidence;
it is not a calibrated probability that two Models are interchangeable.
The licensed, design-separated corpus and versioned evaluation golden are
recorded beside the core fixtures. Export/repair quality and hardware performance
are separate claims, with separate acceptance evidence.

`modules.similarity` owns input-versioned fingerprints, indexed bounded retrieval,
multiple observations per Model pair, run checkpoints, source freshness and
append-only review decisions. `runtime.similarity` owns wakeups and maintenance
drain. Both thumbnail work and similarity analysis share compute admission;
`artifact_content` owns materialization and source leases. A lost run/source
lease fences publication, and restarts resume the same committed run.

Review state and freshness are independent: a source edit makes evidence stale
without erasing a human confirmation or rejection. An algorithm change creates
a new proposal linked to the preceding candidate; reruns of the same algorithm
preserve its human decision. Both endpoints require EDIT access. Evidence-only
confirmation preserves Models and Revisions. Verified component/plate proposals
use the existing Multipart owner in the same transaction as their decision,
including preservation of existing Choices when appending to a composition.

Family confirmation remains conditional integration C-FAMILY. The capability
is false and direct requests fail without writes until a real Family resolver
exists. The independent schema has no foreign keys to absent Family tables.

`printstash_core.inference` defines the framework-free embedding contract.
`modules.inference` owns a local CPU adapter, immutable Space/Generation rows,
native little-endian float32 vectors and bounded cosine queries. Preplaced
manifest-pinned models must pass digest, signature and canary checks. No model
is downloaded during analysis, and no geometry or rendered views leave the
server. CLIP supports text-to-shape; image-only DINO supports Model queries.
Semantic neighbors are never promoted to exact geometry without geometric
verification. A future search platform may adopt the durable native vectors
without re-embedding; model/recipe changes require an explicit new generation.
