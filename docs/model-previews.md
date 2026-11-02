# Model covers and multi-angle previews

Library cards keep one large cover. Use the arrows to walk around a solid Model,
or **Model views** to open a selectable thumbnail strip. Top and Bottom are named
choices in that gallery; they are outside the horizontal orbit. Planar objects
use named Face, Reverse and Oblique edge choices instead of horizontal arrows.
There is no autoplay or pointer-position scrubbing.

A chosen cover can be an uploaded PNG/JPEG/WebP, a capture of the current 3D view,
or a source image. Reset to automatic restores the latest automatic thumbnail.
Chosen covers survive regeneration. Covers with no measured camera are labelled
as covers, separately from generated viewpoints.

## Fetching and access

Primary image requests start only inside the viewport preload range. A 350 ms
sustained fine-pointer hover warms the adjacent published orbit views. Arrow
hover/focus promotes its destination; clicking fetches immediately. The current
decoded image remains visible until the latest selection has downloaded and
decoded. Failed replacements are retryable.

Touch waits for deliberate selection before warming one neighbor. Save-Data,
2G and slow-2G permit only requested views. The shared authenticated cache
deduplicates transfers, caps all image downloads at two, and admits at most one
speculative download while reserving capacity for visible/requested images.
Ordinary card hover never requests full geometry or starts server-side generation.
A deliberate expanded gallery may fetch its directly selectable thumbnails.

Native buttons have names, keyboard focus, and 44 px targets; dots are indicators,
not extra tab stops. Preview buttons do not activate, select, or drag the card.
Session changes retire private cached bytes and suppress stale decoded results.

The additive `previews` read model leaves `thumbnail_url` intact. A Model with
no live mesh Artifact reports `kind: "none"`. Otherwise `kind: "artifact"`
names the one selected Artifact, chosen by descending Revision number and
Artifact ID, so views from different Artifacts never mix. Its `state`
(pending, queued, running, ready, failed, cancelled or unsupported) is
reported separately from `published`, the last valid generation. Previous
images therefore stay available while a replacement is pending or after it
fails. Only validated published images have URLs.
Manifest/image routes enforce the same Model visibility and share-token rules as
other previews; revoked shares and trashed subjects cannot read them.

## Camera and generation contract

Solid profiles use azimuths −35°, +55°, +145°, +235° at elevation +18°, in that
wrapping order. Top/Bottom use ±90°. Their screen-up direction is imported +Y;
other generated cameras retain imported +Z as screen up where possible.
Camera matrices, orthographic projection scale/offset, source center, dimensions,
roles, IDs, ordering, recipe, source SHA-256 and image digests are recorded.

Planar classification uses the shared area-weighted surface covariance. Sorted
eigenvalues must satisfy smallest/middle < 0.04 and middle/largest >= 0.09.
This separates plates from rods and works after rotation. The broad-face normal
has a deterministic sign anchored in the imported frame; it does not claim
semantic front/back. Ambiguous shapes keep the imported-frame orbit. The oblique
edge view is 65° from the face. 3MF build/component transforms are applied before
analysis. No candidates are pruned by silhouette or presumed symmetry.

Recipe 1 produces lossless 480 × 360 WebPs: six views for solids, five for planar
objects, including Top and Bottom. Limits are 128 MiB source bytes, 500,000 faces,
1 MiB per image, 6 MiB per bundle, and a 32 KiB manifest. Native memory and timeout
admission also apply. Oversized sources receive an explicit resource-limit
outcome; optional unavailable converters are unsupported. Neither outcome
invalidates the original Artifact or its primary thumbnail. These discrete
views help recognition; they do not promise complete geometric coverage.

Every image crosses the canonical thumbnail validation seam before publication.
Images occupy contiguous ranges in one immutable, opaque StorageBackend object.
The manifest and ownership receipt commit only after every image validates.
Range-capable storage transfers just the requested image; the streaming fallback
keeps memory bounded to that image. Content length and digest are checked on read.
The ordinary derivative ownership census, backup, migration and purge paths own
the whole bundle. Previous published generations remain readable during
replacement, cancellation, and retry.

## Scheduling and reuse

`derivatives.previews` is an independent, durable derivative Job and Work Source.
It has its own control, **Multi-angle Model card views** in Settings →
Background work (`VAULT_DERIVATIVES_PREVIEWS_ENABLED`). Views follow covers,
so they also need mesh processing enabled. It uses backfill priority,
one queued/running batch at a time, and requires a terminal primary-thumbnail
attempt. Active mesh/G-code primary work gets a two-minute grace period. That
grace is persisted and does not move when fresh imports arrive; afterwards one
bounded secondary batch can progress. Running native work is not preempted.
Cancellation checkpoints and progress updates occur between images. Existing
libraries use the same bounded discovery cursor, without removing their covers.

The worker loads/composes the mesh once and prepares welded geometry/normals once
for the entire batch. Each view still pays projection, rasterization and encoding.
Remote original bytes use the existing Artifact cache; warm, cold, disabled and
evicted cases follow the normal materialization path. Linked Library sources also
use the existing descriptor-pinned source boundary.

Existing converted STL reuse was evaluated. Those representations are requested
by viewer use and commonly do not exist during initial preview backfill. They
also introduce a separate recipe and float32 serialization boundary. Selecting
original geometry on a cache miss but converted geometry on a hit would make
framing/rendered bytes depend on cache availability. Recipe 1 consistently reads
original bytes and adds no geometry cache. STEP previews do not request the
unused B-rep evidence sidecar. Future conversion reuse must first define a
canonical representation and version its identity.

## What previews do and do not establish

The views are discrete recognition aids for telling similar Models apart before
opening one. They do not show every surface and do not replace the 3D viewer
for detailed inspection. No measured human recognition-speed or accuracy
improvement is claimed. That evaluation needs people choosing between similar
candidates with rear or underside differences, and has not been run.
