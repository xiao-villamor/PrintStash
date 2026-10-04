"""Retire private benchmark work, then hold admission through app teardown.

Only the trusted, process-local benchmark vault may call this helper. The caller
owns releasing the maintenance gate after lifespan shutdown and retaining the
workspace when cleanup cannot prove quiescence.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from app.db.models import JobKind
    from app.schemas.jobs import JobStatus


@dataclass(frozen=True)
class CancelledJob:
    job_id: str
    kind: JobKind


@dataclass(frozen=True)
class CleanupObservation:
    elapsed_ms: float
    quiescent: bool
    active_mutations: int
    active_readers: bool
    cancelled: tuple[CancelledJob, ...]
    errors: tuple[str, ...]


def _active_jobs(client: TestClient) -> list[JobStatus]:
    from app.schemas.jobs import JobStatus

    response = client.get(
        "/api/v1/jobs", params={"terminal_limit": 0, "include_system": True}
    )
    if response.status_code != 200:
        raise RuntimeError(f"private_jobs_list_http_{response.status_code}")
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("private_jobs_list_not_array")
    return [
        job for item in payload if not (job := JobStatus.model_validate(item)).terminal
    ]


def _cancel_job(client: TestClient, job: JobStatus) -> bool:
    from app.db.models import JobState
    from app.schemas.jobs import JobStatus

    response = client.post(f"/api/v1/jobs/{job.job_id}/cancel")
    if response.status_code == 409:
        current = client.get(f"/api/v1/jobs/{job.job_id}")
        if current.status_code != 200:
            raise RuntimeError(
                f"private_job_recheck_http_{current.status_code}:{job.job_id}"
            )
        # A still-active conflict is retried on the next pass. Terminal success
        # belongs to the racing owner and is not counted as our cancellation.
        JobStatus.model_validate(current.json())
        return False
    if response.status_code != 200:
        raise RuntimeError(
            f"private_job_cancel_http_{response.status_code}:{job.job_id}"
        )
    withdrawn = JobStatus.model_validate(response.json())
    if withdrawn.job_id != job.job_id or withdrawn.state is not JobState.CANCELLED:
        raise ValueError(f"private_job_cancel_not_withdrawn:{job.job_id}")
    return True


def cleanup_private_jobs(
    client: TestClient, *, deadline_seconds: float = 30
) -> CleanupObservation:
    """Cancel private active Jobs; return with restore maintenance held.

    The deadline bounds repeated cancellation/drain observation. API calls use
    the existing application/DB timeout contracts. Job terminal state alone
    does not establish physical quiescence.
    """
    if not math.isfinite(deadline_seconds) or deadline_seconds < 0:
        raise ValueError("cleanup deadline must be finite and nonnegative")
    # Keep application imports lazy: benchmark configuration precedes Settings.
    from app.modules.storage.storage_backend import generations
    from app.runtime import maintenance

    started = time.monotonic()
    deadline = started + deadline_seconds
    epoch = generations.current_epoch()
    cancelled: dict[str, CancelledJob] = {}
    errors: list[str] = []
    held = False
    quiescent = False
    try:
        while True:
            active = _active_jobs(client)
            if active:
                if held:
                    maintenance.end_restore_maintenance()
                    held = False
                for job in active:
                    if _cancel_job(client, job):
                        cancelled[job.job_id] = CancelledJob(job.job_id, job.kind)
            elif not held:
                maintenance.hold_restore_maintenance()
                held = True
                # Re-list under the gate: a newly admitted Job can appear after
                # the first list, including one created by a finishing ingest.
                continue
            elif maintenance.active_mutations() == 0 and not generations.has_readers(
                epoch
            ):
                quiescent = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append("private_cleanup_deadline_exceeded")
                break
            time.sleep(min(0.05, remaining))
    except Exception as exc:  # noqa: BLE001 - failed cleanup is report evidence
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        # Even a failed drain must prevent new private mutation while shutdown
        # runs. The caller retains this gate until after TestClient teardown.
        maintenance.hold_restore_maintenance()
    mutations = maintenance.active_mutations()
    readers = generations.has_readers(epoch)
    return CleanupObservation(
        elapsed_ms=(time.monotonic() - started) * 1000,
        quiescent=quiescent and mutations == 0 and not readers and not errors,
        active_mutations=mutations,
        active_readers=readers,
        cancelled=tuple(cancelled.values()),
        errors=tuple(errors),
    )
