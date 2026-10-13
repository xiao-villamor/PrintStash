# Mesh measurement evidence

`MetadataRead.volume_measurement` is required. Its five fields are `state`,
`unit`, `method`, `value_mm3`, and `cause`; unknown tags, missing fields and
incompatible combinations are rejected. `unit` is always `mm3`. The compatible
`volume_mm3` field equals `value_mm3` exactly, including null; neither field is
rounded for persistence or transport.

| State | Method | Value | Cause |
|---|---|---|---|
| `measured` | `mesh_surface_integral` | Finite and positive | null |
| `unavailable` | `mesh_surface_integral` | null | `not_watertight`, `inconsistent_winding`, `non_positive_integral`, `nonfinite_integral`, or `measurement_failed` |
| `not_calculated` | null | null | `enrichment_pending`, `not_applicable`, `not_requested`, `geometry_unavailable`, or `topology_not_evaluated` |
| `legacy_unassessed` | null | Finite historical scalar, or null | null |

The metadata policy first requires a watertight, consistently wound surface.
STL's independent facet vertices may be welded in a measurement copy; winding
is not repaired. Signed integrals retain negative inner-shell contributions and
require a positive aggregate. They are not Boolean unions of overlapping solids
and do not establish absence of self-intersections. Optional similarity
fingerprints keep their own policy and algorithm identity.

The library integral uses component-local coordinates to avoid loss of precision
at large translations. Face connectivity labels identify each component origin;
referenced triangles are gathered in batches of at most 4,096 faces. Trimesh's
`mass_properties` evaluates signed batch integrals, and `math.fsum` combines
those values. Explicit zero center of mass avoids irrelevant centroid division
for zero-volume partial batches. A full expanded source triangle array is not
cached. The source mesh remains unchanged; only a mesh needing vertex welding
gets a whole measurement copy.

A complete bounded STL scan or preview can publish dimensions and face counts
without evaluating topology. Its volume is `not_calculated` with
`topology_not_evaluated`. An incomplete fallback preview cannot publish sampled
bounds/counts as source measurements. A thumbnail-only request retains
`not_requested`; refusing geometry retains `geometry_unavailable`. Native
outputs cannot contain legacy provenance or the durable row causes
`enrichment_pending` and `not_applicable`.

New mesh Metadata rows start pending. G-code and DXF volume is explicitly not
applicable. Scalar-only caller/archive metadata is accepted as
`legacy_unassessed`, including finite zero and negative historical scalars;
providing that old shape never certifies a measurement. Modern evidence must
match its compatible scalar, and internal database evidence columns are not
accepted as public input. Validation precedes Artifact publication.

Bounding dimensions are independently nullable, finite, nonnegative millimetres.
Zero and tiny finite dimensions are retained exactly. Nonfinite extents derived
from finite source coordinates cause an explicit invalid-source refusal rather
than an infinite JSON number or invented zero. Unexpected integral failures
retain independent dimensions/counts and log their original exception.

## Upgrade and export

The additive migration retains finite historical volume as unassessed. Old
nonfinite volume and nonfinite/negative dimensions become null, with repair
counts logged, before adding constraints. All other facts and Metadata identity
are preserved. Downgrade cannot reconstruct those unusable historical numbers.
SQLite uses four native column additions followed by one pinned table rebuild;
PostgreSQL uses the same generated constraint contract. New rows use pending
defaults after the historical transition.

Mesh metadata recipe 9 replaces recipe 8; G-code metadata recipe 2 replaces
recipe 1. Existing published facts remain visible until a successful
replacement. Failed regeneration does not erase those independent facts; its
terminal refusal is available through the derivative endpoint. Thumbnail and
fingerprint recipes do not change for this measurement correction.

JSON export and portable archives carry the required public variant. Portable
imports also retain old scalar-only archive compatibility. CSV appends
`volume_state`, `volume_unit`, `volume_method`, and `volume_cause` after all old
columns. These cells come from public evidence; scalar volume is retained in its
existing column without rounding. Models lacking Metadata keep blank cells.
Raw database restore migrates its staged database before adoption; database
transfer requires a source already at the current schema.

## Scoped cost observations

These are fresh-process measurements of the geometry-extraction phase, not
end-to-end throughput or a performance gate. This is a small sample on a shared
host with uncontrolled scheduling. Each case has three observations;
imports and synthetic source creation precede the timed phase, and process peak
RSS includes them. The baseline is the production evaluator at commit
`7ce82e1b61ef38895c3ad99e9c415f8dc6173c09`. All candidates receive the same
synthetic source arrays and preserve them. The rejected candidate invoked the
library once per connected component; the selected candidate uses bounded
facet batches.

| Source | Production median ms / max RSS MiB | Per-component median ms / max RSS MiB | Bounded batches median ms / max RSS MiB |
|---|---|---|---|
| One 81,920-face icosphere | 145.841 / 209.61 | 210.747 / 215.24 | 210.672 / 192.21 |
| 1,000 tetrahedra, 4,000 faces | 6.562 / 162.14 | 357.582 / 162.07 | 9.106 / 163.04 |
| 10,000 tetrahedra, 40,000 faces | 84.224 / 182.45 | 3,729.807 / 176.96 | 91.829 / 173.46 |

The additional connectivity work costs about 45% in the single large-surface
observation. Batching avoids the severe Python overhead of one library call per
small solid and bounds triangle scratch to a batch. One box of dimensions
1×2×3 at a translation of 10¹⁵ mm measures 5.333333333333333 in production and
6 in both corrected candidates. Two instances at opposite translations measure
10.666666666666666 in production and 12 in the corrected candidates. These
analytic checks distinguish precision recovery from merely faster execution.
