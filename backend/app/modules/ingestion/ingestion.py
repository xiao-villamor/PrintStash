"""Artifact commit: staged bytes become a durable, deduplicated Artifact.

This is the whole hot path of ingestion. Hash, dedupe, resolve the Model,
publish the blob and commit the File row with an empty ``metadata`` row, then
nudge the derivative sources. Nothing here parses G-code, renders a mesh or
extracts a thumbnail: those are derivatives, computed by their own jobs from
the committed bytes, so an upload is durable as soon as its bytes are.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, ParamSpec, TypeVar

from sqlalchemy import case, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

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
from app.modules.storage import storage
from app.modules.storage.hashing import sha256_file
from app.modules.storage.storage_backend.contracts import (
    StagedRemoteObject,
    StorageCollisionError,
)
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import provider_ref_for_backend, publish_file
from app.modules.work.contracts import JobOutcome
from app.modules.work.jobs import failure_of

if TYPE_CHECKING:
    from app.modules.library.provenance import ProvenanceContext

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


class ArtifactDurabilityError(RuntimeError):
    """A committed artifact cannot be used from a fresh session/storage view."""


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
) -> None:
    """Verify rows and the blob from a new transaction before publishing terminal."""
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
        primary_key = artifact.path
        external = artifact.is_external

    if external:
        # A linked NAS Artifact is owned by its library, not the vault backend,
        # so only its row is ours to verify.
        return
    if not get_backend().exists(primary_key):
        raise ArtifactDurabilityError("artifact_blob_not_durable")


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
    meta: dict[str, Any] | None = None,
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
    staged_origin: StagedRemoteObject | None = None,
) -> File:
    """Persist a staged artifact onto *model*: the only Artifact-persistence path.

    Owns: version allocation, the canonical blob move, the File row and its
    Metadata row. ``meta`` carries facts the caller already has (a portable
    library import); otherwise the Metadata row starts empty, every value
    unknown, and the derivative jobs fill it. After the commit it nudges the
    derivative sources, which find this Artifact by themselves.

    ``staged_origin`` identifies the verified bytes already in the vault's
    store, allowing publication by server-side copy instead of re-upload.

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
                    remote_source=staged_origin,
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
        )
        # One transaction for the whole artifact: a File row committed without
        # its Metadata row would have no row for the derivatives to fill.
        # flush() allocates the id provenance needs without ending the
        # transaction.
        session.add(file_row)
        session.flush()
        assert file_row.id is not None
        if provenance_context is not None:
            # The File id exists, but the Artifact has not yet become visible.
            # A provenance failure therefore follows the established rollback
            # path for both its link and the bytes/row it describes.
            _attach_ingested_artifact(session, file_row, provenance_context)
        # A caller's facts may carry detection-only keys (e.g.
        # printer_preset_name) that have no Metadata column.
        meta = meta or {}
        md_fields = {
            k: v
            for k, v in meta.items()
            if k in Metadata.model_fields and k not in {"id", "file_id", "created_at"}
        }
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
        content_changed(session, "model", [model_id])
        # A driver may acknowledge a committed transaction as an exception
        # (for example, a connection loss after COMMIT). From here onward the
        # blob must be preserved until a fresh session resolves the outcome.
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

    # Derivatives are not part of the File+Metadata integrity boundary. The
    # sources find this Artifact by themselves; the nudge only makes that
    # happen now rather than at the next tick. A resolved commit returns the
    # detached row without touching the rolled-back caller session.
    from app.modules.derivatives.jobs import nudge_for

    nudge_for(file_row)
    if commit_resolved:
        return file_row
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
    segments: list[str] = []
    if (
        library.collection_mode == ExternalLibraryCollectionMode.MIRROR
        and model.collection_id is not None
    ):
        coll = session.get(Collection, model.collection_id)
        if coll is not None:
            seen: set[int] = set()
            while coll is not None:
                if (
                    coll.id is None
                    or coll.id in seen
                    or coll.name in {"", ".", ".."}
                    or "/" in coll.name
                    or "\\" in coll.name
                ):
                    raise StorageCollisionError(
                        "external_library_invalid_collection_path"
                    )
                seen.add(coll.id)
                segments.append(coll.name)
                coll = (
                    session.get(Collection, coll.parent_id) if coll.parent_id else None
                )
    dest_dir = root.joinpath(*reversed(segments))
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


