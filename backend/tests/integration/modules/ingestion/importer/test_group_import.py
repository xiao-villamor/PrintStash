"""Job-state coverage for ``import_resolved_groups`` (collection fan-out).

Integration rather than unit, despite what it looks like: the Job is a ``jobs``
row, and that row has a foreign key to `users`. It lived under `tests/unit/`
while foreign keys were unenforced and an owner id of `1` could refer to nobody;
the tier guard rejected it the moment enforcement came back on.

The regression these guard: a collection where every member fails to download
must report the job as ``failed`` (not ``completed``), so the UI stops showing a
silently-broken import as success.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import IngestionEntryState, JobKind, User
from app.db.session import get_session_factory
from app.modules.ingestion import batch_store, importer
from app.modules.ingestion.batch_contracts import EntryRecord, JobBatch
from app.modules.ingestion.importer import ResolvedGroup
from app.modules.work.jobs import jobs
from app.schemas.jobs import JobStatus
from tests.factories import build_job, build_user
from tests.factories.ops import build_job_context


def _run(
    session: Session, owner: User, groups: list[ResolvedGroup]
) -> tuple[JobStatus | None, tuple[EntryRecord, ...]]:
    """Import *groups* as *owner* and return the resulting job status.

    The owner is passed in rather than hardcoded because `jobs.owner_user_id` is
    a foreign key: an id that merely happens to be free is refused, here and in
    production.
    """
    job = build_job(session, kind=JobKind.INGESTION_COLLECTION, owner=owner)
    importer.import_resolved_groups(
        job_context=build_job_context(job.id),
        groups=groups,
        collection="Test",
        tags=None,
        actor_user_id=owner.id,
        session_factory=get_session_factory(),
    )
    return jobs.get(job.id), batch_store.results(JobBatch(job.id), limit=2)


@pytest.fixture
def owner(db_session: Session) -> User:
    """The user these import jobs belong to."""
    return build_user(db_session, "importer-owner")


class TestRunGroupImport:
    def test_all_members_failing_marks_job_failed(
        self, db_session: Session, owner: User
    ) -> None:
        job, records = _run(
            db_session,
            owner,
            [
                ResolvedGroup(
                    source_url="u1", title="A", error="makerworld_login_required"
                ),
                ResolvedGroup(
                    source_url="u2", title="B", error="makerworld_login_required"
                ),
            ],
        )
        assert job is not None
        assert job.state == "failed"
        # Members agree on one error -> surface it (UI shows the login message).
        assert job.error == "makerworld_login_required"
        assert job.result["imported"] == 0
        assert len(records) == 2
        assert [record.state for record in records] == [IngestionEntryState.FAILED] * 2
        assert [record.error_code for record in records] == [
            "makerworld_login_required"
        ] * 2
        assert batch_store.counts(JobBatch(job.job_id)).failed == 2

    def test_mixed_member_errors_use_generic_code(
        self, db_session: Session, owner: User
    ) -> None:
        job, records = _run(
            db_session,
            owner,
            [
                ResolvedGroup(
                    source_url="u1", title="A", error="makerworld_login_required"
                ),
                ResolvedGroup(source_url="u2", title="B", error="no_importable_files"),
            ],
        )
        assert job is not None
        assert job.state == "failed"
        assert job.error == "collection_import_failed"
        assert len(records) == 2
        assert [record.state for record in records] == [IngestionEntryState.FAILED] * 2
        assert [record.error_code for record in records] == [
            "makerworld_login_required",
            "no_importable_files",
        ]
        assert batch_store.counts(JobBatch(job.job_id)).succeeded == 0

    def test_empty_group_without_error_still_fails(
        self, db_session: Session, owner: User
    ) -> None:
        job, records = _run(
            db_session, owner, [ResolvedGroup(source_url="u1", title="A")]
        )
        assert job is not None
        assert job.state == "failed"
        # No explicit member error falls back to the per-member default, which is the
        # single distinct code here, so it surfaces rather than the generic one.
        assert job.error == "no_importable_files"
        assert len(records) == 1
        assert records[0].state is IngestionEntryState.FAILED
        assert records[0].error_code == "no_importable_files"
        assert records[0].file_id is None and records[0].model_id is None
        assert batch_store.counts(JobBatch(job.job_id)).failed == 1
