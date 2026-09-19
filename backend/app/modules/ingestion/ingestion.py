"""Ingestion orchestrator — runs in a FastAPI BackgroundTask."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, ParamSpec, TypeVar

from sqlalchemy import case, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

import app.modules.media.mesh_operations as mesh_operations
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    ArtifactMaterialRequirement,
    Collection,
    CollectionRole,
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelTagLink,
    OwnedStorageObject,
    StagingLease,
    StorageObjectState,
    User,
)
from app.db.projections import content_changed
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.modules.identity import rbac
from app.modules.library import taxonomy
from app.modules.media import gcode_parser, thumbnail
from app.modules.storage import storage
from app.modules.storage.hashing import sha256_file
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import provider_ref_for_backend, publish_file
from app.runtime.jobs import registry

if TYPE_CHECKING:
    from app.modules.library.provenance import ProvenanceContext
    from app.modules.sources.contracts import SourceEntry

logger = get_logger(__name__)


ProgressFn = Callable[[str], None]
_P = ParamSpec("_P")
_R = TypeVar("_R")

# SQLite deployments are intentionally single-process, but background tasks
# can persist two Artifacts concurrently in different worker threads. Striped
# locks avoid an unbounded per-Model lock registry while keeping unrelated
# Models concurrent. The database counter below remains the cross-process/
# Postgres source of truth.
_ARTIFACT_LOCKS = tuple(threading.RLock() for _ in range(256))


def _noop_progress(_label: str) -> None:
    return None


@dataclass
class IngestionStrategy:
    """Variant step in the pipeline: parse a staged file into metadata + thumbnail.

    ``step_labels`` enumerates the labels ``process`` reports, in order, so the
    pipeline can map them onto step counters for job progress hints.
    """

    file_type: FileType
    overwrite_thumbnail: bool
    process: Callable[[Path, ProgressFn], tuple[dict[str, Any], bytes | None]]
    step_labels: tuple[str, ...]


class ArtifactDurabilityError(RuntimeError):
    """A committed artifact cannot be used from a fresh session/storage view."""


class ThumbnailDurabilityError(RuntimeError):
    """A thumbnail reported as generated is not visible in storage."""


class ArtifactCommitUncertain(RuntimeError):
    """The domain commit outcome is unknown; storage evidence was preserved."""


def _fault_injection_checkpoint(_stage: str, _job_id: str) -> None:
    """Stable monkeypatch seam for commit-boundary regression tests."""


def _resolve_committed_artifact(
    *,
    backend,
    key: str,
    model_id: int,
    blob_hash: str,
    ingestion_key: str | None,
) -> File | None:
    """Resolve a commit acknowledgement failure without touching the blob."""
    with get_session_factory().session() as verification:
        namespace = backend.namespace_for(key)
        provider_ref = provider_ref_for_backend(backend, namespace=namespace)
        provider_scope = OwnedStorageObject.provider_ref == provider_ref
        # Local receipts written before provider_ref was introduced are safe
        # to resolve only within the current backend namespace. Remote legacy
        # NULL receipts intentionally have no compatibility path: their
        # destination cannot be proven and destructive recovery must fail
        # closed.
        if backend.backend_name == "local":
            provider_scope = provider_scope | OwnedStorageObject.provider_ref.is_(None)  # type: ignore[union-attr]
        ownership = verification.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == backend.backend_name,
                OwnedStorageObject.namespace == namespace,
                provider_scope,
                OwnedStorageObject.key == key,
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
            )
        ).first()
        if ownership is None:
            return None
        statement = select(File).where(
            File.model_id == model_id,
            File.path == key,
            File.sha256 == blob_hash,
        )
        if ingestion_key is not None:
            statement = statement.where(File.ingestion_key == ingestion_key)
        candidate = verification.exec(statement).first()
        if candidate is None:
            return None
        # Return a detached copy; the caller's failed transaction must not be
        # reused after the acknowledgement boundary.
        verification.expunge(candidate)
        return candidate


def _attach_ingested_artifact(
    session: Session, file_row: File, context: ProvenanceContext
) -> None:
    """Attach provenance without taking over artifact transaction ownership.

    The import is deliberately deferred so all existing ingestion callers stay
    independent of the optional capture/provenance feature until they pass a
    context.  The provenance service must not commit here: this caller owns the
    File, metadata, storage receipts, and their rollback as one transaction.
    """
    from app.modules.library.provenance import attach_ingested_artifact

    attach_ingested_artifact(session, file_row, context)


def verify_durable_artifact(
    session_factory: SessionFactory,
    *,
    model_id: int,
    file_id: int,
    thumbnail_status: str,
) -> None:
    """Verify rows and objects from a new transaction before publishing terminal."""
    with session_factory.scoped_session() as verification_session:
        model = verification_session.get(Model, model_id)
        artifact = verification_session.get(File, file_id)
        metadata = verification_session.exec(
            select(Metadata).where(Metadata.file_id == file_id)
        ).first()
        if (
            model is None
            or artifact is None
            or artifact.model_id != model_id
            or metadata is None
        ):
            raise ArtifactDurabilityError("artifact_rows_not_durable")
        from app.modules.storage.artifact_content import resolve
        source = resolve(artifact)
        thumbnail_key = artifact.thumbnail_path

    backend = get_backend()
    if not source.exists():
        raise ArtifactDurabilityError("artifact_blob_not_durable")
    if thumbnail_status in {"generated", "fallback_generated"}:
        # New generations are immutable, recipe-versioned objects. Keep the
        # legacy address only as a read-compatible fallback for artifacts that
        # predate durable thumbnail generations.
        candidate = thumbnail_key or backend.thumbnail_key(file_id)
        if not backend.exists(candidate):
            raise ThumbnailDurabilityError("thumbnail_blob_not_durable")


def _model_exists_with_slug(session: Session, slug: str) -> bool:
    stmt = select(Model).where(Model.slug == slug)
    return session.exec(stmt).first() is not None


def _reserve_next_version(session: Session, model_id: int) -> int:
    """Atomically reserve and return one Artifact version for a Model.

    Updating the owner row serializes callers on both SQLite and Postgres. The
    increment lives in the same transaction as the File row, so a failed
    persistence rolls the reservation back together with the row.
    """
    # Self-heal counters for rows created by older integrations/tests that may
    # have inserted File rows directly. The migration backfills production
    # data, while this floor keeps the invariant true for future raw imports.
    minimum_next = (
        select(func.coalesce(func.max(File.version) + 1, 1))
        .where(File.model_id == model_id)
        .scalar_subquery()
    )
    reserved = case(
        (Model.next_file_version < minimum_next, minimum_next),
        else_=Model.next_file_version,
    )
    statement = (
        update(Model)  # pyright: ignore[reportCallIssue]
        .where(Model.id == model_id)  # pyright: ignore[reportArgumentType]
        .values(next_file_version=reserved + 1)
        .returning(Model.next_file_version)  # pyright: ignore[reportArgumentType]
    )
    next_value = session.execute(statement).scalar_one_or_none()
    if next_value is None:
        raise RuntimeError("artifact_model_not_found")
    return int(next_value) - 1


def _reserve_version_before_publication(session: Session, model: Model) -> int:
    """Durably allocate the logical version before storage publication.

    The short independent transaction avoids holding SQLite's caller write lock
    while the ownership ledger reserves its storage key. A failed publication
    may leave a harmless version gap; no File row can observe a duplicate.
    """
    assert model.id is not None
    with Session(bind=session.get_bind(), expire_on_commit=False) as reservation:
        version = _reserve_next_version(reservation, model.id)
        reservation.commit()
    session.expire(model, ["next_file_version"])
    return version


def _serialize_artifact_persistence(func: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(func)
    def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        model = kwargs.get("model")
        if not isinstance(model, Model) or model.id is None:
            raise RuntimeError("artifact_model_not_persisted")
        lock = _ARTIFACT_LOCKS[model.id % len(_ARTIFACT_LOCKS)]
        with lock:
            return func(*args, **kwargs)

    return serialized


def _available_vault_key(backend, *, slug: str, version: int, filename: str) -> str:
    """Create an opaque backend-owned key for a new Artifact.

    ``slug`` and ``version`` remain in the signature while legacy callers are
    migrated, but neither user labels nor manifest values participate in new
    physical destinations.
    """
    del slug, version
    suffix = Path(storage.validate_leaf_name(filename)).suffix.lower()
    return backend.blob_key("_objects", 0, f"{uuid.uuid4().hex}{suffix}")


def _apply_taxonomy(
    session: Session,
    model: Model,
    collection: Optional[str],
    tags_raw: Optional[str],
    *,
    overwrite_collection: bool = False,
) -> None:
    """Resolve & attach collection + tags. Idempotent."""
    if collection:
        cat = taxonomy.resolve_or_create_collection(session, collection)
        if cat is not None:
            if overwrite_collection or model.collection_id is None:
                model.collection_id = cat.id
            session.add(model)
            content_changed(session, "model", [model.id])
            session.commit()

    tag_names = taxonomy.parse_tag_input(tags_raw)
    if tag_names:
        new_tags = taxonomy.resolve_or_create_tags(session, tag_names)
        existing_ids = {
            row.tag_id
            for row in session.exec(
                select(ModelTagLink).where(ModelTagLink.model_id == model.id)
            ).all()
        }
        for tag in new_tags:
            if tag.id not in existing_ids:
                session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        session.add(model)
        content_changed(session, "model", [model.id])
        session.commit()


def resolve_or_create_model(
    session: Session,
    *,
    dedup_hash: str,
    model_name: str,
    source_url: str | None = None,
    actor: User | None = None,
) -> tuple[Model, bool]:
    """Look up a Model by content hash, creating one when absent.

    Returns ``(model, created)``. On a dedup hit the model is un-trashed and,
    when *actor* is supplied, the caller's EDIT permission on its collection is
    enforced (system callers such as the library scanner pass ``actor=None``).
    Shared by the upload pipeline and the external-library scan engine so both
    agree on model identity.
    """
    existing = session.exec(select(Model).where(Model.hash == dedup_hash)).first()
    if existing is None:
        base_slug = storage.slugify(model_name)
        slug = storage.ensure_unique_slug(
            base_slug, lambda s: _model_exists_with_slug(session, s)
        )
        model = Model(
            name=model_name, slug=slug, hash=dedup_hash, source_url=source_url
        )
        session.add(model)
        try:
            content_changed(session, "model", (row.id for row in (model,)))
            session.commit()
        except IntegrityError:
            # Another upload of the same bytes won the race between the SELECT
            # above and this INSERT (Model.hash is unique). Dedup onto theirs
            # rather than failing the second uploader's request.
            session.rollback()
            existing = session.exec(
                select(Model).where(Model.hash == dedup_hash)
            ).first()
            if existing is None:
                raise
        else:
            session.refresh(model)
            return model, True

    if actor is not None:
        rbac.require_model_collection_role(
            session, actor, existing.collection_id, CollectionRole.EDIT
        )
    if existing.purge_token is not None:
        raise RuntimeError("resource_purge_in_progress")
    existing.deleted_at = None
    existing.deleted_by = None
    existing.updated_at = utcnow()
    session.add(existing)
    content_changed(session, "model", [existing.id])
    session.commit()
    session.refresh(existing)
    return existing, False


@_serialize_artifact_persistence
def persist_artifact(
    session: Session,
    *,
    model: Model,
    staged_path: Path,
    original_filename: str,
    file_type: FileType,
    blob_hash: str,
    meta: dict[str, Any],
    thumb_bytes: bytes | None,
    overwrite_thumbnail: bool,
    revision_label: str | None = None,
    revision_status: FileRevisionStatus | None = None,
    revision_notes: str | None = None,
    is_recommended: bool = False,
    move_blob: bool = True,
    dest_key_override: str | None = None,
    is_external: bool = False,
    external_library_id: int | None = None,
    source_mtime: float | None = None,
    ingestion_key: str | None = None,
    provenance_context: ProvenanceContext | None = None,
    session_factory: SessionFactory | None = None,
    actor_user_id: int | None = None,
    source_entry: SourceEntry | None = None,
) -> File:
    """Persist a parsed, staged artifact onto *model* — the deep core shared
    by background ingestion and synchronous revision attachment.

    Owns: version allocation, the canonical blob move, the File row, the
    thumbnail write (+ model thumbnail selection), and the Metadata row.

    Destination modes:
    - **Vault** (default): write into vault storage at ``blob_key(...)`` via
      ``move_in``.
    - **External index-in-place** (scan): ``move_blob=False`` with
      ``dest_key_override`` set to the file's existing on-disk path — nothing is
      moved; ``is_external``/``external_library_id``/``source_mtime`` are recorded.
    - **External write-back** (web upload/revision into a NAS library): pass the
      computed NAS destination as ``dest_key_override`` (caller makes it
      collision-safe) with ``move_blob=True`` and the external markers; the staged
      upload is moved onto the library root.
    """
    assert model.id is not None
    backend = get_backend()

    if is_external:
        # External roots are independently owned from the active vault
        # backend. Revalidate the durable marker before reserving a version or
        # publishing bytes so a remount/replacement cannot receive a write.
        from app.modules.sources.root_binding import (
            ExternalRootBindingError,
            assert_root_binding,
        )

        library = (
            session.get(ExternalLibrary, external_library_id)
            if external_library_id is not None
            else None
        )
        if library is None:
            raise ExternalRootBindingError("unbound", "external_library_missing")
        if library.source_kind.value == "mounted":
            assert_root_binding(library)
        elif move_blob or library.writeback_enabled:
            # Remote sources are intentionally read-only. A future writeback
            # capability must prove atomic create/replace per protocol before
            # it can cross this boundary.
            raise ExternalRootBindingError("read_only", "remote_writeback_disabled")

    if ingestion_key is not None:
        existing_ingestion = session.exec(
            select(File).where(File.ingestion_key == ingestion_key)
        ).first()
        if existing_ingestion is not None:
            return existing_ingestion

    model_id = model.id
    # Callers hand this service transaction ownership and it commits on
    # success. End any read-only transaction used to load the Model before the
    # counter UPDATE so SQLite never has to upgrade a stale read transaction
    # while another process owns the write lock.
    session.commit()
    version = _reserve_version_before_publication(session, model)
    dest_key = (
        dest_key_override
        if dest_key_override is not None
        else _available_vault_key(
            backend,
            slug=model.slug,
            version=version,
            filename=original_filename,
        )
    )
    from app.modules.storage.capacity import CapacityManager, CapacityResource
    from app.modules.storage.capacity_estimates import vault_allocation

    reservation = None
    if move_blob:
        allocation = (
            CapacityResource.for_path(
                Path(dest_key), staged_path.stat().st_size, role="external writeback"
            )
            if is_external
            else vault_allocation(staged_path.stat().st_size)
        )
        reservation = CapacityManager(session_factory or get_session_factory()).reserve(
            f"artifact:{model_id}:{version}", [allocation]
        )
    blob_receipt = None
    commit_started = False
    commit_resolved = False
    try:
        if move_blob:
            # ``move_in`` performs the only authoritative collision check using
            # the backend's atomic create-only primitive. An earlier exists()
            # check would be a TOCTOU race.
            if is_external:
                # A NAS path is independent of the active vault backend. Bind
                # a local adapter to this library root so its ownership proof
                # cannot be interpreted as an S3/WebDAV key.
                library = (
                    session.get(ExternalLibrary, external_library_id)
                    if external_library_id is not None
                    else None
                )
                library_root = (
                    Path(library.root_path).expanduser().resolve(strict=False)
                    if library is not None
                    else Path(dest_key).parent
                )
                from app.modules.sources.root_binding import expected_root_marker

                external_backend = LocalStorageBackend(
                    external_roots=(library_root,),
                    external_root_bindings={library_root: expected_root_marker(library)}
                    if library is not None
                    else None,
                )
                # Linked NAS bytes remain user-owned: publish add-only and do
                # not create a vault ownership-ledger row or delete intent.
                blob_receipt = external_backend.move_in(staged_path, dest_key)
            else:
                blob_receipt = publish_file(
                    session,
                    backend,
                    dest_key,
                    staged_path,
                    object_kind="artifact",
                    sha256=blob_hash,
                    move=True,
                )
        if blob_receipt is not None:
            size_bytes = blob_receipt.size
        elif is_external and not move_blob:
            # An external Artifact is indexed in place.  Its opaque ``path``
            # is a NAS path, not a key in the active vault backend (which may
            # be S3/WebDAV), so never ask that backend to stat it.
            size_bytes = staged_path.stat().st_size
        else:
            size_bytes = backend.stat_size(dest_key)

        # For write-back into a NAS library, capture the on-disk mtime of the file we
        # just wrote so the next scan recognises it as unchanged (no re-import).
        if is_external and source_mtime is None:
            try:
                source_mtime = Path(dest_key).stat().st_mtime
            except OSError:
                source_mtime = None

        if file_type == FileType.GCODE:
            recommended_rows = session.exec(
                select(File).where(
                    File.model_id == model_id,
                    File.file_type == FileType.GCODE,
                    File.is_recommended == True,  # noqa: E712
                    live(File),
                )
            ).all()
            if is_recommended:
                # Clear first and flush before inserting the replacement so the
                # partial unique index is never transiently violated.
                for recommended in recommended_rows:
                    recommended.is_recommended = False
                    session.add(recommended)
                if recommended_rows:
                    session.flush()
            else:
                # A Model's first live G-code claims the recommendation marker.
                is_recommended = not recommended_rows

        file_row = File(
            model_id=model_id,
            path=dest_key,
            original_filename=original_filename,
            file_type=file_type,
            version=version,
            size_bytes=size_bytes,
            sha256=blob_hash,
            revision_label=revision_label,
            revision_status=revision_status,
            revision_notes=revision_notes,
            is_recommended=is_recommended,
            is_external=is_external,
            external_library_id=external_library_id,
            source_mtime=source_mtime,
            ingestion_key=ingestion_key,
            source_key=source_entry.key if source_entry else None,
            source_etag=source_entry.etag if source_entry else None,
            source_version_id=source_entry.version_id if source_entry else None,
            source_verified_at=utcnow() if is_external else None,
        )
        # One transaction for the whole artifact: a File row committed before its
        # Metadata is a model that renders with no print time, filament or cost and
        # no error to explain it. flush() allocates the id the thumbnail key needs
        # without ending the transaction.
        session.add(file_row)
        session.flush()
        assert file_row.id is not None
        if provenance_context is not None:
            # The File id exists, but the Artifact has not yet become visible.
            # A provenance failure therefore follows the established rollback
            # path for both its link and the bytes/row it describes.
            _attach_ingested_artifact(session, file_row, provenance_context)
        # The parser may carry detection-only keys (e.g. printer_preset_name)
        # that have no Metadata column.
        md_fields = {k: v for k, v in meta.items() if k in Metadata.model_fields}
        session.add(Metadata(file_id=file_row.id, **md_fields))
        requirements = meta.get("material_requirements")
        if isinstance(requirements, list):
            for requirement in requirements:
                if not isinstance(requirement, dict):
                    continue
                material_type = requirement.get("material_type")
                if not isinstance(material_type, str) or not material_type.strip():
                    continue
                session.add(
                    ArtifactMaterialRequirement(
                        file_id=file_row.id,
                        tool_index=int(requirement.get("tool_index") or 0),
                        material_type=material_type.strip(),
                        color_hex=requirement.get("color_hex"),
                    )
                )
        from app.modules.media.analysis_generations import request_enrichment

        request_enrichment(
            session, file_row,
            promote_thumbnail=overwrite_thumbnail or not model.thumbnail_path,
            preserve_metadata=bool(meta), actor_user_id=actor_user_id,
        )
        content_changed(session, "model", [model_id])
        # A driver may acknowledge a committed transaction as an exception
        # (for example, a connection loss after COMMIT). From here onward the
        # blob must be preserved until a fresh session resolves the outcome.
        from app.modules.ingestion.commands import require_execution_claim
        require_execution_claim(session)
        require_ingestion_actor(session, actor_user_id, model=model)
        commit_started = True
        session.commit()
    except Exception as exc:
        session.rollback()
        if commit_started and blob_receipt is not None and not is_external:
            try:
                resolved = _resolve_committed_artifact(
                    backend=backend,
                    key=blob_receipt.key,
                    model_id=model_id,
                    blob_hash=blob_hash,
                    ingestion_key=ingestion_key,
                )
            except Exception as resolution_exc:
                # The acknowledgement and the verification query are separate
                # failure domains. If the latter is unavailable, the outcome is
                # still unknown and the committed bytes must remain retryable.
                raise ArtifactCommitUncertain(
                    f"artifact commit outcome unknown for {blob_receipt.key}"
                ) from resolution_exc
            if resolved is not None:
                file_row = resolved
                commit_resolved = True
            else:
                # Unknown commit state is retryable and reconciled by the
                # ownership worker. Deleting here could destroy a committed
                # artifact when only the acknowledgement was lost.
                raise ArtifactCommitUncertain(
                    f"artifact commit outcome unknown for {blob_receipt.key}"
                ) from exc
        else:
            # Before the domain commit boundary, exact receipt rollback is safe.
            # External-library bytes remain user-owned for the next scan.
            if blob_receipt is not None and not is_external:
                backend.rollback_create(blob_receipt)
            raise

    finally:
        if reservation is not None:
            reservation.release()

    # A successfully resolved commit is terminal for this call. The detached
    # row is returned and thumbnail work is left to the derivative reconciler;
    # continuing with the rolled-back caller session could create a second,
    # unrelated transaction against stale Model state.
    if commit_resolved:
        return file_row

    # Derived outputs are registered in the source transaction and produced by
    # the bounded enrichment worker. Precomputed bytes are no longer a barrier
    # to source readiness; explicit rebuilds use the media publication owner.
    session.refresh(file_row)
    return file_row


@dataclass
class WriteTarget:
    """Resolved destination for a blob about to be persisted.

    ``dest_key=None`` means the default vault location (``blob_key``); a non-None
    value is an absolute path under a NAS library root (write-back).
    """

    dest_key: str | None
    is_external: bool
    external_library_id: int | None
    source_mtime: float | None


def _collision_safe_path(directory: Path, filename: str) -> Path:
    """Return a path in *directory* for *filename* that does not clobber an
    existing file (append -2, -3, ...). We never overwrite bytes on the NAS."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 2
    while True:
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def resolve_write_target(
    session: Session,
    *,
    model: Model,
    original_filename: str,
    collection: Optional[str],
    target_library_id: int | None,
) -> WriteTarget:
    """Decide whether a new blob is written back into a NAS library or vault.

    Rules: a model that already has external (NAS-linked) files keeps new
    files/revisions in that same library (write-back follows the model); a
    brand-new model uses the upload's chosen ``target_library_id``; otherwise the
    blob goes to vault storage. When the feature is disabled everything is vault.
    """
    from app.modules.administration.runtime_config import external_libraries_enabled
    from app.modules.sources.root_binding import (
        ExternalRootBindingError,
        assert_root_binding,
    )

    vault = WriteTarget(None, False, None, None)
    # The external-library toggle is a deployment-wide policy switch.  Keep
    # the historical vault behavior while it is off, including when callers
    # still send a stale/explicit target_library_id from an older client.
    if not external_libraries_enabled(session):
        return vault

    library_id: int | None = None
    existing_ext = session.exec(
        select(File).where(
            File.model_id == model.id,
            File.is_external == True,  # noqa: E712
            live(File),
        )
    ).first()
    if existing_ext is not None and existing_ext.external_library_id is not None:
        library_id = existing_ext.external_library_id
    elif target_library_id is not None:
        library_id = target_library_id

    if library_id is None:
        return vault

    library = session.get(ExternalLibrary, library_id)
    if library is None:
        raise ExternalRootBindingError("missing", "external_library_missing")

    if library.source_kind.value != "mounted":
        raise ExternalRootBindingError("read_only", "remote_writeback_disabled")

    # An explicitly selected library, or a model already linked to one, must
    # fail closed. Falling back to vault storage would make the UI appear to
    # succeed while silently breaking the external mirror contract.
    assert_root_binding(library)
    # Store and derive destinations from one canonical root.  This keeps scan
    # paths, collection mapping, and write-back keys identical even when an
    # operator configured the library through a symlink or relative path.
    root = Path(library.root_path).expanduser().resolve(strict=False)
    subpath = ""
    if (
        library.collection_mode == ExternalLibraryCollectionMode.MIRROR
        and model.collection_id is not None
    ):
        coll = session.get(Collection, model.collection_id)
        if coll is not None:
            subpath = coll.path
    dest_dir = root / subpath if subpath else root
    dest_path = _collision_safe_path(dest_dir, original_filename)
    try:
        canonical_root = root.resolve(strict=True)
        canonical_target = dest_path.resolve(strict=False)
        canonical_target.relative_to(canonical_root)
    except (OSError, RuntimeError, ValueError) as exc:
        # A mirrored collection may have been replaced by a symlink since the
        # library was configured. Never follow it outside the declared NAS
        # boundary. The final create remains atomic/no-replace for collision
        # safety after this topology check.
        raise StorageCollisionError("external_library_symlink_escape") from exc
    # Directory creation is deliberately deferred to LocalStorageBackend's
    # descriptor-pinned publication primitive.  Calling Path.mkdir here would
    # recreate a missing mount (or create descendants through a replacement
    # pathname) after the binding check above.
    return WriteTarget(str(canonical_target), True, library_id, None)


