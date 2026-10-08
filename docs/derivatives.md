# Derivatives

A **derivative** is a pure function of one Artifact's bytes and a **recipe**:
its metadata (geometry, or slicer facts), its thumbnail, and for binary G-code
its converted toolpath. Uploading commits a bare Artifact; derivatives are
produced afterwards by Jobs, so an upload never waits on a renderer and a
renderer crash never loses an upload.

## Data model

`artifact_derivatives` holds one row per Artifact, kind and recipe version:

| Column | Meaning |
| --- | --- |
| `file_id`, `kind`, `recipe_version` | Unique together; a row at an older recipe does not count |
| `state` | `running`, `ready`, `skipped`, `failed` or `cancelled` |
| `attempts`, `next_attempt_at`, `failure_reason` | Retry bookkeeping |
| `attempt_token` | Unique publication authority while the attempt is running |
| `storage_key`, `output_json` | Where the output lives and a small summary |
| `duration_ms`, `peak_rss_bytes` | What producing it cost |

The outputs themselves stay with their owners: geometry and slicer facts in
`metadata`, the thumbnail as a blob pointed at by `File.thumbnail_path`, the
toolpath as a blob under `_derivatives/`. No row means **pending**: nothing has
been attempted at the current recipe, so every value the kind supplies is
unknown. Search, filters, sorting and similarity treat an unknown value as
unknown, never as zero.

A kind is satisfied for now when it is `ready` or `skipped` (until an
administrator regenerates it), `cancelled`, `failed` while waiting out its
backoff (`DERIVATIVE_BACKOFF_SECONDS`, doubling, capped at a day), `failed`
after `DERIVATIVE_MAX_ATTEMPTS` or with a deterministic cause (bytes that can
never render), or `running` unless a lost execution left it there too long.

## Why kinds are pulled

Earlier releases pushed enrichment from the upload path, so every new kind or
renderer change needed backfill code and a startup `UPDATE`, and an Artifact
whose push was lost stayed without a thumbnail forever. Now each producer
group's source (`derivatives.source.DerivativeSource`) asks the database which
Artifacts are missing a kind at its current recipe:

- **bounded candidate windows** followed by anti-joins against satisfying
  derivatives and active Jobs, so active subjects cannot hide eligible results;
- a recent timestamp index with a newest head and a durable ascending keyset
  for interactive uploads, plus a frozen ID rotation for older backfill;
- separate admission allowances within each shared lane: up to its concurrency
  in queued interactive Jobs, plus one active backfill Job. A durable turn
  alternates when both priorities compete for a one-item discovery budget.

Each candidate window examines at most 5,000 Artifacts, independent of library
size. New uploads cannot extend an old rotation indefinitely, and a full
backfill allowance preserves its position until a slot becomes available.
Viewer requests use their request timestamp, including requests for old
Artifacts. See [background work](architecture/background-work.md) for queue
ordering and physical resource admission.

A new kind, a recipe bump or "regenerate all" therefore needs no migration
code: the anti-join starts matching again and the reconciler works through the
library at backfill priority while uploads keep interactive priority.

## Producer groups

| Definition | Lane | Kinds | Produced from |
| --- | --- | --- | --- |
| `derivatives.mesh` | `derive.native` | `metadata` (geometry), `thumbnail` | One bounded source preparation; 3MF resources and placements remain retained until topology or fingerprints need materialization |
| `derivatives.gcode` | `derive.light` | `metadata` (slicer facts, material requirements), `thumbnail` | One header read; no embedded image means `skipped` |
| `derivatives.toolpath` | `derive.native` | `toolpath` | The binary G-code converter, under resource limits |

A producer derives only the kinds still owed, records every outcome on the
kind's row, and tells viewers of the Model on `model:<id>` so an open page
refreshes when a thumbnail lands.

## Mesh rendering

A visual pass owns one immutable render preparation: referenced relative
positions, welded identities and smooth normals. The preview and orthographic
views reuse it; embeddings and view fingerprints consume final pixels without
PNG serialization. Stored previews retain the existing PNG/WebP encoding and
recipe identities. See [the preparation contract](shared-visual-preparation.md)
for memory bounds, RGB policies and compatibility evidence.

