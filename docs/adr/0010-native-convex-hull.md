# Native convex hull volume

Status: accepted.

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

Finite points are sorted/deduplicated and normalized before native products;
volume is restored to physical units afterward. No QJ perturbation turns a
coplanar input into a synthetic solid. Native precision failures become an
unavailable hull descriptor. Invalid input and nonrepresentable volume are
rejected. Input limits run before deduplication/native preparation. The former
algorithm-specific operation counter is removed; callers may set a lower
`max_points` ceiling, and the native supervisor retains memory/time enforcement.
[ConvexHull contract](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.ConvexHull.html).
