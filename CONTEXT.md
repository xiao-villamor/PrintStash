# PrintStash Vault

Self-hosted 3D printing asset manager: a vault of logical Models backed by
versioned file artifacts, with slicer metadata, printer presence, and trash
retention. This file pins the project's domain language.

## Language

### Library

**Model**:
A printable logical asset deduplicated by source-mesh sha256; owns versioned
Files and remains independently addressable and reusable even when referenced
by Multipart Models. The Organized library view may group a referenced Model
under its Multipart Model instead of duplicating both cards at the same level.
_Avoid_: asset, item, part

**Artifact** (File):
One physical stored blob (STL/3MF/OBJ/G-code) at a version under a Model.
_Avoid_: upload, attachment

**Multipart Model**:
An independent library grouping that describes one object made from several
printable Models. It references Models without moving or owning them and has
its own description, collection, cover, guides and tags. A cover may be a
private image uploaded to managed storage or a custom external image URL;
otherwise the UI falls back to a member Model thumbnail. Its tags do not
propagate to member Models, and member tags do not propagate to the grouping.
_Avoid_: assembly Model, merged Model, collection

**Multipart Part**:
A named physical role within a Multipart Model, such as “base”, “handle” or
“lid”. It has one Model when fixed and several Model Choices when alternatives
are valid.
_Avoid_: Part Group, folder, variant group

**Model Choice**:
One existing Model that can satisfy a Multipart Part. A Model can be referenced
by any number of Multipart Models and keeps its own Artifacts, preview, G-code
Revisions and print history.
_Avoid_: Part Option, source file option, revision

**Multipart Guide**:
A Document linked to one Multipart Model for assembly or printing guidance.
Guides may be Markdown, PDF or raster images, remain visible in Documents, and
survive deletion of the grouping as ordinary Documents.
_Avoid_: Model file, Artifact, attachment

Multipart Models and ordinary Models share one library. Organized is the
default presentation: it shows each Multipart Model once and suppresses
duplicate top-level cards for the Models it references. Everything shows both,
Multipart sets only shows groupings, and Parts only shows referenced Models.
Search always reveals a matching Model, including in Organized. This is a
presentation rule, not ownership: adding a piece or alternative never transfers
or duplicates that Model's Files, and removing a piece or deleting the grouping
only removes the reference. The Model and all of its Artifacts and Revisions
remain addressable from search, Everything, Parts only and any other Multipart
Model that reuses it. Multipart Parts have an explicit order; every part is
required, while its Model Choices are alternatives where exactly one is
selected for a build.

**Artifact persistence**:
The invariant-heavy sequence `version → canonical publication → File row +
Metadata + committed ownership`, owned solely by
`services/ingestion.persist_artifact`. That primary boundary is atomic: once
the database commit begins, uncertain outcomes preserve the published bytes and
their ownership evidence for reconciliation rather than deleting them.
Thumbnails are retryable derivatives published after the primary transaction;
their failure never invalidates an otherwise complete Artifact. Both background
ingestion and revision attachment call this service; nothing else re-implements
it.

**Revision**:
A G-code Artifact with test-outcome bookkeeping (label, status, notes,
recommended marker). Revision numbers are derived from version order, never
stored. A Model with at least one live G-code revision has exactly one
recommended revision; a Model with no live G-code — none uploaded, or every
revision deleted — has none, and the recommended marker is null. The first
upload claims the marker (enforced in artifact persistence), marking another
clears it from the rest, and deleting the recommended revision promotes the
newest surviving revision (or leaves none when it was the last).

**Model views**:
The read-model module (`services/model_views`) — single owner of every
Model → response-schema composition (browse list, detail, export, trash
list, vault stats). Routers never hand-map Model rows.
_Avoid_: serializers, read builders scattered in routers

### Trash

**Live**:
A row not in the trash. Expressed in queries only via the
`app.db.scopes.live()` predicate.
_Avoid_: hand-written `deleted_at.is_(None)`

**Trashed**:
A soft-deleted row awaiting retention expiry; query via
`app.db.scopes.trashed()`.
_Avoid_: deleted (ambiguous with hard delete)

**Trash lifecycle**:
soft-delete → restore or expiry → GC preview → explicit approval → quarantine →
revalidated hard delete. `services/trash` owns individual transitions and
`services/gc_planner` owns automatic expiry. Automatic GC never approves its
own plan. It requires a recent, verified backup on an independent S3 provider,
an unchanged candidate digest, Verified active storage, and a completed
quarantine interval. PrintStash never walks configured storage and deletes
files merely because no database row claims them; failed writes clean up only
their exact destinations at the write site.