@dataclass(frozen=True)
class StagedArtifact:
    """Everything the commit needs about one staged file, from its intent row."""

    staged_path: Path
    original_filename: str
    model_name: str
    file_type: FileType
    collection: Optional[str] = None
    tags: Optional[str] = None
    source_hash: Optional[str] = None
    source_url: Optional[str] = None
    target_library_id: int | None = None
    staged_origin: StagedRemoteObject | None = None


@dataclass(frozen=True)
class CommitOutcome:
    model_id: int
    file_id: int
    created: bool
    deduplicated: bool
    committed_at: datetime
    resumed: bool = False


Report = Callable[..., None]


def _no_report(**_fields: Any) -> None:
    return None


def commit_staged_artifact(
    artifact: StagedArtifact,
    *,
    ingestion_key: str,
    actor_user_id: int | None = None,
    session_factory: SessionFactory | None = None,
    provenance_context: ProvenanceContext | None = None,
    report: Report = _no_report,
) -> CommitOutcome:
    """Commit one staged file as an Artifact, exactly once per ``ingestion_key``.

    Idempotent across attempts: a retry after the commit finds the Artifact by
    its ingestion key and reports it as resumed instead of creating another.
    Raises on failure; the staged bytes are left for the caller's lease.
    """
    session_factory = session_factory or get_session_factory()
    with session_factory.scoped_session() as recovery:
        committed = recovery.exec(
            select(File).where(File.ingestion_key == ingestion_key)
        ).first()
        if committed is not None:
            assert committed.id is not None
            return CommitOutcome(
                model_id=committed.model_id,
                file_id=committed.id,
                created=False,
                deduplicated=False,
                committed_at=committed.uploaded_at,
                resumed=True,
            )

    report(stage="hashing", label="hashing", step=1, total_steps=2, progress=0)
    blob_hash = sha256_file(artifact.staged_path)

    if provenance_context is not None:
        provenance_context = replace(provenance_context, blob_sha256=blob_hash)
        from app.modules.library.provenance import (
            attach_existing_artifact,
            preflight_existing_artifact,
        )

        with session_factory.scoped_session() as session:
            preflight = preflight_existing_artifact(session, provenance_context)
        if preflight.status == "reusable":
            assert preflight.model_id is not None and preflight.file_id is not None
            # A byte-level duplicate can still carry a newer source snapshot.
            # Upsert it before returning the existing Artifact; this keeps the
            # dedupe invariant without making capture freshness depend on a
            # new blob write.
            with session_factory.scoped_session() as session:
                existing_file = session.get(File, preflight.file_id)
                if existing_file is None:
                    raise RuntimeError("captured_artifact_missing")
                attach_existing_artifact(session, existing_file, provenance_context)
                session.commit()
            return CommitOutcome(
                model_id=preflight.model_id,
                file_id=preflight.file_id,
                created=False,
                deduplicated=True,
                committed_at=utcnow(),
            )
        if preflight.status == "trashed":
            raise RuntimeError("captured_artifact_trashed")

    dedup_hash = (
        artifact.source_hash.lower()
        if artifact.file_type == FileType.GCODE and artifact.source_hash
        else blob_hash
    )
    report(stage="ingesting", label="persisting", step=2, total_steps=2, progress=50)
    with session_factory.scoped_session() as session:
        actor = session.get(User, actor_user_id) if actor_user_id is not None else None
        model, created = resolve_or_create_model(
            session,
            dedup_hash=dedup_hash,
            model_name=artifact.model_name,
            source_url=artifact.source_url,
            actor=actor,
        )
        assert model.id is not None
        _apply_taxonomy(session, model, artifact.collection, artifact.tags)
        dest = resolve_write_target(
            session,
            model=model,
            original_filename=artifact.original_filename,
            collection=artifact.collection,
            target_library_id=artifact.target_library_id,
        )
        _fault_injection_checkpoint("before_commit", ingestion_key)
        file_row = persist_artifact(
            session,
            model=model,
            staged_path=artifact.staged_path,
            original_filename=artifact.original_filename,
            file_type=artifact.file_type,
            blob_hash=blob_hash,
            dest_key_override=dest.dest_key,
            is_external=dest.is_external,
            external_library_id=dest.external_library_id,
            source_mtime=dest.source_mtime,
            ingestion_key=ingestion_key,
            provenance_context=provenance_context,
            session_factory=session_factory,
            staged_origin=artifact.staged_origin,
        )
        assert file_row.id is not None
        model_id, file_id = model.id, file_row.id
    _fault_injection_checkpoint("after_commit", ingestion_key)
    verify_durable_artifact(session_factory, model_id=model_id, file_id=file_id)
    return CommitOutcome(
        model_id=model_id,
        file_id=file_id,
        created=created,
        deduplicated=not created,
        committed_at=utcnow(),
    )


