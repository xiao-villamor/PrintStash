"""Exporting the whole library as one portable archive, and importing it back.

This is the self-hoster's escape hatch: the archive is what someone takes with them when
they move machines, so an export that succeeds and an import that half-works is worse than
either failing outright.

The import side is the interesting one, because it takes an arbitrary zip from the network
and writes it to disk before anything validates it. Four separate limits stand between a
caller and a full disk, and each answers with a **different** status so an operator can
tell them apart: the server is holding too many pending uploads (503), *this user* is
holding too many (429), the staging quota or the free space is exhausted (507), and the
body outgrew what was left while it was being written (507 again, but discovered
mid-stream). Whatever happens, the partial file is removed, and the import's Job exists
only together with the staged bytes it owns, so nothing is left pending forever.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import Job
from tests.integration.api.v1._ingest_assertions import completed_job, drain_work


def _zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def imported_model(client: TestClient, auth_headers, local_storage) -> None:
    """One real ingested model, so the archive writer has a blob to read."""
    uploaded = client.post(
        "/api/v1/ingest/model",
        headers=auth_headers,
        files={"file": ("cube.stl", b"solid cube\nendsolid cube\n", "application/sla")},
        data={"model_name": "Archive Me"},
    )
    completed_job(client, uploaded)


class TestExportLibraryArchive:
    def test_hands_back_a_zip(
        self, client: TestClient, auth_headers, imported_model
    ) -> None:
        response = client.get("/api/v1/models/library-archive", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"

    def test_the_archive_it_writes_can_be_imported_back(
        self, client: TestClient, auth_headers, imported_model
    ) -> None:
        archive = client.get(
            "/api/v1/models/library-archive", headers=auth_headers
        ).content

        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={"file": ("printstash-library-v1.zip", archive, "application/zip")},
        )

        assert response.status_code == 202, response.text
        drain_work()
        job = client.get(
            f"/api/v1/jobs/{response.json()['job_id']}", headers=auth_headers
        )
        assert job.json()["state"] == "completed", job.json()

    def test_reports_an_archive_too_large_to_build(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api

        def too_large(*_args: object, **_kwargs: object):
            raise ValueError("archive_too_large")

        monkeypatch.setattr(models_api.library_transfer, "create_archive", too_large)

        response = client.get("/api/v1/models/library-archive", headers=auth_headers)

        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "archive_too_large"

    def test_reports_a_blob_that_changed_while_the_archive_was_written(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api

        def mismatch(*_args: object, **_kwargs: object):
            raise ValueError("archive_blob_hash_mismatch")

        monkeypatch.setattr(models_api.library_transfer, "create_archive", mismatch)

        response = client.get("/api/v1/models/library-archive", headers=auth_headers)

        # 409: the library moved under the writer, so the archive would be a lie.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "archive_blob_hash_mismatch"

    def test_surfaces_an_error_it_has_no_mapping_for(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api

        def unexpected(*_args: object, **_kwargs: object):
            raise ValueError("something_nobody_planned_for")

        monkeypatch.setattr(models_api.library_transfer, "create_archive", unexpected)

        # Swallowing it as a 400 would turn a bug into a client error.
        with pytest.raises(ValueError, match="something_nobody_planned_for"):
            client.get("/api/v1/models/library-archive", headers=auth_headers)

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/library-archive").status_code == 401


class TestImportLibraryArchive:
    def test_refuses_anything_that_is_not_a_zip(
        self, client: TestClient, auth_headers, local_storage
    ) -> None:
        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={"file": ("archive.tar", b"not a zip", "application/x-tar")},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "archive_zip_required"

    def test_fails_the_job_for_a_zip_with_no_manifest(
        self, client: TestClient, auth_headers, local_storage
    ) -> None:
        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={
                "file": (
                    "bad.zip",
                    _zip({"not-a-manifest.txt": "hello"}),
                    "application/zip",
                )
            },
        )

        assert response.status_code == 202, response.text
        drain_work()
        job = client.get(
            f"/api/v1/jobs/{response.json()['job_id']}", headers=auth_headers
        )
        assert job.json()["state"] == "failed"
        assert job.json()["error"] == "portable_manifest_invalid"

    def test_refuses_when_the_server_is_already_holding_too_many_uploads(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_max_pending", 1)
        _hold_one_lease(client, auth_headers)

        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={"file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")},
        )

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "staging_capacity_exceeded"

    def test_refuses_when_this_user_is_already_holding_too_many_uploads(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_max_active_per_user", 1)
        _hold_one_lease(client, auth_headers)

        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={"file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")},
        )

        # 429, not 503: one noisy user must not read as a server-wide outage.
        assert response.status_code == 429, response.text
        assert response.json()["detail"] == "staging_capacity_exceeded"

    def test_refuses_when_there_is_no_room_left_to_stage(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setitem(_overlay, "staging_min_free_gb", 1_000_000)

        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={"file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")},
        )

        assert response.status_code == 507, response.text
        assert response.json()["detail"] == "staging_capacity_exceeded"

    def test_stops_a_body_that_outgrows_the_room_it_was_given(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _leave_free_bytes(monkeypatch, 4096)

        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={
                "file": ("archive.zip", _zip({"a.txt": "a" * 65536}), "application/zip")
            },
        )

        # The declared-size gates pass, so the write loop's running count is the
        # only thing left between the caller and a full disk.
        assert response.status_code == 507, response.text
        assert response.json()["detail"] == "staging_capacity_exceeded"

    def test_removes_the_partial_file_it_had_already_written(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.modules.ingestion import inbox

        _leave_free_bytes(monkeypatch, 4096)

        client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={
                "file": ("archive.zip", _zip({"a.txt": "a" * 65536}), "application/zip")
            },
        )

        assert list(inbox.settings.incoming_dir.iterdir()) == []

    def test_a_refused_lease_leaves_neither_a_job_nor_its_bytes(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The Job and its lease commit together: a Job without its staged bytes
        # is a queue entry that can only ever fail.
        from app.api.v1 import models as models_api
        from app.modules.ingestion import inbox

        def refused(*_args: object, **_kwargs: object):
            raise ValueError("staging_capacity_exceeded")

        monkeypatch.setattr(models_api.staging_leases, "create_job_lease", refused)

        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={"file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")},
        )

        assert response.status_code == 507, response.text
        db_session.expire_all()
        assert db_session.exec(select(Job)).all() == []
        assert list(inbox.settings.incoming_dir.iterdir()) == []

    def test_a_broken_staging_ledger_leaves_neither_a_job_nor_its_bytes(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api
        from app.modules.ingestion import inbox

        def unavailable(*_args: object, **_kwargs: object):
            raise RuntimeError("staging ledger unavailable")

        monkeypatch.setattr(models_api.staging_leases, "create_job_lease", unavailable)

        # Not a caller error: it surfaces as a server error rather than a 400.
        with pytest.raises(RuntimeError, match="staging ledger unavailable"):
            client.post(
                "/api/v1/models/library-import",
                headers=auth_headers,
                files={
                    "file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")
                },
            )

        db_session.expire_all()
        assert db_session.exec(select(Job)).all() == []
        assert list(inbox.settings.incoming_dir.iterdir()) == []

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers, local_storage
    ) -> None:
        response = client.post(
            "/api/v1/models/library-import",
            headers=user_headers("import-ordinary"),
            files={"file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")},
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, local_storage
    ) -> None:
        response = client.post(
            "/api/v1/models/library-import",
            files={"file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")},
        )

        assert response.status_code == 401, response.text


def _leave_free_bytes(monkeypatch: pytest.MonkeyPatch, free: int) -> None:
    """Make the staging area report `free` bytes of headroom, and no quota floor."""
    from app.api.v1 import models as models_api

    real_disk_usage = models_api.shutil.disk_usage

    class _Usage:
        def __init__(self, real) -> None:
            self.total, self.used, self.free = real.total, real.used, free

    monkeypatch.setattr(
        models_api.shutil, "disk_usage", lambda path: _Usage(real_disk_usage(path))
    )
    monkeypatch.setitem(_overlay, "staging_min_free_gb", 0)


def _hold_one_lease(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Take one staging lease, so the next import meets a full staging area."""
    response = client.post(
        "/api/v1/models/library-import",
        headers=auth_headers,
        files={"file": ("first.zip", _zip({"a.txt": "a"}), "application/zip")},
    )
    assert response.status_code == 202, response.text


