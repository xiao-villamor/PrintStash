"""Read-only staging summaries for visible Job pages, without work-service dependencies."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sqlmodel import Session, col, select
from sqlmodel.sql.expression import SelectOfScalar

from app.db.models import IngestRequest, StagingLease
from app.schemas.jobs import JobStagingSummary, JobStatus

from .staging_leases import _entry_present, _matching_path, _quarantine_entry_path


def _releasable(lease: StagingLease) -> bool:
    if lease.capture_upload_slot_origin_id is not None:
        return False
    return _matching_path(lease) is not None or (
        not _entry_present(Path(lease.path))
        and not _entry_present(_quarantine_entry_path(Path(lease.path), lease.id))
    )


def summary(
    status: JobStatus, leases: list[StagingLease], *, ingest: bool
) -> JobStagingSummary | None:

    if not leases:
        return None
    return JobStagingSummary(
        retained_bytes=sum(lease.size_bytes for lease in leases),
        lease_count=len(leases),
        earliest_expiry=min(lease.expires_at for lease in leases),
        discard_available=(
            status.terminal and ingest and all(_releasable(lease) for lease in leases)
        ),
    )


def attach_summaries(
    session: Session, statuses: Sequence[JobStatus], selection: SelectOfScalar[str]
) -> None:
    """One lease query for the selected page; never one query per Job."""
    from collections import defaultdict

    grouped = defaultdict(list)
    ingest_ids = set()
    for lease, ingest_id in session.exec(
        select(StagingLease, IngestRequest.job_id)
        .outerjoin(IngestRequest, col(IngestRequest.job_id) == col(StagingLease.job_id))
        .where(col(StagingLease.job_id).in_(selection))
    ).all():
        grouped[lease.job_id].append(lease)
        if ingest_id is not None:
            ingest_ids.add(ingest_id)
    for status in statuses:
        status.staging = summary(
            status, grouped[status.job_id], ingest=status.job_id in ingest_ids
        )
