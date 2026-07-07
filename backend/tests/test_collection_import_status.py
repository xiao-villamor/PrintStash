"""Job-state coverage for ``import_resolved_groups`` (collection fan-out).

The regression these guard: a collection where every member fails to download
must report the job as ``failed`` (not ``completed``), so the UI stops showing a
silently-broken import as success.
"""

from __future__ import annotations

from app.services import importer
from app.services.importer import ResolvedGroup
from app.services.jobs import registry


def _run(groups: list[ResolvedGroup]) -> object:
    job_id = registry.create(owner_user_id=1)
    importer.import_resolved_groups(
        job_id=job_id,
        groups=groups,
        collection="Test",
        tags=None,
        actor_user_id=1,
        session_factory=lambda: None,  # never used: no group has staged files
    )
    return registry.get(job_id)


def test_all_members_failing_marks_job_failed() -> None:
    job = _run(
        [
            ResolvedGroup(source_url="u1", title="A", error="makerworld_login_required"),
            ResolvedGroup(source_url="u2", title="B", error="makerworld_login_required"),
        ]
    )
    assert job is not None
    assert job.state == "failed"
    # Members agree on one error -> surface it (UI shows the login message).
    assert job.error == "makerworld_login_required"
    assert job.result["imported"] == 0


def test_mixed_member_errors_use_generic_code() -> None:
    job = _run(
        [
            ResolvedGroup(source_url="u1", title="A", error="makerworld_login_required"),
            ResolvedGroup(source_url="u2", title="B", error="no_importable_files"),
        ]
    )
    assert job is not None
    assert job.state == "failed"
    assert job.error == "collection_import_failed"


def test_empty_group_without_error_still_fails() -> None:
    job = _run([ResolvedGroup(source_url="u1", title="A")])
    assert job is not None
    assert job.state == "failed"
    # No explicit member error falls back to the per-member default, which is the
    # single distinct code here, so it surfaces rather than the generic one.
    assert job.error == "no_importable_files"
