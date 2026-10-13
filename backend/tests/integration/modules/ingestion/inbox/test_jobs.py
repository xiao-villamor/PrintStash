"""Pending Import Jobs: resolving captures, importing them, and retention.

Resolution is pulled: every captured item with a source URL is owed a
resolve, so a capture never depends on the request that made it having
dispatched anything, and the user is waiting on it (interactive, owned by
them). Cancelling a Pending Import's Job fails the item as cancelled but keeps
it retryable; a Job that fails marks the item failed with a display-safe
reason. Settled items are never touched by either. A Pending Import owns its
retry (it may reselect files), so the Job's own retry defers to that flow.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import InboxItem, InboxItemState, JobKind, WorkPriority
from app.modules.ingestion import inbox
from app.modules.ingestion.inbox import ResolveSource

SOURCE = ResolveSource()
DEFINITIONS = {definition.name: definition for definition in inbox.definitions()}
RESOLVE = DEFINITIONS[JobKind.INGESTION_INBOX_RESOLVE]
IMPORT = DEFINITIONS[JobKind.INGESTION_INBOX_IMPORT]


@pytest.fixture
def owner(make_user):
    return make_user()


def _item(session: Session, item_id: int) -> InboxItem:
    session.expire_all()
    item = session.get(InboxItem, item_id)
    assert item is not None
    return item


class TestResolveSource:
    def test_a_captured_url_is_owed_a_resolve(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, source_url="https://www.printables.com/model/1")

        (work,) = SOURCE.pending(db_session, now=utcnow(), limit=10)

        assert (work.subject_key, work.priority, work.owner_user_id) == (
            f"inbox_item/{item.id}",
            WorkPriority.INTERACTIVE,
            owner.id,
        )

    def test_a_capture_without_a_url_has_nothing_to_resolve(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        make_inbox_item(owner, source_url=None)

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    @pytest.mark.parametrize(
        "state",
        [
            InboxItemState.REVIEW,
            InboxItemState.COMPLETED,
            InboxItemState.FAILED,
            InboxItemState.DISMISSED,
        ],
    )
    def test_an_item_past_capture_is_not_offered(
        self, db_session: Session, owner, make_inbox_item, state: InboxItemState
    ) -> None:
        make_inbox_item(owner, state=state, source_url="https://example.com/model")

        assert SOURCE.pending(db_session, now=utcnow(), limit=10) == []

    def test_never_offers_more_than_asked(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        for index in range(3):
            make_inbox_item(owner, source_url=f"https://example.com/model/{index}")

        assert len(SOURCE.pending(db_session, now=utcnow(), limit=2)) == 2

    def test_is_never_due_on_time_alone(self, db_session: Session) -> None:
        assert SOURCE.next_due(db_session, now=utcnow()) is None


class TestWithdraw:
    @pytest.mark.parametrize(
        "state",
        [InboxItemState.CAPTURED, InboxItemState.RESOLVING, InboxItemState.IMPORTING],
    )
    def test_cancelling_fails_the_item_but_keeps_it_retryable(
        self, db_session: Session, owner, make_inbox_item, state: InboxItemState
    ) -> None:
        item = make_inbox_item(owner, state=state)

        IMPORT.cancel(db_session, f"inbox_item/{item.id}")
        db_session.commit()

        withdrawn = _item(db_session, item.id)
        assert (withdrawn.state, withdrawn.error_code, withdrawn.retryable) == (
            InboxItemState.FAILED,
            "cancelled",
            True,
        )

    def test_a_settled_item_is_not_touched(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, state=InboxItemState.COMPLETED)

        RESOLVE.cancel(db_session, f"inbox_item/{item.id}")
        db_session.commit()

        assert _item(db_session, item.id).state == InboxItemState.COMPLETED


class TestFailure:
    def test_a_failed_job_fails_its_item_with_a_safe_reason(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, state=InboxItemState.IMPORTING)

        IMPORT.on_failure(
            db_session, f"inbox_item/{item.id}", "cannot read /srv/private/secret.stl"
        )
        db_session.commit()

        failed = _item(db_session, item.id)
        assert (failed.state, failed.retryable) == (InboxItemState.FAILED, True)
        assert "/srv/private" not in (failed.error_code or "")

    def test_a_captured_item_is_not_failed_by_a_lost_job(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        # Still captured, the source offers it again; failing it would strand it.
        item = make_inbox_item(owner, state=InboxItemState.CAPTURED)

        RESOLVE.on_failure(db_session, f"inbox_item/{item.id}", "boom")
        db_session.commit()

        assert _item(db_session, item.id).state == InboxItemState.CAPTURED

    def test_the_job_retry_defers_to_the_pending_import(
        self, db_session: Session, owner, make_inbox_item
    ) -> None:
        item = make_inbox_item(owner, state=InboxItemState.FAILED)

        assert IMPORT.retry(db_session, f"inbox_item/{item.id}") is False


class TestRetention:
    def test_runs_every_hour(self, db_session: Session) -> None:
        source = DEFINITIONS[JobKind.INGESTION_INBOX_RETENTION].source
        assert source is not None

        assert source.cron(db_session) == "35 * * * *"  # type: ignore[attr-defined]


class TestCompletionOwnership:
    def test_old_completion_preserves_a_new_imports_staging(
        self, db_session, make_job, make_inbox_item, make_model, owner
    ):
        import json

        from app.core.config import settings
        from app.db.models import JobState
        from app.db.session import get_session_factory
        from app.modules.work.contracts import JobExecution

        item = make_inbox_item(owner, state=InboxItemState.IMPORTING)
        model = make_model()
        old = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            subject=f"inbox_item/{item.id}",
            owner=owner,
            state=JobState.COMPLETED,
            attempts=1,
            status_json=json.dumps({"model_id": model.id}),
        )
        execution = JobExecution(old.id, old.attempts, old.execution_epoch)
        current = make_job(
            kind=old.kind,
            subject=old.subject_key,
            owner=owner,
            state=JobState.RUNNING,
            attempts=1,
        )
        settings.incoming_dir.mkdir(parents=True, exist_ok=True)
        staged = settings.incoming_dir / "current-import.gcode"
        staged.write_bytes(b"new import owns these bytes")
        item.job_id = current.id
        item.staging_key = str(staged)
        db_session.add(item)
        db_session.commit()

        inbox._finish_import(item.id, execution, get_session_factory())

        db_session.refresh(item)
        assert item.state is InboxItemState.IMPORTING
        assert item.job_id == current.id
        assert item.resulting_model_id is None
        assert staged.read_bytes() == b"new import owns these bytes"

    def test_rejected_completion_retains_cover_recovery_ownership(
        self, db_session, make_job, make_inbox_item, make_model, owner, monkeypatch
    ):
        import io
        import json
        import uuid

        from PIL import Image
        from sqlmodel import select

        from app.core.config import settings
        from app.db.models import (
            JobState,
            ModelProvenanceSource,
            ModelSourceCover,
            OwnedStorageObject,
            StagingLease,
            StorageObjectState,
        )
        from app.db.session import get_session_factory
        from app.modules.library import source_covers
        from app.modules.storage.storage_backend.runtime import get_backend
        from app.modules.work.contracts import JobExecution

        model = make_model()
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="test",
            identity_key=uuid.uuid4().hex * 2,
            canonical_url="https://example.test/retired-cover",
        )
        db_session.add(source)
        db_session.commit()
        source_id = source.id
        item = make_inbox_item(owner, state=InboxItemState.IMPORTING)
        old = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            subject=f"inbox_item/{item.id}",
            owner=owner,
            state=JobState.COMPLETED,
            attempts=1,
            status_json=json.dumps({"model_id": model.id}),
        )
        execution = JobExecution(old.id, old.attempts, old.execution_epoch)
        current = make_job(
            kind=old.kind,
            subject=old.subject_key,
            owner=owner,
            state=JobState.RUNNING,
            attempts=1,
        )
        settings.incoming_dir.mkdir(parents=True, exist_ok=True)
        staged = settings.incoming_dir / "current-cover-import.gcode"
        staged.write_bytes(b"current import staging")
        item.job_id = current.id
        item.staging_key = str(staged)
        db_session.add(item)
        db_session.commit()
        output = io.BytesIO()
        Image.new("RGB", (8, 8), "navy").save(output, format="PNG")
        backend = get_backend()

        def publish_cover(session, _row):
            return source_covers.prepare_candidate(
                session,
                backend,
                provenance_source_id=source_id,
                actor_id=None,
                data=output.getvalue(),
                content_type="image/png",
            )

        monkeypatch.setattr(inbox, "_attach_capture_cover", publish_cover)
        inbox._finish_import(item.id, execution, get_session_factory())

        db_session.expire_all()
        preserved = db_session.get(InboxItem, item.id)
        assert preserved.state is InboxItemState.IMPORTING
        assert preserved.job_id == current.id
        assert preserved.resulting_model_id is None
        assert staged.read_bytes() == b"current import staging"
        assert db_session.exec(select(ModelSourceCover)).all() == []
        assert db_session.exec(select(StagingLease)).all() == []
        proof = db_session.exec(select(OwnedStorageObject)).one()
        assert proof.state is StorageObjectState.PENDING
        assert proof.sha256 is not None
        assert backend.object_info(proof.key) is not None
        assert source_covers.reconcile_pending(db_session, backend) == 0
        db_session.commit()
        assert db_session.exec(select(ModelSourceCover)).all() == []
        assert staged.read_bytes() == b"current import staging"


class TestSourceCoverCompletionFence:
    @pytest.mark.parametrize(
        "existing", [False, True], ids=["first-publication", "replacement"]
    )
    def test_retired_import_preserves_the_visible_cover(
        self,
        db_session,
        make_job,
        make_inbox_item,
        make_model,
        make_provenance_source,
        owner,
        monkeypatch,
        existing,
    ):
        import io
        import json

        from PIL import Image
        from sqlmodel import select

        from app.db.models import (
            JobState,
            ModelSourceCover,
            OwnedStorageObject,
            StorageObjectState,
        )
        from app.db.session import get_session_factory
        from app.modules.library import source_covers
        from app.modules.storage.storage_backend.runtime import get_backend
        from app.modules.work.contracts import JobExecution

        model = make_model()
        source = make_provenance_source(model)
        source_id = source.id

        def image(color):
            output = io.BytesIO()
            Image.new("RGB", (8, 8), color).save(output, format="PNG")
            return output.getvalue()

        backend = get_backend()
        key = None
        original_bytes = None
        if existing:
            written = source_covers.put(
                db_session,
                backend,
                provenance_source_id=source_id,
                actor_id=owner.id,
                data=image("red"),
                content_type="image/png",
            )
            db_session.commit()
            key = written.cover.storage_key
            original_bytes = backend.read_bytes(key)
        item = make_inbox_item(owner, state=InboxItemState.IMPORTING)
        retired = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            subject=f"inbox_item/{item.id}",
            owner=owner,
            state=JobState.COMPLETED,
            attempts=1,
            status_json=json.dumps({"model_id": model.id}),
        )
        execution = JobExecution(retired.id, retired.attempts, retired.execution_epoch)
        current = make_job(
            kind=retired.kind,
            subject=retired.subject_key,
            owner=owner,
            state=JobState.RUNNING,
            attempts=1,
        )
        item.job_id = current.id
        db_session.add(item)
        db_session.commit()
        monkeypatch.setattr(
            inbox,
            "_attach_capture_cover",
            lambda session, row: source_covers.prepare_candidate(
                session,
                backend,
                provenance_source_id=source_id,
                actor_id=owner.id,
                data=image("navy"),
                content_type="image/png",
            ),
        )
        inbox._finish_import(item.id, execution, get_session_factory())
        db_session.expire_all()
        covers = db_session.exec(
            select(ModelSourceCover).where(
                ModelSourceCover.provenance_source_id == source_id
            )
        ).all()
        if existing:
            assert len(covers) == 1
            assert covers[0].storage_key == key
            assert backend.read_bytes(key) == original_bytes
        else:
            assert covers == []
        item = db_session.get(InboxItem, item.id)
        assert item.state is InboxItemState.IMPORTING
        assert item.job_id == current.id
        assert item.resulting_model_id is None
        pending = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.state == StorageObjectState.PENDING
            )
        ).all()
        assert len(pending) == 1
        assert pending[0].token is not None


@pytest.fixture
def resolve_download(monkeypatch):
    from unittest.mock import AsyncMock

    import httpx

    from app.modules.ingestion import import_resolvers, importer
    from tests.factories.content import gcode, zip_bytes

    body = zip_bytes({"part.gcode": gcode(marker="resolve-custody")})
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _: None)
    monkeypatch.setattr(
        import_resolvers, "resolve_capture_manifest", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        import_resolvers,
        "resolve_connected_provider_capture",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        import_resolvers, "list_model_files", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(importer, "_resolve_or_raise", lambda _: None)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    monkeypatch.setattr(importer, "pinned_transport", lambda _: transport)
    return body


class TestResolveStep:
    def test_cleanup_preserves_sealed_archive_during_resolution(
        self,
        db_session,
        owner,
        make_inbox_item,
        make_job,
        local_storage,
        resolve_download,
        monkeypatch,
    ):
        from pathlib import Path

        from sqlmodel import select

        from app.db.models import CapacityReservation
        from app.db.models.ingestion_scratch import IngestionScratchWindow
        from app.db.session import get_session_factory
        from app.modules.ingestion import importer, scratch_windows
        from tests.factories.ops import build_job_context

        item = make_inbox_item(owner, source_url="https://cdn.test/archive.zip")
        job = make_job(
            kind=JobKind.INGESTION_INBOX_RESOLVE,
            subject=f"inbox_item/{item.id}",
            owner=owner,
        )
        context = build_job_context(job.id)
        inspect = importer.inspect_archive
        observed = []

        def inspect_with_cleanup(path):
            with get_session_factory().scoped_session() as session:
                row = session.exec(
                    select(IngestionScratchWindow).where(
                        IngestionScratchWindow.path == str(path.parent)
                    )
                ).one()
                assert row.origin_job_id == job.id
                assert row.execution_epoch == context.execution_epoch
                assert not scratch_windows.cleanup_window(row.id)
                assert (
                    session.get(CapacityReservation, row.capacity_operation_id)
                    is not None
                )
            assert path.read_bytes() == resolve_download
            observed.append(path)
            return inspect(path)

        monkeypatch.setattr(importer, "inspect_archive", inspect_with_cleanup)

        inbox._resolve_step(context)

        fresh = _item(db_session, item.id)
        assert fresh.state == InboxItemState.REVIEW, fresh.error_code
        assert fresh.staging_key is not None
        assert Path(fresh.staging_key).read_bytes() == resolve_download
        assert len(observed) == 1
        assert not observed[0].exists()
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(IngestionScratchWindow)).all() == []
            assert session.exec(select(CapacityReservation)).all() == []

    def test_retry_reclaims_prior_download_before_new_resolution(
        self,
        db_session,
        owner,
        make_inbox_item,
        make_job,
        local_storage,
        resolve_download,
        monkeypatch,
    ):
        import httpx
        from sqlmodel import select

        from app.db.models import CapacityReservation
        from app.db.models.ingestion_scratch import IngestionScratchWindow
        from app.db.session import get_session_factory
        from app.modules.ingestion import importer, scratch_windows
        from tests.factories.ops import build_job_context

        item = make_inbox_item(owner, source_url="https://cdn.test/archive.zip")
        job = make_job(
            kind=JobKind.INGESTION_INBOX_RESOLVE,
            subject=f"inbox_item/{item.id}",
            owner=owner,
        )
        abandoned = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.DOWNLOAD,
            max_bytes=1024,
            owner=scratch_windows.JobWindowOwner(job.id, "prior-epoch"),
        )
        partial = abandoned.directory / "unfinished.part"
        partial.write_bytes(b"old download")
        abandoned.detach()
        observed = []

        def response(_):
            with get_session_factory().scoped_session() as session:
                assert session.get(IngestionScratchWindow, abandoned.id) is None
                assert session.get(CapacityReservation, abandoned.operation_id) is None
                assert len(session.exec(select(IngestionScratchWindow)).all()) == 1
                assert len(session.exec(select(CapacityReservation)).all()) == 1
            assert not partial.exists()
            observed.append(True)
            return httpx.Response(200, content=resolve_download)

        monkeypatch.setattr(
            importer, "pinned_transport", lambda _: httpx.MockTransport(response)
        )
        context = build_job_context(job.id)

        inbox._resolve_step(context)

        assert observed == [True]
        assert _item(db_session, item.id).state == InboxItemState.REVIEW
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(IngestionScratchWindow)).all() == []
            assert session.exec(select(CapacityReservation)).all() == []


class TestImportStepInputCustody:
    @pytest.mark.parametrize("mode", ["copy", "archive"])
    def test_cancelled_import_retains_source_during_concurrent_dismiss(
        self,
        db_session,
        owner,
        make_inbox_item,
        make_job,
        local_storage,
        monkeypatch,
        mode,
    ):
        import hashlib
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from app.core.config import settings
        from app.core.errors import OperationError
        from app.db.models import InboxSourceKind, StagingLease
        from app.db.session import get_session_factory, override_session_factory
        from app.modules.ingestion import importer, staging_leases
        from app.modules.work import runner, service
        from tests.factories.content import gcode, zip_bytes
        from tests.factories.ops import build_job_context

        payload = gcode(marker="inbox-live-source")
        body = {"copy": payload, "archive": zip_bytes({"part.gcode": payload})}[mode]
        manifest = {
            "copy": {"kind": "browser_file", "filename": "part.gcode"},
            "archive": {
                "kind": "archive",
                "entries": [{"id": "part.gcode"}],
                "selected_ids": ["part.gcode"],
            },
        }[mode]
        source = (
            settings.incoming_dir
            / {"copy": "browser.gcode", "archive": "browser.zip"}[mode]
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(body)
        item = make_inbox_item(
            owner,
            state=InboxItemState.IMPORTING,
            source_kind=InboxSourceKind.BROWSER,
            manifest=manifest,
            source_url="https://capture.test/model",
            staging_key=str(source),
        )
        job = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            subject=f"inbox_item/{item.id}",
            owner=owner,
        )
        item.job_id = job.id
        db_session.add(item)
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=job.id,
            owner_user_id=owner.id,
            path=source,
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
        db_session.commit()
        context = build_job_context(job.id)
        factory = get_session_factory()
        entered, resume = Event(), Event()
        module, name = {
            "copy": (inbox, "_copy_import_source"),
            "archive": (importer, "archive_entry_specs"),
        }[mode]
        produce = getattr(module, name)

        def pause_source(*args, **kwargs):
            with source.open("rb") as opened:
                assert opened.read(1) == body[:1]
                entered.set()
                assert resume.wait(timeout=10), "parent did not resume Inbox source"
                return produce(*args, **kwargs)

        def actor():
            override_session_factory(factory)
            return runner._run_step(IMPORT.steps[0], context, mutating=IMPORT.mutating)

        monkeypatch.setattr(module, name, pause_source)
        _ = owner.id, owner.is_superuser
        db_session.expunge(owner)

        with ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(actor)
            try:
                assert entered.wait(timeout=10), (
                    "Inbox did not begin reading its source"
                )
                service.cancel(job.id, actor=owner)
                fresh = _item(db_session, item.id)
                assert fresh.state == InboxItemState.FAILED
                with pytest.raises(OperationError, match="staging_cleanup_failed"):
                    inbox.dismiss(db_session, fresh)
                db_session.rollback()

                assert not running.done()
                assert source.read_bytes() == body
                with factory.scoped_session() as session:
                    assert session.get(StagingLease, lease.id) is not None
            finally:
                resume.set()
                running.result(timeout=15)

        fresh = _item(db_session, item.id)
        inbox.dismiss(db_session, fresh)
        assert fresh.state == InboxItemState.DISMISSED
        assert not source.exists()
        with factory.scoped_session() as session:
            assert session.get(StagingLease, lease.id) is None
