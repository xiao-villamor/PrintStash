"""Ingestion orchestrator — runs in a FastAPI BackgroundTask."""

from __future__ import annotations

import threading
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
    User,
)
from app.db.scopes import live
from app.db.session import SessionFactory
from app.services import gcode_parser, rbac, storage, taxonomy, thumbnail
from app.services.hashing import sha256_file
from app.services.jobs import registry
from app.services.profile_detection import upsert_detected_profiles
from app.services.storage_backend import get_backend

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
        update(Model)
        .where(Model.id == model_id)
        .values(next_file_version=reserved + 1)
        .returning(Model.next_file_version)
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
        else backend.blob_key(model.slug, version, original_filename)
    )

    if move_blob:
        backend.move_in(staged_path, dest_key)
    size_bytes = backend.stat_size(dest_key)

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
    )
    # One transaction for the whole artifact: a File row committed before its
    # Metadata is a model that renders with no print time, filament or cost and
    # no error to explain it. flush() allocates the id the thumbnail key needs
    # without ending the transaction.
    try:
        session.add(file_row)
        session.flush()
        assert file_row.id is not None

        if thumb_bytes:
            thumb_key = backend.thumbnail_key(file_row.id)
            backend.write_bytes(thumbnail.to_webp(thumb_bytes), thumb_key)
            if overwrite_thumbnail or not model.thumbnail_path:
                model.thumbnail_path = thumb_key
                model.thumbnail_file_id = file_row.id
                session.add(model)

        # The parser may carry detection-only keys (e.g. printer_preset_name)
        # that have no Metadata column.
        md_fields = {k: v for k, v in meta.items() if k in Metadata.model_fields}
        session.add(Metadata(file_id=file_row.id, **md_fields))
        session.commit()
    except Exception:
        session.rollback()
        # The blob was already moved into place. Leaving it is safe: no row
        # claims it, so the orphan sweep collects it (see storage_utils).
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
    logger.info("ingest[%s] start file=%s", job_id, original_filename)

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
        report("hashing")
        blob_hash = sha256_file(staged_path)
        logger.info("ingest[%s] sha256=%s", job_id, blob_hash)

        meta, thumb_bytes = strategy.process(staged_path, report)
        if thumb_bytes is None and strategy.file_type not in (FileType.GCODE,):
            logger.warning("ingest[%s] thumbnail render returned None", job_id)
        elif thumb_bytes:
            logger.info(
                "ingest[%s] thumbnail extracted (%d bytes)", job_id, len(thumb_bytes)
            )

        dedup_hash = (source_hash or blob_hash).lower()

        report("persisting")
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
            logger.info(
                "ingest[%s] %s model_id=%s slug=%s",
                job_id,
                "new" if created else "dedup hit",
                model.id,
                model.slug,
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
            )
            assert file_row.id is not None

            upsert_detected_profiles(session, meta)

            registry.update(
                job_id,
                state="completed",
                model_id=model.id,
                file_id=file_row.id,
                processed=1,
                total=1,
                succeeded=1,
                deduplicated=0 if created else 1,
                result={"created": created, "name": original_filename},
            )
            logger.info(
                "ingest[%s] done model_id=%s file_id=%s v=%s",
                job_id,
                model.id,
                file_row.id,
                file_row.version,
            )

    except Exception as exc:  # noqa: BLE001 — top-level task boundary
        logger.exception("ingest[%s] failed: %s", job_id, exc)
        registry.update(job_id, state="failed", error=str(exc))


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
    meta, thumb_bytes = _gcode_strategy().process(staged_path)

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

    upsert_detected_profiles(session, meta)

    if is_recommended:
        other_gcode = session.exec(
            select(File).where(
                File.model_id == model.id,
                File.id != file_row.id,
                File.file_type == FileType.GCODE,
                live(File),
            )
        ).all()
        for other in other_gcode:
            other.is_recommended = False
            session.add(other)

    model.updated_at = utcnow()
    session.add(model)
    session.commit()
    session.refresh(file_row)
    return file_row
