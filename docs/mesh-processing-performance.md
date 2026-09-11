# Complete geometry within bounded resources

The default similarity budget is now 2,000,000 faces, shared with the mesh and
STEP analysis boundaries. It is an admission ceiling, not a promise to allocate
that much on every host: the existing byte, RAM, concurrency, archive and worker
budgets still apply. An explicit operator setting remains authoritative.

The repository Benchy contains 225,706 STL triangles. The previous 200,000-face
similarity ceiling reduced this ordinary model to a 10,000-triangle sample even
when the renderer could load it. Complete analysis now retains its full surface;
normal cleanup removes duplicate and degenerate triangles, leaving 225,143 valid
faces. Source bytes are never rewritten. Its non-manifold geometry still cannot
supply valid solid-volume descriptors; that is distinct from sampled geometry.
The repository Spatula 3MF has 6,704 faces and complete solid descriptors.

## Work removed

- Canonical keys reuse numeric vertex ordering between winding passes and retain
  only the selected sample orientation. Preview views avoid computing retrieval
  hashes that would be discarded.
- A single resource can reuse its already-computed whole-mesh descriptors, with
  independent result objects. Scaled or transformed 3MF resources retain their
  own physical measurements.
- Convex hull construction visits exterior point sets instead of testing every
  interior tessellation point against every hull face. Point/plane work remains
  capped, including adversarial inputs.
- Surface verification measures prepared geometry directly. Its spatial tree
  starts with a nearby surface bound before proving the closest point.
- The rasterizer resolves visible fragments before shading them. Smaller working
  chunks reduce temporary allocations without removing triangles or pixels.
- Fingerprint-only scans omit unused large thumbnails. Existing exact pair proofs
  are reused after authorization and rehashing both source files, only for the
  same Artifacts, Models, components, algorithm, verifier and sufficient sampling.
  Approximate evidence is still rechecked. Changing bytes, moving an Artifact or
  requesting more samples cannot use an incompatible cached proof.
- Idle similarity polling no longer retains storage objects. The runtime still
  drains every admitted unit for restore, but source retention starts only after
  an authorized analysis unit is claimed; active analysis still excludes deletion.
- Linux service and parent-slice memory limits participate in RAM admission, as
  container-root limits already did. STEP capacity reservations now account for
  the admitted triangle output instead of a fixed 32 MiB allocation.

## Measurements

Baseline: #154 head `9663e77a`, before merge into `77ad5313`. Same x86_64 host,
Python 3.13.14, NumPy 2.5.2 and repository files. Profiling adds overhead; these
are development measurements, not Raspberry Pi or NAS certification. The old
full-Benchy analysis required an explicitly uncapped, read-only profiling
experiment because its normal result was partial.

| Operation | Before | Optimized development measurement |
|---|---:|---:|
| Full Benchy fingerprint preparation | 43.00 s | 16.32 s |
| Full Spatula fingerprint preparation | 5.77 s | 2.10 s |
| Benchy geometry verification, 5,000 samples | 47.86 s | 27.04 s |
| Benchy thumbnail peak process RSS | 513 MiB | 379 MiB |

These profiles were collected before the final smaller render chunks and exact
pair cache. They describe individual operations, not cold-library throughput.
The complete 640 × 480 Benchy preview is pixel-identical to the baseline on this
host. `tests/integration/modules/media/test_mesh_render.py` retains a baseline
image, asserts its exact alpha silhouette, and compares colors composited on
light and dark backgrounds within one channel level. This measures visible
variation without amplifying rounding in nearly transparent edge pixels.

The standalone `tests.fakes.similarity_resource_probe` analyzes and renders the
full Benchy, verifies geometry with 5,000 samples, and requests a second thumbnail
under an actual 1 GiB cgroup and a one-CPU quota. On `ff47a943`, the measured
cgroup peak was 484,212,736 bytes and peak process RSS was 542,371,840 bytes
(517 MiB). The complete probe took 62.32 seconds, including 21.76 seconds for
analysis. It asserts ready, complete geometry, an image, exact comparison and
cleanup. This is a resource-limit test, not an emulation of a particular NAS
processor. Both Benchy and the original Spatula preview were also inspected.

`tests.fakes.similarity_cold_benchmark` builds a new library from byte-distinct
exports of repository designs. It seeds no fingerprints, restarts its worker
between bounded batches, checks every source digest and verifies cached response
behavior. Run from `backend/`:

