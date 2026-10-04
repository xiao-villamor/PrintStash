# Native convex hull volume

Status: accepted. Final rollout validation is tracked below.

The Python QuickHull descriptor exhausts its 20-million point/plane budget on a
valid deterministic sphere with 20,000 exterior points. A failed hull descriptor
also follows expensive loops and allocations before reporting unavailability.

Use SciPy/Qhull behind `mesh.similarity.hull.hull_volume`, an array-to-scalar
operation. The optional `printstash-core[mesh]` dependency owns SciPy; the backend
selects that extra. Base core imports remain independent of native mesh packages.
The supported Python floor is 3.14. The resolver floor is SciPy 1.18.1,
with NumPy 2.5.3 and Pillow 12.3.0. CPython 3.14 wheels are published for Linux
x86-64 and ARM64. Wheel availability is packaging evidence; runtime validation
is recorded independently. [SciPy distribution](https://pypi.org/project/scipy/1.18.1/).
The measurements below retain their original Python and dependency versions.

Finite points are sorted/deduplicated and normalized before native products;
volume is restored to physical units afterward. No QJ perturbation turns a
coplanar input into a synthetic solid. Native precision failures become an
unavailable hull descriptor. Invalid input and nonrepresentable volume are
rejected. Input limits run before deduplication/native preparation. The former
algorithm-specific operation counter is removed; callers may set a lower
`max_points` ceiling, and the native supervisor retains memory/time enforcement.
[ConvexHull contract](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.ConvexHull.html).

## Evidence

Local microbench, two sets of three repetitions after import, Python 3.12.3, NumPy 2.5.2,
SciPy 1.17.1, one OpenBLAS/OpenMP thread. Points use NumPy seed 20261004, normal
vectors normalized onto the unit sphere. Peak RSS covers the whole subprocess,
including Python, imports and point preparation. A repeat reads Linux VmHWM
directly to exclude inherited rusage high-water marks. Other work was running on this
host, so these figures are diagnostic observations, not a throughput guarantee.

| Input | Python hull | Native hull | Native process peak RSS |
| --- | --- | --- | --- |
| 20,000 exterior points | All 3 attempts refused after 6.20–8.44 s | 0.174–0.203 s; volume 4.186268511528313 | 81.5–82.3 MiB |
| 200,000 exterior points | Not run | 2.52–3.38 s; volume 4.188537727143043 | 201.1–201.3 MiB |

The analytic enclosing sphere has volume 4π/3. The lower polyhedral volumes
converge to it as the sample grows. New-process SciPy import cost was
0.347–0.607 s; the former hull import was 0.007–0.025 s and its process peak was
48.0–48.5 MiB. The native implementation trades a larger dependency/startup footprint
for a usable result and shorter kernel execution. Worker-level measurements
remain required before claiming end-to-end ingestion gains.

Regression evidence covers dense exterior and interior sets, physical cube and
tetrahedron volumes, translations/reordering, scales from 1e-100 to 1e100,
coplanar/collinear/duplicate/empty inputs, NaN/Inf, input ceilings and immutable
source arrays. The whole core similarity suite passes (255 cases); backend
fingerprint and lease checks pass (30 cases), as do isolated worker/processor checks
(59 cases) and the final added precision edges (29 hull cases). Historical fingerprint states
ready/partial/failed at geometry-v3 acquire a new geometry-v4 analysis row;
historical rows and verifier calibration are retained.

SciPy uses BSD licensing and includes upstream Qhull and bundled-library notices
in its wheel. Distribution retains those installed notices. No fallback to the
removed Python hull is retained: missing native dependencies are installation
errors, not an excuse to silently run the exhausted algorithm.

## Follow-through

Run the application's normal CI against the final head and the full integrated
gate when the ingestion changes are assembled. Deep CI remains deferred until
the implementation plan is complete. It covers the independent core package
on Python 3.14.8. Record ARM runtime validation and the final worker/load
measurement with the rollout evidence; do not infer those from this microbench.