def require_ingestion_actor(
    session: Session, actor_user_id: int | None, *, collection: str | None = None,
    model: Model | None = None,
) -> User | None:
    """Recheck current authority at execution and source publication boundaries."""
    if actor_user_id is None:
        return None  # Explicit system-owned scanner work.
    from app.core.errors import ErrorKind, OperationError

    actor = session.exec(select(User).where(User.id == actor_user_id)
        .execution_options(populate_existing=True).with_for_update()).first()
    if actor is None or not actor.is_active:
        raise OperationError("ingestion_actor_unavailable", kind=ErrorKind.FORBIDDEN)
    if model is not None:
        rbac.require_model_collection_role(session, actor, model.collection_id, CollectionRole.EDIT)
    elif not actor.is_superuser:
        # An accepted archive may create descendants of the granted collection.
        # Use the nearest live ancestor, with the same inherited RBAC policy.
        path = "/".join(storage.slugify(part.strip()) for part in (collection or "").split("/") if part.strip())
        prefixes = ["/".join(path.split("/")[:length]) for length in range(1, len(path.split("/")) + 1)] if path else []
        candidates = session.exec(select(Collection).where(Collection.path.in_(prefixes), live(Collection))).all()
        target = max(candidates, key=lambda row: len(row.path), default=None)
        rbac.require_collection_role(session, actor, target.id if target else None, CollectionRole.EDIT)
    return actor