def release_job_staging(
    job_id: str, session_factory: SessionFactory | None = None
) -> None:
    """Remove a finished upload Job's own staged file and its lease.

    A capture-slot lease is never released here: its bytes belong to the
    capture slot until the Pending Import that owns it is settled.
    """
    session_factory = session_factory or get_session_factory()
    with session_factory.scoped_session() as session:
        leases = session.exec(
            select(StagingLease).where(
                StagingLease.job_id == job_id,
                StagingLease.capture_upload_slot_origin_id.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        for lease in leases:
            Path(lease.path).unlink(missing_ok=True)
            session.delete(lease)
        session.commit()


def _committed_by_key(
    ingestion_key: str, session_factory: SessionFactory | None
) -> tuple[int, int] | None:
    """The (model, file) an ingestion key already committed, if any."""
    session_factory = session_factory or get_session_factory()
    try:
        with session_factory.scoped_session() as session:
            row = session.exec(
                select(File).where(File.ingestion_key == ingestion_key)
            ).first()
            if row is None or row.id is None:
                return None
            return row.model_id, row.id
    except Exception:  # noqa: BLE001 - an unreadable DB is not proof of a commit
        return None


def ingest_staged_file(
    *,
    job_id: str,
    artifact: StagedArtifact,
    actor_user_id: int | None,
    session_factory: SessionFactory | None = None,
    provenance_context: ProvenanceContext | None = None,
    ingestion_key: str | None = None,
) -> CommitOutcome | None:
    """Commit one staged file on behalf of Job ``job_id`` and settle the Job."""
    from app.modules.work.jobs import jobs

    key = ingestion_key or job_id
    jobs.update(
        job_id,
        current_item=artifact.original_filename,
        total=1,
    )
    try:
        outcome = commit_staged_artifact(
            artifact,
            ingestion_key=key,
            actor_user_id=actor_user_id,
            session_factory=session_factory,
            provenance_context=provenance_context,
            report=lambda **fields: jobs.update(job_id, **fields),
        )
    except Exception as exc:  # noqa: BLE001 - the Job records the failure
        logger.exception("ingestion commit failed", extra={"job_id": job_id})
        committed = _committed_by_key(key, session_factory)
        if committed is None:
            jobs.finish(
                job_id, JobOutcome.FAILED, error=failure_of(exc), retryable=True
            )
            return None
        # The Artifact is durable even though a later step failed: report what
        # exists rather than a failure the user would retry into a duplicate.
        model_id, file_id = committed
        jobs.finish(
            job_id,
            JobOutcome.COMPLETED,
            completion="partial",
            model_id=model_id,
            file_id=file_id,
            error=failure_of(exc),
            processed=1,
            total=1,
            succeeded=1,
        )
        release_job_staging(job_id, session_factory)
        return None
    _fault_injection_checkpoint("before_terminal", job_id)
    jobs.finish(
        job_id,
        JobOutcome.COMPLETED,
        completion="complete",
        model_id=outcome.model_id,
        file_id=outcome.file_id,
        committed_at=outcome.committed_at,
        processed=1,
        total=1,
        succeeded=1,
        deduplicated=1 if outcome.deduplicated else 0,
        result={
            "created": outcome.created,
            "name": artifact.original_filename,
            **({"resumed": True} if outcome.resumed else {}),
            **({"deduplicated": True} if outcome.deduplicated else {}),
        },
    )
    release_job_staging(job_id, session_factory)
    return outcome


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
    staged_origin: StagedRemoteObject | None = None,
) -> File:
    """Attach a staged G-code file as a new revision of an existing model.

    Synchronous and cheap: hash, publish, commit. The revision's slicer
    metadata, detected profiles and thumbnail are derivatives, so the request
    returns as soon as the bytes are durable.
    """
    assert model.id is not None
    blob_hash = sha256_file(staged_path)

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
        staged_origin=staged_origin,
    )
    assert file_row.id is not None

    model.updated_at = utcnow()
    session.add(model)
    content_changed(session, "model", [model.id])
    session.commit()
    session.refresh(file_row)
    return file_row
