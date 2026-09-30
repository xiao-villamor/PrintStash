"""Realtime notices about Jobs and derivatives.

A notice carries ids and a hint, never data a reader needs authorization for:
the client refetches through the authorized routes. A Job notice goes to its
owner's channel and to the admin channel; a system Job (no owner) only to the
admin channel. Publication is best effort: no bound publisher, or a publisher
that fails, never fails the write that caused the notice.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.time import utcnow
from app.db.models import JobKind, JobState, WorkPriority
from app.modules.work import events
from app.schemas.jobs import JobStatus


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    def publish_threadsafe(self, channel: str, payload: dict) -> None:
        self.sent.append((channel, payload))


@pytest.fixture
def publisher() -> Iterator[Recorder]:
    recorder = Recorder()
    events.bind(recorder)
    try:
        yield recorder
    finally:
        events.bind(None)


def _status(**fields) -> JobStatus:
    now = utcnow()
    return JobStatus(
        staging=None,
        job_id="j1",
        kind=JobKind.INGESTION_UPLOAD,
        state=JobState.RUNNING,
        priority=WorkPriority.INTERACTIVE,
        attempts=1,
        resubmits=0,
        created_at=now,
        updated_at=now,
        **fields,
    )


class TestJobChanged:
    def test_tells_everyone_watching_an_owned_job(self, publisher: Recorder) -> None:
        events.job_changed(_status(owner_user_id=7, progress=40.0))

        assert [channel for channel, _ in publisher.sent] == ["jobs:7", "work:admin"]
        assert publisher.sent[0][1] == {
            "type": "job",
            "job_id": "j1",
            "kind": JobKind.INGESTION_UPLOAD,
            "state": "running",
            "progress": 40.0,
        }

    def test_a_system_job_tells_only_the_administrators(
        self, publisher: Recorder
    ) -> None:
        events.job_changed(_status())

        assert [channel for channel, _ in publisher.sent] == ["work:admin"]

    def test_carries_no_protected_data(self, publisher: Recorder) -> None:
        events.job_changed(
            _status(owner_user_id=7, error="boom", result={"model_id": 3})
        )

        assert all(
            set(payload) == {"type", "job_id", "kind", "state", "progress"}
            for _, payload in publisher.sent
        )


class TestDerivativeChanged:
    def test_tells_the_viewers_of_the_model(self, publisher: Recorder) -> None:
        events.derivative_changed(
            model_id=3, file_id=9, kind="thumbnail", state="ready"
        )

        assert publisher.sent == [
            (
                "model:3",
                {
                    "type": "derivative",
                    "model_id": 3,
                    "file_id": 9,
                    "kind": "thumbnail",
                    "state": "ready",
                },
            )
        ]


class TestPublication:
    def test_without_a_publisher_nothing_is_sent(self) -> None:
        events.bind(None)

        events.job_changed(_status(owner_user_id=7))

    def test_a_failing_publisher_never_fails_the_caller(self) -> None:
        class Broken:
            def publish_threadsafe(self, channel, payload):
                raise ConnectionError("bus down")

        events.bind(Broken())
        try:
            events.job_changed(_status(owner_user_id=7))
        finally:
            events.bind(None)
