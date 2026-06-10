"""In-process job registry for Stage 1 background tasks.

Stage 3+ may swap this for Redis/Celery; the interface is intentionally narrow
so the swap is mechanical.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any, Dict, Optional
import uuid

from app.core.time import utcnow
from app.schemas.ingest import IngestJobStatus, JobState

# Finished jobs are kept around long enough for clients to poll the terminal
# state, then dropped so the registry cannot grow without bound.
_FINISHED_TTL = timedelta(hours=1)
_MAX_JOBS = 1000


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: Dict[str, IngestJobStatus] = {}
        self._lock = threading.Lock()

    def _prune_locked(self) -> None:
        cutoff = utcnow() - _FINISHED_TTL
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

    def create(self) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._prune_locked()
            self._jobs[job_id] = IngestJobStatus(job_id=job_id, state="pending")
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
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if state is not None:
                job.state = state
                if state == "running" and job.started_at is None:
                    job.started_at = utcnow()
                if state in ("completed", "failed"):
                    job.finished_at = utcnow()
                    job.progress = 100.0
            if model_id is not None:
                job.model_id = model_id
            if file_id is not None:
                job.file_id = file_id
            if error is not None:
                job.error = error
            if step is not None:
                job.step = step
            if total_steps is not None:
                job.total_steps = total_steps
            if label is not None:
                job.label = label
            if progress is not None:
                job.progress = max(0.0, min(100.0, float(progress)))
            if result is not None:
                job.result = result

    def get(self, job_id: str) -> Optional[IngestJobStatus]:
        with self._lock:
            return self._jobs.get(job_id)


registry = JobRegistry()
