"""The ingest routes: what each one refuses at the door, and what it hands to a Job.

Every ingest route validates the request synchronously and returns 202 with the
id of the Job that does the work. A request the route can refuse (a blank URL, a
private host, a non-ZIP archive, an empty or unknown selection) is refused with
400 before anything is staged, because a Job that fails on input the route
already had wastes a lane slot and leaves the user watching a spinner for an
answer the server knew immediately.

Selections are the other half: a manifest (an inspected archive, a multi-file
model page, a reviewed collection) is selected from at most once, only by its
owner, and another user's manifest reads as missing, never as forbidden.
"""

from __future__ import annotations

import errno
import io
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.modules.ingestion.background as ingest_background
from app.api.v1 import ingest as ingest_module
from app.core.config import _overlay, settings
from app.db.models import ExternalLibrary, IngestRequestKind, JobKind, JobState, User
from app.modules.administration import runtime_config
from app.modules.identity.auth import create_access_token
from app.modules.ingestion import import_resolvers, importer
from app.modules.ingestion.importer import ImportError_
from tests._env import use_local_storage
from tests.factories import build_user
from tests.integration.api.v1._ingest_assertions import drain_work


def _cube_stl_bytes() -> bytes:
    return (
        b"solid cube\nfacet normal 0 0 1\nouter loop\n"
        b"endloop\nendfacet\nendsolid cube\n"
    )