The software renderer subtracts the mesh's bounding-box center in float64 before
converting relative coordinates to float32 for camera projection and shading.
Small geometry far from the origin therefore retains the precision provided by
3MF coordinates and transforms or other float64 sources. This does not restore
detail already lost when binary STL coordinates were written as float32. Source
Artifact coordinates and physical metadata are unchanged by rendering.

Only vertices referenced by triangle faces participate in framing, camera
selection and normal welding. The renderer compacts that surface in a private
view and remaps faces inside each existing chunk; unused source vertices remain
unchanged. Face indices must refer to the source vertex array.

Referenced-relative rendering was introduced with mesh thumbnail recipe 4 and
view-descriptor recipe 2. The current mesh recipe identities are listed below.
Search visual recipe 3
and the derived embedding-space rasterizer token `referenced-relative-f64-v2`
invalidate earlier rendered inputs and vectors. Encoder asset manifests and
their digests are unchanged.
Native encoder alignment has its own stable `encoder_space()` identity. Point
exports and search visual profiles use that identity to pair image/text towers;
a renderer update never requires rewriting preplaced Point manifests. Legacy
mesh-view inference uses `space()`, whose identity includes the rasterizer.
Thumbnail and multiview search vectors additionally carry their `VisualRecipe`,
so changed rendered inputs cannot reuse older derived vectors. Rebuild a search
generation after its rendering recipe changes; an incompatible generation is
not silently relabelled or reused.

Historical verifier calibration remains tied to its original fingerprint and
verification versions; it is not relabelled as a new measurement. Fingerprint
preparation already compacts referenced vertices, so unused-vertex rendering
does not change its contents, descriptor recipe or algorithm identity.

## Mesh volume measurements

Mesh metadata publishes a volume only for a closed surface with consistent
triangle winding and a finite, positive signed volume. STL facet vertices are
welded in a measurement copy; this does not repair winding or change the source
Artifact. Open or inconsistently wound surfaces retain their bounding dimensions
and triangle counts, with unknown (`null`) volume. A globally reversed surface
also retains unknown metadata volume under the positive-orientation policy.
Fingerprint extraction results use the closed `FingerprintResultState` set: `ready`,
`partial`, `failed`, and `unsupported`. Worker frames and persisted rows retain
these literal strings; an unknown worker state is rejected as a malformed reply.
`pending` belongs to the persistence lease lifecycle, not an extraction result.

Similarity fingerprints have a separate established policy: they report the
magnitude for consistently wound closed surfaces, including a global reversal,
and retain `volume_reason = inconsistent_winding` when winding is inconsistent.

These topology checks do not establish that a surface has no self-intersections,
and the integral is not a Boolean union of overlapping solids. STL coordinates
are assumed to be millimetres. Metadata recipe 4 recalculates existing measurements
to remove volumes previously published for inconsistently wound surfaces; the
fingerprint algorithm is unchanged.

## Optional fingerprint budgets

Similarity's triangle cap admits fingerprint analysis using the actual loaded
face count, including expanded 3MF instances. It does not reduce the separate
mesh load or rendering limits. A mesh admitted for measurements and preview
therefore retains those outputs even when its fingerprint is refused with
`geometry_work_limit`. Sources exceeding the mesh load limit still use the
existing bounded strategies, including explicitly partial STL fingerprints.

Fingerprint results contain serialized descriptors. Prepared scene buffers are
released after analysis, and a failed full STL render releases its loaded mesh
before the streaming strategy starts. Sample preparation has its own scope so
construction errors also release temporary arrays before recovery begins.

Metadata recipe 7 and thumbnail recipe 5 recover terminal resource-limit
refusals produced by the earlier coupled admission policy. The derivative source
finds missing current-recipe outputs, including when an earlier fingerprint
failure remains cached. Successful content algorithms and the fingerprint
algorithm version stay unchanged.
Mesh thumbnail recipe 6 refreshes streamed previews whose valid oblique facets
were discarded by the former degeneracy filter. Collinear and repeated facets
retain the same rejection threshold.

## Retained 3MF measurements and previews

The bounded 3MF reader preserves each reachable resource once, with explicit
instance transforms. Dimensions and triangle counts are measured from referenced
placed surfaces in bounded point chunks, without allocating a whole placed mesh.
Closed indexed resources use the existing component-local signed volume integral;
negative cavity contributions survive until the aggregate is evaluated. This
preserves the established additive policy for overlapping closed solids, rather
than computing a Boolean union.

