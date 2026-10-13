"""Batch withdrawal preserves completed Artifact commits without starting new work."""

import pytest
from sqlmodel import select

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.db.models import File, IngestRequestKind, Job, JobState
from app.db.session import get_session_factory
from app.modules.ingestion import importer
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work import service
from tests.factories.content import gcode
from tests.factories.ops import build_job_context


class TestBatchWithdrawal:
    @pytest.mark.parametrize("grouped", [False, True], ids=["flat", "grouped"])
    @pytest.mark.parametrize("superseded", [False, True], ids=["cancel", "retry-epoch"])
    def test_stops_after_the_committed_artifact(
        self, make_user, make_ingest_request, tmp_path, monkeypatch, grouped, superseded
    ) -> None:
        owner = make_user("batch-withdrawal")
        request = make_ingest_request(owner, kind=IngestRequestKind.COLLECTION)
        context = build_job_context(request.job_id)
        with get_session_factory().scoped_session() as session:
            baseline = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
        first = tmp_path / "first.gcode"
        second = tmp_path / "second.gcode"
        first_bytes = gcode(marker="committed-first")
        first.write_bytes(first_bytes)
        second.write_bytes(gcode(marker="must-not-import"))
        committed = []
        ingest = importer._ingest_one_file

        def commit_then_withdraw(*args, **kwargs):
            outcome = ingest(*args, **kwargs)
            assert outcome is not None and "file_id" in outcome, outcome
            committed.append(outcome["file_id"])
            with get_session_factory().scoped_session() as session:
                file = session.get(File, outcome["file_id"])
                assert file is not None
                assert get_backend().read_bytes(file.path) == first_bytes
            service.cancel(request.job_id, actor=owner)
            if superseded:
                service.retry(request.job_id, actor=owner)
            return outcome

        monkeypatch.setattr(importer, "_ingest_one_file", commit_then_withdraw)
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            if grouped:
                importer.import_resolved_groups(
                    job_context=context,
                    groups=[
                        importer.ResolvedGroup(
                            source_url="https://example.com/model",
                            title="Batch",
                            staged_files=[(first, first.name), (second, second.name)],
                        )
                    ],
                    collection=None,
                    tags=None,
                    actor_user_id=owner.id,
                    session_factory=get_session_factory(),
                )
            else:
                importer.import_assets(
                    job_context=context,
                    staged_files=[(first, first.name), (second, second.name)],
                    collection=None,
                    tags=None,
                    source_url=None,
                    actor_user_id=owner.id,
                    session_factory=get_session_factory(),
                )
        assert len(committed) == 1
        with get_session_factory().scoped_session() as session:
            files = session.exec(select(File)).all()
            unchanged = {
                file.id: file.model_dump() for file in files if file.id in baseline
            }
            assert unchanged == baseline
            new_files = [file for file in files if file.id not in baseline]
            assert {file.id for file in new_files} == set(committed)
            assert get_backend().read_bytes(new_files[0].path) == first_bytes
            job = session.get(Job, request.job_id)
            assert job is not None
            assert job.state is (JobState.QUEUED if superseded else JobState.CANCELLED)
            assert (job.execution_epoch != context.execution_epoch) is superseded
        assert not first.exists()
        assert not second.exists()

    def test_retry_reuses_the_committed_artifact(
        self, make_user, make_ingest_request, tmp_path, monkeypatch
    ) -> None:
        owner = make_user("batch-retry")
        request = make_ingest_request(owner, kind=IngestRequestKind.COLLECTION)
        context = build_job_context(request.job_id)
        with get_session_factory().scoped_session() as session:
            baseline = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
        paths = [tmp_path / "first.gcode", tmp_path / "second.gcode"]
        bodies = [gcode(marker="retry-first"), gcode(marker="retry-second")]
        for path, body in zip(paths, bodies, strict=True):
            path.write_bytes(body)
        original = importer._ingest_one_file
        committed = []

        def commit_then_cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            assert result is not None and "file_id" in result, result
            committed.append(result["file_id"])
            service.cancel(request.job_id, actor=owner)
            return result

        monkeypatch.setattr(importer, "_ingest_one_file", commit_then_cancel)
        arguments = dict(
            staged_files=[(path, path.name) for path in paths],
            collection=None,
            tags=None,
            source_url=None,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            importer.import_assets(job_context=context, **arguments)
        monkeypatch.setattr(importer, "_ingest_one_file", original)
        for path, body in zip(paths, bodies, strict=True):
            path.write_bytes(body)
        service.retry(request.job_id, actor=owner)
        retry = build_job_context(request.job_id)
        with cancellation_scope(retry.cancelled):
            importer.import_assets(job_context=retry, **arguments)
        with get_session_factory().scoped_session() as session:
            files = session.exec(select(File).order_by(File.id)).all()
            unchanged = {
                file.id: file.model_dump() for file in files if file.id in baseline
            }
            assert unchanged == baseline
            new_files = [file for file in files if file.id not in baseline]
            assert len(new_files) == 2
            assert new_files[0].id == committed[0]
            assert [get_backend().read_bytes(file.path) for file in new_files] == bodies
            job = session.get(Job, request.job_id)
            assert job is not None and job.state is JobState.COMPLETED


class TestDownloadWithdrawal:
    @pytest.mark.asyncio
    async def test_releases_staging_after_chunk_cancellation(
        self, make_user, make_ingest_request, monkeypatch, db_session
    ) -> None:
        import httpx

        from app.core.config import settings
        from app.core.url_safety import PinnedTarget
        from app.db.models import CapacityReservation, IngestionScratchWindow

        owner = make_user("download-withdrawal")
        request = make_ingest_request(owner)
        context = build_job_context(request.job_id)
        closed = []

        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"; first chunk\n" * 80000
                service.cancel(request.job_id, actor=owner)
                yield b"; canceled chunk\n" * 80000

            async def aclose(self):
                closed.append(True)

        url = "https://example.com/source.gcode"
        monkeypatch.setattr(
            importer,
            "_resolve_or_raise",
            lambda _: PinnedTarget(url, "example.com", 443, "93.184.216.34"),
        )
        monkeypatch.setattr(
            importer,
            "pinned_transport",
            lambda _: httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Chunks())
            ),
        )
        with get_session_factory().scoped_session() as session:
            windows_before = {
                row.id for row in session.exec(select(IngestionScratchWindow))
            }
            credits_before = {
                row.operation_id for row in session.exec(select(CapacityReservation))
            }
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            await importer.download_to_staging(url)
        assert closed
        with get_session_factory().scoped_session() as session:
            assert {
                row.id for row in session.exec(select(IngestionScratchWindow))
            } == windows_before
            assert {
                row.operation_id for row in session.exec(select(CapacityReservation))
            } == credits_before
        root = settings.incoming_dir / "scratch-windows"
        assert not root.exists() or list(root.iterdir()) == []


