"""Defend upload handoff routing and durable terminal mirroring.

A failure here means verified staging could enter no ingestion pipeline or leave
the upload session active after the background job has already failed.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import app.modules.ingestion.artifact_uploads.handoff as handoff
from app.core.time import utcnow
from app.db.models import ArtifactUploadSession, ArtifactUploadState


class _Session:
    def __init__(self, upload: ArtifactUploadSession | None) -> None:
        self.upload = upload

    def get(self, _model: object, _identifier: str):
        return self.upload


class _Factory:
    def __init__(self, upload: ArtifactUploadSession | None) -> None:
        self.session = _Session(upload)

    @contextmanager
    def scoped_session(self):
        yield self.session


def _upload() -> ArtifactUploadSession:
    return ArtifactUploadSession(
        id="handoff-upload",
        owner_user_id=1,
        purpose="external_writeback",
        target_role="new_model",
        filename="notes.txt",
        media_type="text/plain",
        declared_size=4,
        adapter_id="api_chunks",
        state=ArtifactUploadState.INGESTING,
        expires_at=utcnow() + timedelta(hours=1),
    )


class TestRunVerifiedUploadIngestion:
    def test_ignores_a_session_that_no_longer_owns_ingestion(self, tmp_path: Path) -> None:
        handoff.run_verified_upload_ingestion(
            upload_id="missing",
            job_id="job-1",
            staged_path=tmp_path / "missing.upload",
            session_factory=_Factory(None),  # type: ignore[arg-type]
        )

    def test_mirrors_an_unsupported_handoff_as_failed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        upload = _upload()
        transitions: list[tuple[ArtifactUploadState, dict[str, object]]] = []

        class _Manager:
            def transition(self, _upload, target, **changes):
                transitions.append((target, changes))
                _upload.state = target

        monkeypatch.setattr(
            handoff,
            "SqlArtifactUploadManager",
            lambda *_args, **_kwargs: _Manager(),
        )
        monkeypatch.setattr(
            handoff.registry,
            "get",
            lambda _job_id: SimpleNamespace(state="failed", retryable=False),
        )
        finished: list[dict[str, object]] = []
        monkeypatch.setattr(
            handoff.registry,
            "finish",
            lambda _job_id, **changes: finished.append(changes),
        )
        monkeypatch.setattr(handoff, "record_artifact_upload_event", lambda *_args: None)

        handoff.run_verified_upload_ingestion(
            upload_id=upload.id,
            job_id="job-1",
            staged_path=tmp_path / upload.id / "assembled.upload",
            session_factory=_Factory(upload),  # type: ignore[arg-type]
        )

        assert finished == [
            {
                "state": "failed",
                "error": "artifact_upload_purpose_not_supported",
                "retryable": False,
            }
        ]
        assert transitions == [
            (
                ArtifactUploadState.FAILED,
                {"error_code": "artifact_ingestion_failed", "retryable": False},
            )
        ]