Resources that need global welding produce an explicit topology decision. The
engine attempts whole-scene materialization once within the load budget. If that
evaluation cannot run within its budget, exact dimensions and counts remain
available with volume not calculated and cause `topology_not_evaluated`.
Unexpected failures confined to volume calculation retain the dimensions/counts
with `measurement_failed`.
Optional fingerprint admission uses the placed face count and can refuse analysis
without withdrawing those measurements or a usable preview.

Rendering consumes the retained resources after any topology/fingerprint mesh is
released. Relative float32 positions and global normal arrays still scale with
expanded referenced vertices; face traversal is repeatable and chunked. This
avoids retaining a full float64 mesh and a full face buffer merely to produce the
preview, but is not zero-copy instancing.

| Current output identity | Version |
| --- | --- |
| Mesh metadata recipe | 12 |
| Mesh thumbnail recipe | 12 |
| Fingerprint interpretation | `geometry-v7-sh5f4577c4` |
| Viewer STL recipe | 2 (unchanged) |

The mesh recipes refresh prior measurements/previews under the retained-scene
eligibility and budget policy. The fingerprint version invalidates earlier
eligibility receipts; descriptor mathematics, SH basis and verifier calibration
are unchanged. Historical measured evidence and human review decisions are not
relabelled. Viewer conversion continues to require a materialized STL output.
The [retained-scene contract](retained-3mf-scenes.md) records the behavior and
verification evidence.

## Bounded STL measurements

Mesh metadata recipe 6 refreshes existing measurements. An STL that exceeds the
full mesh/render cap can still supply exact bounding
coordinates and facet count to a metadata-only request. The scanner reads bounded
binary or ASCII blocks independently of thumbnail rendering, preserves ASCII
coordinates in float64, and accepts measurements only after a complete read of a
stable source. It does not validate closed topology, so volume remains unknown.

Binary STL requires exactly its declared records; trailing bytes, truncation and
non-finite vertices are rejected. ASCII accepts complete facets without an
`endsolid` line, ignores blank/comment lines, and rejects incomplete facets or
content after `endsolid`. Byte, facet, line and line-length limits still apply;
read errors, changed sources and budget refusals publish no complete measurements.
The preview worker shares this parser. Streaming camera bounds cover every
validated source facet, including small components far from a dense main part;
percentile framing and spatial clipping no longer remove source geometry. ASCII
coordinates remain float64 through centering and projection, then bounded screen
coordinates and scaled depth convert to float32 for rasterization. Finite values
near the float32 limit therefore remain renderable after rotation. Raster work
budgets still determine whether a preview is complete.

Mesh metadata recipe 8 and thumbnail recipe 7 refresh measurements and previews
from the former streaming camera policy. Fallback sampling and full mesh loading now share the block iterator described
below.

## Shared STL source validation

STL scanning, retained fallback sampling and full mesh materialization share one
bounded binary/ASCII reader. The reader validates all source facet coordinates
through EOF before certifying completion. A successful sample reports exact
source bounds and facet count even when its retained representation is partial.
`source_complete` certifies that source read; `complete` additionally requires
all source facets to be retained and the raster budget to complete. Neither flag
certifies closed topology. Binary stored normals are ignored because normals
are derived from vertices; ASCII normal tokens retain the finite syntax rule.

Sources are pinned by device, inode, size, modification time and change time
across scan/materialization/render passes. A replaced source is refused even if
its size and modification time are restored. The canonical reader and sampler
raise distinct invalid-source, resource-limit and source-changed failures;
legacy preview/analysis adapters retain their existing refusal result until their
outcome contracts migrate together.

Retained fallback facets use fixed vectorized index priorities and source order,
with the first and last facet retained when the cap permits. The subset is
independent of block size and binary/ASCII encoding. Unlike selective binary
record seeks, source validation reads the full bounded source even for a tiny
sample; this additional work detects malformed facets outside the retained set.
Binary materialization needs one full pass after its header probe. ASCII scans
for exact allocation size before a second full pass; both share one deadline
and source snapshot. Preparation/pass reuse is a separate concern. Source
validation is bounded by 1 GiB by default; it is not a timing improvement.

