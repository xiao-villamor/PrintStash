"""A retry identifies source units independently of their execution order."""

import pytest
from sqlmodel import select

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.db.models import File, IngestRequestKind, JobState
from app.db.session import get_session_factory
from app.modules.ingestion import importer
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work import service
from app.modules.work.jobs import jobs
from tests.factories.content import gcode
from tests.factories.ops import build_job_context


class TestBatchIdentity:
    def test_retry_resumes_confirmed_item_after_reordering(
        self, make_user, make_ingest_request, tmp_path, monkeypatch
    ) -> None:
        owner = make_user("reordered-batch")
        request = make_ingest_request(owner, kind=IngestRequestKind.COLLECTION)
        context = build_job_context(request.job_id)
        with get_session_factory().scoped_session() as session:
            baseline = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
        first = tmp_path / "first.gcode"
        second = tmp_path / "second.gcode"
        first_bytes = gcode(marker="reordered-first")
        second_bytes = gcode(marker="reordered-second")
        first.write_bytes(first_bytes)
        second.write_bytes(second_bytes)
        original = importer._ingest_one_file
        committed = []

        def commit_then_cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            assert result is not None and "file_id" in result, result
            committed.append(result["file_id"])
            service.cancel(request.job_id, actor=owner)
            return result

        arguments = dict(
            collection=None,
            tags=None,
            source_url=None,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )
        monkeypatch.setattr(importer, "_ingest_one_file", commit_then_cancel)
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            importer.import_assets(
                job_context=context,
                staged_files=[(first, first.name), (second, second.name)],
                **arguments,
            )
        assert len(committed) == 1
        with get_session_factory().scoped_session() as session:
            confirmed = session.get(File, committed[0])
            assert confirmed is not None
            original_key = confirmed.ingestion_key
            assert get_backend().read_bytes(confirmed.path) == first_bytes
        assert not first.exists()
        assert not second.exists()
        # Only unfinished work is downloaded again. The confirmed source path
        # deliberately stays absent, so hashing it cannot masquerade as resume.
        second.write_bytes(second_bytes)
        monkeypatch.setattr(importer, "_ingest_one_file", original)
        service.retry(request.job_id, actor=owner)
        retry = build_job_context(request.job_id)
        with cancellation_scope(retry.cancelled):
            importer.import_assets(
                job_context=retry,
                staged_files=[(second, second.name), (first, first.name)],
                **arguments,
            )
        status = jobs.get(request.job_id)
        assert status is not None and status.state is JobState.COMPLETED
        assert status.result is not None
        assert status.failed == 0
        assert status.result["imported"] == 2
        assert all("error" not in item for item in status.result["items"])
        assert all(item["deduplicated"] is False for item in status.result["items"])
        with get_session_factory().scoped_session() as session:
            files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in files if file.id in baseline
            } == baseline
            new_files = {file.id: file for file in files if file.id not in baseline}
            assert len(new_files) == 2
            assert committed[0] in new_files
            assert new_files[committed[0]].ingestion_key == original_key
            assert get_backend().read_bytes(new_files[committed[0]].path) == first_bytes
            second_files = [
                file for file in new_files.values() if file.id != committed[0]
            ]
            assert len(second_files) == 1
            assert get_backend().read_bytes(second_files[0].path) == second_bytes
        assert not first.exists()
        assert not second.exists()


class TestAmbiguousInputs:
    def test_refuses_ambiguous_staged_names(
        self,
        make_user,
        make_ingest_request,
        make_model,
        make_file,
        tmp_path,
        monkeypatch,
    ) -> None:
        from pathlib import Path

        owner = make_user("ambiguous-batch")
        request = make_ingest_request(owner, kind=IngestRequestKind.COLLECTION)
        context = build_job_context(request.job_id)
        make_file(make_model(name="Existing Artifact"))
        factory = get_session_factory()
        with factory.scoped_session() as session:
            baseline = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
        first = tmp_path / "missing-first.gcode"
        second = tmp_path / "missing-second.gcode"
        assert not first.exists()
        assert not second.exists()
        original_open = Path.open

        def forbid_source_open(path, *args, **kwargs):
            if path in (first, second):
                raise AssertionError("ambiguous source was opened")
            return original_open(path, *args, **kwargs)

        def forbid_ingestion(*args, **kwargs):
            raise AssertionError("ambiguous source reached Artifact ingestion")

        monkeypatch.setattr(Path, "open", forbid_source_open)
        monkeypatch.setattr(importer, "_ingest_one_file", forbid_ingestion)
        with (
            cancellation_scope(context.cancelled),
            pytest.raises(
                importer.ImportError_, match="^batch_source_identity_ambiguous$"
            ),
        ):
            importer.import_assets(
                job_context=context,
                staged_files=[(first, "same.gcode"), (second, "same.gcode")],
                collection=None,
                tags=None,
                source_url=None,
                actor_user_id=owner.id,
                session_factory=factory,
            )
        with factory.scoped_session() as session:
            assert {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            } == baseline