def _zip_bytes(*, entry: str = "cube.stl", content: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr(entry, content or _cube_stl_bytes())
    return buf.getvalue()


def _job(client: TestClient, response, headers: dict[str, str]) -> dict:
    """Drain the Job an accepted request queued and return its final status."""
    assert response.status_code == 202, response.text
    drain_work()
    job = client.get(f"/api/v1/jobs/{response.json()['job_id']}", headers=headers)
    assert job.status_code == 200, job.text
    return job.json()


def _inspected(client: TestClient, headers: dict[str, str]) -> dict:
    """An inspected one-entry archive's manifest, ready to select from."""
    payload = _job(
        client,
        client.post(
            "/api/v1/ingest/archive/inspect",
            headers=headers,
            files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
        ),
        headers,
    )
    assert payload["state"] == "completed", payload
    return payload["result"]


def _headers_for(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def manifest_owner(db_session: Session) -> User:
    return build_user(db_session, "manifest-owner")


@pytest.fixture
def model_files_manifest(make_ingest_request, manifest_owner):
    """A finished URL request whose multi-file page listed one STL."""

    def make(files: list[dict] | None = None) -> str:
        request = make_ingest_request(
            manifest_owner,
            kind=IngestRequestKind.URL,
            state=JobState.COMPLETED,
            manifest_json=json.dumps(
                {
                    "kind": "model_files",
                    "page_url": "https://www.printables.com/model/1",
                    "page_title": "x",
                    "files": (
                        files
                        if files is not None
                        else [{"file_id": "1", "name": "cube.stl", "file_type": "stl"}]
                    ),
                }
            ),
        )
        return request.job_id

    return make


@pytest.fixture
def collection_manifest(make_ingest_request, manifest_owner):
    """A finished URL request whose reviewed collection lists one member."""
    request = make_ingest_request(
        manifest_owner,
        kind=IngestRequestKind.URL,
        state=JobState.COMPLETED,
        manifest_json=json.dumps(
            {
                "kind": "collection",
                "title": "Cool",
                "target_collection": "Cool",
                "members": [
                    {
                        "page_url": "https://printables.com/model/1",
                        "title": "A",
                        "source_id": "1",
                    }
                ],
            }
        ),
    )
    return request.job_id


class TestStageUpload:
    def test_stage_upload_rejects_stream_exceeding_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """Exercise ``_stage_upload``'s own size guard directly.

        In production this sits behind ``RequestBodyLimitMiddleware``, which
        enforces the same ``settings.max_upload_bytes`` ceiling on the raw HTTP
        body before multipart parsing even starts, so a real oversized upload
        never reaches this inner check via the ASGI stack. It is still a real,
        independently callable guard against a future caller that streams a
        file in without going through that middleware.
        """
        use_local_storage(tmp_path)
        _overlay["max_upload_mb"] = 0.001  # ~1KB

        class _FakeUpload:
            def __init__(self, data: bytes) -> None:
                self.file = io.BytesIO(data)

        with pytest.raises(HTTPException) as exc_info:
            ingest_module._stage_upload(_FakeUpload(b"G28\n" * 10_000), ".gcode")  # type: ignore[arg-type]

        assert exc_info.value.status_code == 413
        assert exc_info.value.detail == "upload_too_large"


class TestIngestModel:
    def test_ingests_model_without_hardlinks(
        self, tmp_path, client, auth_headers, monkeypatch
    ):
        def unavailable(*args, **kwargs):
            raise PermissionError(errno.EPERM, "Operation not permitted")

        monkeypatch.setattr(os, "link", unavailable)
        use_local_storage(tmp_path)
        original = _cube_stl_bytes()

        payload = _job(
            client,
            client.post(
                "/api/v1/ingest/model",
                headers=auth_headers,
                files={"file": ("cube.stl", original, "application/sla")},
            ),
            auth_headers,
        )

        assert payload["state"] == "completed", payload
        response = client.get(
            f"/api/v1/files/{payload['file_id']}/download",
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        assert response.content == original

    def test_ingest_model_superuser_can_target_unknown_collection(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        payload = _job(
            client,
            client.post(
                "/api/v1/ingest/model",
                headers=auth_headers,
                files={"file": ("cube.stl", _cube_stl_bytes(), "application/sla")},
                data={"collection": "brand/new/path"},
            ),
            auth_headers,
        )
        assert payload["state"] == "completed", payload

    def test_ingest_model_target_library_not_found(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        use_local_storage(tmp_path)
        runtime_config.set_external_libraries_enabled(db_session, True)
        response = client.post(
            "/api/v1/ingest/model",
            headers=auth_headers,
            files={"file": ("cube.stl", _cube_stl_bytes(), "application/sla")},
            data={"target_library_id": "999"},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "library_not_found"

    def test_ingest_model_target_library_disabled(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        use_local_storage(tmp_path)
        runtime_config.set_external_libraries_enabled(db_session, True)
        lib = ExternalLibrary(
            name="nas", root_path=str(tmp_path / "nas"), enabled=False
        )
        db_session.add(lib)
        db_session.commit()
        db_session.refresh(lib)

        response = client.post(
            "/api/v1/ingest/model",
            headers=auth_headers,
            files={"file": ("cube.stl", _cube_stl_bytes(), "application/sla")},
            data={"target_library_id": str(lib.id)},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "library_not_found"


class TestIngestUrl:
    def test_ingest_url_rejects_blank_url(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/url", headers=auth_headers, json={"url": "   "}
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "url_required"

    def test_ingest_url_rejects_unsafe_url(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        with patch.object(
            ingest_module.importer,
            "validate_public_url",
            side_effect=ImportError_("private_host_blocked"),
        ):
            response = client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": "http://127.0.0.1/x.stl"},
            )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "private_host_blocked"

    def test_runs_a_url_ingest_through_to_a_completed_job(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        staged = settings.incoming_dir / "url-cube.stl"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(_cube_stl_bytes())

        async def fake_download(url: str):
            return staged, "cube.stl"

        with (
            patch.object(importer, "validate_public_url", return_value=None),
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers, "list_model_files", AsyncMock(return_value=None)
            ),
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(importer, "download_to_staging", fake_download),
        ):
            payload = _job(
                client,
                client.post(
                    "/api/v1/ingest/url",
                    headers=auth_headers,
                    json={"url": "https://cdn.test/cube.stl"},
                ),
                auth_headers,
            )
        assert payload["state"] == "completed", payload


class TestInspectArchiveBackground:
    def test_inspect_archive_background_rejects_missing_filename(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        boundary = "inspectboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename=""\r\n'
            "Content-Type: application/zip\r\n\r\n"
            "x\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        response = client.post(
            "/api/v1/ingest/archive/inspect",
            content=body,
            headers={
                **auth_headers,
                "content-type": f"multipart/form-data; boundary={boundary}",
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "filename_required"

    def test_inspect_archive_background_rejects_unsupported_suffix(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        response = client.post(
            "/api/v1/ingest/archive/inspect",
            headers=auth_headers,
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "unsupported_file_type"

    def test_inspect_archive_background_rejects_invalid_zip(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        response = client.post(
            "/api/v1/ingest/archive/inspect",
            headers=auth_headers,
            files={"file": ("bundle.zip", b"not actually a zip", "application/zip")},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "archive_invalid"

    def test_uploaded_zip_inspection_runs_as_reconnectable_job(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        _overlay["staging_dir"] = tmp_path / "staging"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("models/cube.stl", b"solid cube\nendsolid cube\n")

        queued = client.post(
            "/api/v1/ingest/archive/inspect",
            headers=auth_headers,
            files={"file": ("models.zip", archive.getvalue(), "application/zip")},
        )
        assert queued.status_code == 202
        drain_work()
        status = client.get(
            f"/api/v1/jobs/{queued.json()['job_id']}", headers=auth_headers
        )

        assert status.status_code == 200
        payload = status.json()
        assert payload["state"] == "completed"
        assert payload["stage"] == "completed"
        assert payload["result"]["kind"] == "archive_manifest"
        assert payload["result"]["archive_id"] == queued.json()["job_id"]
        assert payload["result"]["entries"][0]["name"] == "models/cube.stl"
        assert status.headers["cache-control"] == "no-store"

    def test_a_refused_archive_fails_its_job_with_the_reason(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        with patch.object(
            importer, "inspect_archive", side_effect=ImportError_("archive_zip_bomb")
        ):
            payload = _job(
                client,
                client.post(
                    "/api/v1/ingest/archive/inspect",
                    headers=auth_headers,
                    files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
                ),
                auth_headers,
            )

        assert (payload["state"], payload["error"]) == ("failed", "archive_zip_bomb")
        assert payload["retryable"] is False


class TestSelectArchiveEntries:
    def test_select_archive_entries_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/archive/does-not-exist/select",
            headers=auth_headers,
            json={"names": ["cube.stl"]},
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "archive_not_found"

    def test_another_users_archive_reads_as_not_found(
        self,
        tmp_path: Path,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)
        other = build_user(db_session, "not-the-owner")

        response = client.post(
            f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
            headers=_headers_for(other),
            json={"names": ["cube.stl"]},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "archive_not_found"

    def test_select_archive_entries_rejects_empty_selection(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)
        response = client.post(
            f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
            headers=auth_headers,
            json={"names": []},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "no_entries_selected"

    def test_rejects_a_selection_of_names_the_archive_lacks(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)
        response = client.post(
            f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
            headers=auth_headers,
            json={"names": ["elsewhere.stl"]},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "no_importable_files"

    def test_select_archive_entries_imports_chosen_files(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)
        payload = _job(
            client,
            client.post(
                f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
                headers=auth_headers,
                json={"names": ["cube.stl"]},
            ),
            auth_headers,
        )
        assert payload["state"] == "completed", payload
        assert payload["kind"] == JobKind.INGESTION_ARCHIVE_SELECTION

    def test_an_unsafe_entry_fails_the_selection_job(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)
        with patch.object(
            importer,
            "extract_selected",
            side_effect=ImportError_("archive_entry_unsafe"),
        ):
            payload = _job(
                client,
                client.post(
                    f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
                    headers=auth_headers,
                    json={"names": ["cube.stl"]},
                ),
                auth_headers,
            )
        assert (payload["state"], payload["error"]) == (
            "failed",
            "archive_entry_unsafe",
        )

    def test_an_extraction_yielding_nothing_fails_the_selection_job(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)
        with patch.object(importer, "extract_selected", return_value=[]):
            payload = _job(
                client,
                client.post(
                    f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
                    headers=auth_headers,
                    json={"names": ["cube.stl"]},
                ),
                auth_headers,
            )
        assert (payload["state"], payload["error"]) == (
            "failed",
            "no_importable_files",
        )

    def test_an_archive_is_selected_from_once(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """A second selection is refused rather than importing the same files again."""
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)
        url = f"/api/v1/ingest/archive/{manifest['archive_id']}/select"
        first = client.post(url, headers=auth_headers, json={"names": ["cube.stl"]})

        second = client.post(url, headers=auth_headers, json={"names": ["cube.stl"]})

        assert first.status_code == 202, first.text
        assert second.status_code == 409, second.text
        assert second.json()["detail"] == "archive_already_claimed"

    def test_select_archive_entries_accepts_entry_ids(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)

        payload = _job(
            client,
            client.post(
                f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
                headers=auth_headers,
                json={"entry_ids": [manifest["entries"][0]["entry_id"]]},
            ),
            auth_headers,
        )

        # Ids rather than names, so a filename with awkward bytes is still selectable.
        assert payload["state"] == "completed", payload

    def test_select_archive_entries_rejects_an_entry_id_that_is_not_in_the_archive(
        self, tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        use_local_storage(tmp_path)
        manifest = _inspected(client, auth_headers)

        response = client.post(
            f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
            headers=auth_headers,
            json={"entry_ids": ["not-a-real-entry"]},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "archive_entry_not_found"


class TestSelectModelFiles:
    def test_select_model_files_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/url/files/does-not-exist/select",
            headers=auth_headers,
            json={"file_ids": ["1"]},
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "files_not_found"

    def test_another_users_page_listing_reads_as_not_found(
        self, client: TestClient, db_session: Session, model_files_manifest
    ) -> None:
        token = model_files_manifest()
        other = build_user(db_session, "not-the-lister")

        response = client.post(
            f"/api/v1/ingest/url/files/{token}/select",
            headers=_headers_for(other),
            json={"file_ids": ["1"]},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "files_not_found"

    def test_select_model_files_rejects_empty_selection(
        self, client: TestClient, auth_headers: dict[str, str], model_files_manifest
    ) -> None:
        response = client.post(
            f"/api/v1/ingest/url/files/{model_files_manifest()}/select",
            headers=auth_headers,
            json={"file_ids": []},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "no_files_selected"

    def test_select_model_files_rejects_unmatched_ids(
        self, client: TestClient, auth_headers: dict[str, str], model_files_manifest
    ) -> None:
        response = client.post(
            f"/api/v1/ingest/url/files/{model_files_manifest()}/select",
            headers=auth_headers,
            json={"file_ids": ["does-not-exist"]},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "no_files_selected"

    def test_select_model_files_imports_chosen_files(
        self,
        tmp_path: Path,
        client: TestClient,
        auth_headers: dict[str, str],
        model_files_manifest,
    ) -> None:
        use_local_storage(tmp_path)
        staged = tmp_path / "staging" / "cube.stl"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(_cube_stl_bytes())

        async def fake_resolve(url: str, files):
            return ["https://cdn.test/cube.stl"]

        async def fake_download_and_collect(url: str):
            return [(staged, "cube.stl")]

        with (
            patch.object(
                import_resolvers, "resolve_selected_download", side_effect=fake_resolve
            ),
            patch.object(
                ingest_background,
                "_download_and_collect",
                side_effect=fake_download_and_collect,
            ),
        ):
            payload = _job(
                client,
                client.post(
                    f"/api/v1/ingest/url/files/{model_files_manifest()}/select",
                    headers=auth_headers,
                    json={"file_ids": ["1"]},
                ),
                auth_headers,
            )
        assert payload["state"] == "completed", payload

    def test_a_page_listing_is_selected_from_once(
        self, client: TestClient, auth_headers: dict[str, str], model_files_manifest
    ) -> None:
        url = f"/api/v1/ingest/url/files/{model_files_manifest()}/select"
        first = client.post(url, headers=auth_headers, json={"file_ids": ["1"]})

        second = client.post(url, headers=auth_headers, json={"file_ids": ["1"]})

        assert first.status_code == 202, first.text
        assert second.status_code == 404, second.text
        assert second.json()["detail"] == "files_not_found"


class TestSelectCollectionMembers:
    def test_select_collection_members_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/ingest/collection/does-not-exist/select",
            headers=auth_headers,
            json={"member_ids": ["1"]},
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "collection_not_found"

    def test_select_collection_members_rejects_empty_selection(
        self, client: TestClient, auth_headers: dict[str, str], collection_manifest
    ) -> None:
        response = client.post(
            f"/api/v1/ingest/collection/{collection_manifest}/select",
            headers=auth_headers,
            json={"member_ids": []},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "no_members_selected"

    def test_select_collection_members_rejects_unmatched_ids(
        self, client: TestClient, auth_headers: dict[str, str], collection_manifest
    ) -> None:
        response = client.post(
            f"/api/v1/ingest/collection/{collection_manifest}/select",
            headers=auth_headers,
            json={"member_ids": ["does-not-exist"]},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "no_members_selected"

    def test_select_collection_members_imports_chosen_members(
        self,
        tmp_path: Path,
        client: TestClient,
        auth_headers: dict[str, str],
        collection_manifest,
    ) -> None:
        use_local_storage(tmp_path)
        staged = tmp_path / "staging" / "cube.stl"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(_cube_stl_bytes())

        with patch.object(
            ingest_background,
            "_stage_members",
            AsyncMock(
                return_value=[
                    importer.ResolvedGroup(
                        source_url="https://printables.com/model/1",
                        title="A",
                        staged_files=[(staged, "cube.stl")],
                    )
                ]
            ),
        ):
            payload = _job(
                client,
                client.post(
                    f"/api/v1/ingest/collection/{collection_manifest}/select",
                    headers=auth_headers,
                    json={"member_ids": ["1"]},
                ),
                auth_headers,
            )
        assert payload["state"] == "completed", payload