def run_ingestion_pipeline(
    *,
    job_id: str,
    staged_path: Path,
    original_filename: str,
    model_name: str,
    collection: Optional[str],
    tags: Optional[str],
    source_hash: Optional[str],
    strategy: IngestionStrategy,
    actor_user_id: int | None = None,
    session_factory: SessionFactory | None = None,
    source_url: Optional[str] = None,
    target_library_id: int | None = None,
    provenance_context: ProvenanceContext | None = None,
    on_progress: Callable[[float], None] | None = None,
    defer_fingerprint: bool = False,
) -> None:
    """Publish source bytes, compatibility facts and durable enrichment intent.

    Geometry, previews, profiles and search are processed by background workers.
    """
    logger.info("ingestion_job job_id=%s stage=start result=running", job_id)

    # Step plan: hashing → strategy sub-steps → persisting. The registry keeps
    # the coarse state machine; step/label/progress are additive hints.
    step_plan = ("hashing", *strategy.step_labels, "persisting")
    total_steps = len(step_plan)

    def report(label: str) -> None:
        try:
            step = step_plan.index(label) + 1
        except ValueError:
            step = None  # type: ignore[assignment]
        registry.update(
            job_id,
            step=step,
            total_steps=total_steps,
            label=label,
            progress=(step - 1) / total_steps * 100 if step else None,
            stage=(
                "hashing"
                if label == "hashing"
                else "thumbnailing"
                if "thumbnail" in label
                else "ingesting"
            ),
            current_item=original_filename,
        )

        if on_progress is not None and step is not None:
            on_progress((step - 1) / total_steps * 100)

    registry.update(job_id, state="running", total_steps=total_steps)

    if session_factory is None:
        from app.db.session import get_session_factory

        session_factory = get_session_factory()

    try:
        with session_factory.scoped_session() as recovery_session:
            require_ingestion_actor(recovery_session, actor_user_id, collection=collection)
            committed = recovery_session.exec(
                select(File).where(File.ingestion_key == job_id)
            ).first()
            if committed is not None:
                registry.finish(
                    job_id,
                    state="completed",
                    completion="complete",
                    model_id=committed.model_id,
                    file_id=committed.id,
                    committed_at=committed.uploaded_at,
                    thumbnail_status=(
                        "generated" if committed.thumbnail_path else "skipped"
                    ),
                    processed=1,
                    total=1,
                    succeeded=1,
                    result={"created": False, "resumed": True},
                )
                staged_path.unlink(missing_ok=True)
                with session_factory.scoped_session() as cleanup_session:
                    lease = cleanup_session.exec(
                        select(StagingLease).where(
                            StagingLease.background_job_id == job_id,
                            StagingLease.capture_upload_slot_origin_id.is_(None),
                        )
                    ).first()
                    if lease is not None:
                        cleanup_session.delete(lease)
                        cleanup_session.commit()
                return
        report("hashing")
        blob_hash = sha256_file(staged_path)
        logger.info("ingestion_job job_id=%s stage=hashed result=running", job_id)

        if provenance_context is not None:
            provenance_context = replace(provenance_context, blob_sha256=blob_hash)
            from app.modules.library.provenance import preflight_existing_artifact

            with session_factory.scoped_session() as session:
                preflight = preflight_existing_artifact(session, provenance_context)
            if preflight.status == "reusable":
                assert preflight.model_id is not None and preflight.file_id is not None
                # A byte-level duplicate can still carry a newer source
                # snapshot.  Upsert it before returning the existing Artifact;
                # this preserves the dedupe invariant without making capture
                # freshness depend on a new blob write.
                from app.modules.library.provenance import attach_existing_artifact

                with session_factory.scoped_session() as session:
                    existing_file = session.get(File, preflight.file_id)
                    if existing_file is None:
                        raise RuntimeError("captured_artifact_missing")
                    existing_model = session.get(Model, existing_file.model_id)
                    if existing_model is None:
                        raise RuntimeError("captured_model_missing")
                    require_ingestion_actor(session, actor_user_id, model=existing_model)
                    attach_existing_artifact(session, existing_file, provenance_context)
                    session.commit()
                registry.finish(
                    job_id,
                    state="completed",
                    completion="complete",
                    model_id=preflight.model_id,
                    file_id=preflight.file_id,
                    processed=1,
                    total=1,
                    succeeded=1,
                    deduplicated=1,
                    result={
                        "created": False,
                        "deduplicated": True,
                        "name": original_filename,
                    },
                )
                staged_path.unlink(missing_ok=True)
                return
            if preflight.status == "trashed":
                raise RuntimeError("captured_artifact_trashed")

        # Compatibility facts remain bounded prerequisites for G-code printing.
        # Full mesh decoding, rendering and profile enrichment run after saving.
        meta = gcode_parser.parse(staged_path) if strategy.file_type == FileType.GCODE else {}
        thumb_bytes = None

        dedup_hash = (
            source_hash.lower()
            if strategy.file_type == FileType.GCODE and source_hash
            else blob_hash
        )

        report("persisting")
        durable_ids: tuple[int, int] | None = None
        from app.core.config import settings

        thumbnail_status = "pending" if settings.thumbnail_processing == "background" else "skipped"
        thumbnail_reason = None if thumbnail_status == "pending" else settings.thumbnail_processing
        created = False
        with session_factory.scoped_session() as session:
            actor = require_ingestion_actor(session, actor_user_id, collection=collection)
            model, created = resolve_or_create_model(
                session,
                dedup_hash=dedup_hash,
                model_name=model_name,
                source_url=source_url,
                actor=actor,
            )
            assert model.id is not None

            _apply_taxonomy(session, model, collection, tags)

            # Resolve where the blob lands: a NAS library (write-back) or vault.
            dest = resolve_write_target(
                session,
                model=model,
                original_filename=original_filename,
                collection=collection,
                target_library_id=target_library_id,
            )

            _fault_injection_checkpoint("before_commit", job_id)
            file_row = persist_artifact(
                session,
                model=model,
                staged_path=staged_path,
                original_filename=original_filename,
                file_type=strategy.file_type,
                blob_hash=blob_hash,
                meta=meta,
                thumb_bytes=thumb_bytes,
                overwrite_thumbnail=strategy.overwrite_thumbnail,
                dest_key_override=dest.dest_key,
                is_external=dest.is_external,
                external_library_id=dest.external_library_id,
                source_mtime=dest.source_mtime,
                ingestion_key=job_id,
                provenance_context=provenance_context,
                session_factory=session_factory,
                actor_user_id=actor_user_id,
            )
            assert file_row.id is not None
            durable_ids = (model.id, file_row.id)
        committed_at = utcnow()
        assert durable_ids is not None
        model_id, file_id = durable_ids
        registry.update(
            job_id,
            model_id=model_id,
            file_id=file_id,
            committed_at=committed_at,
            thumbnail_status=thumbnail_status,  # type: ignore[arg-type]
            thumbnail_reason=thumbnail_reason,
            processed=1,
            total=1,
            succeeded=1,
            deduplicated=0 if created else 1,
        )
        _fault_injection_checkpoint("after_commit", job_id)
        try:
            verify_durable_artifact(
                session_factory,
                model_id=model_id,
                file_id=file_id,
                thumbnail_status=thumbnail_status,
            )
        except ThumbnailDurabilityError:
            thumbnail_status = "failed"
            thumbnail_reason = "thumbnail_blob_not_durable"
            verify_durable_artifact(
                session_factory,
                model_id=model_id,
                file_id=file_id,
                thumbnail_status=thumbnail_status,
            )
        _fault_injection_checkpoint("before_terminal", job_id)
        registry.finish(
            job_id,
            state="completed",
            completion="complete",
            thumbnail_status=thumbnail_status,  # type: ignore[arg-type]
            thumbnail_reason=thumbnail_reason,
            result={"created": created, "name": original_filename},
        )
        staged_path.unlink(missing_ok=True)
        with session_factory.scoped_session() as cleanup_session:
            lease = cleanup_session.exec(
                select(StagingLease).where(
                    StagingLease.background_job_id == job_id,
                    StagingLease.capture_upload_slot_origin_id.is_(None),
                )
            ).first()
            if lease is not None:
                cleanup_session.delete(lease)
                cleanup_session.commit()

    except Exception as exc:  # noqa: BLE001 — top-level task boundary
        logger.exception("ingestion_job job_id=%s stage=pipeline result=failed", job_id)
        # A fault after commit still produced a useful durable Model. Publish a
        # partial result so clients can repair optional post-processing instead
        # of reporting a destructive false failure.
        if "durable_ids" in locals() and durable_ids is not None:
            model_id, file_id = durable_ids
            try:
                verify_durable_artifact(
                    session_factory,
                    model_id=model_id,
                    file_id=file_id,
                    thumbnail_status=(
                        thumbnail_status if thumbnail_status != "failed" else "skipped"
                    ),
                )
            except Exception:  # noqa: BLE001 — durability decides failed vs partial
                registry.finish(job_id, state="failed", error=str(exc), retryable=True)
            else:
                registry.finish(
                    job_id,
                    state="completed",
                    completion="partial",
                    model_id=model_id,
                    file_id=file_id,
                    committed_at=locals().get("committed_at", utcnow()),
                    thumbnail_status=thumbnail_status,  # type: ignore[arg-type]
                    thumbnail_reason="post_commit_exception",
                    error="post_commit_exception",
                    processed=1,
                    total=1,
                    succeeded=1,
                    result={"created": created},
                    retryable=True,
                )
        else:
            registry.finish(job_id, state="failed", error=str(exc), retryable=True)