class TestExtractionWithdrawal:
    def test_cleans_owned_outputs_after_partial_extraction(
        self,
        make_user,
        make_ingest_request,
        make_job,
        tmp_path,
        monkeypatch,
        db_session,
    ) -> None:
        import hashlib
        import zipfile

        from app.core.config import settings
        from app.modules.ingestion import staging_leases
        from tests.factories.content import zip_bytes

        owner = make_user("extraction-withdrawal")
        request = make_ingest_request(owner)
        context = build_job_context(request.job_id)
        archive_owner = make_job(owner=owner)
        archive = tmp_path / "leased.zip"
        source = zip_bytes(
            {
                "first.gcode": gcode(marker="first"),
                "second.gcode": b"; chunk\n" * 400000,
            },
            compress=False,
        )
        archive.write_bytes(source)
        staging_leases.create_job_lease(
            db_session,
            job_id=archive_owner.id,
            owner_user_id=owner.id,
            path=archive,
            size_bytes=len(source),
            sha256=hashlib.sha256(source).hexdigest(),
        )
        db_session.commit()
        read = zipfile.ZipExtFile.read
        reads = []
        from app.core.cancellation import time as probe_time

        advanced_probe_time = probe_time.monotonic() + 60
        before = (
            set(settings.incoming_dir.iterdir())
            if settings.incoming_dir.exists()
            else set()
        )

        def withdraw_during_second(stream, size=-1):
            block = read(stream, size)
            if stream.name == "second.gcode" and block and not reads:
                reads.append(len(block))
                # Advance only the cooperative probe clock so the next actual
                # bounded read observes the newly committed cancellation.
                service.cancel(request.job_id, actor=owner)
                monkeypatch.setattr(
                    "app.core.cancellation.time.monotonic", lambda: advanced_probe_time
                )
            return block

        monkeypatch.setattr(zipfile.ZipExtFile, "read", withdraw_during_second)
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            importer.extract_selected(archive, ["first.gcode", "second.gcode"])
        assert reads == [1024 * 1024]
        assert set(settings.incoming_dir.iterdir()) == before
        assert archive.read_bytes() == source
        with get_session_factory().scoped_session() as session:
            leases = staging_leases.job_leases(session, archive_owner.id)
            assert len(leases) == 1
            assert staging_leases._matching_path(leases[0]) == archive