class TestLibraryImportExecution:
    @pytest.mark.asyncio
    async def test_owns_import_sessions(
        self, app, client, auth_headers, imported_model, db_session
    ):
        from threading import get_ident

        from app.db.session import get_session_factory, override_session_factory
        from tests.fakes.thread_sessions import ThreadBoundSessionFactory

        exported = client.get("/api/v1/models/library-archive", headers=auth_headers)
        assert exported.status_code == 200, exported.text
        factory = ThreadBoundSessionFactory(db_session.get_bind())
        previous = get_session_factory()
        override_session_factory(factory)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                response = await api.post(
                    "/api/v1/models/library-import",
                    headers=auth_headers,
                    files={
                        "file": ("library.zip", exported.content, "application/zip")
                    },
                )
        finally:
            override_session_factory(previous)

        assert response.status_code == 202, response.text
        assert db_session.get(Job, response.json()["job_id"]) is not None
        assert factory.opened_count > 0
        assert factory.active_count == 0
        assert get_ident() not in factory.thread_ids

    @pytest.mark.asyncio
    async def test_keeps_health_responsive_during_staging(
        self, app, client, auth_headers, imported_model, monkeypatch, loop_handshake
    ):
        from app.api.v1 import models as models_api

        exported = client.get("/api/v1/models/library-archive", headers=auth_headers)
        assert exported.status_code == 200, exported.text
        wait_for_loop, observations = loop_handshake
        loop = asyncio.get_running_loop()
        fsync = models_api.os.fsync
        health_statuses = []
        observed_staging = False
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:

            def delayed_staging(fd):
                nonlocal observed_staging
                if not observed_staging:
                    observed_staging = True
                    wait_for_loop()
                    health = asyncio.run_coroutine_threadsafe(
                        api.get("/api/v1/health"), loop
                    )
                    try:
                        health_statuses.append(health.result(timeout=2).status_code)
                    except TimeoutError:
                        health.cancel()
                        health_statuses.append(None)
                return fsync(fd)

            monkeypatch.setattr(models_api.os, "fsync", delayed_staging)
            response = await api.post(
                "/api/v1/models/library-import",
                headers=auth_headers,
                files={"file": ("library.zip", exported.content, "application/zip")},
            )

        assert response.status_code == 202, response.text
        assert observations == [True]
        assert health_statuses == [200]
