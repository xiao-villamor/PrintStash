# Similarity calibration fixtures

`manifest.json` records the immutable Thingi10K dataset revision, original
Thingiverse design, creator, license, source URL, and SHA-256 for each checked-in
STL. Every selected source is marked Creative Commons Public Domain Dedication
(CC0-1.0) in the pinned dataset's contextual metadata.

`pairs.csv` assigns all derivatives of one design to one partition. The four
initially inspected real designs belong to training/calibration. Gear design
23030 and stand design 512350 were reserved until the surface-verification-v2
rules were frozen. Their 18 derivatives form the real held-out evaluation.
The two chiral tetrahedron checks are separately original analytic fixtures.
Different tessellation resolutions of the same parametric washer remain in one
calibration partition; they are not counted as independent held-out designs.

Variants include binary/ASCII STL or OBJ re-export, rigid transforms, 0.5×, 2×,
25.4× scales, triangle subdivision, and micro-repair. The repair variant removes
the smallest face after two subdivision levels. Washer remeshing reduces the
number of radial sections to one quarter; a changed inner radius is a negative
fit example. These operations preserve their design ownership in the CSV.

The versioned evaluation JSON records actual measurements and a confusion table,
not invented labels or mocked scores. The integration test checks precision and
recall, version identity, and metric goldens with a fixed numeric tolerance.
Changing evidence under the same algorithm requires investigation, not silently
rewriting the golden. The sample is small: its measured acceptance rates are
regression gates, not statistical guarantees for arbitrary libraries.

`surface-verification-v3` adds the original coordinate axes as an alignment
hypothesis before the same full vertex/triangle correspondence proof. This fixes
the OBJ re-export case `70561-obj`: the v2 golden measured `similar_shape` even
though its independently assigned label is `identical_geometry`. Its v3 result
proves exact equivalence; the other 19 labels remain unchanged and all measured
distance/scale/voxel metrics stay within the existing tolerance. The v2 reference
is retained. New reference names include both fingerprint and verifier versions.
No labels, splits, precision/recall requirements or numeric tolerances changed.

Exploratory raw QEM reduction was not labeled automatically as a positive match:
on these thin/open source files it could close holes, change topology, or fail to
reach the requested 25% face count. The positive quarter-count fixture uses an
analytic annulus with a known preserved surface. Severe destructive reductions
are outside the positive repair/remesh claim.

The quarter-count integration gate also runs in the reverse direction on both
held-out real designs: a once-subdivided surface is reduced to its original
triangulation, with the actual 4:1 face ratio asserted before retrieval and
verification acceptance. This controls surface preservation explicitly; it does
not certify arbitrary QEM simplification or count a derivative as a new design.