```sh
uv run python -m tests.fakes.similarity_cold_benchmark \
  --root /tmp/new-mesh-library --count 1001 --batch 250
```

The completed 1,001-Artifact run used nine repository designs, including 11
Benchy and 125 Spatula exports, under an actual 1 GiB cgroup and one-CPU quota.
All 1,001 whole-Artifact fingerprints were ready and complete, with one attempt
each and every source digest preserved. Cold fingerprint preparation took
1,095.45 seconds (18.26 minutes); total worker time was 5,283.45 seconds
(88.06 minutes), across 36 worker processes. Candidate processing traversed
3,877 whole/component fingerprints, performed 3,852 verifications and reused
25 exact proofs. Peak process RSS was 511,004,672 bytes (487 MiB). The final
database occupied 28,286,976 bytes; an authorized cached candidate read took
50.44 milliseconds and returned ten results.

This run used one candidate per fingerprint and 256 verification samples;
default settings cost more. Its byte-distinct exports exercise ingestion,
restarts and bounded retention, but are not 1,001 independent designs. The
report keeps cold fingerprint time separate from candidate verification and
cached reads, and does not certify a Raspberry Pi or NAS processor.

## Partial Rust implementation assessment

A standalone Rust prototype of the spatial-tree/point-to-triangle kernel was
compiled with Rust 1.75 at optimization level 3. Its source is retained in
`backend/tests/fakes/proximity_probe.rs`; it is a research executable, not
a production parser or accelerator. The same 225,143 valid Benchy
triangles and 5,000 offset surface queries were supplied to both implementations.
Maximum distance error was `2.3e-16` and coordinate error `3.6e-15`. Under the same
busy development host, NumPy tree construction/query took 3.44/7.58 seconds;
Rust took 0.79/0.41 seconds, with 1,554,198 triangle tests. File serialization and
process startup brought the Rust prototype to 1.47 seconds including input
serialization. This is kernel evidence, not a claim that the whole application
gets the same speedup.

A second experiment substituted the prototype into complete verification:
46.09 seconds with NumPy versus 31.65 seconds with the Rust subprocess, preserving
exact classification, distances and voxel overlap. Both timings include
concurrent development checks and should not be compared directly with the
idle profiling table above. The prototype rebuilt its tree per query, so a
persistent per-comparison tree remains a concrete opportunity.

The evidence supports a narrow native accelerator for surface proximity rather
than moving storage, ingestion, capture or HTTP into Rust. The native boundary
should own an immutable copy of admitted geometry, preserve the point/triangle
work ceiling, and retain the NumPy implementation as a portable fallback. A
process boundary can reuse the application's existing watchdog, cancellation and
capacity patterns without coupling core business contracts to a Python ABI.
An in-process extension would need owned arrays before releasing the GIL and
separate wheel validation on every supported architecture and Python version.
See [PyO3's concurrency guidance](https://pyo3.rs/main/performance.html),
[stable ABI distribution](https://pyo3.rs/main/building-and-distribution), and
[maturin platform guidance](https://www.maturin.rs/distribution.html).

To reproduce the native experiment from `backend/`:

```sh
rustc --edition=2021 -C opt-level=3 tests/fakes/proximity_probe.rs -o /tmp/proximity-probe
uv run python -m tests.fakes.proximity_probe --native /tmp/proximity-probe --verify
```

This branch contains the optimized NumPy implementation; the Rust measurements
are prototype evidence, not a shipped accelerator or an ARM performance claim.

## Existing installations

An explicitly stored 200,000-face limit is preserved. Increase it in Similar
Models settings and start a manual analysis to retry previously partial
Artifacts. Ready fingerprints and human review decisions remain valid; no
Artifact, Revision or canonical Model is replaced by this work.

Verification evidence uses `surface-verification-v3`. An original-axis alignment
hypothesis now proves equivalent exports whose PCA axes differ through rounding;
the full correspondence proof remains mandatory. The previous calibration
reference is retained, and the new reference records all 20 cases with their
unchanged independent labels. Older pair proofs are reverified before entering
the exact-proof cache under this recipe. A changed verifier or sample count gets
its own observation, preserving earlier evidence and human review decisions;
the latest compatible exact proof can then serve repeated runs.
