"""Builders for durable ingestion scratch custody before any payload admission."""

from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import Job
from app.db.models.ingestion_scratch import (
    IngestionScratchWindow,
    ScratchWindowKind,
    ScratchWindowPhase,
)
from tests.factories._support import nth, save


def build_ingestion_scratch_window(
    session: Session,
    *,
    directory: Path | None = None,
    job: Job | None = None,
    execution_epoch: str | None = None,
    **overrides: Any,
) -> IngestionScratchWindow:
    """Create a valid PREPARING receipt, or explicitly describe later custody.

    The default has no lock, directory or capacity claim. Callers arranging an
    existing physical window pass its directory and identities explicitly.
    """
    number = nth("ingestion-scratch")
    identifier = f"scratch-{number}"
    if directory is None:
        directory = settings.incoming_dir / "scratch-windows" / identifier
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = directory.parent.stat()
    if (job is None) != (execution_epoch is None):
        raise ValueError("scratch job ownership requires an execution epoch")
    overrides.setdefault("id", identifier)
    overrides.setdefault("path", str(directory))
    overrides.setdefault("lock_path", str(directory.with_name(identifier + ".lock")))
    overrides.setdefault("parent_device", parent.st_dev)
    overrides.setdefault("parent_inode", parent.st_ino)
    overrides.setdefault("marker_token", f"scratch-marker-{number}")
    overrides.setdefault("kind", ScratchWindowKind.LOCAL_COPY)
    overrides.setdefault("phase", ScratchWindowPhase.PREPARING)
    overrides.setdefault("job_id", None if job is None else job.id)
    overrides.setdefault("origin_job_id", None if job is None else job.id)
    overrides.setdefault("execution_epoch", execution_epoch)
    overrides.setdefault("request_token", f"scratch-request-{number}")
    overrides.setdefault("capacity_operation_id", f"ingestion-scratch:{identifier}")
    overrides.setdefault("max_bytes", 16)
    overrides.setdefault("available_at", utcnow())
    return save(session, IngestionScratchWindow(**overrides))