def _gcode_strategy() -> IngestionStrategy:
    def process(
        path: Path, report: ProgressFn = _noop_progress
    ) -> tuple[dict[str, Any], bytes | None]:
        report("parsing_metadata")
        meta = gcode_parser.parse(path)
        report("extracting_thumbnail")
        thumb_bytes = thumbnail.extract(path)
        return meta, thumb_bytes

    return IngestionStrategy(
        file_type=FileType.GCODE,
        overwrite_thumbnail=False,
        process=process,
        step_labels=("parsing_metadata", "extracting_thumbnail"),
    )


def _mesh_strategy(
    file_type: FileType, *, defer_fingerprint: bool = False
) -> IngestionStrategy:

    def process(
        path: Path, report: ProgressFn = _noop_progress
    ) -> tuple[dict[str, Any], bytes | None]:
        from app.modules.ingestion.extensions import (
            MeshExtractionOptions,
            extraction_options,
        )

        # Bulk imports queue optional fingerprints after the Artifact is durable.
        # Direct uploads can reuse this mesh load for inline fingerprints.
        options: MeshExtractionOptions = (
            {} if defer_fingerprint else extraction_options(get_session_factory())
        )
        return mesh_operations.analyze_mesh(
            path,
            report=report,
            file_type=file_type.value,
            output_format="WEBP",
            **options,
        )

    return IngestionStrategy(
        file_type=file_type,
        overwrite_thumbnail=True,
        process=process,
        step_labels=("loading_mesh", "extracting_geometry", "rendering_thumbnail"),
    )


