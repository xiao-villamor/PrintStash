"""Ingestion orchestrator — runs in a FastAPI BackgroundTask."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional, ParamSpec, TypeVar

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
    StagingLease,
    User,
)
from app.db.scopes import live
from app.db.session import SessionFactory
from app.services import gcode_parser, rbac, storage, taxonomy, thumbnail
from app.services.hashing import sha256_file
from app.services.jobs import registry
from app.services.mesh_processing import FallbackThumbnail
from app.services.profile_detection import upsert_detected_profiles
from app.services.storage_backend import StorageCollisionError, get_backend
from app.services.storage_ownership import record_creation

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


def _fault_injection_checkpoint(_stage: str, _job_id: str) -> None:
    """Stable monkeypatch seam for commit-boundary regression tests."""


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
        primary_key = artifact.path

    backend = get_backend()
    if not backend.exists(primary_key):
        raise ArtifactDurabilityError("artifact_blob_not_durable")
    if thumbnail_status in {"generated", "fallback_generated"}:
        if not backend.exists(backend.thumbnail_key(file_id)):
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
    version = _reserve_next_version(session, model_id)
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
    blob_receipt = None
    thumbnail_receipt = None
    try:
        if move_blob:
            # ``move_in`` performs the only authoritative collision check using
            # the backend's atomic create-only primitive. An earlier exists()
            # check would be a TOCTOU race.
            blob_receipt = backend.move_in(staged_path, dest_key)
        size_bytes = (
            blob_receipt.size
            if blob_receipt is not None
            else backend.stat_size(dest_key)
        )

        # For write-back into a NAS library, capture the on-disk mtime of the file we
        # just wrote so the next scan recognises it as unchanged (no re-import).
        if is_external and source_mtime is None:
            direct = backend.direct_path(dest_key)
            if direct is not None:
                try:
                    source_mtime = direct.stat().st_mtime
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
        # One transaction for the whole artifact: a File row committed before its
        # Metadata is a model that renders with no print time, filament or cost and
        # no error to explain it. flush() allocates the id the thumbnail key needs
        # without ending the transaction.
        session.add(file_row)
        session.flush()
        assert file_row.id is not None
        if blob_receipt is not None and not is_external:
            record_creation(session, blob_receipt, object_kind="artifact")

        if thumb_bytes:
            candidate_thumbnail_key = backend.thumbnail_key(file_row.id)
            try:
                encoded_thumbnail = thumbnail.to_webp(thumb_bytes)
                thumbnail_receipt = backend.create_bytes(
                    encoded_thumbnail, candidate_thumbnail_key
                )
            except Exception:  # noqa: BLE001 - thumbnail is a retryable derivative
                logger.exception(
                    "thumbnail derivation failed; continuing Artifact persistence",
                    extra={"file_id": file_row.id},
                )
            else:
                record_creation(session, thumbnail_receipt, object_kind="thumbnail")
                file_row.thumbnail_path = candidate_thumbnail_key
                session.add(file_row)
                if overwrite_thumbnail or not model.thumbnail_path:
                    model.thumbnail_path = candidate_thumbnail_key
                    model.thumbnail_file_id = file_row.id
                    session.add(model)

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
        session.commit()
    except Exception:
        session.rollback()
        # Delete only exact destinations selected by this failed write.  Never
        # rely on a later directory walk to infer ownership.
        if thumbnail_receipt is not None:
            backend.rollback_create(thumbnail_receipt)
        # External-library bytes become user-owned at publication and are never
        # removed by rollback cleanup. A failed DB transaction may leave an
        # unindexed file, which is safer and the next scan can discover it.
        if blob_receipt is not None and not is_external:
            backend.rollback_create(blob_receipt)
        raise

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
    from app.services.runtime_config import external_libraries_enabled

    vault = WriteTarget(None, False, None, None)
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
        return vault
    backend = get_backend()
    if backend.direct_path(backend.blob_key("probe", 0, "probe")) is None:
        raise RuntimeError("external_library_requires_local_storage_backend")

    root = Path(library.root_path)
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
    return WriteTarget(str(dest_path), True, library_id, None)


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
) -> None:
    """Full ingestion pipeline.

    Hash, dedup, persist the model, version-manage the file blob, extract
    thumbnail, build metadata — all behind a single call. The *strategy*
    determines what parse+thumbnail variant runs (gcode or mesh). The
    *session_factory* is a callable that returns a new SQLModel Session;
    when absent, falls back to the module-level engine (legacy).
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

    registry.update(job_id, state="running", total_steps=total_steps)

    if session_factory is None:
        from app.db.session import get_session_factory

        session_factory = get_session_factory()

    try:
        with session_factory.scoped_session() as recovery_session:
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
                            StagingLease.background_job_id == job_id
                        )
                    ).first()
                    if lease is not None:
                        cleanup_session.delete(lease)
                        cleanup_session.commit()
                return
        report("hashing")
        blob_hash = sha256_file(staged_path)
        logger.info("ingestion_job job_id=%s stage=hashed result=running", job_id)

        meta, thumb_bytes = strategy.process(staged_path, report)
        if thumb_bytes is None and strategy.file_type not in (FileType.GCODE,):
            logger.warning(
                "ingestion_job job_id=%s stage=thumbnail result=missing", job_id
            )
        elif thumb_bytes:
            logger.info(
                "ingestion_job job_id=%s stage=thumbnail result=generated", job_id
            )

        dedup_hash = (
            source_hash.lower()
            if strategy.file_type == FileType.GCODE and source_hash
            else blob_hash
        )

        report("persisting")
        durable_ids: tuple[int, int] | None = None
        thumbnail_status = (
            "fallback_generated"
            if isinstance(thumb_bytes, FallbackThumbnail)
            else "generated"
            if thumb_bytes
            else "skipped"
            if strategy.file_type == FileType.GCODE
            else "failed"
        )
        thumbnail_reason = (
            None
            if thumb_bytes
            else "no_embedded_thumbnail"
            if strategy.file_type == FileType.GCODE
            else "renderer_no_output"
        )
        created = False
        with session_factory.scoped_session() as session:
            actor = (
                session.get(User, actor_user_id) if actor_user_id is not None else None
            )
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
            )
            assert file_row.id is not None
            durable_ids = (model.id, file_row.id)
            try:
                upsert_detected_profiles(session, meta)
            except Exception:  # noqa: BLE001 - derived data never invalidates Artifact
                logger.exception(
                    "ingestion_job job_id=%s derived profiles failed", job_id
                )

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
            completion="partial" if thumbnail_status == "failed" else "complete",
            thumbnail_status=thumbnail_status,  # type: ignore[arg-type]
            thumbnail_reason=thumbnail_reason,
            result={"created": created, "name": original_filename},
        )
        staged_path.unlink(missing_ok=True)
        with session_factory.scoped_session() as cleanup_session:
            lease = cleanup_session.exec(
                select(StagingLease).where(StagingLease.background_job_id == job_id)
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


def _mesh_strategy(file_type: FileType) -> IngestionStrategy:
    from app.services import mesh_processing

    def process(
        path: Path, report: ProgressFn = _noop_progress
    ) -> tuple[dict[str, Any], bytes | None]:
        # Single mesh load for both geometry and thumbnail.
        return mesh_processing.analyze_mesh(path, report=report)

    return IngestionStrategy(
        file_type=file_type,
        overwrite_thumbnail=True,
        process=process,
        step_labels=("loading_mesh", "extracting_geometry", "rendering_thumbnail"),
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
) -> None:
    """Public entry point for mesh ingestion (called from the model upload router)."""
    run_ingestion_pipeline(
        job_id=job_id,
        staged_path=staged_path,
        original_filename=original_filename,
        model_name=model_name,
        collection=collection,
        tags=tags,
        source_hash=source_hash,
        strategy=_mesh_strategy(file_type),
        actor_user_id=actor_user_id,
        session_factory=session_factory,
        source_url=source_url,
        target_library_id=target_library_id,
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
) -> File:
    """Attach a staged G-code file as a new revision of an existing model."""
    assert model.id is not None
    blob_hash = sha256_file(staged_path)
    meta, thumb_bytes = _gcode_strategy().process(staged_path, _noop_progress)

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
    )
    assert file_row.id is not None

    try:
        upsert_detected_profiles(session, meta)
    except Exception:  # noqa: BLE001 - derived profile can be repaired independently
        logger.exception(
            "gcode revision profile derivation failed", extra={"file_id": file_row.id}
        )

    model.updated_at = utcnow()
    session.add(model)
    session.commit()
    session.refresh(file_row)
    return file_row
