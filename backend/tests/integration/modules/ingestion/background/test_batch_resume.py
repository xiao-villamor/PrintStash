"""Batch drivers resume confirmed units before reopening their sources."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path

import httpx
import pytest
from sqlmodel import select

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.core.url_safety import PinnedTarget
from app.db.models import (
    File,
    FileType,
    IngestionEntry,
    IngestionEntryState,
    IngestRequestKind,
    JobState,
)
from app.db.session import get_session_factory
from app.modules.ingestion import background, import_resolvers, importer, staging_leases
from app.modules.ingestion.ingestion import StagedArtifact, commit_staged_artifact
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work import service
from app.modules.work.jobs import jobs
from app.schemas.ingest import UrlIngestRequest
from tests.factories.content import gcode, zip_bytes
from tests.factories.ops import build_job_context


class TestBatchResume:
    @pytest.mark.asyncio
    async def test_resumes_a_confirmed_url_without_network(
        self, make_user, make_ingest_request, tmp_path, monkeypatch
    ) -> None:
        owner = make_user("url-confirmed-retry")
        url = "https://example.com/confirmed.gcode"
        request = make_ingest_request(owner, source_url=url)
        factory = get_session_factory()
        context = build_job_context(request.job_id)
        body = gcode(marker="url-confirmed-retry")
        downloads: list[str] = []
        staged_paths: list[Path] = []
        committed: list[int] = []
        with factory.scoped_session() as session:
            baseline = {file.id for file in session.exec(select(File)).all()}

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield body

        def serve(outbound: httpx.Request) -> httpx.Response:
            downloads.append(str(outbound.url))
            return httpx.Response(200, stream=Body())

        monkeypatch.setattr(
            importer,
            "_resolve_or_raise",
            lambda target: PinnedTarget(target, "example.com", 443, "93.184.216.34"),
        )
        monkeypatch.setattr(
            importer, "pinned_transport", lambda _: httpx.MockTransport(serve)
        )
        download = importer.download_to_staging
        ingest = importer._ingest_one_file

        async def observe_download(*args, **kwargs):
            result = await download(*args, **kwargs)
            staged_paths.append(result[0])
            return result

        def commit_then_cancel(*args, **kwargs):
            result = ingest(*args, **kwargs)
            assert result is not None and "file_id" in result, result
            committed.append(result["file_id"])
            with factory.scoped_session() as session:
                receipt = session.exec(
                    select(IngestionEntry).where(
                        IngestionEntry.job_id == request.job_id
                    )
                ).one()
                assert receipt.state is IngestionEntryState.IMPORTED
                assert receipt.file_id == committed[0]
            service.cancel(request.job_id, actor=owner)
            return result

        monkeypatch.setattr(importer, "download_to_staging", observe_download)
        monkeypatch.setattr(importer, "_ingest_one_file", commit_then_cancel)
        arguments = dict(
            req=UrlIngestRequest(url=url),
            actor_user_id=owner.id,
            session_factory=factory,
        )
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            await background.import_from_url(job_context=context, **arguments)
        assert len(committed) == 1
        assert len(staged_paths) == 1
        assert not staged_paths[0].exists()
        assert downloads == [url]
        monkeypatch.setattr(importer, "_ingest_one_file", ingest)

        async def forbid_resolver(*args, **kwargs):
            raise AssertionError("confirmed URL retry reopened its resolver")

        def forbid_get(outbound: httpx.Request) -> httpx.Response:
            raise AssertionError(f"confirmed URL retry requested {outbound.url}")

        monkeypatch.setattr(import_resolvers, "list_model_files", forbid_resolver)
        monkeypatch.setattr(import_resolvers, "resolve_page_url", forbid_resolver)
        monkeypatch.setattr(
            importer, "pinned_transport", lambda _: httpx.MockTransport(forbid_get)
        )
        service.retry(request.job_id, actor=owner)
        retry = build_job_context(request.job_id)
        assert retry.execution_epoch != context.execution_epoch
        with cancellation_scope(retry.cancelled):
            await background.import_from_url(job_context=retry, **arguments)
        status = jobs.get(request.job_id)
        assert status is not None and status.state is JobState.COMPLETED
        with factory.scoped_session() as session:
            files = [
                file
                for file in session.exec(select(File)).all()
                if file.id not in baseline
            ]
            assert [file.id for file in files] == committed
            assert get_backend().read_bytes(files[0].path) == body
            receipts = session.exec(
                select(IngestionEntry).where(IngestionEntry.job_id == request.job_id)
            ).all()
            assert len(receipts) == 1
            assert receipts[0].file_id == committed[0]
            assert receipts[0].state is IngestionEntryState.IMPORTED
        assert not staged_paths[0].exists()
        assert downloads == [url]

    @pytest.mark.asyncio
    async def test_continues_after_a_selected_source_http_failure(
        self, make_user, make_ingest_request, monkeypatch
    ) -> None:
        owner = make_user("selected-http-partial")
        page_url = "https://www.printables.com/model/123-selected"
        request = make_ingest_request(
            owner, kind=IngestRequestKind.URL_SELECTION, source_url=page_url
        )
        factory = get_session_factory()
        context = build_job_context(request.job_id)
        first = import_resolvers.ModelFile("first", "first.gcode", "gcode")
        second = import_resolvers.ModelFile("second", "second.gcode", "gcode")
        sources = (
            import_resolvers.SelectedFileDownload(
                first, "https://example.com/first.gcode"
            ),
            import_resolvers.SelectedFileDownload(
                second, "https://example.com/second.gcode"
            ),
        )
        body = gcode(marker="selected-second-committed")
        requests_seen: list[str] = []
        with factory.scoped_session() as session:
            baseline = {file.id for file in session.exec(select(File)).all()}

        async def resolve(page, selected):
            assert page == page_url
            assert selected == [first, second]
            return sources

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield body

        def serve(outbound: httpx.Request) -> httpx.Response:
            requests_seen.append(outbound.url.path)
            if outbound.url.path == "/first.gcode":
                return httpx.Response(500, content=b"upstream unavailable")
            assert outbound.url.path == "/second.gcode"
            return httpx.Response(200, stream=Body())

        monkeypatch.setattr(import_resolvers, "resolve_selected_sources", resolve)
        monkeypatch.setattr(
            importer,
            "_resolve_or_raise",
            lambda target: PinnedTarget(target, "example.com", 443, "93.184.216.34"),
        )
        monkeypatch.setattr(
            importer, "pinned_transport", lambda _: httpx.MockTransport(serve)
        )
        with cancellation_scope(context.cancelled):
            await background.run_file_selection_import(
                job_context=context,
                page_url=page_url,
                files=[first, second],
                collection=None,
                tags=None,
                actor_user_id=owner.id,
                session_factory=factory,
            )
        status = jobs.get(request.job_id)
        assert status is not None and status.state is JobState.COMPLETED
        assert (status.processed, status.total, status.succeeded, status.failed) == (
            2,
            2,
            1,
            1,
        )
        assert requests_seen == ["/first.gcode", "/second.gcode"]
        with factory.scoped_session() as session:
            files = [
                file
                for file in session.exec(select(File)).all()
                if file.id not in baseline
            ]
            assert len(files) == 1
            assert get_backend().read_bytes(files[0].path) == body
            receipts = session.exec(
                select(IngestionEntry)
                .where(IngestionEntry.job_id == request.job_id)
                .order_by(IngestionEntry.id)
            ).all()
            assert len(receipts) == 2
            assert receipts[0].state is IngestionEntryState.FAILED
            assert receipts[0].error_code == "HTTPStatusError"
            assert receipts[0].file_id is None
            assert receipts[1].state is IngestionEntryState.IMPORTED
            assert receipts[1].file_id == files[0].id

    def test_adopts_a_legacy_archive_artifact_before_extraction(
        self, make_user, make_ingest_request, tmp_path, monkeypatch
    ) -> None:
        owner = make_user("legacy-archive-resume")
        request = make_ingest_request(owner, kind=IngestRequestKind.ARCHIVE_SELECTION)
        context = build_job_context(request.job_id)
        factory = get_session_factory()
        first_body = gcode(marker="legacy-first-committed")
        second_body = gcode(marker="legacy-second-pending")
        archive_bytes = zip_bytes(
            {"first.gcode": first_body, "second.gcode": second_body}, compress=False
        )
        archive = tmp_path / "retained.zip"
        archive.write_bytes(archive_bytes)
        with factory.scoped_session() as session:
            baseline = {file.id for file in session.exec(select(File)).all()}
            staging_leases.create_job_lease(
                session,
                job_id=request.job_id,
                owner_user_id=owner.id,
                path=archive,
                size_bytes=len(archive_bytes),
                sha256=hashlib.sha256(archive_bytes).hexdigest(),
                check_capacity=False,
            )
            session.commit()
        first = tmp_path / "legacy-first.gcode"
        first.write_bytes(first_body)
        legacy_key = importer.item_ingestion_key(request.job_id, "0:first.gcode")
        published = commit_staged_artifact(
            StagedArtifact(
                staged_path=first,
                original_filename="first.gcode",
                model_name="first",
                file_type=FileType.GCODE,
            ),
            ingestion_key=legacy_key,
            actor_user_id=owner.id,
            session_factory=factory,
        )
        first.unlink(missing_ok=True)
        with factory.scoped_session() as session:
            assert (
                session.exec(
                    select(IngestionEntry).where(
                        IngestionEntry.job_id == request.job_id
                    )
                ).all()
                == []
            )
        source_reads: list[str] = []
        read = zipfile.ZipExtFile.read

        def observe_read(source, *args, **kwargs):
            source_reads.append(source.name)
            assert source.name != "first.gcode", "legacy committed member was reopened"
            return read(source, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipExtFile, "read", observe_read)
        with cancellation_scope(context.cancelled):
            background.run_archive_selection(
                job_context=context,
                archive=archive,
                archive_name=archive.name,
                names=["first.gcode", "second.gcode"],
                collection=None,
                tags=None,
                source_url=None,
                actor_user_id=owner.id,
                session_factory=factory,
            )
        status = jobs.get(request.job_id)
        assert status is not None and status.state is JobState.COMPLETED
        assert source_reads
        assert set(source_reads) == {"second.gcode"}
        with factory.scoped_session() as session:
            files = [
                file
                for file in session.exec(select(File).order_by(File.id)).all()
                if file.id not in baseline
            ]
            assert len(files) == 2
            assert files[0].id == published.file_id
            assert files[0].ingestion_key == legacy_key
            assert [get_backend().read_bytes(file.path) for file in files] == [
                first_body,
                second_body,
            ]
            receipts = session.exec(
                select(IngestionEntry)
                .where(IngestionEntry.job_id == request.job_id)
                .order_by(IngestionEntry.id)
            ).all()
            assert len(receipts) == 2
            assert {receipt.file_id for receipt in receipts} == {
                file.id for file in files
            }
            assert all(
                receipt.state is IngestionEntryState.IMPORTED for receipt in receipts
            )
        assert archive.read_bytes() == archive_bytes

    @pytest.mark.asyncio
    async def test_preserves_distinct_archive_sources_with_equal_member_names(
        self, make_user, make_ingest_request, tmp_path, monkeypatch
    ) -> None:
        owner = make_user("legacy-multiple-archives")
        page_url = "https://www.printables.com/model/123-two-archives"
        request = make_ingest_request(
            owner, kind=IngestRequestKind.URL_SELECTION, source_url=page_url
        )
        factory = get_session_factory()
        context = build_job_context(request.job_id)
        first = import_resolvers.ModelFile("archive-first", "first.zip", "zip")
        second = import_resolvers.ModelFile("archive-second", "second.zip", "zip")
        first_body = gcode(marker="first-archive-shared-name")
        second_body = gcode(marker="second-archive-different-content")
        responses = {
            "/first.zip": zip_bytes({"shared.gcode": first_body}, compress=False),
            "/second.zip": zip_bytes({"shared.gcode": second_body}, compress=False),
        }
        with factory.scoped_session() as session:
            baseline = {file.id for file in session.exec(select(File)).all()}
        legacy_source = tmp_path / "legacy-shared.gcode"
        legacy_source.write_bytes(first_body)
        legacy = commit_staged_artifact(
            StagedArtifact(
                staged_path=legacy_source,
                original_filename="shared.gcode",
                model_name="shared",
                file_type=FileType.GCODE,
                source_url=page_url,
            ),
            ingestion_key=importer.item_ingestion_key(request.job_id, "0:shared.gcode"),
            actor_user_id=owner.id,
            session_factory=factory,
        )
        legacy_source.unlink(missing_ok=True)

        async def resolve(page, selected):
            assert page == page_url
            assert selected == [first, second]
            return (
                import_resolvers.SelectedFileDownload(
                    first, "https://example.com/first.zip"
                ),
                import_resolvers.SelectedFileDownload(
                    second, "https://example.com/second.zip"
                ),
            )

        class Body(httpx.AsyncByteStream):
            def __init__(self, body):
                self.body = body

            async def __aiter__(self):
                yield self.body

        def serve(outbound: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=Body(responses[outbound.url.path]))

        monkeypatch.setattr(import_resolvers, "resolve_selected_sources", resolve)
        monkeypatch.setattr(
            importer,
            "_resolve_or_raise",
            lambda target: PinnedTarget(target, "example.com", 443, "93.184.216.34"),
        )
        monkeypatch.setattr(
            importer, "pinned_transport", lambda _: httpx.MockTransport(serve)
        )
        with cancellation_scope(context.cancelled):
            await background.run_file_selection_import(
                job_context=context,
                page_url=page_url,
                files=[first, second],
                collection=None,
                tags=None,
                actor_user_id=owner.id,
                session_factory=factory,
            )
        status = jobs.get(request.job_id)
        assert status is not None and status.state is JobState.COMPLETED
        with factory.scoped_session() as session:
            files = [
                file
                for file in session.exec(select(File).order_by(File.id)).all()
                if file.id not in baseline
            ]
            assert len(files) == 2
            assert files[0].id == legacy.file_id
            assert [get_backend().read_bytes(file.path) for file in files] == [
                first_body,
                second_body,
            ]
            receipts = session.exec(
                select(IngestionEntry)
                .where(IngestionEntry.job_id == request.job_id)
                .order_by(IngestionEntry.id)
            ).all()
            assert len(receipts) == 2
            assert len({receipt.file_id for receipt in receipts}) == 2
            assert receipts[0].file_id == legacy.file_id

    def test_refuses_a_replaced_leased_archive(
        self, make_user, make_ingest_request, tmp_path
    ) -> None:
        from app.modules.ingestion import jobs as ingestion_jobs

        owner = make_user("replaced-archive-lease")
        request = make_ingest_request(
            owner,
            kind=IngestRequestKind.ARCHIVE_SELECTION,
            selection_json=json.dumps(
                {"archive_name": "source.zip", "names": ["shared.gcode"]}
            ),
        )
        factory = get_session_factory()
        context = build_job_context(request.job_id)
        source = tmp_path / "leased.zip"
        original = zip_bytes(
            {"shared.gcode": gcode(marker="leased-original")}, compress=False
        )
        replacement = zip_bytes(
            {"shared.gcode": gcode(marker="foreign-replacement")}, compress=False
        )
        source.write_bytes(original)
        received = source.stat()
        with factory.scoped_session() as session:
            baseline = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
            staging_leases.create_job_lease(
                session,
                job_id=request.job_id,
                owner_user_id=owner.id,
                path=source,
                size_bytes=len(original),
                sha256=hashlib.sha256(original).hexdigest(),
                check_capacity=False,
            )
            session.commit()
        foreign = tmp_path / "replacement.zip"
        foreign.write_bytes(replacement)
        foreign.replace(source)
        assert (source.stat().st_dev, source.stat().st_ino) != (
            received.st_dev,
            received.st_ino,
        )
        with (
            cancellation_scope(context.cancelled),
            pytest.raises(RuntimeError, match="^staging_expired$"),
        ):
            ingestion_jobs._archive_selection(context)
        with factory.scoped_session() as session:
            assert {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            } == baseline
            leases = staging_leases.job_leases(session, request.job_id)
            assert len(leases) == 1
            assert leases[0].inode == received.st_ino
        assert source.read_bytes() == replacement

    @pytest.mark.asyncio
    async def test_resumes_a_partial_archive_after_a_crc_failure(
        self, make_user, make_ingest_request, monkeypatch
    ) -> None:
        owner = make_user("partial-archive-crc")
        page_url = "https://www.printables.com/model/123-crc-retry"
        request = make_ingest_request(
            owner, kind=IngestRequestKind.URL_SELECTION, source_url=page_url
        )
        context = build_job_context(request.job_id)
        factory = get_session_factory()
        selected = import_resolvers.ModelFile("crc-archive", "parts.zip", "zip")
        first_body = gcode(marker="crc-first-durable")
        second_body = gcode(marker="crc-second-repaired")
        valid = zip_bytes(
            {"first.gcode": first_body, "second.gcode": second_body}, compress=False
        )
        with zipfile.ZipFile(io.BytesIO(valid)) as archive:
            info = archive.getinfo("second.gcode")
        name_size, extra_size = struct.unpack_from(
            "<HH", valid, info.header_offset + 26
        )
        payload_offset = info.header_offset + 30 + name_size + extra_size
        corrupt = bytearray(valid)
        corrupt[payload_offset] ^= 1
        response_body = [bytes(corrupt)]
        downloads: list[str] = []
        with factory.scoped_session() as session:
            baseline = {file.id for file in session.exec(select(File)).all()}

        async def resolve(page, files):
            assert page == page_url
            assert files == [selected]
            return (
                import_resolvers.SelectedFileDownload(
                    selected, "https://example.com/parts.zip"
                ),
            )

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield response_body[0]

        def serve(outbound: httpx.Request) -> httpx.Response:
            downloads.append(str(outbound.url))
            return httpx.Response(200, stream=Body())

        monkeypatch.setattr(import_resolvers, "resolve_selected_sources", resolve)
        monkeypatch.setattr(
            importer,
            "_resolve_or_raise",
            lambda target: PinnedTarget(target, "example.com", 443, "93.184.216.34"),
        )
        monkeypatch.setattr(
            importer, "pinned_transport", lambda _: httpx.MockTransport(serve)
        )
        arguments = dict(
            page_url=page_url,
            files=[selected],
            collection=None,
            tags=None,
            actor_user_id=owner.id,
            session_factory=factory,
        )
        with cancellation_scope(context.cancelled):
            await background.run_file_selection_import(job_context=context, **arguments)
        partial = jobs.get(request.job_id)
        assert partial is not None and partial.state is JobState.COMPLETED
        assert (
            partial.processed,
            partial.total,
            partial.succeeded,
            partial.failed,
        ) == (2, 2, 1, 1)
        assert partial.retryable
        with factory.scoped_session() as session:
            receipts = session.exec(
                select(IngestionEntry)
                .where(IngestionEntry.job_id == request.job_id)
                .order_by(IngestionEntry.id)
            ).all()
            assert [receipt.state for receipt in receipts] == [
                IngestionEntryState.IMPORTED,
                IngestionEntryState.FAILED,
            ]
            first_id = receipts[0].file_id
            first_file = session.get(File, first_id)
            assert first_file is not None
            assert get_backend().read_bytes(first_file.path) == first_body
        response_body[0] = valid
        source_reads: list[str] = []
        read = zipfile.ZipExtFile.read

        def observe_retry_read(source, *args, **kwargs):
            source_reads.append(source.name)
            assert source.name != "first.gcode", (
                "retry reopened confirmed archive entry"
            )
            return read(source, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipExtFile, "read", observe_retry_read)
        service.retry(request.job_id, actor=owner)
        retry = build_job_context(request.job_id)
        assert retry.execution_epoch != context.execution_epoch
        with cancellation_scope(retry.cancelled):
            await background.run_file_selection_import(job_context=retry, **arguments)
        final = jobs.get(request.job_id)
        assert final is not None and final.state is JobState.COMPLETED
        assert (final.total, final.succeeded, final.failed) == (2, 2, 0)
        assert source_reads
        assert set(source_reads) == {"second.gcode"}
        assert len(downloads) == 2
        with factory.scoped_session() as session:
            files = [
                file
                for file in session.exec(select(File).order_by(File.id)).all()
                if file.id not in baseline
            ]
            assert len(files) == 2
            assert files[0].id == first_id
            assert [get_backend().read_bytes(file.path) for file in files] == [
                first_body,
                second_body,
            ]
