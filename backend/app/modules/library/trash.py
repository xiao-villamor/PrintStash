"""Trash lifecycle for the library — the single owner of soft-delete semantics.

Soft-delete → restore → expiry → hard delete (rows + explicitly owned blobs)
all live here. Query-side filtering uses ``app.db.scopes.live/trashed``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from sqlalchemy import update
from sqlmodel import Session, col, delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    ArtifactDerivative,
    ArtifactMaterialRequirement,
    Collection,
    CollectionTagLink,
    Document,
    ExternalLibrary,
    ExternalLibraryTombstone,
    File,
    FileTagLink,
    FileType,
    InboxItem,
    Metadata,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    ModelStar,
    MultipartModelChoice,
    MultipartPart,
    OwnedStorageObject,
    PrintBatch,
    Printer,
    PrinterFile,
    PrintJob,
    ShareLink,
    StorageObjectState,
    Tag,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
)
from app.db.projections import content_changed
from app.db.scopes import live, trashed
from app.db.session import get_session_factory
from app.modules.library import part_options
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageBackend,
    StorageTier,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import (
    PreparedOwnedDeletion,
    enqueue_prepared_owned_deletion,
    enqueue_prevalidated_receipt,
    prepare_owned_key_deletion,
    process_storage_delete_intents,
    record_legacy_blocked_intent,
)
from app.modules.storage.storage_ownership import (
    UnsafeStorageDeleteError,
    provider_ref_for_backend,
    require_owned_key,
    sweep_orphaned_publications,
)
from app.runtime.maintenance import guarded_destructive_operation

logger = get_logger(__name__)
_DOCUMENT_IMAGE_RE = re.compile(
    r"/api/v1/documents/(\d+)/images/([0-9a-f]{64}\.(?:png|jpe?g|gif|webp))"
)
_COLLECTION_IMAGE_RE = re.compile(
    r"/api/v1/collections/(\d+)/images/([0-9a-f]{64}\.(?:png|jpe?g|gif|webp))"
)


class PurgeConflictError(UnsafeStorageDeleteError):
    """The resource changed or was restored before its purge claim landed."""


class SourceFileLifecycleError(ValueError):
    """A file cannot use the managed source Artifact lifecycle."""


def _require_managed_source(file_row: File) -> None:
    if file_row.file_type == FileType.GCODE:
        raise SourceFileLifecycleError("source_file_required")
    if file_row.is_external:
        raise SourceFileLifecycleError("linked_source_protected")


def soft_delete_source_file(
    session: Session, model: Model, file_row: File, actor: User
) -> None:
    """Trash one managed source without changing the Model or its Revisions."""
    _require_managed_source(file_row)
    now = utcnow()
    file_row.deleted_at = now
    file_row.deleted_by = actor.id
    if model.thumbnail_file_id == file_row.id:
        model.thumbnail_file_id = None
        model.thumbnail_path = None
    model.updated_at = now
    session.add(file_row)
    session.add(model)
    content_changed(session, "model", [model.id])
    session.commit()


def restore_source_file(session: Session, model: Model, file_row: File) -> None:
    """Restore a source while keeping purge ownership and tombstone guards."""
    _require_managed_source(file_row)
    restore_resource(session, file_row, commit=False)
    model.updated_at = utcnow()
    session.add(model)
    session.commit()


class StorageRiskConfirmationRequired(UnsafeStorageDeleteError):
    """A non-Verified backend requires one-shot destructive confirmation."""

    def __init__(self, operation: str) -> None:
        self.tier = get_backend().capabilities.tier
        self.operation = operation
        super().__init__("storage_risk_confirmation_required")

    @property
    def detail(self) -> dict[str, str]:
        return {
            "code": "storage_risk_confirmation_required",
            "tier": self.tier.value,
            "operation": self.operation,
            "required_confirmation": "confirm_storage_risk=true",
        }


def _require_storage_risk(
    *, operation: str, storage_backed: bool, confirmed: bool
) -> None:
    if (
        storage_backed
        and get_backend().capabilities.tier is not StorageTier.VERIFIED
        and not confirmed
    ):
        raise StorageRiskConfirmationRequired(operation)


def _require_destructive_maintenance_safe(session: Session) -> None:
    unsafe = session.exec(
        select(VaultAuditFinding.id).where(
            VaultAuditFinding.code == "managed_storage_namespace_escape",
            VaultAuditFinding.state == VaultAuditFindingState.OPEN,
        )
    ).first()
    if unsafe is not None:
        raise UnsafeStorageDeleteError("storage_cleanup_blocked")


def _claim_purge(session: Session, resource) -> str:
    if resource.id is None:
        raise PurgeConflictError("storage_cleanup_blocked")
    token = uuid.uuid4().hex
    statement = (
        update(type(resource))
        .where(
            type(resource).id == resource.id,
            type(resource).deleted_at == resource.deleted_at,
            type(resource).purge_token == None,  # noqa: E711
        )
        .values(purge_token=token)
        .returning(type(resource).id)
    )
    if session.execute(statement).scalar_one_or_none() is None:
        raise PurgeConflictError("storage_cleanup_blocked")
    resource.purge_token = token
    return token


def _verify_destructive_access(backend: StorageBackend, keys: list[str]) -> None:
    """Keep provider probe failures inside the destructive-operation contract."""
    try:
        backend.verify_destructive_access(keys)
    except Exception as exc:
        raise UnsafeStorageDeleteError("storage_delete_access_unverified") from exc


def _preflight_primary_keys(
    session: Session, keys: Iterable[str], *, allow_unverified: bool = False
) -> None:
    backend = get_backend()
    exact_keys = list(dict.fromkeys(keys))
    if not exact_keys:
        return
    # Abort read-only/permission failures before deleting the first byte.
    _verify_destructive_access(backend, exact_keys)
    for key in exact_keys:
        if not allow_unverified:
            require_owned_key(session, backend, key)


def _preflight_primary_files(
    session: Session, files: Iterable[File], *, allow_unverified: bool = False
) -> None:
    """Verify current receipts or reconstruct proof for pre-0.12 Artifacts."""
    backend = get_backend()
    rows = [file_row for file_row in files if not file_row.is_external]
    if not rows:
        return
    _verify_destructive_access(backend, list(dict.fromkeys(row.path for row in rows)))
    for file_row in rows:
        if allow_unverified:
            continue
        _prepare_primary_artifact(session, file_row, allow_unverified=allow_unverified)


@dataclass(frozen=True)
class _VerifiedPurgeDeletion:
    receipt: CreationReceipt
    sha256: str
    resource_id: int


@dataclass(frozen=True)
class _LegacyPurgeDeletion:
    key: str
    size_bytes: int
    sha256: str
    resource_id: int


PurgeDeletion = PreparedOwnedDeletion | _VerifiedPurgeDeletion | _LegacyPurgeDeletion


def _prepare_primary_artifact(
    session: Session, file_row: File, *, allow_unverified: bool
) -> PurgeDeletion:
    backend = get_backend()
    _verify_destructive_access(backend, [file_row.path])
    prepared = prepare_owned_key_deletion(
        session,
        backend,
        file_row.path,
        resource_kind="file",
        resource_id=file_row.id,
        allow_unverified=allow_unverified,
    )
    if prepared is not None:
        return prepared
    if file_row.id is None:
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    if allow_unverified:
        return _LegacyPurgeDeletion(
            file_row.path, file_row.size_bytes, file_row.sha256, file_row.id
        )
    namespace = backend.namespace_for(file_row.path)
    current_ref = provider_ref_for_backend(backend, namespace=namespace)
    existing = session.exec(
        select(OwnedStorageObject.id).where(
            OwnedStorageObject.backend == backend.backend_name,
            OwnedStorageObject.namespace == namespace,
            OwnedStorageObject.key == file_row.path,
            OwnedStorageObject.provider_ref == current_ref,
            OwnedStorageObject.state == StorageObjectState.COMMITTED,
        )
    ).first()
    if existing is not None:
        raise UnsafeStorageDeleteError("storage_object_no_longer_matches_receipt")
    try:
        receipt = backend.adopt_existing(
            file_row.path,
            expected_size=file_row.size_bytes,
            expected_sha256=file_row.sha256,
        )
    except Exception as exc:
        raise UnsafeStorageDeleteError("storage_ownership_unverified") from exc
    if receipt.backend == "local" and receipt.provider_ref is None:
        # Local adoption has already verified the physical object. Bind that
        # proof to this installation before handing it to durable deletion.
        receipt = replace(receipt, provider_ref=current_ref)
    if (
        receipt.backend != backend.backend_name
        or receipt.namespace != namespace
        or receipt.key != file_row.path
        or receipt.provider_ref != current_ref
    ):
        raise UnsafeStorageDeleteError("storage_ownership_unverified")
    return _VerifiedPurgeDeletion(receipt, file_row.sha256, file_row.id)


def prepare_purge_deletions(
    session: Session,
    resources: Iterable[Model | File | Document | Collection],
    *,
    confirm_storage_risk: bool = False,
) -> list[PurgeDeletion]:
    """Preflight a complete purge batch before domain claims or locator locks."""
    backend = get_backend()
    resources = list(resources)
    files: dict[int, File] = {}
    keys: list[tuple[str, bool, str, int]] = []
    for resource in resources:
        if resource.id is None:
            continue
        if isinstance(resource, Model):
            for file_row in session.exec(
                select(File).where(File.model_id == resource.id)
            ).all():
                if file_row.id is not None:
                    files[file_row.id] = file_row
            covers = session.exec(
                select(ModelSourceCover)
                .join(ModelProvenanceSource)
                .where(ModelProvenanceSource.model_id == resource.id)
            ).all()
            keys.extend(
                (cover.storage_key, True, "model_source_cover", cover.id)
                for cover in covers
                if cover.id is not None
            )
        elif isinstance(resource, File):
            files[resource.id] = resource
        elif isinstance(resource, Document):
            if resource.filename:
                keys.append(
                    (
                        backend.document_file_key(resource.id, resource.filename),
                        True,
                        "document",
                        resource.id,
                    )
                )
            keys.extend(
                (
                    backend.document_image_key(resource.id, name),
                    False,
                    "document_image",
                    resource.id,
                )
                for identifier, name in _DOCUMENT_IMAGE_RE.findall(resource.body or "")
                if int(identifier) == resource.id
            )
        elif isinstance(resource, Collection):
            keys.extend(
                (
                    backend.collection_image_key(resource.id, name),
                    False,
                    "collection_image",
                    resource.id,
                )
                for identifier, name in _COLLECTION_IMAGE_RE.findall(
                    resource.readme or ""
                )
                if int(identifier) == resource.id
            )
        else:
            raise ValueError("purge_resource_unsupported")
    prepared: list[PurgeDeletion] = []
    for identifier, file_row in files.items():
        if not file_row.is_external:
            prepared.append(
                _prepare_primary_artifact(
                    session, file_row, allow_unverified=confirm_storage_risk
                )
            )
        current = file_row.thumbnail_path or backend.thumbnail_key(identifier)
        keys.extend(
            (key, False, kind, identifier)
            for key, kind in (
                (current, "file_thumbnail"),
                (backend.thumbnail_key(identifier), "file_thumbnail_legacy_webp"),
                (backend.legacy_thumbnail_key(identifier), "file_thumbnail_legacy"),
            )
        )
        keys.extend(
            (key, False, "artifact_derivative", identifier)
            for key in session.exec(
                select(ArtifactDerivative.storage_key).where(
                    ArtifactDerivative.file_id == identifier,
                    col(ArtifactDerivative.storage_key).is_not(None),
                )
            ).all()
            if key
        )
        shared_owner = session.exec(
            select(File.id).where(
                col(File.id).not_in(list(files)), File.sha256 == file_row.sha256
            )
        ).first()
        if shared_owner is None and file_row.sha256:
            keys.append(
                (backend.stl_cache_key(file_row.sha256), False, "stl_cache", identifier)
            )
    seen = {
        value.receipt.key if not isinstance(value, _LegacyPurgeDeletion) else value.key
        for value in prepared
    }
    for key, required, kind, resource_id in keys:
        if key in seen:
            continue
        seen.add(key)
        if required:
            _verify_destructive_access(backend, [key])
        value = prepare_owned_key_deletion(
            session,
            backend,
            key,
            required_proof=required,
            resource_kind=kind,
            resource_id=resource_id,
            allow_unverified=confirm_storage_risk,
        )
        if value is not None:
            prepared.append(value)
    return prepared


def enqueue_purge_deletions(session: Session, prepared: list[PurgeDeletion]) -> None:
    """Flush all logical removal before attaching sorted SQL-only retirement."""
    session.flush()
    backend = get_backend()

    def key(value: PurgeDeletion):
        if isinstance(value, _LegacyPurgeDeletion):
            return backend.backend_name, backend.namespace_for(value.key), value.key
        return value.receipt.backend, value.receipt.namespace, value.receipt.key

    for value in sorted(prepared, key=key):
        if isinstance(value, _LegacyPurgeDeletion):
            record_legacy_blocked_intent(
                session,
                backend,
                key=value.key,
                size_bytes=value.size_bytes,
                sha256=value.sha256,
                object_kind="legacy_artifact",
                resource_id=value.resource_id,
            )
        elif isinstance(value, _VerifiedPurgeDeletion):
            enqueue_prevalidated_receipt(
                session,
                value.receipt,
                object_kind="legacy_artifact",
                sha256=value.sha256,
                resource_kind="file",
                resource_id=value.resource_id,
            )
        else:
            enqueue_prepared_owned_deletion(
                session,
                value,
                required_proof=value.resource_kind
                in {"file", "document", "model_source_cover"},
            )


def trash_expires_at(
    deleted_at: datetime | None, retention_days: int
) -> datetime | None:
    if deleted_at is None or retention_days < 0:
        return None
    return deleted_at + timedelta(days=retention_days)


def soft_delete_model(session: Session, model: Model) -> None:
    """Move a model to the trash."""
    model.deleted_at = utcnow()
    model.updated_at = utcnow()
    _record_model_tombstones(session, model)
    session.add(model)
    content_changed(session, "model", [model.id])
    session.commit()


def soft_delete_models(session: Session, models: Iterable[Model]) -> None:
    """Move several models to the trash without committing.

    Caller is responsible for the single ``session.commit()`` so a batch is
    persisted atomically.
    """
    now = utcnow()
    changed_ids = []
    for model in models:
        model.deleted_at = now
        model.updated_at = now
        _record_model_tombstones(session, model)
        session.add(model)
        changed_ids.append(model.id)
    content_changed(session, "model", changed_ids)


def record_source_tombstone(session: Session, file_row: File, reason: str) -> None:
    """Persist user intent so discovery cannot immediately resurrect a source."""
    if not file_row.is_external or file_row.external_library_id is None:
        return
    source_key = file_row.source_key
    if not source_key:
        library = session.get(ExternalLibrary, file_row.external_library_id)
        if library is None:
            return
        try:
            source_key = (
                Path(file_row.path)
                .relative_to(Path(library.root_path).expanduser().resolve(strict=False))
                .as_posix()
            )
        except ValueError:
            return
        file_row.source_key = source_key
        session.add(file_row)
    tombstone = session.exec(
        select(ExternalLibraryTombstone).where(
            ExternalLibraryTombstone.library_id == file_row.external_library_id,
            ExternalLibraryTombstone.source_key == source_key,
        )
    ).first()
    if tombstone is None:
        tombstone = ExternalLibraryTombstone(
            library_id=file_row.external_library_id,
            source_key=source_key,
        )
    tombstone.sha256 = file_row.sha256
    tombstone.reason = reason[:32]
    tombstone.cleared_at = None
    session.add(tombstone)


def _record_model_tombstones(session: Session, model: Model) -> None:
    if model.id is None:
        return
    for file_row in session.exec(select(File).where(File.model_id == model.id)).all():
        record_source_tombstone(session, file_row, "model_trashed")


def _clear_model_tombstones(session: Session, model: Model) -> None:
    if model.id is None:
        return
    now = utcnow()
    for file_row in session.exec(select(File).where(File.model_id == model.id)).all():
        _clear_file_tombstone(session, file_row, now=now)


def _clear_file_tombstone(
    session: Session, file_row: File, *, now: datetime | None = None
) -> None:
    if file_row.external_library_id is None or not file_row.source_key:
        return
    tombstone = session.exec(
        select(ExternalLibraryTombstone).where(
            ExternalLibraryTombstone.library_id == file_row.external_library_id,
            ExternalLibraryTombstone.source_key == file_row.source_key,
            ExternalLibraryTombstone.cleared_at == None,  # noqa: E711
        )
    ).first()
    if tombstone is not None:
        tombstone.cleared_at = now or utcnow()
        session.add(tombstone)


def restore_resource(session: Session, resource, *, commit: bool = True) -> None:
    """Restore one soft-deleted row unless a purge already owns it.

    Once ``purge_token`` is set, storage deletion may already have crossed the
    database boundary. Restoring that row would expose an object whose bytes can
    disappear underneath it, so every restore entrypoint shares this guard.
    """
    if resource.deleted_at is None:
        return
    if getattr(resource, "purge_token", None) is not None:
        raise PurgeConflictError("storage_cleanup_blocked")
    if isinstance(resource, Model):
        _clear_model_tombstones(session, resource)
    elif isinstance(resource, File):
        _clear_file_tombstone(session, resource)
    resource.deleted_at = None
    if hasattr(resource, "deleted_by"):
        resource.deleted_by = None
    if hasattr(resource, "updated_at"):
        resource.updated_at = utcnow()
    session.add(resource)
    if isinstance(resource, File):
        content_changed(session, "model", [resource.model_id])
    elif isinstance(resource, (Model, Collection, Document)):
        content_changed(session, type(resource).__name__.lower(), [resource.id])
    if commit:
        session.commit()


def restore_model(session: Session, model: Model) -> None:
    """Bring a model back from the trash. No-op when it is live."""
    restore_resource(session, model)


@guarded_destructive_operation
def hard_delete_file(
    session: Session,
    file_row: File,
    *,
    maintain_revision_invariant: bool = True,
    ownership_preflighted: bool = False,
    purge_claimed_by_parent: bool = False,
    confirm_storage_risk: bool = False,
    prepared_deletions: list[PurgeDeletion] | None = None,
) -> None:
    """Permanently remove one Artifact and every vault-owned dependent.

    Linked external bytes belong to the user and are never deleted. The caller
    owns the surrounding transaction and commit.
    """
    if file_row.id is None:
        return
    if not purge_claimed_by_parent:
        _require_storage_risk(
            operation="purge_file",
            storage_backed=True,
            confirmed=confirm_storage_risk,
        )
    own_deletions = prepared_deletions is None
    deletions = (
        prepare_purge_deletions(
            session, [file_row], confirm_storage_risk=confirm_storage_risk
        )
        if own_deletions
        else prepared_deletions
    )
    assert deletions is not None
    _require_destructive_maintenance_safe(session)
    if not purge_claimed_by_parent:
        _claim_purge(session, file_row)

    file_id = int(file_row.id)
    model = session.get(Model, file_row.model_id)
    if model is not None and model.thumbnail_file_id == file_id:
        model.thumbnail_file_id = None
        model.thumbnail_path = None
        model.updated_at = utcnow()
        session.add(model)

    was_live_recommended = (
        maintain_revision_invariant
        and file_row.file_type == FileType.GCODE
        and file_row.deleted_at is None
        and file_row.is_recommended
    )
    if was_live_recommended:
        file_row.is_recommended = False
        session.add(file_row)
        session.flush()
        replacement = session.exec(
            select(File)
            .where(
                File.model_id == file_row.model_id,
                File.id != file_id,
                File.file_type == FileType.GCODE,
                live(File),
            )
            .order_by(File.version.desc())  # type: ignore[attr-defined]
        ).first()
        if replacement is not None:
            replacement.is_recommended = True
            session.add(replacement)

    # Every table with a NOT NULL foreign key to this row, or the delete is refused.
    # `foreign_keys=ON` is a production pragma and these are all `RESTRICT`, so a
    # child left behind is a failed purge rather than a dangling id — and the two
    # rows the ownership ledger cares about are already gone by this point.
    session.exec(delete(PrinterFile).where(col(PrinterFile.file_id) == file_id))
    part_options.remove_file_from_groups(session, file_id)
    session.exec(delete(FileTagLink).where(col(FileTagLink.file_id) == file_id))
    session.exec(delete(PrintJob).where(col(PrintJob.file_id) == file_id))
    session.exec(delete(Metadata).where(col(Metadata.file_id) == file_id))
    session.exec(delete(PrintBatch).where(col(PrintBatch.file_id) == file_id))
    session.exec(
        delete(ArtifactMaterialRequirement).where(
            col(ArtifactMaterialRequirement.file_id) == file_id
        )
    )
    session.delete(file_row)
    content_changed(session, "model", [file_row.model_id])

    if own_deletions:
        enqueue_purge_deletions(session, deletions)


@guarded_destructive_operation
def hard_delete_document(
    session: Session,
    document: Document,
    *,
    ownership_preflighted: bool = False,
    confirm_storage_risk: bool = False,
    prepared_deletions: list[PurgeDeletion] | None = None,
) -> None:
    """Permanently remove a Document row and every vault-owned blob."""
    if document.id is None:
        return
    storage_backed = bool(document.filename) or bool(
        _DOCUMENT_IMAGE_RE.search(document.body or "")
    )
    _require_storage_risk(
        operation="purge_document",
        storage_backed=storage_backed,
        confirmed=confirm_storage_risk,
    )
    own_deletions = prepared_deletions is None
    deletions = (
        prepare_purge_deletions(
            session, [document], confirm_storage_risk=confirm_storage_risk
        )
        if own_deletions
        else prepared_deletions
    )
    assert deletions is not None
    _require_destructive_maintenance_safe(session)
    _claim_purge(session, document)
    session.delete(document)
    content_changed(session, "document", [document.id])

    if own_deletions:
        enqueue_purge_deletions(session, deletions)


def restore_document(session: Session, document: Document) -> None:
    restore_resource(session, document, commit=False)


@guarded_destructive_operation
def hard_delete_collection(
    session: Session,
    collection: Collection,
    *,
    confirm_storage_risk: bool = False,
    prepared_deletions: list[PurgeDeletion] | None = None,
) -> None:
    """Permanently remove a Collection and its explicitly referenced images."""
    if collection.id is None:
        return
    _require_storage_risk(
        operation="purge_collection",
        storage_backed=bool(_COLLECTION_IMAGE_RE.search(collection.readme or "")),
        confirmed=confirm_storage_risk,
    )
    own_deletions = prepared_deletions is None
    deletions = (
        prepare_purge_deletions(
            session, [collection], confirm_storage_risk=confirm_storage_risk
        )
        if own_deletions
        else prepared_deletions
    )
    assert deletions is not None
    _require_destructive_maintenance_safe(session)
    _claim_purge(session, collection)
    session.exec(
        delete(CollectionTagLink).where(
            col(CollectionTagLink.collection_id) == collection.id
        )
    )
    session.delete(collection)
    content_changed(session, "collection", [collection.id])

    if own_deletions:
        enqueue_purge_deletions(session, deletions)


@guarded_destructive_operation
def hard_delete_model(
    session: Session,
    model: Model,
    *,
    ownership_preflighted: bool = False,
    confirm_storage_risk: bool = False,
    prepared_deletions: list[PurgeDeletion] | None = None,
) -> None:
    """Permanently remove a model, related DB rows, and stored blobs."""
    if model.id is None:
        return
    file_rows = session.exec(select(File).where(File.model_id == model.id)).all()
    has_cover = session.exec(
        select(ModelSourceCover.id)
        .join(ModelProvenanceSource)
        .where(ModelProvenanceSource.model_id == model.id)
        .limit(1)
    ).first()
    _require_storage_risk(
        operation="purge_model",
        storage_backed=bool(file_rows) or has_cover is not None,
        confirmed=confirm_storage_risk,
    )

    own_deletions = prepared_deletions is None
    deletions = (
        prepare_purge_deletions(
            session, [model], confirm_storage_risk=confirm_storage_risk
        )
        if own_deletions
        else prepared_deletions
    )
    assert deletions is not None
    _require_destructive_maintenance_safe(session)

    _claim_purge(session, model)
    model.thumbnail_file_id = None
    model.thumbnail_path = None
    session.add(model)
    session.flush()
    for file_row in file_rows:
        hard_delete_file(
            session,
            file_row,
            maintain_revision_invariant=False,
            ownership_preflighted=True,
            purge_claimed_by_parent=True,
            confirm_storage_risk=confirm_storage_risk,
            prepared_deletions=deletions,
        )
    session.flush()

    session.exec(delete(ShareLink).where(col(ShareLink.model_id) == model.id))
    inbox_rows = session.exec(
        select(InboxItem).where(InboxItem.resulting_model_id == model.id)
    ).all()
    for inbox in inbox_rows:
        inbox.resulting_model_id = None
        session.add(inbox)
    session.exec(delete(ModelStar).where(col(ModelStar.model_id) == model.id))
    # Multipart compositions reference Models without owning them. Detach this
    # member before the restrictive Model FK is enforced; an empty part no
    # longer represents a useful choice and is removed, while its aggregate
    # remains available to be edited.
    member_part_ids = session.exec(
        select(MultipartModelChoice.multipart_part_id).where(
            col(MultipartModelChoice.model_id) == model.id
        )
    ).all()
    session.exec(
        delete(MultipartModelChoice).where(
            col(MultipartModelChoice.model_id) == model.id
        )
    )
    session.flush()
    for part_id in member_part_ids:
        if (
            session.exec(
                select(MultipartModelChoice.id)
                .where(MultipartModelChoice.multipart_part_id == part_id)
                .limit(1)
            ).first()
            is None
        ):
            session.exec(delete(MultipartPart).where(col(MultipartPart.id) == part_id))
    part_options.remove_model_from_groups(session, model.id)
    # Don't bulk-delete the tag links here: ``Model.tags`` is a link_model
    # (many-to-many) relationship, so deleting the model already removes its
    # ModelTagLink rows. Doing both makes the ORM's cascade try to delete rows
    # this manual DELETE already removed -> StaleDataError on commit (purging any
    # *tagged* model, including the expired-trash cron, would 500).
    session.delete(model)
    content_changed(session, "model", [model.id])

    if own_deletions:
        enqueue_purge_deletions(session, deletions)


def hard_delete_expired_models(
    session: Session,
    retention_days: int,
    *,
    confirm_storage_risk: bool = False,
) -> list[int]:
    if retention_days < 0:
        return []

    cutoff = utcnow() - timedelta(days=retention_days)
    models = session.exec(
        select(Model).where(
            trashed(Model),
            Model.deleted_at <= cutoff,  # type: ignore[operator]
        )
    ).all()
    prepared = prepare_purge_deletions(
        session, models, confirm_storage_risk=confirm_storage_risk
    )
    purged_ids = [model.id for model in models if model.id is not None]
    for model in models:
        hard_delete_model(
            session,
            model,
            ownership_preflighted=True,
            confirm_storage_risk=confirm_storage_risk,
            prepared_deletions=prepared,
        )
    enqueue_purge_deletions(session, prepared)
    return [int(model_id) for model_id in purged_ids]


def _cleanup_orphan_blobs(session: Session) -> int:
    """Never infer ownership by walking configured storage.

    A local ``data_dir`` can be a mistakenly mounted user library, and absence
    from the database is not proof that PrintStash created a file.  Destructive
    cleanup is therefore limited to exact keys held by rows being hard-deleted
    above.  Failed writes clean up their own exact destinations at the write
    site.  Keep this compatibility seam (and the result field) as a no-op so
    older callers cannot accidentally reintroduce discovery-based deletion.
    """
    del session
    return 0


def gc_soft_deleted(
    retention_days: int | None = None,
    *,
    confirm_storage_risk: bool = False,
    scheduled: bool = True,
) -> dict[str, int]:
    """Hourly GC: purge expired trash rows and their exact owned blob keys.

    No-ops while a backup restore is in progress — restore replaces the DB
    file and disposes the engine, so a GC pass racing it would run queries
    against a database that no longer matches its connection.
    """
    from app.runtime.maintenance import restore_in_progress

    if restore_in_progress():
        logger.info("gc skipped: backup restore in progress")
        return {"rows": 0, "orphan_blobs": 0}
    effective_retention = (
        int(settings.trash_retention_days) if retention_days is None else retention_days
    )
    if effective_retention < 0:
        # Retention controls expiry, not the durable delete outbox.  A
        # previously authorized intent must still be retried while operators
        # keep trash indefinitely, otherwise disabling retention strands
        # already-purged bytes forever.
        logger.info("gc expiry skipped: trash retention is disabled")
        storage_result = process_storage_delete_intents()
        return {
            "rows": 0,
            "orphan_blobs": 0,
            "storage_completed": storage_result.completed,
            "storage_pending": storage_result.pending,
            "storage_blocked": storage_result.blocked,
        }
    cutoff = utcnow() - timedelta(days=effective_retention)
    purged = {"rows": 0, "orphan_blobs": 0}
    with get_session_factory().scoped_session() as session:
        expired_models = session.exec(
            select(Model).where(
                trashed(Model),
                Model.deleted_at <= cutoff,  # type: ignore[operator]
            )
        ).all()
        expired_documents = session.exec(
            select(Document).where(
                trashed(Document),
                Document.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        expired_files = session.exec(
            select(File).where(
                trashed(File),
                File.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()

        expired_model_ids = {
            int(model.id) for model in expired_models if model.id is not None
        }
        standalone_expired_files = [
            file_row
            for file_row in expired_files
            if file_row.model_id not in expired_model_ids
        ]

        resources_blocked = 0
        for model in expired_models:
            try:
                hard_delete_model(
                    session,
                    model,
                    confirm_storage_risk=confirm_storage_risk and not scheduled,
                )
                session.commit()
                purged["rows"] += 1
            except UnsafeStorageDeleteError:
                session.rollback()
                resources_blocked += 1
                logger.warning(
                    "gc skipped unverifiable model", extra={"model_id": model.id}
                )
        for document in expired_documents:
            try:
                hard_delete_document(
                    session,
                    document,
                    confirm_storage_risk=confirm_storage_risk and not scheduled,
                )
                session.commit()
                purged["rows"] += 1
            except UnsafeStorageDeleteError:
                session.rollback()
                resources_blocked += 1
                logger.warning(
                    "gc skipped unverifiable document",
                    extra={"document_id": document.id},
                )
        for file_row in standalone_expired_files:
            try:
                hard_delete_file(
                    session,
                    file_row,
                    confirm_storage_risk=confirm_storage_risk and not scheduled,
                )
                session.commit()
                purged["rows"] += 1
            except UnsafeStorageDeleteError:
                session.rollback()
                resources_blocked += 1
                logger.warning(
                    "gc skipped unverifiable artifact", extra={"file_id": file_row.id}
                )
        expired_collections = session.exec(
            select(Collection).where(
                trashed(Collection),
                Collection.deleted_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for collection in expired_collections:
            try:
                hard_delete_collection(
                    session,
                    collection,
                    confirm_storage_risk=confirm_storage_risk and not scheduled,
                )
                session.commit()
                purged["rows"] += 1
            except UnsafeStorageDeleteError:
                session.rollback()
                resources_blocked += 1
                logger.warning(
                    "gc skipped blocked collection",
                    extra={"collection_id": collection.id},
                )
        for model in (Tag, Printer, User):
            result = session.exec(
                delete(model).where(
                    trashed(model),
                    model.deleted_at < cutoff,  # type: ignore[attr-defined]
                )
            )
            purged["rows"] += int(result.rowcount or 0)
        session.commit()
        storage_result = process_storage_delete_intents()
        purged["storage_completed"] = storage_result.completed
        purged["storage_pending"] = storage_result.pending
        purged["storage_blocked"] = storage_result.blocked
        orphan_result = sweep_orphaned_publications(session, get_backend())
        purged["publication_orphans_reclaimed"] = orphan_result.reclaimed
        purged["publication_orphans_cleared"] = orphan_result.cleared
        purged["publication_orphans_blocked"] = orphan_result.blocked
        purged["publication_orphans_pending"] = orphan_result.pending
        session.commit()
        purged["resources_blocked"] = resources_blocked
        purged["orphan_blobs"] = _cleanup_orphan_blobs(session)
    logger.info(
        "gc complete: rows=%s orphan_blobs=%s", purged["rows"], purged["orphan_blobs"]
    )
    return purged