Fingerprint interpretation version `geometry-v6-sh5f4577c4` invalidates eligibility
receipts from the former partial sampling policy. Historical descriptor evidence
and human review decisions remain retained; descriptor math, the SH basis and
verifier calibration are unchanged. Metadata recipe 11 and thumbnail recipe 10
refresh affected outputs; this sampling change leaves the viewer STL recipe
unchanged. Viewer conversion uses recipe 3 as documented below. The earlier 3MF capability
policy and independent embedded previews remain in force. The
[STL reader contract](stl-reader.md) specifies completion, sampling and refusals.

## Measurement precision

Mesh metadata recipe 5 stores dimensions and valid volume without rounding them
to two decimal places. Values remain in millimeters and cubic millimeters;
display formatting is a consumer concern. This preserves small parts and the
precision available from each source, including bounds obtained by complete STL
streaming and fallback scans. Existing rounded metadata is eligible for backfill.
Binary STL still carries float32 coordinates; removing output rounding cannot
recover precision already absent from the input. Volume retains the closure,
winding, finite-value and positive-orientation requirements of recipe 4.

## Measurement evidence availability

Mesh metadata recipe 9 publishes required volume provenance independently of
optional similarity fingerprints. Complete bounded STL scans retain dimensions
and counts with explicitly unassessed topology. G-code metadata recipe 2 marks
mesh volume as not applicable. The additive migration preserves finite historical
scalars as unassessed, and replaces unusable nonfinite volume or nonphysical
dimensions with unknown values before enforcing the new constraints.

The public variant, scalar compatibility, signed component-local integral,
upgrade behavior, CSV additions and exact test matrix are described in
[mesh measurement evidence](mesh-measurements.md). Thumbnail and fingerprint
recipes retain their existing identities for this measurement change.

## Bumping a recipe

The recipe constants in `app/modules/derivatives/kinds.py` are the code's
statement that a kind's output would now differ for the same bytes. Increase
one, by hand, in the change that alters what its producer emits: a new
renderer, a parser that reads a field it used to miss, a different encoding.
Do not bump for a refactor that cannot change any output. Every Artifact is
re-derived in the background, and its old output stays visible until the
replacement is ready.

## Adding a kind

1. Add the kind and its recipe constant to `kinds.py`, in the group whose
   producer can compute it from the bytes it already reads (or a new group with
   its own definition and lane).
2. Produce it in `producers.py`, recording `ready`, `skipped` or `failed`
   through `records`; decide which failures are deterministic.
3. Store the output with its owner, never in `artifact_derivatives`.
4. Treat it as unknown until ready wherever it is read.
5. Test the source (`pending` finds it), the producer (each outcome), the Job
   (convergence after commit) and the consumer's unknown case.

## Operating

- **Per Artifact:** the Model's Files tab shows what is still being prepared
  and what failed; an editor can retry a failure
  (`POST /api/v1/files/{id}/derivatives/{kind}/retry`).
- **Library-wide:** Settings → Background work offers, per kind, *Derive
  missing* (nudge only) and *Regenerate all* (every Artifact again, current
  outputs kept until replaced).
- **Audit repair:** a vault audit that finds a thumbnail missing from storage
  invalidates and re-derives it (`derivatives.repair`).

## Mesh geometry outcomes

Mesh replies use the request/result types and tagged geometry codec owned by
`modules/media/mesh_contracts.py`, independently from thumbnail strategy
selection. Replies carry a geometry outcome independently from thumbnail status.
A validated embedded preview can remain ready when geometry is refused. Refused
geometry records a failed metadata derivative, with terminal resource/malformed
input reasons; it does not publish a successful all-unknown measurement row.
Ready geometry can still have an unknown volume for an open mesh.

Metadata recipe 3 replaces the earlier output semantics. The ordinary bounded
Work Source backfills it. Terminal attempts stay exhausted across scans, nudges
and restarts for the same bytes and recipe. Explicit retry, changed content or a
new recipe makes work eligible; timeout backoff keeps the configured maximum.
Original downloads and signed slicer downloads continue to use Artifact bytes.


## 3MF required capabilities

