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
mid-stream). Whatever happens, the partial file is removed and the job is marked failed
and retryable rather than left pending forever.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core.config import _overlay


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
    assert uploaded.status_code == 202, uploaded.text
    job = client.get(
        f"/api/v1/ingest/jobs/{uploaded.json()['job_id']}", headers=auth_headers
    )
    assert job.json()["state"] == "completed", job.json()


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
        job = client.get(
            f"/api/v1/ingest/jobs/{response.json()['job_id']}", headers=auth_headers
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
        job = client.get(
            f"/api/v1/ingest/jobs/{response.json()['job_id']}", headers=auth_headers
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
        from app.services import inbox

        _leave_free_bytes(monkeypatch, 4096)

        client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={
                "file": ("archive.zip", _zip({"a.txt": "a" * 65536}), "application/zip")
            },
        )

        assert list(inbox.settings.incoming_dir.iterdir()) == []

    def test_fails_the_job_when_staging_breaks_after_it_was_created(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api
        from app.services.jobs import registry

        created = _record_created_jobs(monkeypatch)

        def broken_clock():
            raise ValueError("staging_capacity_exceeded")

        monkeypatch.setattr(models_api, "utcnow", broken_clock)

        response = client.post(
            "/api/v1/models/library-import",
            headers=auth_headers,
            files={"file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")},
        )

        # A job left pending forever is a queue an operator cannot clear.
        assert response.status_code == 507, response.text
        status = registry.get(created[0])
        assert status is not None
        assert status.state == "failed"

    def test_fails_the_job_when_taking_the_staging_lease_breaks(
        self,
        client: TestClient,
        auth_headers,
        local_storage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import models as models_api
        from app.services.jobs import registry

        created = _record_created_jobs(monkeypatch)

        def broken_clock():
            raise RuntimeError("staging ledger unavailable")

        # Anything that is not a ValueError takes the other arm: the job is still
        # failed, but with the generic reason rather than the caller's.
        monkeypatch.setattr(models_api, "utcnow", broken_clock)

        with pytest.raises(RuntimeError, match="staging ledger unavailable"):
            client.post(
                "/api/v1/models/library-import",
                headers=auth_headers,
                files={
                    "file": ("archive.zip", _zip({"a.txt": "a"}), "application/zip")
                },
            )

        status = registry.get(created[0])
        assert status is not None
        assert status.state == "failed"
        assert status.error == "staging_lease_failed"

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


def _record_created_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the ids of jobs the import route creates, so their state can be read."""
    from app.api.v1 import models as models_api

    created: list[str] = []
    real_create = models_api.registry.create

    def recording_create(*args: object, **kwargs: object) -> str:
        job_id = real_create(*args, **kwargs)
        created.append(job_id)
        return job_id

    monkeypatch.setattr(models_api.registry, "create", recording_create)
    return created


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
