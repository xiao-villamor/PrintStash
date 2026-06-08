"""Ingestion orchestrator — runs in a FastAPI BackgroundTask."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelTagLink,
)
from app.db.session import SessionFactory
from app.services import gcode_parser, storage, taxonomy, thumbnail
from app.services.hashing import sha256_file
from app.services.jobs import registry
from app.services.profile_detection import upsert_detected_profiles
from app.services.storage_backend import get_backend

logger = get_logger(__name__)


@dataclass
class IngestionStrategy:
    """Variant step in the pipeline: parse a staged file into metadata + thumbnail."""

    file_type: FileType
    overwrite_thumbnail: bool
    process: Callable[[Path], tuple[dict[str, Any], bytes | None]]


def _model_exists_with_slug(session: Session, slug: str) -> bool:
    stmt = select(Model).where(Model.slug == slug)
    return session.exec(stmt).first() is not None


def _next_version_for_model(session: Session, model_id: int) -> int:
    stmt = select(File).where(File.model_id == model_id)
    files = session.exec(stmt).all()
    if not files:
        return 1
    return max(f.version for f in files) + 1


def _apply_taxonomy(
    session: Session,
    model: Model,
    category: Optional[str],
    tags_raw: Optional[str],
    *,
    overwrite_category: bool = False,
) -> None:
    """Resolve & attach category + tags. Idempotent."""
    if category:
        cat = taxonomy.resolve_or_create_category(session, category)
        if cat is not None:
            if overwrite_category or model.category_id is None:
                model.category_id = cat.id
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


def run_ingestion_pipeline(
    *,
    job_id: str,
    staged_path: Path,
    original_filename: str,
    model_name: str,
    category: Optional[str],
    tags: Optional[str],
    source_hash: Optional[str],
    strategy: IngestionStrategy,
    session_factory: SessionFactory | None = None,
) -> None:
    """Full ingestion pipeline.

    Hash, dedup, persist the model, version-manage the file blob, extract
    thumbnail, build metadata — all behind a single call. The *strategy*
    determines what parse+thumbnail variant runs (gcode or mesh). The
    *session_factory* is a callable that returns a new SQLModel Session;
    when absent, falls back to the module-level engine (legacy).
    """
    logger.info("ingest[%s] start file=%s", job_id, original_filename)
    registry.update(job_id, state="running")

    if session_factory is None:
        from app.db.session import get_session_factory

        session_factory = get_session_factory()

    try:
        blob_hash = sha256_file(staged_path)
        logger.info("ingest[%s] sha256=%s", job_id, blob_hash)

        meta, thumb_bytes = strategy.process(staged_path)
        if thumb_bytes is None and strategy.file_type not in (FileType.GCODE,):
            logger.warning("ingest[%s] thumbnail render returned None", job_id)
        elif thumb_bytes:
            logger.info(
                "ingest[%s] thumbnail extracted (%d bytes)", job_id, len(thumb_bytes)
            )

        dedup_hash = (source_hash or blob_hash).lower()

        with session_factory.scoped_session() as session:
            existing = session.exec(
                select(Model).where(Model.hash == dedup_hash)
            ).first()

            if existing is None:
                base_slug = storage.slugify(model_name)
                slug = storage.ensure_unique_slug(
                    base_slug, lambda s: _model_exists_with_slug(session, s)
                )
                model = Model(name=model_name, slug=slug, hash=dedup_hash)
                session.add(model)
                session.commit()
                session.refresh(model)
                logger.info(
                    "ingest[%s] new model id=%s slug=%s", job_id, model.id, model.slug
                )
            else:
                model = existing
                model.updated_at = utcnow()
                session.add(model)
                session.commit()
                session.refresh(model)
                logger.info("ingest[%s] dedup hit model_id=%s", job_id, model.id)

            assert model.id is not None

            _apply_taxonomy(session, model, category, tags)

            version = _next_version_for_model(session, model.id)
            dest_key = storage.canonical_blob_path(
                model.slug, version, original_filename
            )

            storage.move_file(staged_path, dest_key)
            size_bytes = get_backend().stat_size(dest_key)

            file_row = File(
                model_id=model.id,
                path=dest_key,
                original_filename=original_filename,
                file_type=strategy.file_type,
                version=version,
                size_bytes=size_bytes,
                sha256=blob_hash,
            )
            session.add(file_row)
            session.commit()
            session.refresh(file_row)
            assert file_row.id is not None

            if thumb_bytes:
                thumb_key = storage.thumbnail_path_for(file_row.id)
                get_backend().write_bytes(thumb_bytes, thumb_key)
                if strategy.overwrite_thumbnail or not model.thumbnail_path:
                    model.thumbnail_path = thumb_key
                    model.thumbnail_file_id = file_row.id
                    session.add(model)
                    session.commit()

            md = Metadata(file_id=file_row.id, **meta)
            session.add(md)
            session.commit()
            upsert_detected_profiles(session, meta)

            registry.update(
                job_id,
                state="completed",
                model_id=model.id,
                file_id=file_row.id,
            )
            logger.info(
                "ingest[%s] done model_id=%s file_id=%s v=%s",
                job_id,
                model.id,
                file_row.id,
                version,
            )

    except Exception as exc:  # noqa: BLE001 — top-level task boundary
        logger.exception("ingest[%s] failed: %s", job_id, exc)
        registry.update(job_id, state="failed", error=str(exc))


def _gcode_strategy() -> IngestionStrategy:
    def process(path: Path) -> tuple[dict[str, Any], bytes | None]:
        meta = gcode_parser.parse(path)
        thumb_bytes = thumbnail.extract(path)
        return meta, thumb_bytes

    return IngestionStrategy(
        file_type=FileType.GCODE,
        overwrite_thumbnail=False,
        process=process,
    )


def _mesh_strategy(file_type: FileType) -> IngestionStrategy:
    from app.services import mesh_processing

    def process(path: Path) -> tuple[dict[str, Any], bytes | None]:
        geometry = mesh_processing.extract_geometry(path)
        thumb_bytes = mesh_processing.render_thumbnail(path)
        return geometry, thumb_bytes

    return IngestionStrategy(
        file_type=file_type,
        overwrite_thumbnail=True,
        process=process,
    )


def ingest_orca_gcode(
    *,
    job_id: str,
    staged_path: Path,
    original_filename: str,
    model_name: str,
    category: Optional[str],
    tags: Optional[str],
    source_hash: Optional[str],
    session_factory: SessionFactory | None = None,
) -> None:
    """Public entry point for G-code ingestion (called from the OrcaSlicer router)."""
    run_ingestion_pipeline(
        job_id=job_id,
        staged_path=staged_path,
        original_filename=original_filename,
        model_name=model_name,
        category=category,
        tags=tags,
        source_hash=source_hash,
        strategy=_gcode_strategy(),
        session_factory=session_factory,
    )


def ingest_mesh(
    *,
    job_id: str,
    staged_path: Path,
    original_filename: str,
    model_name: str,
    category: Optional[str],
    tags: Optional[str],
    file_type: FileType,
    source_hash: Optional[str],
    session_factory: SessionFactory | None = None,
) -> None:
    """Public entry point for mesh ingestion (called from the model upload router)."""
    run_ingestion_pipeline(
        job_id=job_id,
        staged_path=staged_path,
        original_filename=original_filename,
        model_name=model_name,
        category=category,
        tags=tags,
        source_hash=source_hash,
        strategy=_mesh_strategy(file_type),
        session_factory=session_factory,
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
    version = _next_version_for_model(session, model.id)
    dest_key = storage.canonical_blob_path(model.slug, version, original_filename)

    storage.move_file(staged_path, dest_key)
    size_bytes = get_backend().stat_size(dest_key)

    file_row = File(
        model_id=model.id,
        path=dest_key,
        original_filename=original_filename,
        file_type=FileType.GCODE,
        version=version,
        size_bytes=size_bytes,
        sha256=blob_hash,
        revision_label=revision_label.strip()
        if revision_label and revision_label.strip()
        else None,
        revision_status=revision_status,
        revision_notes=revision_notes.strip()
        if revision_notes and revision_notes.strip()
        else None,
        is_recommended=is_recommended,
    )
    session.add(file_row)
    session.commit()
    session.refresh(file_row)
    assert file_row.id is not None

    if is_recommended:
        other_gcode = session.exec(
            select(File).where(
                File.model_id == model.id,
                File.id != file_row.id,
                File.file_type == FileType.GCODE,
                File.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        ).all()
        for other in other_gcode:
            other.is_recommended = False
            session.add(other)

    if thumb_bytes:
        thumb_key = storage.thumbnail_path_for(file_row.id)
        get_backend().write_bytes(thumb_bytes, thumb_key)
        if not model.thumbnail_path:
            model.thumbnail_path = thumb_key
            model.thumbnail_file_id = file_row.id

    md = Metadata(file_id=file_row.id, **meta)
    model.updated_at = utcnow()
    session.add(md)
    session.add(model)
    session.commit()
    session.refresh(file_row)
    return file_row