def strategy_for_artifact(file_type: FileType) -> IngestionStrategy:
    """Select the processing policy shared by ingestion, discovery and repair."""
    return (
        _gcode_strategy() if file_type == FileType.GCODE else _mesh_strategy(file_type)
    )


def ingest_orca_gcode(
    *,
    job_id: str,
    staged_path: Path,
    original_filename: str,
    model_name: str,
    collection: Optional[str],
    tags: Optional[str],
    source_hash: Optional[str],
    actor_user_id: int | None = None,
    session_factory: SessionFactory | None = None,
    source_url: Optional[str] = None,
    target_library_id: int | None = None,
    provenance_context: ProvenanceContext | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> None:
    """Public entry point for G-code ingestion (called from the OrcaSlicer router)."""
    run_ingestion_pipeline(
        job_id=job_id,
        staged_path=staged_path,
        original_filename=original_filename,
        model_name=model_name,
        collection=collection,
        tags=tags,
        source_hash=source_hash,
        strategy=_gcode_strategy(),
        actor_user_id=actor_user_id,
        session_factory=session_factory,
        source_url=source_url,
        target_library_id=target_library_id,
        provenance_context=provenance_context,
        on_progress=on_progress,
    )


def ingest_mesh(
    *,
    job_id: str,
    staged_path: Path,
    original_filename: str,
    model_name: str,
    collection: Optional[str],
    tags: Optional[str],
    file_type: FileType,
    source_hash: Optional[str],
    actor_user_id: int | None = None,
    session_factory: SessionFactory | None = None,
    source_url: Optional[str] = None,
    target_library_id: int | None = None,
    provenance_context: ProvenanceContext | None = None,
    on_progress: Callable[[float], None] | None = None,
    defer_fingerprint: bool = False,
    prepared_analysis: Callable[[Path, ProgressFn], tuple[dict[str, Any], bytes | None]]
    | None = None,
) -> None:
    """Public entry point for mesh ingestion (called from the model upload router)."""
    strategy = _mesh_strategy(file_type, defer_fingerprint=defer_fingerprint)
    if prepared_analysis is not None:
        strategy = replace(strategy, process=prepared_analysis)
    run_ingestion_pipeline(
        job_id=job_id,
        staged_path=staged_path,
        original_filename=original_filename,
        model_name=model_name,
        collection=collection,
        tags=tags,
        source_hash=source_hash,
        strategy=strategy,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
        source_url=source_url,
        target_library_id=target_library_id,
        provenance_context=provenance_context,
        on_progress=on_progress,
        defer_fingerprint=defer_fingerprint,
    )


def add_gcode_revision_to_model(
    *,
    session: Session,
    model: Model,
    staged_path: Path,
    original_filename: str,
    revision_label: str | None,
    revision_status: FileRevisionStatus | None,
    revision_notes: str | None,
    is_recommended: bool,
    actor_user_id: int | None = None,
    ingestion_key: str | None = None,
) -> File:
    """Attach a staged G-code file as a new revision of an existing model."""
    assert model.id is not None
    require_ingestion_actor(session, actor_user_id, model=model)
    if ingestion_key is not None:
        committed = session.exec(select(File).where(File.ingestion_key == ingestion_key)).first()
        if committed is not None:
            if committed.model_id != model.id:
                raise RuntimeError("revision_ingestion_identity_mismatch")
            return committed
    blob_hash = sha256_file(staged_path)
    meta, thumb_bytes = gcode_parser.parse(staged_path), None

    # Revisions follow the model: if it lives in a NAS library, write back there.
    dest = resolve_write_target(
        session,
        model=model,
        original_filename=original_filename,
        collection=None,
        target_library_id=None,
    )

    file_row = persist_artifact(
        session,
        model=model,
        staged_path=staged_path,
        original_filename=original_filename,
        file_type=FileType.GCODE,
        blob_hash=blob_hash,
        meta=meta,
        thumb_bytes=thumb_bytes,
        overwrite_thumbnail=False,
        revision_label=revision_label.strip()
        if revision_label and revision_label.strip()
        else None,
        revision_status=revision_status,
        revision_notes=revision_notes.strip()
        if revision_notes and revision_notes.strip()
        else None,
        is_recommended=is_recommended,
        dest_key_override=dest.dest_key,
        is_external=dest.is_external,
        external_library_id=dest.external_library_id,
        source_mtime=dest.source_mtime,
        actor_user_id=actor_user_id,
        ingestion_key=ingestion_key,
    )
    assert file_row.id is not None

    model.updated_at = utcnow()
    session.add(model)
    content_changed(session, "model", [model.id])
    session.commit()
    session.refresh(file_row)
    return file_row
