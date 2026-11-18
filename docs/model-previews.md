# Model covers and multi-angle previews

Library cards keep one large cover. Use the arrows to browse a Model's views,
or **Model views** to open a selectable thumbnail strip. Top and Bottom are named
choices in that gallery; they are outside the horizontal orbit. Planar objects
cycle through Cover, Face, Reverse, Oblique edge, Top and Bottom with the same arrows.
There is no autoplay or pointer-position scrubbing.

Single-cover and multi-angle cards reserve the same image area and control row.
The expanded gallery grows with the screen, up to 1152 px wide, while its image
height stays within the viewport and its thumbnail strip scrolls on narrow screens.

On cards, the cover occupies the default face in the arrow cycle: Cover → 55° →
145° → 235° → Cover (or the reverse). This avoids repeating the automatic cover's
−35° angle on the first click and always lets you return to the cover. **Model
views** retains every generated image, including −35° for custom or source-image
covers. Cards without a cover cycle through all four generated orbit views.

A chosen cover can be an uploaded PNG/JPEG/WebP, a capture of the current 3D view,
or a source image. Reset to automatic restores the latest automatic thumbnail.
Chosen covers survive regeneration. Covers with no measured camera are labelled
as covers, separately from generated viewpoints.

## Fetching and access

Primary image requests start only inside the viewport preload range. A 350 ms
sustained fine-pointer hover warms the adjacent published views. Arrow
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
The view-count button stays visible beside the current angle. On pointer devices,
arrows fade in on preview hover; keyboard focus and reduced motion reveal
them immediately. Touch keeps the arrows visible. Controls use the same outlined
surfaces as the rest of the app, and the gallery retains its normal exit transition.
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

Solid profiles follow the shared cover camera profile (recipe 2), using azimuths −35°, +55°, +145°, +235° at elevation +30°, in that
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

Recipe 4 renders the views at the **Model image quality** setting in Settings →
Previews (`VAULT_MODEL_THUMBNAIL_WIDTH`): Compact 320 × 240, Standard 640 × 480
(the default) or High 1280 × 960, the same size and supersampling as the cover.
Sets from earlier recipes are 480 × 360 and update once in the background; a
later change to the setting applies to new views, and **Recreate all images**
rebuilds existing covers and views together. Four orbit views are rendered, plus
Top and Bottom when enabled. A pole that would show only the edge of a plate
standing upright renders empty and is left out of its set; an empty orbit view
fails the set. Sources follow the same limits as covers and
every other mesh path: `VAULT_MESH_MAX_LOAD_MB` (default 200) and
`VAULT_MESH_MAX_RENDER_TRIANGLES` (default 2,000,000). Raise them in your
environment to include larger files. Output is bounded at 1 MiB per image, 6 MiB
per bundle and a 32 KiB manifest. Native memory and timeout admission also apply,
so on small hosts keep `VAULT_MAX_RENDER_JOBS` low. Oversized sources receive an explicit resource-limit
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

Views have their own control, **Multi-angle Model card views** in Settings →
Background work (`VAULT_DERIVATIVES_PREVIEWS_ENABLED`). They follow covers, so
they also need mesh processing enabled.

When a mesh Job (`derivatives.mesh`) has a cover or metadata to produce and the
views are owed, it claims them in the same transaction and renders them in the
same worker, right after publishing the cover, from the mesh it already loaded.
The cover is never delayed by its own views, and views run on every native slot
like covers do. If that pass ends before its views (a crash, a cancellation, a
superseded attempt), the claim is handed back untouched or recorded as a
retryable failure, so the views do not wait out the one-hour stale window. A
replayed mesh Job finishes views its previous execution stranded.

`derivatives.previews` remains an independent, durable derivative Job and Work
Source for views no cover pass owes: existing libraries, a newly enabled
setting, and retries. It uses backfill priority, one queued/running batch at a
time, and requires a terminal primary-thumbnail attempt. Active mesh/G-code primary work gets a two-minute grace period. That
grace is persisted and does not move when fresh imports arrive; afterwards one
bounded secondary batch can progress. Running native work is not preempted.
Cancellation checkpoints and progress updates occur between images. Existing
libraries use the same bounded discovery cursor, without removing their covers.

Either way the mesh is loaded/composed once and welded geometry/normals are
prepared once for every view of the set; both paths share one renderer and
publish identical bundles. Each view still pays projection, rasterization and
encoding, roughly in proportion to its pixel count.
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

## Generation preferences and progress

Administrators can turn **Multi-angle previews** on or off in **Settings →
Previews → Model preview images**. This is the same vault-wide control as
Background work. Turning it off saves extra rendering work; ordinary cover
images continue, existing views remain available, and an in-flight job may
finish. Turning it on fills missing or outdated view sets in the background.

The **Preparing previews** task shows total, done, remaining, skipped and failed
counts for enabled image producers across the live library, including work not
yet admitted to the queue. One preview task produces one cover image or one
multi-angle set for an Artifact. Counts follow the current recipe and reset
for requested regeneration; they are not estimates from queue size. Retryable
failures remain pending until they succeed or exhaust their attempts.