The [3MF capability policy](3mf-capabilities.md) applies to reached Core model
parts and the supported Production external-reference subset. Unknown required
namespaces produce `unsupported_capability` metadata/viewer refusals and
`unsupported_3mf_capability` fingerprint refusals. No incomplete geometry is
published as successful. Original downloads remain usable, and a validated
embedded preview can independently become ready. Mesh metadata recipe 10,
thumbnail recipe 9 and viewer STL recipe 2 refresh outputs under this policy;
the fingerprint mathematical algorithm is unchanged. Its interpretation cache
version advances to `geometry-v5-sh5f4577c4`, so earlier candidate evidence remains
historical rather than actionable as a current interpretation.

## On-demand 3D viewer STL

`viewer_stl` recipe 3 is produced by `derivatives.viewer_stl` in `derive.native`.
Only 3MF, OBJ and STEP Artifacts with `files.viewer_requested_at` set are eligible;
uploads, scans and card hover do not request conversion. The first authorized
`GET /api/v1/files/{id}/stl` (or the scoped share endpoint) persists this demand
and nudges the source. Reconciliation recovers it after a lost nudge or restart.
The active-Subject constraint shares work across requests.

The caller's request Session owns demand; its queries finish before commit and
before the work hint. Viewer requests do not open a nested SQL Session.

Conversion writes a staged file in the admitted preparation workspace. A fixed
44-byte manifest reports its size and SHA256; the parent verifies an open regular
file descriptor and publishes that same stream through `StorageBackend`.
It does not load the whole STL into a parent byte buffer. Source preparation and
disk reservations include the 256 MiB output ceiling independently of compressed
input size, and remain held through publication. Deployments with a preparation
quota below that bound must raise it to admit viewer conversion. The native
permit is released after supervised exit and descendant cleanup, before storage
publication; prepared bytes and disk reservations retain their separate lifetime. Source/output
identity, execution authority and the published size are checked before READY;
a rejected output retains pending ownership for existing orphan recovery.
Converted output must have a nonzero binary STL facet count and exact framing.
The worker checks face batches before export, refusing nonfinite float32
coordinates or a nondegenerate face that would collapse after packing. Recipe 3
rebuilds earlier viewer outputs on demand under these stricter checks; valid
conversions and original STL bytes retain their coordinate and byte semantics.

Original STL is served directly. Other formats return 202 with `DerivativeRead`
and `Retry-After: 1` until publication, 200 with the stored representation when
ready, or 422 with the recorded failure `detail`. Preparation responses use
`private, no-store`. Resource and invalid-input refusals are terminal for the
recipe; timeouts/storage failures use bounded derivative backoff. Explicit retry
uses the existing derivative retry endpoint. Mesh processing policy gates new
work; published previews remain readable while disabled.

Ready STL objects use immutable owned publication and `storage_key`, so backup,
Vault migration and trash retain their existing ownership contracts. A missing
published object becomes eligible for repair. Legacy STL caches are regenerated
once on access. Original downloads and signed slicer handoff never depend on STL
preparation. The browser waits for STL bytes, displays persisted failures, and
refreshes authenticated previews after derivative completion or policy changes.

## Live processing policy

`modules/derivatives/policy.py` owns the three groups' database overrides and
frozen deployment defaults. Discovery, admission, manual actions and status
projection all consult it; process-local settings overlays do not decide live
policy. Definitions remain registered while disabled so historical Jobs remain
inspectable. `disabled` is a read projection, never a stored derivative state.

Admission locks the configuration singleton using a SQLite write transaction
or PostgreSQL row lock, then opens the derivative attempt in that transaction.
An admitted producer may finish after disablement. The producer repeats this
check on actual execution, including recovery replay; cached engine checkpoints
never grant permission for a new attempt.

The reconciler cancels queued, delayed, interrupted and retrying Jobs with
`derivative_group_disabled`. It preserves healthy execution and settles orphaned
in-flight derivative rows using normal failure/backoff rules. Policy cancellation
does not invoke the user cancellation hook or satisfy a missing derivative. It
does not count towards repeated-submission cooldown.

New regenerate-all markers are scoped to enabled producer definitions in
`derivative_group_regenerations`; earlier kind-wide markers remain readable.
This prevents a thumbnail regeneration from invalidating a disabled group's
outputs on later re-enablement. Nothing changes the recipes.

