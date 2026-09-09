"""Verified upload handoff into the existing Artifact ingestion pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import select

from app.core.time import utcnow
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    ArtifactUploadSession,
    ArtifactUploadState,
    StagingLease,
)
from app.db.session import SessionFactory
from app.modules.ingestion.ingestion import ingest_mesh, ingest_orca_gcode
from app.runtime.jobs import registry

from .api_chunks import ApiChunkUploadAdapter


def run_verified_upload_ingestion(
    *,
    upload_id: str,
    job_id: str,
    staged_path: Path,
    session_factory: SessionFactory,
) -> None:
    """Run the normal pipeline, then mirror its durable terminal result."""

    with session_factory.scoped_session() as session:
        upload = session.get(ArtifactUploadSession, upload_id)
        if upload is None or upload.state != ArtifactUploadState.INGESTING:
            return
        options = json.loads(upload.request_json)
        purpose = upload.purpose
        filename = upload.filename
        owner_user_id = upload.owner_user_id

    suffix = Path(filename).suffix.lower()
    common = {
        "job_id": job_id,
        "staged_path": staged_path,
        "original_filename": filename,
        "model_name": str(options.get("model_name") or Path(filename).stem),
        "collection": options.get("collection"),
        "tags": options.get("tags"),
        "source_hash": options.get("source_hash"),
        "actor_user_id": owner_user_id,
        "session_factory": session_factory,
        "target_library_id": options.get("target_library_id"),
    }
    if purpose in {"gcode", "slicer"}:
        ingest_orca_gcode(**common)
    elif purpose in {"model", "external_writeback"} and suffix in SUFFIX_TO_FILE_TYPE:
        ingest_mesh(file_type=SUFFIX_TO_FILE_TYPE[suffix], **common)
    else:
        registry.finish(
            job_id,
            state="failed",
            error="artifact_upload_purpose_not_supported",
            retryable=False,
        )

    result = registry.get(job_id)
    completed = result is not None and str(result.state) == "completed"
    with session_factory.scoped_session() as session:
        upload = session.get(ArtifactUploadSession, upload_id)
        if upload is None or upload.state != ArtifactUploadState.INGESTING:
            return
        upload.state = (
            ArtifactUploadState.COMPLETED if completed else ArtifactUploadState.FAILED
        )
        upload.error_code = None if completed else "artifact_ingestion_failed"
        upload.retryable = bool(result.retryable) if result is not None else True
        upload.updated_at = utcnow()
        upload.version += 1
        session.add(upload)
        session.commit()
        session.refresh(upload)
        if completed:
            lease = session.exec(
                select(StagingLease).where(StagingLease.background_job_id == job_id)
            ).first()
            if lease is not None:
                session.delete(lease)
                session.commit()
            ApiChunkUploadAdapter(staged_path.parents[1]).abort_owned(upload)
