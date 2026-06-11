# PrintStash Vault

Self-hosted 3D printing asset manager: a vault of logical Models backed by
versioned file artifacts, with slicer metadata, printer presence, and trash
retention. This file pins the project's domain language; architecture
decisions live in `docs/adr/`.

## Language

### Library

**Model**:
A logical asset deduplicated by source-mesh sha256; owns versioned Files.
_Avoid_: asset, item, part

**Artifact** (File):
One physical stored blob (STL/3MF/OBJ/G-code) at a version under a Model.
_Avoid_: upload, attachment

**Artifact persistence**:
The invariant-heavy sequence `version → canonical move → File row →
thumbnail → Metadata`, owned solely by `services/ingestion.persist_artifact`.
Both background ingestion and revision attachment call it; nothing else
re-implements it.

**Revision**:
A G-code Artifact with test-outcome bookkeeping (label, status, notes,
recommended marker). Revision numbers are derived from version order, never
stored. A Model with G-code always has exactly one recommended revision:
the first upload claims the marker (enforced in artifact persistence),
and marking another clears it from the rest.

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
soft-delete → restore → expiry → hard delete (rows + blobs) → orphan-blob
GC; owned solely by `services/trash` (including the hourly GC loop).

### Storage

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
