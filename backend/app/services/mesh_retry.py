"""Serial mesh-render pass: phase 2 of catalog-first scanning, and manual retry.

External-library scans are two phases (see external_library.scan_library):
phase 1 walks the folder and catalogues every file immediately — File/Model
rows exist right away, browsable, with a "pending" render_status and no
thumbnail. Phase 2, implemented here, walks every non-"ok" row for that
library afterward and does the actual mesh load+render, one file at a time,
with a wider memory budget than a scan-time job gets (nothing else is
competing for RAM by then). This is also safe to call standalone — with no
``library_id`` — to sweep every library's outstanding pending/failed files in
one manual pass.

Why serial + wider budget matters: the RAM-aware cap in ``mesh_processing``
(``_render_job_memory_budget_bytes``) deliberately divides the detected
memory budget by ``max_render_jobs`` so *concurrent* work (bulk web uploads,
which do run on a threadpool) never collectively OOMs the box. A file that
was merely catalogued (not yet attempted) or that failed under that shared
budget may render just fine on its own here.

Every render still goes through ``mesh_worker``'s isolated subprocess with a
hard RLIMIT_AS ceiling — a bad file here still can't take down the API, it
just stays "failed" for next time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlmodel import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import File, Metadata
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.services import mesh_processing, mesh_worker, thumbnail
from app.services.jobs import registry
from app.services.storage_backend import get_backend

logger = get_logger(__name__)

# Statuses eligible for a render attempt — "pending" is a fresh catalog-first
# stub (see mesh_processing.pending_geometry) that's never actually been
# tried yet; the rest are real prior failures. Both are handled identically
# here: try once, with the wider post-scan memory budget.
_RETRYABLE_STATUSES = (
    "pending",
    "skipped_oversize",
    "failed_oom",
    "failed_error",
    "failed_timeout",
)


@dataclass
class RetrySummary:
    attempted: int = 0
    recovered: int = 0
    still_failing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "attempted": self.attempted,
            "recovered": self.recovered,
            "still_failing": self.still_failing,
        }


def _file_source_path(file_row: File) -> Optional[Path]:
    """Resolve *file_row* to a real on-disk path, or None if unreachable.

    External-library files store their absolute on-disk path directly
    (``File.path``, index-in-place — see external_library.py). Vault-owned
    files go through the storage backend's key layout instead.
    """
    if file_row.is_external:
        p = Path(file_row.path)
        return p if p.exists() else None
    direct = get_backend().direct_path(file_row.path)
    return direct if direct is not None and direct.exists() else None


def _boosted_memory_budget_bytes() -> Optional[int]:
    """Memory ceiling for a retry job: the full configured fraction of
    detected RAM, undivided by ``max_render_jobs`` — safe because retries run
    one at a time with nothing else competing, unlike a scan or a bulk upload.
    """
    limit = mesh_processing._detect_memory_limit_bytes()
    if not limit:
        return None
    fraction = max(settings.mesh_memory_budget_fraction, 0.0) or 0.5
    # A bit more headroom than the scan-time per-job share, still leaving
    # something for the rest of the app and the OS.
    boosted_fraction = min(fraction * max(settings.max_render_jobs, 1), 0.85)
    return int(limit * boosted_fraction)


def retry_failed_renders(
    *,
    library_id: Optional[int] = None,
    session_factory: SessionFactory | None = None,
    timeout_s: Optional[float] = None,
    job_id: Optional[str] = None,
    step_offset: int = 0,
) -> dict:
    """Re-attempt every file with a non-"ok" ``render_status``, one at a time.

    When *library_id* is given, only that external library's files are
    retried (called automatically at the end of that library's scan);
    ``None`` retries across every library, for a manual/maintenance pass.

    *job_id*/*step_offset* let a caller (scan_library) keep one continuous
    progress bar across both scan phases instead of resetting to 0 here —
    important for the frontend's idle-based give-up logic: a render phase
    with no progress signal for several minutes on a big/slow library would
    otherwise look indistinguishable from a genuinely hung job.
    """
    if session_factory is None:
        session_factory = get_session_factory()
    timeout_s = timeout_s if timeout_s is not None else settings.mesh_render_timeout_s
    mem_limit = _boosted_memory_budget_bytes()
    backend = get_backend()
    summary = RetrySummary()

    with session_factory.scoped_session() as session:
        query = (
            select(File, Metadata)
            .join(Metadata, Metadata.file_id == File.id)
            .where(Metadata.render_status.in_(_RETRYABLE_STATUSES), live(File))
        )
        if library_id is not None:
            query = query.where(File.external_library_id == library_id)
        candidates = session.exec(query).all()

        if not candidates:
            return summary.as_dict()

        logger.info(
            "mesh_retry: %d file(s) queued for retry (mem_limit=%s MB)",
            len(candidates),
            f"{mem_limit / (1024 * 1024):.0f}" if mem_limit else "unbounded",
        )
        total_with_offset = step_offset + len(candidates)

        for i, (file_row, md_row) in enumerate(candidates, start=1):
            summary.attempted += 1
            if job_id:
                registry.update(
                    job_id,
                    step=step_offset + i,
                    total_steps=total_with_offset,
                    label=f"rendering {file_row.original_filename}",
                    progress=(step_offset + i) / total_with_offset * 100,
                )
            path = _file_source_path(file_row)
            if path is None:
                logger.warning(
                    "mesh_retry: %s (file_id=%s) no longer reachable on disk; skipping",
                    file_row.original_filename,
                    file_row.id,
                )
                summary.still_failing.append(file_row.original_filename)
                continue

            if settings.mesh_isolate_render:
                geometry, thumb, status = mesh_worker.run_isolated_analyze(
                    path,
                    width=640,
                    height=480,
                    mem_limit_bytes=mem_limit,
                    timeout_s=timeout_s,
                )
            else:
                # Isolation disabled (mesh_isolate_render=False) — render
                # directly in this process instead of spawning a subprocess.
                # Slower to recover from a genuinely bad file (an OOM here
                # takes this process down with it), but needed for callers
                # that rely on in-process state — e.g. tests overriding
                # config via the in-memory `_overlay`, which a spawned
                # subprocess would never see (it re-imports a clean config).
                try:
                    geometry, thumb = mesh_processing.analyze_mesh_in_process(
                        path, width=640, height=480
                    )
                    status = mesh_worker.STATUS_OK
                except Exception as exc:  # noqa: BLE001 — still one file among many
                    logger.warning(
                        "mesh_retry: in-process render for %s raised %s: %s",
                        file_row.original_filename,
                        type(exc).__name__,
                        exc,
                    )
                    geometry, thumb, status = None, None, mesh_worker.STATUS_ERROR

            if status != mesh_worker.STATUS_OK:
                logger.warning(
                    "mesh_retry: %s still failing (%s)",
                    file_row.original_filename,
                    status,
                )
                summary.still_failing.append(file_row.original_filename)
                continue

            # Success — update the stored metadata and (if we got one) the
            # thumbnail, same as a normal ingest/reindex would.
            for k, v in geometry.items():
                if k in Metadata.model_fields:
                    setattr(md_row, k, v)
            session.add(md_row)

            if thumb:
                backend.write_bytes(
                    thumbnail.to_webp(thumb), backend.thumbnail_key(file_row.id)
                )
            session.commit()

            summary.recovered += 1
            logger.info(
                "mesh_retry: recovered %s (file_id=%s)",
                file_row.original_filename,
                file_row.id,
            )

    return summary.as_dict()
