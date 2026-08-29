# PrintStash Vault

Self-hosted 3D printing asset manager: a vault of logical Models backed by
versioned file artifacts, with slicer metadata, printer presence, and trash
retention. This file pins the project's domain language.

## Language

### Library

**Model**:
A logical asset deduplicated by source-mesh sha256; owns versioned Files.
_Avoid_: asset, item, part

**Artifact** (File):
One physical stored blob (STL/3MF/OBJ/G-code) at a version under a Model.
_Avoid_: upload, attachment

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
soft-delete → restore → expiry → hard delete (rows + explicitly owned blobs);
owned solely by `services/trash` (including the hourly GC loop). PrintStash
never walks configured storage and deletes files merely because no database row
claims them; failed writes clean up their exact destinations at the write site.

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

**Thumbnail**:
A WebP preview stored under `thumbnail_key()` (`{file_id}.webp`);
`thumbnail.to_webp()` is the single conversion seam every write goes
through. Pre-WebP installs left PNGs under `legacy_thumbnail_key()` —
read/delete only, never written.

### External libraries (NAS folder mirroring)

**External library**:
A user-managed folder (typically on a NAS) that PrintStash mirrors *in place* —
the folder is the source of truth. Files are indexed where they live; only the
generated thumbnail + metadata are stored by the vault. Opt-in and OFF by
default (`SystemConfig.external_libraries_enabled`). Owned by
`services/external_library`.

**Linked file**:
An Artifact with `File.is_external = true`: its blob lives on an external root
(`File.path` is that absolute path), not in vault storage. PrintStash never
overwrites or deletes a linked file's bytes — trash/GC skip external blobs.
_Avoid_: "imported file" (that means a vault-owned copy).

**Mirror scan**:
`scan_library()` reconciling the index with the folder — new files indexed,
removed files trashed, changed files (size/mtime → re-hash) refreshed in place.
Safety rule: a scan **aborts without deleting** when the root is missing/
unreadable, or empty while live indexed files exist (an unmounted NAS must never
trigger mass deletion). Manual (`POST /libraries/{id}/scan`) or the periodic
`_external_scan_loop` in `main.py`.

**Write-back**:
Web uploads/revisions routed into a library folder instead of vault storage
(`ingestion.resolve_write_target`). Revisions follow their model's library
automatically; new uploads pick a target in the upload modal. Collision-safe —
write-back only *adds* files, never overwrites.

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
