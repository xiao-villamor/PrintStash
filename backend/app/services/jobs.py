"""In-process job registry for Stage 1 background tasks.

Stage 3+ may swap this for Redis/Celery; the interface is intentionally narrow
so the swap is mechanical.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import timedelta
from time import monotonic
from typing import Any, Dict, Optional

from sqlalchemy import delete, func, or_
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import BackgroundJob
from app.db.session import get_session_factory
from app.schemas.ingest import (
    ImportCompletion,
    ImportFailedItem,
    ImportStage,
    IngestJobStatus,
    JobState,
)

# Finished jobs are kept around long enough for clients to poll the terminal
# state, then dropped so the registry cannot grow without bound.
_FINISHED_TTL = timedelta(hours=1)
_MAX_JOBS = 1000
_DEFAULT_TERMINAL_HISTORY = 20
_PERSISTED_PRUNE_INTERVAL_S = 60.0
_ACTIVE_STATES = ("pending", "running")
_TERMINAL_STATES = ("completed", "failed")
_SECRET_QUERY = re.compile(
    r"(?i)(token|key|secret|password|cookie|signature|credential)=([^&\s]+)"
)
_ABS_PATH = re.compile(r"(?<![\w.-])(?:[A-Za-z]:[\\/]|/)[^\s:]+")


def safe_item(value: str | None) -> str | None:
    """Return display-safe filename only; never expose server paths."""
    if not value:
        return None
    clean = value.replace("\\", "/").split("/")[-1].strip()
    clean = "".join(ch for ch in clean if ch.isprintable())
    return clean[:180] or None


def safe_error(value: str | None) -> str | None:
    """Remove paths and secret-bearing query values from user-facing errors."""
    if not value:
        return None
    clean = _SECRET_QUERY.sub(r"\1=[redacted]", value)
    clean = _ABS_PATH.sub("[path]", clean)
    clean = " ".join(clean.split())
    return clean[:500]


def _safe_result(value: Any, key: str | None = None) -> Any:
    """Sanitize user-facing error/name fields without breaking manifest URLs.

    ``entries[].name`` (archive manifests) is an archive-relative path, not a
    bare filename — stripping it to a basename would desync the UI from
    ``extract_selected``, which matches selections against the full path.
    """
    if isinstance(value, dict):
        return {
            item_key: _safe_result(item, item_key) for item_key, item in value.items()
        }
    if isinstance(value, list):
        if key == "entries":
            return value
        return [_safe_result(item, key) for item in value]
    if isinstance(value, str) and key in {"error", "errors", "reason"}:
        return safe_error(value)
    if isinstance(value, str) and key == "name":
        return safe_item(value)
    return value


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: Dict[str, IngestJobStatus] = {}
        self._lock = threading.Lock()
        self._last_persisted_prune_at = float("-inf")

    @staticmethod
    def _status_payload(job: IngestJobStatus) -> str:
        return json.dumps(
            job.model_dump(
                mode="json",
                exclude={"job_id", "owner_user_id", "visible"},
            ),
            separators=(",", ":"),
        )

    def _persist(self, job: IngestJobStatus) -> None:
        with get_session_factory().scoped_session() as session:
            row = session.get(BackgroundJob, job.job_id)
            if row is None:
                row = BackgroundJob(
                    id=job.job_id,
                    owner_user_id=job.owner_user_id,
                    visible=job.visible,
                    created_at=utcnow(),
                )
            row.owner_user_id = job.owner_user_id
            row.visible = job.visible
            row.state = job.state
            row.status_json = self._status_payload(job)
            row.updated_at = utcnow()
            row.finished_at = job.finished_at
            session.add(row)
            session.commit()

    def _load_one(self, job_id: str) -> IngestJobStatus | None:
        with get_session_factory().scoped_session() as session:
            row = session.get(BackgroundJob, job_id)
            if row is None:
                return None
            payload = json.loads(row.status_json or "{}")
            return IngestJobStatus(
                job_id=row.id,
                owner_user_id=row.owner_user_id,
                visible=row.visible,
                **payload,
            )

    def _load_for_user(
        self,
        user_id: int,
        *,
        is_superuser: bool,
        terminal_limit: int,
    ) -> list[IngestJobStatus]:
        with get_session_factory().scoped_session() as session:
            terminal_cutoff = utcnow() - _FINISHED_TTL
            scope = [BackgroundJob.visible == True]  # noqa: E712
            if not is_superuser:
                scope.append(
                    or_(
                        BackgroundJob.owner_user_id.is_(None),  # type: ignore[union-attr]
                        BackgroundJob.owner_user_id == user_id,
                    )
                )
            active = session.exec(
                select(BackgroundJob)
                .where(*scope, BackgroundJob.state.in_(_ACTIVE_STATES))  # type: ignore[union-attr]
                .order_by(BackgroundJob.updated_at.desc())  # type: ignore[attr-defined]
                .limit(_MAX_JOBS)
            ).all()
            terminal = session.exec(
                select(BackgroundJob)
                .where(
                    *scope,
                    BackgroundJob.state.in_(_TERMINAL_STATES),  # type: ignore[union-attr]
                    or_(
                        BackgroundJob.finished_at.is_(None),  # type: ignore[union-attr]
                        BackgroundJob.finished_at >= terminal_cutoff,
                    ),
                )
                .order_by(BackgroundJob.updated_at.desc())  # type: ignore[attr-defined]
                .limit(terminal_limit)
            ).all()
            rows = sorted(
                [*active, *terminal], key=lambda row: row.updated_at, reverse=True
            )
            return [
                IngestJobStatus(
                    job_id=row.id,
                    owner_user_id=row.owner_user_id,
                    visible=row.visible,
                    **json.loads(row.status_json or "{}"),
                )
                for row in rows
            ]

    def _persisted_counts(self) -> dict[str, int]:
        with get_session_factory().scoped_session() as session:
            rows = session.exec(
                select(BackgroundJob.state, func.count(BackgroundJob.id)).group_by(
                    BackgroundJob.state
                )
            ).all()
        counts = {str(state): int(count) for state, count in rows}
        counts["total"] = sum(counts.values())
        return counts

    def _prune_persisted_finished(self, cutoff) -> None:
        with get_session_factory().scoped_session() as session:
            session.exec(
                delete(BackgroundJob).where(
                    BackgroundJob.finished_at.is_not(None),  # type: ignore[union-attr]
                    BackgroundJob.finished_at < cutoff,
                )
            )
            session.commit()

    def _prune_locked(self) -> None:
        cutoff = utcnow() - _FINISHED_TTL
        now = monotonic()
        if now - self._last_persisted_prune_at >= _PERSISTED_PRUNE_INTERVAL_S:
            self._prune_persisted_finished(cutoff)
            self._last_persisted_prune_at = now
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]
        for job_id in stale:
            del self._jobs[job_id]
        if len(self._jobs) > _MAX_JOBS:
            # Oldest finished jobs first; dict preserves insertion order.
            finished = [
                job_id
                for job_id, job in self._jobs.items()
                if job.finished_at is not None
            ]
            for job_id in finished[: len(self._jobs) - _MAX_JOBS]:
                del self._jobs[job_id]

    def create(self, owner_user_id: int | None = None, *, visible: bool = True) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._prune_locked()
            self._jobs[job_id] = IngestJobStatus(
                job_id=job_id,
                owner_user_id=owner_user_id,
                visible=visible,
                state="pending",
            )
            self._persist(self._jobs[job_id])
        return job_id

    def update(
        self,
        job_id: str,
        *,
        state: Optional[JobState] = None,
        model_id: Optional[int] = None,
        file_id: Optional[int] = None,
        error: Optional[str] = None,
        step: Optional[int] = None,
        total_steps: Optional[int] = None,
        label: Optional[str] = None,
        progress: Optional[float] = None,
        result: Optional[dict[str, Any]] = None,
        stage: Optional[ImportStage] = None,
        current_item: Optional[str] = None,
        processed: Optional[int] = None,
        total: Optional[int] = None,
        succeeded: Optional[int] = None,
        deduplicated: Optional[int] = None,
        skipped: Optional[int] = None,
        failed: Optional[int] = None,
        completion: Optional[ImportCompletion] = None,
        retryable: Optional[bool] = None,
        failed_items: Optional[list[dict[str, Any] | ImportFailedItem]] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if state is not None:
                previously_terminal = job.state in ("completed", "failed")
                job.state = state
                if state == "running" and job.started_at is None:
                    job.started_at = utcnow()
                if state in ("completed", "failed"):
                    job.finished_at = utcnow()
                    job.progress = 100.0
                    job.stage = "completed"
                    if not previously_terminal:
                        from app.core.metrics import record_ingestion_terminal

                        record_ingestion_terminal(state)
            if model_id is not None:
                job.model_id = model_id
            if file_id is not None:
                job.file_id = file_id
            if error is not None:
                job.error = safe_error(error)
            if step is not None:
                job.step = step
            if total_steps is not None:
                job.total_steps = total_steps
            if label is not None:
                job.label = label
            if progress is not None:
                job.progress = max(0.0, min(100.0, float(progress)))
            if result is not None:
                job.result = _safe_result(result)
            if stage is not None:
                job.stage = stage
            if current_item is not None:
                job.current_item = safe_item(current_item)
            if processed is not None:
                job.processed = max(0, processed)
            if total is not None:
                job.total = max(0, total)
            if succeeded is not None:
                job.succeeded = max(0, succeeded)
            if deduplicated is not None:
                job.deduplicated = max(0, deduplicated)
            if skipped is not None:
                job.skipped = max(0, skipped)
            if failed is not None:
                job.failed = max(0, failed)
            if completion is not None:
                job.completion = completion
            elif state == "completed":
                job.completion = (
                    "completed_with_warnings"
                    if job.failed or job.skipped
                    else "completed"
                )
            elif state == "failed" and not job.succeeded:
                job.completion = "failed_before_import"
            if retryable is not None:
                job.retryable = retryable
            if failed_items is not None:
                job.failed_items = [
                    ImportFailedItem(
                        name=safe_item(
                            item.name
                            if isinstance(item, ImportFailedItem)
                            else str(item.get("name", "item"))
                        )
                        or "item",
                        reason=safe_error(
                            item.reason
                            if isinstance(item, ImportFailedItem)
                            else str(item.get("reason", "import_failed"))
                        )
                        or "import_failed",
                        retryable=(
                            item.retryable
                            if isinstance(item, ImportFailedItem)
                            else bool(item.get("retryable", False))
                        ),
                    )
                    for item in failed_items[:100]
                ]
            self._persist(job)

    def get(self, job_id: str) -> Optional[IngestJobStatus]:
        with self._lock:
            persisted = self._load_one(job_id)
            if persisted is None:
                self._jobs.pop(job_id, None)
                return None
            cached = self._jobs.get(job_id)
            if cached is not None:
                return cached
            self._jobs[job_id] = persisted
            return persisted

    def list_for_user(
        self,
        user_id: int,
        *,
        is_superuser: bool = False,
        terminal_limit: int = _DEFAULT_TERMINAL_HISTORY,
    ) -> list[IngestJobStatus]:
        with self._lock:
            persisted = self._load_for_user(
                user_id,
                is_superuser=is_superuser,
                terminal_limit=max(0, min(100, terminal_limit)),
            )
            for job in persisted:
                self._jobs[job.job_id] = job
            self._prune_locked()
            return persisted

    def snapshot_counts(self) -> Dict[str, int]:
        """Return a count of tracked jobs by state, plus a total.

        Informational snapshot for the health probe, grouped in SQL without
        deserializing every persisted status document.
        """
        counts: Dict[str, int] = {
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
        }
        with self._lock:
            self._prune_locked()
            persisted = self._persisted_counts()
            counts.update(persisted)
        return counts


def reconcile_interrupted_jobs() -> int:
    """Resolve work stranded in RUNNING by an unclean process shutdown."""
    with get_session_factory().scoped_session() as session:
        rows = list(
            session.exec(
                select(BackgroundJob).where(BackgroundJob.state == "running")
            ).all()
        )
    for row in rows:
        jobs = JobRegistry()
        status = jobs.get(row.id)
        if status is None:
            continue
        if row.replay_safe:
            status.state = "pending"
            status.started_at = None
            status.error = None
            jobs._persist(status)
        else:
            jobs.update(
                row.id,
                state="failed",
                error="interrupted_by_restart",
                retryable=True,
            )
    return len(rows)


registry = JobRegistry()