Administrator config updates accept a Boolean to save an override, explicit
`null` to inherit the environment, and omission to preserve it. Manual retries
and repairs return 409 `derivative_group_disabled` before changing rows.
Automatic audit repair leaves disabled findings unrepaired.

Stored thumbnails and metadata remain available. Binary toolpaths serve the
latest ready published output, including a prior recipe; missing outputs return
409 while disabled. The viewer stops polling until a policy notice or resync.
After commit, the event publisher emits a payload-free `derivative_policy`
notice on the authenticated `derivatives:policy` channel. Views refetch through
their authorized endpoints. Periodic reconciliation recovers lost notices or
nudges.


## Attempt publication

Every producer captures an immutable attempt token with its Artifact source,
recipe and regeneration marker. Completion checks that authority inside the
same transaction that publishes metadata, material requirements or thumbnail
pointers. Cancellation, retry, regeneration, trash and source replacement cannot
be overwritten by a late result, including a late failure or skipped outcome.
Job executions also retain their Job attempt identity; stale progress and engine
settlement cannot change a retry or run its failure and staging cleanup hooks.

Publication locks the Job (when present), generation policy, Model, Artifact and
derivative in that order. PostgreSQL publishers share the generation policy lock;
regeneration takes it exclusively. SQLite reserves its writer before reading.
These short transactions contain no renderer, parser or storage publication I/O.
Byte outputs are created first under an attempt-specific immutable key with the
normal durable ownership receipt. Rejection rolls back the domain pointers and
ownership promotion; the exact pending receipt remains for orphan reconciliation.
An uncertain commit never authorizes deleting an output by pathname.

A regeneration makes an older in-flight derivative discoverable immediately. The
existing active-Job uniqueness rule still prevents overlapping Jobs for the same
producer and Artifact; after the old execution settles, discovery starts the new
attempt. Earlier kinds that already committed still notify Model viewers if a
later kind is superseded. Fingerprints carry the original source hash into their
separate content-and-algorithm-versioned publication contract.

The attempt-token migration preserves existing outputs and retry history. Legacy
running attempts cannot prove ownership, so they become retryable interrupted
attempts without charging that interrupted attempt against their retry limit.

The optional similarity cache is keyed by source SHA and algorithm version.
A retired execution may supply that immutable evidence, but cannot overwrite a
ready entry or relabel an old result with the current Artifact's SHA. Creating
a similarity run additionally requires the originating Job's current epoch and
attempt under the Job lock; cancellation/retry therefore creates no new work.

## Convex hull descriptor

The mesh dependency uses SciPy/Qhull for convex hull volume. The application
passes finite three-dimensional arrays through one array-to-scalar owner;
SciPy objects do not cross that boundary. Translation and scale normalization
precede the native calculation, and no coordinate perturbation is enabled.
Coplanar or degenerate input has an explicit unavailable descriptor. Nonfinite
input and an unrepresentable physical volume are rejected.

The input ceiling is `MAX_ANALYSIS_VERTICES` (6,000,000 points), before
preparation allocations. The old Python point/plane `max_work` counter is
removed; `max_points` bounds inputs, while the native worker supervisor enforces
wall-clock and RSS limits. The point ceiling alone is not a memory guarantee.

Fingerprint algorithm `geometry-v4-sh5f4577c4` separates new hull values and
newly available descriptors from the former Python hull recipe. Existing
fingerprints are recalculated without rewriting historical records or verifier
calibration. Mesh measurements and thumbnail recipes are unchanged.

Mesh metadata and thumbnails publish before optional fingerprints. Pending analysis remains durable after basic outputs commit; see [staged mesh outputs](staged-mesh-outputs.md) for framing, recovery and publication fences.

### Compact thumbnail encoding

Mesh thumbnail recipe 12 and G-code thumbnail recipe 2 encode WebP color at
quality 90, method 4, with lossless alpha. The configured dimensions, framing and source
Artifacts are preserved. Native rendering and imported previews share one encoder;
only validated bytes explicitly originating from the current renderer skip a
second encode. Existing derivatives remain visible while normal derivative Jobs
backfill the new recipes. Visual embedding identities include the preview profile
fingerprint, so changed encoded inputs do not reuse an older visual recipe.
