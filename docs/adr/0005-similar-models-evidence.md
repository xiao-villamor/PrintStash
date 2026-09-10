# Similar Models fingerprints retrieve evidence without defining identity

Status: Accepted; first geometry-core increment implemented. Remaining work is
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
admission stay with product adapters. This keeps geometry available to lite and
avoids introducing a second learned-inference runtime beside #166.

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

Every result is currently `partial`. SH has no calibrated projection basis;
view descriptors, hull ratios, volumetric inertia and Component extraction are
explicitly unavailable. Surface covariance ratios are named as such, not passed
off as volumetric inertia. Volume is absent for open/inconsistently wound or
zero-volume surfaces; self-intersection validation remains outside this initial
retrieval operation. These limitations prohibit treating the increment as T0
completion or using its outputs for exact confirmation.

Later application stages retain the plan's separate review state/freshness,
multiple observations per Model pair, resumable checkpoints and shared leases.
Family confirmation uses #155; composition uses the Multipart owner; learned
descriptors use #166. None of those workflows is implemented in this increment.
