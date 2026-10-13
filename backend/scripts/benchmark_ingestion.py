"""Observe real accepted ingestion through production lifespan, SQLite and DBOS.

Availability marks are sampled upper bounds from the public API. Setup and
shutdown are outside the per-upload interval. Repeated bytes keep their existing
identity; the report explicitly distinguishes those observations from fresh work.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import TypeAdapter
from sqlmodel import select

from app.core.config import settings
from app.db.migrate import main as migrate
from app.db.models import DerivativeKind, File, JobState
from app.db.session import get_session_factory
from app.main import app
from app.schemas.jobs import DerivativeRead, DerivativeStatus, JobAccepted, JobStatus
from scripts.benchmark_cleanup import CleanupObservation, cleanup_private_jobs
from scripts.benchmark_pipeline_contracts import (
    IngestionObservation,
    InputIdentity,
    SampleOutcome,
    unavailable_derivative_outcome,
)

_DERIVATIVES = TypeAdapter(list[DerivativeRead])
_TERMINAL = {
    DerivativeStatus.READY,
    DerivativeStatus.FAILED,
    DerivativeStatus.SKIPPED,
    DerivativeStatus.CANCELLED,
    DerivativeStatus.DISABLED,
}
_POLL_SECONDS = 0.05


def _set_up(client: TestClient) -> None:
    client.headers["Origin"] = "http://testserver"
    csrf = client.post("/api/v1/setup/session")
    csrf.raise_for_status()
    client.headers["X-PrintStash-Setup-CSRF"] = csrf.json()["csrf"]
    response = client.post(
        "/api/v1/setup",
        json={
            "username": "benchmark-owner",
            "password": secrets.token_urlsafe(24),
            "storage_backend": "local",
            "data_dir": str(settings.data_dir),
            "thumb_dir": str(settings.thumb_dir),
        },
    )
    response.raise_for_status()
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]


def _existing_sources(identity: InputIdentity) -> frozenset[int]:
    with get_session_factory().scoped_session() as session:
        ids: set[int] = set()
        for file_id in session.exec(
            select(File.id).where(File.sha256 == identity.input_sha256)
        ).all():
            if file_id is None:
                raise RuntimeError("persisted Artifact has no identity")
            ids.add(file_id)
        return frozenset(ids)


def measure_ingestion(
    client: TestClient,
    path: Path,
    identity: InputIdentity,
    *,
    deadline_seconds: float,
) -> IngestionObservation:
    preexisting: frozenset[int] | None = None
    source_probe_ms = None
    accepted_ms = artifact_ms = metadata_ms = thumbnail_ms = None
    metadata_state = thumbnail_state = None
    file_id = None
    output = None
    output_format = output_dimensions = None
    reason = None
    outcome = SampleOutcome.TIMEOUT
    terminal_observed = False
    original_verified = None
    original_verification_ms = None
    observation_ms = None
    started = time.perf_counter()
    deadline = started + deadline_seconds

    def elapsed() -> float:
        return (time.perf_counter() - started) * 1000

    try:
        probe_started = time.perf_counter()
        try:
            preexisting = _existing_sources(identity)
        finally:
            source_probe_ms = (time.perf_counter() - probe_started) * 1000
        with path.open("rb") as source:
            response = client.post(
                "/api/v1/ingest/model",
                files={"file": (identity.name, source, "application/octet-stream")},
            )
        response.raise_for_status()
        accepted = JobAccepted.model_validate(response.json())
        accepted_ms = elapsed()
        while time.perf_counter() < deadline:
            status_response = client.get("/api/v1/jobs/" + accepted.job_id)
            status_response.raise_for_status()
            job = JobStatus.model_validate(status_response.json())
            if job.state in {JobState.FAILED, JobState.CANCELLED}:
                outcome = (
                    SampleOutcome.FAILED
                    if job.state == JobState.FAILED
                    else SampleOutcome.REFUSED
                )
                reason = job.error if job.error is not None else job.state.value
                terminal_observed = True
                break
            if job.file_id is not None and (
                job.committed_at is not None or job.state == JobState.COMPLETED
            ):
                file_id = job.file_id
                if artifact_ms is None:
                    artifact_ms = elapsed()
                response = client.get(f"/api/v1/files/{file_id}/derivatives")
                response.raise_for_status()
                derivatives = [
                    row
                    for row in _DERIVATIVES.validate_python(response.json())
                    if row.kind in {DerivativeKind.METADATA, DerivativeKind.THUMBNAIL}
                ]
                for row in derivatives:
                    if row.kind == DerivativeKind.METADATA:
                        metadata_state = row.state
                        if row.state == DerivativeStatus.READY and metadata_ms is None:
                            metadata_ms = elapsed()
                    if row.kind == DerivativeKind.THUMBNAIL:
                        thumbnail_state = row.state
                        if row.state == DerivativeStatus.READY and thumbnail_ms is None:
                            preview = client.get(f"/api/v1/files/{file_id}/thumbnail")
                            if preview.status_code == 200:
                                output = preview.content
                                with Image.open(BytesIO(output)) as image:
                                    output_format = image.format
                                    output_dimensions = image.size
                                    image.verify()
                                thumbnail_ms = elapsed()
                if {row.kind for row in derivatives} == {
                    DerivativeKind.METADATA,
                    DerivativeKind.THUMBNAIL,
                } and all(row.state in _TERMINAL for row in derivatives):
                    if metadata_ms is not None and thumbnail_ms is not None:
                        outcome = SampleOutcome.COMPLETED
                        terminal_observed = True
                        break
                    failures = [
                        row
                        for row in derivatives
                        if row.state != DerivativeStatus.READY
                    ]
                    if failures:
                        outcome = unavailable_derivative_outcome(failures)
                        terminal_observed = True
                        reason = ";".join(
                            f"{row.kind.value}:{row.failure_reason if row.failure_reason is not None else row.state.value}"
                            for row in failures
                        )
                        break
            time.sleep(min(_POLL_SECONDS, max(0, deadline - time.perf_counter())))
        if not terminal_observed:
            reason = "availability_deadline"
        observation_ms = elapsed()
        if file_id is not None and terminal_observed:
            verification_started = time.perf_counter()
            try:
                original = client.get(f"/api/v1/files/{file_id}/download")
                original.raise_for_status()
                original_verified = (
                    hashlib.sha256(original.content).hexdigest()
                    == identity.input_sha256
                )
                if not original_verified:
                    raise ValueError("committed original differs from uploaded bytes")
            finally:
                original_verification_ms = (
                    time.perf_counter() - verification_started
                ) * 1000
    except Exception as exc:
        # Every failed sample remains visible; no observation is fabricated when
        # a request, response contract or derivative is unavailable.
        outcome = SampleOutcome.FAILED
        reason = f"{type(exc).__name__}: {exc}"
    return IngestionObservation(
        name=identity.name,
        input_sha256=identity.input_sha256,
        input_bytes=identity.input_bytes,
        sample_index=identity.sample_index,
        elapsed_ms=elapsed() if observation_ms is None else observation_ms,
        outcome=outcome,
        reason=reason,
        accepted_ms=accepted_ms,
        artifact_observed_ms=artifact_ms,
        metadata_observed_ms=metadata_ms,
        thumbnail_visible_ms=thumbnail_ms,
        metadata_state=metadata_state,
        thumbnail_state=thumbnail_state,
        file_id=file_id,
        source_preexisting=bool(preexisting) if preexisting is not None else None,
        source_probe_ms=source_probe_ms,
        artifact_reused=file_id in preexisting
        if file_id is not None and preexisting is not None
        else None,
        output_bytes=len(output) if output is not None else 0,
        output_sha256=hashlib.sha256(output).hexdigest()
        if output is not None
        else None,
        output_format=output_format,
        output_dimensions=output_dimensions,
        original_verified=original_verified,
        original_verification_ms=original_verification_ms,
    )


def measure_ingestions(
    work: list[tuple[Path, InputIdentity]],
    *,
    deadline_seconds: float,
) -> tuple[list[IngestionObservation], float, CleanupObservation]:
    from app.modules.storage.storage_backend import generations
    from app.runtime import maintenance

    bootstrap_started = time.perf_counter()
    migrate()
    pending_error: BaseException | None = None
    samples = None
    bootstrap_ms = None
    cleanup = None
    try:
        with TestClient(app) as client:
            epoch = generations.current_epoch()
            try:
                _set_up(client)
                bootstrap_ms = (time.perf_counter() - bootstrap_started) * 1000
                samples = [
                    measure_ingestion(
                        client, path, identity, deadline_seconds=deadline_seconds
                    )
                    for path, identity in work
                ]
            except BaseException as exc:
                # Exit the lifespan normally even when a benchmark operation
                # fails: teardown after a bare yield must still get to run.
                pending_error = exc
            finally:
                try:
                    cleanup = cleanup_private_jobs(client)
                except BaseException as exc:
                    if pending_error is None:
                        pending_error = exc
                    else:
                        pending_error.add_note(
                            f"private cleanup also failed: {type(exc).__name__}: {exc}"
                        )
        if pending_error is not None:
            raise pending_error.with_traceback(pending_error.__traceback__)
        # A terminal Job does not prove its synchronous step has returned.
        # Admission stays held until lifespan teardown and this physical check.
        if samples is None or bootstrap_ms is None or cleanup is None:
            raise RuntimeError("private benchmark observations are incomplete")
        return samples, bootstrap_ms, observe_teardown(cleanup, epoch=epoch)
    finally:
        maintenance.end_restore_maintenance()


def observe_teardown(cleanup: CleanupObservation, *, epoch: str) -> CleanupObservation:
    """Retain live physical work as evidence even after lifespan returns."""
    from app.modules.storage.storage_backend import generations
    from app.runtime import maintenance

    mutations = maintenance.active_mutations()
    readers = generations.has_readers(epoch)
    if mutations or readers:
        return replace(
            cleanup,
            quiescent=False,
            active_mutations=mutations,
            active_readers=readers,
            errors=(*cleanup.errors, "private_work_active_after_teardown"),
        )
    return replace(cleanup, active_mutations=mutations, active_readers=readers)
