# Reusable exact sample-point lookup

Status: accepted; integrated rollout validation pending.

Verification previously formed squared point distances from two squared norms
and a matrix product. This allocates up to one million cells per query block,
repeats target work for each alignment hypothesis, and cancels small distances
when absolute coordinates are large. A reproduced two-point example at origin
1e9 selected the wrong nearest point: the true distance is 0.25.

Use SciPy's native KDTree behind the core `PointNeighbors` owner. SciPy is already
the optional mesh dependency selected for convex hulls. The owner copies and
deduplicates a floating-point target snapshot, preserving original indices.
Power-of-two scaling avoids squared-distance overflow/underflow without rounding
ordinary coordinates. A roundtrip check refuses ranges that lose representable
coordinates. Physical distances are computed from direct differences with hypot.
Integer arrays are rejected explicitly instead of silently rounding large int64
coordinates to float64. Supported callers already supply analysis float arrays.

Exact queries use `eps=0`, one native thread, and blocks of 256 source points.
Equal or nearly equal native distances trigger a bounded exact comparison and
choose the earliest original index. Duplicate points do not multiply tie work.
Target/source sample limits are 10,000 points each; actual verification uses at
most 5,000 per surface. Alignment ranking batches at most 145 hypotheses of 64
points into one reusable index query. This is sample-point proximity, not a
replacement for triangle-surface proximity or the exact correspondence proof.
[Native query contract](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.KDTree.query.html).

Deadline checks surround index construction/native calls and run between query
blocks and tie rows. They cannot interrupt a native call itself; the process
supervisor remains the hard timeout and memory boundary. Source/target arrays
and the native index do not cross the owner's interface. No global index cache
or extra native worker pool is introduced.
