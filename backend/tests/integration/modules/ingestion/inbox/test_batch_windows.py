"""Inbox selections publish independently while preserving capture provenance."""

import json
from pathlib import Path

import httpx
import pytest
from sqlmodel import col, select

from app.core.cancellation import cancellation_scope
from app.core.url_safety import PinnedTarget
from app.db.models import (
    ArtifactProvenanceLink,
    File,
    InboxItem,
    InboxItemCompletion,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    IngestionEntry,
    IngestionEntryState,
    JobKind,
    JobState,
)
from app.db.session import get_session_factory
from app.modules.ingestion import import_resolvers, importer, inbox
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work.jobs import jobs
from tests.factories.content import gcode, zip_bytes
from tests.factories.ops import build_job_context


@pytest.fixture
def remote_window(
    make_user, make_inbox_item, make_job, db_session, local_storage, monkeypatch
):
    bodies = {
        "bad": gcode(marker="recovered-selection"),
        "good": gcode(marker="retained-selection"),
    }
    files = [
        {"id": key, "name": key + ".gcode", "file_type": "gcode", "size": len(data)}
        for key, data in bodies.items()
    ]
    manifest = {
        "schema_version": 2,
        "kind": "model_files",
        "source": {
            "provider": "printables",
            "canonical_url": "https://www.printables.com/model/42",
            "source_item_id": "42",
            "source_revision": None,
            "adapter_version": "printables-v1",
            "fields": {},
        },
        "files": files,
        "selected_ids": ["bad", "good"],
    }
    owner = make_user(superuser=True)
    item = make_inbox_item(
        owner,
        state=InboxItemState.IMPORTING,
        manifest=manifest,
        source_url=manifest["source"]["canonical_url"],
    )
    job = make_job(
        kind=JobKind.INGESTION_INBOX_IMPORT,
        subject=f"inbox_item/{item.id}",
        owner=owner,
    )
    item.job_id = job.id
    db_session.add(item)
    db_session.commit()
    observed = {"available": False, "downloads": [], "resolved": []}

    async def provider_response(self, method, url, **kwargs):
        assert method == "POST"
        requested = [
            identity
            for file in kwargs["json"]["variables"]["files"]
            for identity in file["ids"]
        ]
        observed["resolved"].append(requested)
        return httpx.Response(
            200,
            json={
                "data": {
                    "getDownloadLink": {
                        "output": {
                            "files": [
                                {
                                    "id": identity,
                                    "link": f"https://downloads.example.test/{identity}.gcode",
                                }
                                for identity in requested
                            ]
                        }
                    }
                }
            },
            request=httpx.Request(method, url),
        )

    def download_response(request):
        identity = Path(request.url.path).stem
        observed["downloads"].append(identity)
        if identity == "bad" and not observed["available"]:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            content=bodies[identity],
            headers={"Content-Disposition": f'attachment; filename="{identity}.gcode"'},
        )

    monkeypatch.setattr(
        import_resolvers.ProviderTransport, "request", provider_response
    )
    monkeypatch.setattr(
        importer,
        "_resolve_or_raise",
        lambda url: PinnedTarget(url, "downloads.example.test", 443, "93.184.216.34"),
    )
    monkeypatch.setattr(
        importer, "pinned_transport", lambda _: httpx.MockTransport(download_response)
    )
    with get_session_factory().scoped_session() as session:
        baseline = {
            file.id: file.model_dump() for file in session.exec(select(File)).all()
        }
    return item.id, job.id, owner, bodies, observed, baseline


def _run(item_id, job_id):
    context = build_job_context(job_id)
    with cancellation_scope(context.cancelled):
        inbox.run_import_job(item_id, context)