**GC plan**:
A durable, bounded and immutable preview of expired catalog rows and their
explicitly owned storage keys. At most one plan is active. Approval binds its
exact digest to a backup witness; finalization rechecks the candidate rows,
restore generation, provider identity, backup and quarantine deadline.

**Backup witness**:
The exact archive id, source reference, provider identity and digest of a fully
verified, application-compatible S3 backup created in the previous 24 hours on
a provider different from active Vault storage.

### Storage

**Storage capability tier**:
The runtime-probed safety guarantee of the active storage backend: Verified,
Guarded, or Unguarded. Tiers are derived from capability axes, never from a
provider label. Destructive storage behavior consults the probed tier.

**Storage provider**:
A stable catalogue entry selected by ID (for example `local`, `s3`, or
`nextcloud`). It supplies typed setup fields and resolves to a native or remote
transport; provider identity and transport kind are not interchangeable.

**Ownership intent**:
An `owned_storage_objects` reservation created before bytes are published.
PENDING intents are reclaimable after the grace period, COMMITTED intents are
authoritative ownership records, and BLOCKED intents require operator review.

**Storage key**:
Opaque identifier for a stored blob — an absolute path (local backend) or
an object key (S3 backend). Callers never branch on which.

**Direct path**:
The on-disk `Path` a backend can expose for a key (`direct_path()`), or
None for remote backends. HTTP handlers use it to pick FileResponse vs
streaming.

**Local path**:
`local_path(key)` context manager: the real path locally, a self-cleaning
temp download remotely. The only sanctioned way to feed a stored blob to
code that needs a filesystem path (mesh loading, tar, restore).

**Artifact content**:
`services/artifact_content` is the only read seam for an Artifact's bytes. It
resolves managed storage, descriptor-pinned mounted files, and read-only remote
sources without asking callers to understand `File.path`. Mounted reads reject
symlinks and changes between open, hash and close. Remote reads verify stable
object metadata around materialization.

**Thumbnail**:
A WebP preview stored under `thumbnail_key()` (`{file_id}.webp`);
`thumbnail.to_webp()` is the single conversion seam every write goes
through. Pre-WebP installs left PNGs under `legacy_thumbnail_key()` —
read/delete only, never written.

### Library sources

**External library**:
A user-managed mounted folder or read-only S3, WebDAV, or SFTP namespace that
PrintStash indexes in place. The source remains authoritative; only generated
thumbnails and metadata are stored by the Vault. Opt-in and OFF by default
(`SystemConfig.external_libraries_enabled`). Owned by
`services/external_library`. The UI calls these **Library sources**.

**Storage connection**:
A reusable remote-source profile. Non-secret configuration is stored separately
from encrypted credentials, and API reads never return secret values. A
connection can serve more than one library source.

**Linked file**:
An Artifact with `File.is_external = true`: its bytes live on a library source,
not in managed Vault storage. `File.source_key` is the stable source-relative
identity. `File.path` is an absolute display path for mounted sources and an
opaque `source://` display URI for remote sources. PrintStash never deletes a
linked file's bytes; trash and GC skip them.
_Avoid_: "imported file" (that means a vault-owned copy).

**Discovery epoch**:
A complete, restart-safe reconciliation of one library source. Mounted sources
use a descriptor-pinned filesystem snapshot. Remote sources page through a
durable cursor in bounded slices; absence is interpreted only after the full
epoch completes. Empty or unexpectedly large removal sets fail closed. A
weekly rotating hash check catches changes that preserve size and mtime.

**Discovery tombstone**:
A durable `(library, source_key)` suppression created when a linked Artifact is
trashed. It prevents the still-present source object from being re-imported on
the next scan. Restore or explicit Rediscover clears it.

**Write-back**:
Create-only web uploads or revisions routed into a mounted library folder
instead of Vault storage (`ingestion.resolve_write_target`). It is disabled for
S3, WebDAV, and SFTP library sources. Mounted write-back only adds files and
never overwrites existing bytes.

## Flagged ambiguities

- **"Model"** also names ORM classes (`db/models.py`) and printer hardware
  models (`Metadata.printer_model`). In conversation, unqualified "Model"
  means the library asset.
- **"Backend"** means the StorageBackend adapter in storage discussions,
  and the FastAPI app in deployment discussions. Prefer "storage backend"
  / "API server".

## Example dialogue

> **Dev:** Upload finished but the thumbnail rule looks wrong for revisions.
> **Expert:** Revision attachment never overwrites the Model's thumbnail —
> that rule lives in artifact persistence, so fix it there and both ingest
> paths get it.
> **Dev:** And the trash page shows a model the browse list also shows?
> **Expert:** Then a query is missing the live scope — grep for a list
> query not using `live(Model)`; nothing should write that predicate by
> hand.