def _partial(item_id, job_id, bodies, baseline):
    status = jobs.get(job_id)
    assert status is not None and status.state is JobState.COMPLETED
    assert (status.processed, status.succeeded, status.failed) == (2, 1, 1)
    with get_session_factory().scoped_session() as session:
        item = session.get(InboxItem, item_id)
        assert item is not None and item.state is InboxItemState.COMPLETED
        assert item.completion == InboxItemCompletion.PARTIAL and item.retryable
        entries = {
            entry.display_name: entry
            for entry in session.exec(
                select(IngestionEntry).where(IngestionEntry.inbox_item_id == item_id)
            ).all()
        }
        assert entries["bad.gcode"].state is IngestionEntryState.FAILED
        assert entries["bad.gcode"].error_code
        assert entries["good.gcode"].state is IngestionEntryState.IMPORTED
        results = {
            result.source_selection_id: result
            for result in session.exec(
                select(InboxItemResult).where(InboxItemResult.inbox_item_id == item_id)
            ).all()
        }
        assert results["bad"].state == InboxItemResultState.FAILED
        assert results["bad"].retryable
        assert results["bad"].error_code == entries["bad.gcode"].error_code
        assert results["good"].state == InboxItemResultState.IMPORTED
        files = session.exec(select(File)).all()
        assert {
            file.id: file.model_dump() for file in files if file.id in baseline
        } == baseline
        created = [file for file in files if file.id not in baseline]
        assert len(created) == 1
        assert get_backend().read_bytes(created[0].path) == bodies["good"]
        return created[0].model_dump()


class TestRemoteWindows:
    def test_continues_after_a_failed_selection(self, remote_window):
        item_id, job_id, _, bodies, observed, baseline = remote_window
        _run(item_id, job_id)
        assert observed["downloads"] == ["bad", "good"]
        _partial(item_id, job_id, bodies, baseline)

    def test_legacy_partial_failure_remains_retryable(self, remote_window):
        item_id, job_id, _, bodies, observed, baseline = remote_window
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            assert item is not None
            captured = json.loads(item.manifest_json)
            item.manifest_json = json.dumps(
                {
                    "kind": "model_files",
                    "files": captured["files"],
                    "selected_ids": ["bad", "good"],
                }
            )
            session.add(item)
            session.commit()
        _run(item_id, job_id)
        assert observed["downloads"] == ["bad", "good"]
        status = jobs.get(job_id)
        assert status is not None and status.state is JobState.COMPLETED
        assert (status.processed, status.succeeded, status.failed) == (2, 1, 1)
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            assert item is not None and item.state is InboxItemState.COMPLETED
            assert item.completion == InboxItemCompletion.PARTIAL
            assert item.retryable is True
            entries = session.exec(
                select(IngestionEntry).where(IngestionEntry.inbox_item_id == item_id)
            ).all()
            assert {entry.display_name: entry.state for entry in entries} == {
                "bad.gcode": IngestionEntryState.FAILED,
                "good.gcode": IngestionEntryState.IMPORTED,
            }
            files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in files if file.id in baseline
            } == baseline
            created = [file for file in files if file.id not in baseline]
            assert len(created) == 1
            assert get_backend().read_bytes(created[0].path) == bodies["good"]
            inbox.retry(session, item)
            assert item.state is InboxItemState.REVIEW

    def test_stops_when_a_committed_window_cannot_be_released(
        self, remote_window, monkeypatch
    ):
        import os

        item_id, job_id, _, bodies, observed, baseline = remote_window
        observed["available"] = True
        unlink = os.unlink
        download = importer.download_to_staging
        owned = []
        blocked = []

        async def observe_download(url, **kwargs):
            staged = await download(url, **kwargs)
            if not owned:
                assert staged[0].read_bytes() == bodies["bad"]
                owned.append(staged[0])
            return staged

        def refuse_owned_window(path, *args, **kwargs):
            if owned and Path(path) == owned[0]:
                blocked.append(owned[0])
                raise OSError("owned window cannot be released")
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(importer, "download_to_staging", observe_download)
        # Local publication first uses os.unlink after linking. Its documented
        # copy fallback then commits a separate inode while retaining scratch.
        monkeypatch.setattr(os, "unlink", refuse_owned_window)
        _run(item_id, job_id)
        assert blocked
        assert observed["downloads"] == ["bad"]
        status = jobs.get(job_id)
        assert status is not None and status.state is JobState.FAILED
        assert status.error == "batch_window_release_failed"
        with get_session_factory().scoped_session() as session:
            files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in files if file.id in baseline
            } == baseline
            created = [file for file in files if file.id not in baseline]
            assert len(created) == 1
            assert get_backend().read_bytes(created[0].path) == bodies["bad"]
            item = session.get(InboxItem, item_id)
            assert item.state is InboxItemState.FAILED and item.retryable
            assert item.error_code == "batch_window_release_failed"
        assert blocked[0].read_bytes() == bodies["bad"]
        # Release only this test-owned scratch file after the failure is observed.
        monkeypatch.setattr(os, "unlink", unlink)
        unlink(blocked[0])

    def test_partial_retry_preserves_confirmed_artifacts(self, remote_window):
        item_id, job_id, _, bodies, observed, baseline = remote_window
        _run(item_id, job_id)
        retained = _partial(item_id, job_id, bodies, baseline)
        observed["available"] = True
        observed["downloads"].clear()
        observed["resolved"].clear()
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            assert item is not None
            inbox.retry(session, item)
            assert json.loads(item.manifest_json)["selected_ids"] == ["bad"]
            retry_job = inbox.begin_import(session, item, ["bad"])
            assert retry_job is not None
        _run(item_id, retry_job)
        assert observed["resolved"] == [["bad"]]
        assert observed["downloads"] == ["bad"]
        status = jobs.get(retry_job)
        assert status is not None and status.state is JobState.COMPLETED
        assert (status.processed, status.succeeded, status.failed) == (1, 1, 0)
        with get_session_factory().scoped_session() as session:
            files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in files if file.id in baseline
            } == baseline
            assert session.get(File, retained["id"]).model_dump() == retained
            created = [file for file in files if file.id not in baseline]
            assert len(created) == 2
            assert {get_backend().read_bytes(file.path) for file in created} == set(
                bodies.values()
            )
            item = session.get(InboxItem, item_id)
            assert item.completion == InboxItemCompletion.COMPLETE
            assert not item.retryable
            results = session.exec(
                select(InboxItemResult).where(InboxItemResult.inbox_item_id == item_id)
            ).all()
            assert len(results) == 2
            assert all(
                result.state == InboxItemResultState.IMPORTED for result in results
            )

    def test_preserves_the_advertised_archive_filename(self, remote_window):
        item_id, job_id, _, bodies, observed, baseline = remote_window
        body = zip_bytes({"nested/part.gcode": bodies["good"]})
        bodies.clear()
        bodies["bad"] = body
        observed["available"] = True
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            manifest = json.loads(item.manifest_json)
            manifest["files"] = [
                {
                    "id": "bad",
                    "name": "bundle.zip",
                    "file_type": "stl",
                    "size": len(body),
                }
            ]
            manifest["selected_ids"] = ["bad"]
            item.manifest_json = json.dumps(manifest)
            session.add(item)
            session.commit()
        _run(item_id, job_id)
        with get_session_factory().scoped_session() as session:
            files = [
                file
                for file in session.exec(select(File)).all()
                if file.id not in baseline
            ]
            assert len(files) == 1
            assert files[0].original_filename == "part.gcode"
            link = session.exec(
                select(ArtifactProvenanceLink).where(
                    ArtifactProvenanceLink.file_id == files[0].id
                )
            ).one()
            assert link.source_filename == "bundle.zip"
            assert link.container_entry_path == "nested/part.gcode"

    def test_retires_a_failure_before_expansion(self, remote_window, monkeypatch):
        import io
        import zipfile

        item_id, job_id, _, bodies, observed, baseline = remote_window
        complete_zip = zip_bytes(
            {
                "a.gcode": gcode(marker="confirmed-zip-child"),
                "b.gcode": gcode(marker="recovered-zip-child"),
            },
            compress=False,
        )
        with zipfile.ZipFile(io.BytesIO(complete_zip)) as archive:
            child = archive.getinfo("b.gcode")
            offset = (
                child.header_offset
                + 30
                + len(child.filename.encode())
                + len(child.extra)
            )
        broken_zip = bytearray(complete_zip)
        broken_zip[offset] ^= (
            1  # Keep the frozen directory/CRC unchanged; fail the actual entry read.
        )
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            manifest = json.loads(item.manifest_json)
            manifest["files"] = [
                {
                    "id": "bad",
                    "name": "bundle.zip",
                    "file_type": "stl",
                    "size": len(complete_zip),
                }
            ]
            manifest["selected_ids"] = ["bad"]
            item.manifest_json = json.dumps(manifest)
            session.add(item)
            session.commit()
        _run(item_id, job_id)
        assert observed["downloads"] == ["bad"]
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            assert item.state is InboxItemState.FAILED
            assert (
                session.exec(
                    select(InboxItemResult).where(
                        InboxItemResult.inbox_item_id == item_id
                    )
                )
                .one()
                .result_key
                == "self"
            )
            inbox.retry(session, item)
            expanded_job = inbox.begin_import(session, item, ["bad"])
            assert expanded_job is not None
        observed["available"] = True
        bodies["bad"] = bytes(broken_zip)
        _run(item_id, expanded_job)
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            assert item.state is InboxItemState.COMPLETED
            assert item.completion == InboxItemCompletion.PARTIAL
            results = session.exec(
                select(InboxItemResult).where(InboxItemResult.inbox_item_id == item_id)
            ).all()
            assert len(results) == 2
            assert all(result.result_key != "self" for result in results)
            assert {result.original_filename: result.state for result in results} == {
                "a.gcode": InboxItemResultState.IMPORTED,
                "b.gcode": InboxItemResultState.FAILED,
            }
            retained_file = (
                session.exec(select(File).where(col(File.id).not_in(list(baseline))))
                .one()
                .model_dump()
            )
            # Historical source diagnostics remain available independently of UI retry keys.
            entries = session.exec(
                select(IngestionEntry).where(IngestionEntry.inbox_item_id == item_id)
            ).all()
            assert any(
                entry.display_name == "bundle.zip"
                and entry.state is IngestionEntryState.FAILED
                for entry in entries
            )
            inbox.retry(session, item)
            retry_job = inbox.begin_import(session, item, ["bad"])
            assert retry_job is not None
        bodies["bad"] = complete_zip
        observed["downloads"].clear()
        read = zipfile.ZipExtFile.read
        actual_reads = []

        def observe_read(stream, *args, **kwargs):
            actual_reads.append(stream.name)
            return read(stream, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipExtFile, "read", observe_read)
        _run(item_id, retry_job)
        assert observed["downloads"] == ["bad"]
        assert set(actual_reads) == {"b.gcode"}
        with get_session_factory().scoped_session() as session:
            item = session.get(InboxItem, item_id)
            assert item.state is InboxItemState.COMPLETED
            assert (
                item.completion == InboxItemCompletion.COMPLETE and not item.retryable
            )
            assert session.get(File, retained_file["id"]).model_dump() == retained_file
            files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in files if file.id in baseline
            } == baseline
            assert len([file for file in files if file.id not in baseline]) == 2
            results = session.exec(
                select(InboxItemResult).where(InboxItemResult.inbox_item_id == item_id)
            ).all()
            assert len(results) == 2 and all(
                result.result_key != "self" for result in results
            )
            assert all(
                result.state == InboxItemResultState.IMPORTED for result in results
            )
