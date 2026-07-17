from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import ExternalLibrary, User
from app.schemas.ingest import IngestJobStatus
from app.services import runtime_config
from app.services.auth import create_access_token, hash_password
from app.services.jobs import JobRegistry, safe_error, safe_item


@pytest.mark.parametrize(
    "stage",
    [
        "resolving",
        "downloading",
        "inspecting",
        "extracting",
        "hashing",
        "ingesting",
        "thumbnailing",
        "completed",
    ],
)
def test_registry_supports_every_import_stage(stage: str) -> None:
    jobs = JobRegistry()
    job_id = jobs.create(owner_user_id=7)
    jobs.update(job_id, stage=stage)  # type: ignore[arg-type]
    assert jobs.get(job_id).stage == stage  # type: ignore[union-attr]


def test_progress_keeps_total_unknown_until_discovery() -> None:
    jobs = JobRegistry()
    job_id = jobs.create(owner_user_id=7)
    jobs.update(job_id, state="running", stage="resolving", processed=0)
    assert jobs.get(job_id).total is None  # type: ignore[union-attr]

    jobs.update(job_id, stage="ingesting", total=3, processed=1)
    status = jobs.get(job_id)
    assert status is not None
    assert (status.processed, status.total) == (1, 3)


def test_partial_success_has_summary_and_safe_retry_details() -> None:
    jobs = JobRegistry()
    job_id = jobs.create(owner_user_id=7)
    jobs.update(
        job_id,
        state="completed",
        succeeded=2,
        deduplicated=1,
        skipped=1,
        failed=1,
        retryable=True,
        result={"errors": ["/srv/private/models/broken.stl: token=secret"]},
        failed_items=[
            {
                "name": "/srv/private/models/broken.stl",
                "reason": "read /srv/private/models/broken.stl?token=secret failed",
                "retryable": True,
            }
        ],
    )
    status = jobs.get(job_id)
    assert status is not None
    assert status.completion == "completed_with_warnings"
    assert status.failed_items[0].name == "broken.stl"
    assert "/srv/private" not in status.failed_items[0].reason
    assert "secret" not in status.failed_items[0].reason
    assert "/srv/private" not in str(status.result)
    assert "secret" not in str(status.result)


def test_complete_failure_is_distinct_from_partial_success() -> None:
    jobs = JobRegistry()
    job_id = jobs.create(owner_user_id=7)
    jobs.update(job_id, state="failed", error="download_failed", retryable=True)
    status = jobs.get(job_id)
    assert status is not None
    assert status.completion == "failed_before_import"
    assert status.succeeded == 0


def test_reconnect_listing_respects_owner_permissions() -> None:
    jobs = JobRegistry()
    own = jobs.create(owner_user_id=7)
    other = jobs.create(owner_user_id=8)
    assert [job.job_id for job in jobs.list_for_user(7)] == [own]
    assert {job.job_id for job in jobs.list_for_user(7, is_superuser=True)} == {
        own,
        other,
    }
    assert jobs.get(own).state == "pending"  # type: ignore[union-attr]


def test_display_sanitizers_hide_paths_credentials_and_control_characters() -> None:
    assert safe_item("/mnt/nas/private/Cube\n.stl") == "Cube.stl"
    error = safe_error("failed /mnt/nas/private/Cube.stl?api_key=hunter2")
    assert error is not None
    assert "/mnt/nas" not in error
    assert "hunter2" not in error


def test_progress_schema_rejects_unknown_stage() -> None:
    with pytest.raises(ValueError):
        IngestJobStatus(job_id="bad", state="running", stage="uploading")


def test_uploaded_zip_inspection_runs_as_reconnectable_job(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
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
    job_id = queued.json()["job_id"]
    status = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert status.status_code == 200
    payload = status.json()
    assert payload["state"] == "completed"
    assert payload["stage"] == "completed"
    assert payload["result"]["kind"] == "archive_manifest"
    assert payload["result"]["entries"][0]["name"] == "models/cube.stl"


# --------------------------------------------------------------------------- #
# Everything below drives ``app.api.v1.ingest`` internals directly — pure
# helpers, the pending-registry TTL, and the URL/archive/collection background
# tasks — following the same "patch the module's own network-facing
# functions" approach as ``test_import_resolvers.py``.
# --------------------------------------------------------------------------- #
import uuid as _uuid  # noqa: E402

from app.api.v1 import ingest as ingest_module  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.services import import_resolvers, importer  # noqa: E402
from app.services.importer import ImportError_  # noqa: E402
from app.services.jobs import registry  # noqa: E402


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    from app.core.config import settings

    settings.incoming_dir.mkdir(parents=True, exist_ok=True)


def _regular_user(session: Session, username: str = "regular") -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _cube_stl_bytes() -> bytes:
    return (
        b"solid cube\nfacet normal 0 0 1\nouter loop\n"
        b"endloop\nendfacet\nendsolid cube\n"
    )


def test_pending_registry_prunes_entries_past_ttl() -> None:
    registry_ = ingest_module._PendingRegistry()
    stale = ingest_module._PendingModelFiles(
        page_url="https://x", page_title="x", owner_user_id=1, files=[], created_at=0.0
    )
    registry_._items["stale-token"] = stale
    fresh_token = registry_.add(
        ingest_module._PendingModelFiles(
            page_url="https://y", page_title="y", owner_user_id=1, files=[]
        )
    )
    assert registry_.get("stale-token") is None
    assert registry_.get(fresh_token) is not None
    assert registry_.pop(fresh_token) is not None
    assert registry_.get(fresh_token) is None


def test_makerworld_cookie_prefers_override_then_settings_then_none() -> None:
    _overlay["makerworld_cookie"] = ""
    assert ingest_module._makerworld_cookie("  session=abc  ") == "session=abc"
    assert ingest_module._makerworld_cookie(None) is None
    assert ingest_module._makerworld_cookie("   ") is None

    _overlay["makerworld_cookie"] = "instance=cookie"
    assert ingest_module._makerworld_cookie(None) == "instance=cookie"
    assert ingest_module._makerworld_cookie("override") == "override"


def test_collection_target_nests_under_parent_and_defaults_title() -> None:
    assert ingest_module._collection_target(None, "My Model") == "My Model"
    assert ingest_module._collection_target("Parent/", "Child") == "Parent/Child"
    assert ingest_module._collection_target(None, "  ") == "Imported collection"
    assert ingest_module._collection_target("  ", "  ") == "Imported collection"


def test_owns_helper_permissions() -> None:
    owner = User(id=1, username="owner", hashed_password="x", is_superuser=False)
    other = User(id=2, username="other", hashed_password="x", is_superuser=False)
    admin = User(id=3, username="admin", hashed_password="x", is_superuser=True)
    assert ingest_module._owns(None, other) is True
    assert ingest_module._owns(1, owner) is True
    assert ingest_module._owns(1, other) is False
    assert ingest_module._owns(1, admin) is True


@pytest.mark.asyncio
async def test_download_and_collect_skips_non_importable_direct_file(
    monkeypatch,
) -> None:
    async def fake_download(url: str):
        staged = Path.cwd() / "readme.txt"
        staged.write_bytes(b"not a model")
        return staged, "readme.txt"

    monkeypatch.setattr(ingest_module.importer, "download_to_staging", fake_download)
    result = await ingest_module._download_and_collect("https://cdn.test/readme.txt")
    assert result == []


@pytest.mark.asyncio
async def test_download_and_collect_extracts_zip_entries(monkeypatch) -> None:
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as bundle:
        bundle.writestr("cube.stl", _cube_stl_bytes())

    async def fake_download(url: str):
        staged = Path.cwd() / "bundle.zip"
        staged.write_bytes(zip_bytes.getvalue())
        return staged, "bundle.zip"

    monkeypatch.setattr(ingest_module.importer, "download_to_staging", fake_download)
    result = await ingest_module._download_and_collect("https://cdn.test/bundle.zip")
    assert [name for _path, name in result] == ["cube.stl"]


@pytest.mark.asyncio
async def test_stage_members_isolates_per_member_failures() -> None:
    good = import_resolvers.CollectionMember(
        page_url="https://ok.test/model", title="Good", source_id="1"
    )
    bad = import_resolvers.CollectionMember(
        page_url="https://bad.test/model", title="Bad", source_id="2"
    )
    crashy = import_resolvers.CollectionMember(
        page_url="https://crash.test/model", title="Crashy", source_id="3"
    )

    async def fake_resolve(url: str, *, makerworld_cookie=None):
        if url == "https://bad.test/model":
            raise importer.ImportError_("member_resolve_failed")
        if url == "https://crash.test/model":
            raise RuntimeError("boom")
        return None  # unresolved -> treat page url itself as a direct link

    async def fake_download_and_collect(url: str):
        return [(Path("cube.stl"), "cube.stl")] if url == good.page_url else []

    with (
        patch.object(import_resolvers, "resolve_page_url", side_effect=fake_resolve),
        patch.object(
            ingest_module,
            "_download_and_collect",
            side_effect=fake_download_and_collect,
        ),
    ):
        groups = await ingest_module._stage_members(
            [good, bad, crashy], makerworld_cookie=None
        )

    by_title = {g.title: g for g in groups}
    assert by_title["Good"].error is None
    assert by_title["Good"].staged_files == [(Path("cube.stl"), "cube.stl")]
    assert by_title["Bad"].error == "member_resolve_failed"
    assert by_title["Crashy"].error == "boom"


def test_ingest_model_superuser_can_target_unknown_collection(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    response = client.post(
        "/api/v1/ingest/model",
        headers=auth_headers,
        files={"file": ("cube.stl", _cube_stl_bytes(), "application/sla")},
        data={"collection": "brand/new/path"},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["state"] == "completed", job.json()


def test_ingest_model_target_library_not_found(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _configure_storage(tmp_path)
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
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _configure_storage(tmp_path)
    runtime_config.set_external_libraries_enabled(db_session, True)
    lib = ExternalLibrary(name="nas", root_path=str(tmp_path / "nas"), enabled=False)
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


# --------------------------------------------------------------------------- #
# ZIP archive endpoints: direct upload + inspect-in-background.
# --------------------------------------------------------------------------- #
def _zip_bytes(*, entry: str = "cube.stl", content: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr(entry, content or _cube_stl_bytes())
    return buf.getvalue()


def test_ingest_archive_rejects_missing_filename(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    boundary = "archiveboundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename=""\r\n'
        "Content-Type: application/zip\r\n\r\n"
        "x\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    response = client.post(
        "/api/v1/ingest/archive",
        content=body,
        headers={
            **auth_headers,
            "content-type": f"multipart/form-data; boundary={boundary}",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "filename_required"


def test_ingest_archive_rejects_unsupported_suffix(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    response = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "unsupported_file_type"


def test_ingest_archive_rejects_invalid_zip(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    response = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", b"not actually a zip", "application/zip")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "archive_invalid"


def test_ingest_archive_upload_returns_manifest(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    response = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["archive_name"] == "bundle.zip"
    assert payload["entries"][0]["name"] == "cube.stl"


def test_inspect_archive_background_rejects_missing_filename(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
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
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    response = client.post(
        "/api/v1/ingest/archive/inspect",
        headers=auth_headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "unsupported_file_type"


def test_inspect_archive_background_rejects_invalid_zip(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    response = client.post(
        "/api/v1/ingest/archive/inspect",
        headers=auth_headers,
        files={"file": ("bundle.zip", b"not actually a zip", "application/zip")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "archive_invalid"


@pytest.mark.asyncio
async def test_inspect_uploaded_archive_reports_import_error(tmp_path: Path) -> None:
    _configure_storage(tmp_path)
    staged = tmp_path / "staged.zip"
    staged.write_bytes(_zip_bytes())
    job_id = registry.create(owner_user_id=1)

    with patch.object(
        ingest_module.importer,
        "inspect_archive",
        side_effect=ImportError_("archive_zip_bomb"),
    ):
        await ingest_module._inspect_uploaded_archive(
            job_id=job_id,
            staged=staged,
            original_filename="staged.zip",
            actor_user_id=1,
        )

    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "archive_zip_bomb"
    assert not staged.exists()


@pytest.mark.asyncio
async def test_inspect_uploaded_archive_reports_unexpected_error(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    staged = tmp_path / "staged2.zip"
    staged.write_bytes(_zip_bytes())
    job_id = registry.create(owner_user_id=1)

    with patch.object(
        ingest_module.importer, "inspect_archive", side_effect=RuntimeError("boom")
    ):
        await ingest_module._inspect_uploaded_archive(
            job_id=job_id,
            staged=staged,
            original_filename="staged2.zip",
            actor_user_id=1,
        )

    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "boom"


def test_select_archive_entries_not_found(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/ingest/archive/does-not-exist/select",
        headers=auth_headers,
        json={"names": ["cube.stl"]},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "archive_not_found"


def test_select_archive_entries_owner_mismatch_hidden_as_not_found(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _configure_storage(tmp_path)
    upload = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
    )
    archive_id = upload.json()["archive_id"]
    importer.archives._items[archive_id].owner_user_id = 999999

    other = _regular_user(db_session, "not-the-owner")
    other_headers = {
        "Authorization": f"Bearer {create_access_token(other.id, other.username, scope='write')}"
    }
    response = client.post(
        f"/api/v1/ingest/archive/{archive_id}/select",
        headers=other_headers,
        json={"names": ["cube.stl"]},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "archive_not_found"


def test_select_archive_entries_rejects_empty_selection(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    upload = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
    )
    archive_id = upload.json()["archive_id"]
    response = client.post(
        f"/api/v1/ingest/archive/{archive_id}/select",
        headers=auth_headers,
        json={"names": []},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "no_entries_selected"


def test_select_archive_entries_imports_chosen_files(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    upload = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
    )
    archive_id = upload.json()["archive_id"]
    response = client.post(
        f"/api/v1/ingest/archive/{archive_id}/select",
        headers=auth_headers,
        json={"names": ["cube.stl"]},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["state"] == "completed", job.json()


# --------------------------------------------------------------------------- #
# URL import endpoint + background task branches.
# --------------------------------------------------------------------------- #
def test_ingest_url_rejects_blank_url(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/ingest/url", headers=auth_headers, json={"url": "   "}
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "url_required"


def test_ingest_url_rejects_unsafe_url(
    client: TestClient, auth_headers: dict[str, str]
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


@pytest.mark.asyncio
async def test_import_from_url_collection_resolve_failure_marks_job_failed(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.schemas.ingest import UrlIngestRequest

    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://printables.com/collections/9")
    with (
        patch.object(
            import_resolvers, "classify_collection", return_value="printables"
        ),
        patch.object(
            import_resolvers, "resolve_collection_url", AsyncMock(return_value=None)
        ),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "collection_resolve_failed"


@pytest.mark.asyncio
async def test_import_from_url_download_import_error_marks_job_failed(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.schemas.ingest import UrlIngestRequest

    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://cdn.test/model.stl")
    with (
        patch.object(import_resolvers, "classify_collection", return_value=None),
        patch.object(
            import_resolvers, "list_model_files", AsyncMock(return_value=None)
        ),
        patch.object(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        ),
        patch.object(
            ingest_module.importer,
            "download_to_staging",
            AsyncMock(side_effect=ImportError_("download_failed")),
        ),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "download_failed"


@pytest.mark.asyncio
async def test_import_from_url_unexpected_download_error_marks_job_failed(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.schemas.ingest import UrlIngestRequest

    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://cdn.test/model.stl")
    with (
        patch.object(import_resolvers, "classify_collection", return_value=None),
        patch.object(
            import_resolvers, "list_model_files", AsyncMock(return_value=None)
        ),
        patch.object(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        ),
        patch.object(
            ingest_module.importer,
            "download_to_staging",
            AsyncMock(side_effect=RuntimeError("network blew up")),
        ),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "network blew up"


@pytest.mark.asyncio
async def test_import_from_url_non_file_response_reports_not_a_direct_file(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.core.config import settings
    from app.schemas.ingest import UrlIngestRequest

    staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.html"
    staged.write_bytes(b"<html>not a model</html>")
    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://example.com/some-page")

    async def fake_download(url: str):
        return staged, "some-page.html"

    with (
        patch.object(import_resolvers, "classify_collection", return_value=None),
        patch.object(
            import_resolvers, "list_model_files", AsyncMock(return_value=None)
        ),
        patch.object(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        ),
        patch.object(ingest_module.importer, "download_to_staging", fake_download),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "url_not_a_direct_file"
    assert not staged.exists()


@pytest.mark.asyncio
async def test_import_from_url_zip_response_stages_archive_manifest(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.core.config import settings
    from app.schemas.ingest import UrlIngestRequest

    staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.zip"
    staged.write_bytes(_zip_bytes())
    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://cdn.test/bundle.zip")

    async def fake_download(url: str):
        return staged, "bundle.zip"

    with (
        patch.object(import_resolvers, "classify_collection", return_value=None),
        patch.object(
            import_resolvers, "list_model_files", AsyncMock(return_value=None)
        ),
        patch.object(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        ),
        patch.object(ingest_module.importer, "download_to_staging", fake_download),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "completed"
    assert status.result["kind"] == "archive_manifest"


@pytest.mark.asyncio
async def test_import_from_url_multi_file_page_stages_files_manifest(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.schemas.ingest import UrlIngestRequest

    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://www.printables.com/model/123-x")
    files = [
        import_resolvers.ModelFile(file_id="1", name="a.stl", file_type="stl"),
        import_resolvers.ModelFile(file_id="2", name="b.stl", file_type="stl"),
    ]

    with (
        patch.object(import_resolvers, "classify_collection", return_value=None),
        patch.object(
            import_resolvers,
            "list_model_files",
            AsyncMock(return_value=("Cool Model", files)),
        ),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "completed"
    assert status.result["kind"] == "model_files_manifest"
    assert status.result["page_title"] == "Cool Model"
    assert len(status.result["files"]) == 2


@pytest.mark.asyncio
async def test_handle_collection_url_review_stages_manifest(tmp_path: Path) -> None:
    _configure_storage(tmp_path)
    from app.schemas.ingest import UrlIngestRequest

    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://printables.com/collections/9", review=True)
    members = [
        import_resolvers.CollectionMember(
            page_url="https://printables.com/model/1", title="A", source_id="1"
        )
    ]
    with patch.object(
        import_resolvers,
        "resolve_collection_url",
        AsyncMock(return_value=("Cool Collection", members)),
    ):
        await ingest_module._handle_collection_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "completed"
    assert status.result["kind"] == "collection_manifest"
    assert status.result["collection_name"] == "Cool Collection"
    assert len(status.result["members"]) == 1


@pytest.mark.asyncio
async def test_handle_collection_url_auto_imports_members(tmp_path: Path) -> None:
    _configure_storage(tmp_path)
    from app.schemas.ingest import UrlIngestRequest

    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://printables.com/collections/9", review=False)
    members = [
        import_resolvers.CollectionMember(
            page_url="https://printables.com/model/1", title="A", source_id="1"
        )
    ]
    staged = tmp_path / "staging" / "cube.stl"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(_cube_stl_bytes())

    with (
        patch.object(
            import_resolvers,
            "resolve_collection_url",
            AsyncMock(return_value=("Cool Collection", members)),
        ),
        patch.object(
            ingest_module,
            "_stage_members",
            AsyncMock(
                return_value=[
                    importer.ResolvedGroup(
                        source_url=members[0].page_url,
                        title="A",
                        staged_files=[(staged, "cube.stl")],
                    )
                ]
            ),
        ),
    ):
        await ingest_module._handle_collection_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "completed"
    assert status.succeeded == 1


def test_select_model_files_not_found(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/ingest/url/files/does-not-exist/select",
        headers=auth_headers,
        json={"file_ids": ["1"]},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "files_not_found"


def test_select_model_files_rejects_empty_selection(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = ingest_module.pending_model_files.add(
        ingest_module._PendingModelFiles(
            page_url="https://x", page_title="x", owner_user_id=None, files=[]
        )
    )
    response = client.post(
        f"/api/v1/ingest/url/files/{token}/select",
        headers=auth_headers,
        json={"file_ids": []},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "no_files_selected"


def test_select_model_files_rejects_unmatched_ids(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    files = [import_resolvers.ModelFile(file_id="1", name="a.stl", file_type="stl")]
    token = ingest_module.pending_model_files.add(
        ingest_module._PendingModelFiles(
            page_url="https://x", page_title="x", owner_user_id=None, files=files
        )
    )
    response = client.post(
        f"/api/v1/ingest/url/files/{token}/select",
        headers=auth_headers,
        json={"file_ids": ["does-not-exist"]},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "no_files_selected"


def test_select_model_files_imports_chosen_files(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    files = [import_resolvers.ModelFile(file_id="1", name="cube.stl", file_type="stl")]
    token = ingest_module.pending_model_files.add(
        ingest_module._PendingModelFiles(
            page_url="https://www.printables.com/model/1",
            page_title="x",
            owner_user_id=None,
            files=files,
        )
    )
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
            ingest_module,
            "_download_and_collect",
            side_effect=fake_download_and_collect,
        ),
    ):
        response = client.post(
            f"/api/v1/ingest/url/files/{token}/select",
            headers=auth_headers,
            json={"file_ids": ["1"]},
        )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["state"] == "completed", job.json()


def test_select_collection_members_not_found(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/ingest/collection/does-not-exist/select",
        headers=auth_headers,
        json={"member_ids": ["1"]},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "collection_not_found"


def test_select_collection_members_rejects_empty_selection(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = ingest_module.pending_collections.add(
        ingest_module._PendingCollection(
            title="Cool",
            target_collection="Cool",
            owner_user_id=None,
            members=[
                import_resolvers.CollectionMember(
                    page_url="https://x", title="A", source_id="1"
                )
            ],
        )
    )
    response = client.post(
        f"/api/v1/ingest/collection/{token}/select",
        headers=auth_headers,
        json={"member_ids": []},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "no_members_selected"


def test_select_collection_members_imports_chosen_members(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    member = import_resolvers.CollectionMember(
        page_url="https://printables.com/model/1", title="A", source_id="1"
    )
    token = ingest_module.pending_collections.add(
        ingest_module._PendingCollection(
            title="Cool",
            target_collection="Cool",
            owner_user_id=None,
            members=[member],
        )
    )
    staged = tmp_path / "staging" / "cube.stl"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(_cube_stl_bytes())

    with patch.object(
        ingest_module,
        "_stage_members",
        AsyncMock(
            return_value=[
                importer.ResolvedGroup(
                    source_url=member.page_url,
                    title="A",
                    staged_files=[(staged, "cube.stl")],
                )
            ]
        ),
    ):
        response = client.post(
            f"/api/v1/ingest/collection/{token}/select",
            headers=auth_headers,
            json={"member_ids": ["1"]},
        )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.json()["state"] == "completed", job.json()


@pytest.mark.asyncio
async def test_run_file_selection_import_reports_import_error(tmp_path: Path) -> None:
    _configure_storage(tmp_path)
    job_id = registry.create(owner_user_id=1)
    with patch.object(
        import_resolvers,
        "resolve_selected_download",
        AsyncMock(side_effect=ImportError_("printables_resolve_failed")),
    ):
        await ingest_module._run_file_selection_import(
            job_id=job_id,
            page_url="https://www.printables.com/model/1",
            files=[],
            collection=None,
            tags=None,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "printables_resolve_failed"


@pytest.mark.asyncio
async def test_run_file_selection_import_no_files_reports_failure(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    job_id = registry.create(owner_user_id=1)
    with (
        patch.object(
            import_resolvers,
            "resolve_selected_download",
            AsyncMock(return_value=["https://cdn.test/readme.txt"]),
        ),
        patch.object(
            ingest_module, "_download_and_collect", AsyncMock(return_value=[])
        ),
    ):
        await ingest_module._run_file_selection_import(
            job_id=job_id,
            page_url="https://www.printables.com/model/1",
            files=[],
            collection=None,
            tags=None,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "no_importable_files"


@pytest.mark.asyncio
async def test_run_collection_member_import_reports_unexpected_error(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    job_id = registry.create(owner_user_id=1)
    with patch.object(
        ingest_module, "_stage_members", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        await ingest_module._run_collection_member_import(
            job_id=job_id,
            members=[],
            target_collection="Cool",
            tags=None,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "boom"


def test_stage_upload_rejects_stream_exceeding_max_bytes(
    tmp_path: Path, monkeypatch
) -> None:
    """Exercise ``_stage_upload``'s own size guard directly.

    In production this sits behind ``RequestBodyLimitMiddleware``, which
    enforces the same ``settings.max_upload_bytes`` ceiling on the raw HTTP
    body before multipart parsing even starts — so a real oversized upload
    never reaches this inner check via the ASGI stack. ``_stage_upload`` is
    still exercised directly here since it is a real, independently callable
    guard (e.g. against a future caller that streams a file in without going
    through that middleware).
    """
    _configure_storage(tmp_path)
    _overlay["max_upload_mb"] = 0.001  # ~1KB

    class _FakeUpload:
        def __init__(self, data: bytes) -> None:
            self.file = io.BytesIO(data)

    upload = _FakeUpload(b"G28\n" * 10_000)
    with pytest.raises(Exception) as exc_info:
        ingest_module._stage_upload(upload, ".gcode")  # type: ignore[arg-type]
    from fastapi import HTTPException

    assert isinstance(exc_info.value, HTTPException)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "upload_too_large"


@pytest.mark.asyncio
async def test_download_and_collect_returns_direct_mesh_file(monkeypatch) -> None:
    async def fake_download(url: str):
        staged = Path.cwd() / "cube.stl"
        staged.write_bytes(_cube_stl_bytes())
        return staged, "cube.stl"

    monkeypatch.setattr(ingest_module.importer, "download_to_staging", fake_download)
    result = await ingest_module._download_and_collect("https://cdn.test/cube.stl")
    assert [name for _path, name in result] == ["cube.stl"]


@pytest.mark.asyncio
async def test_stage_members_reports_no_importable_files_without_error() -> None:
    empty = import_resolvers.CollectionMember(
        page_url="https://empty.test/model", title="Empty", source_id="9"
    )
    with (
        patch.object(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        ),
        patch.object(
            ingest_module, "_download_and_collect", AsyncMock(return_value=[])
        ),
    ):
        groups = await ingest_module._stage_members([empty], makerworld_cookie=None)
    assert groups[0].error == "no_importable_files"


@pytest.mark.asyncio
async def test_import_from_url_zip_inspect_import_error_marks_job_failed(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.core.config import settings
    from app.schemas.ingest import UrlIngestRequest

    staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.zip"
    staged.write_bytes(_zip_bytes())
    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://cdn.test/bundle.zip")

    async def fake_download(url: str):
        return staged, "bundle.zip"

    with (
        patch.object(import_resolvers, "classify_collection", return_value=None),
        patch.object(
            import_resolvers, "list_model_files", AsyncMock(return_value=None)
        ),
        patch.object(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        ),
        patch.object(ingest_module.importer, "download_to_staging", fake_download),
        patch.object(
            ingest_module.importer,
            "inspect_archive",
            side_effect=ImportError_("archive_zip_bomb"),
        ),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "archive_zip_bomb"
    assert not staged.exists()


@pytest.mark.asyncio
async def test_import_from_url_single_direct_file_imports_successfully(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    from app.core.config import settings
    from app.schemas.ingest import UrlIngestRequest

    staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.stl"
    staged.write_bytes(_cube_stl_bytes())
    job_id = registry.create(owner_user_id=1)
    req = UrlIngestRequest(url="https://cdn.test/cube.stl")

    async def fake_download(url: str):
        return staged, "cube.stl"

    with (
        patch.object(import_resolvers, "classify_collection", return_value=None),
        patch.object(
            import_resolvers, "list_model_files", AsyncMock(return_value=None)
        ),
        patch.object(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        ),
        patch.object(ingest_module.importer, "download_to_staging", fake_download),
    ):
        await ingest_module._import_from_url(
            job_id=job_id,
            req=req,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "completed", status.error
    assert status.model_id is not None


def test_ingest_url_creates_job_and_completes(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    from app.core.config import settings

    staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.stl"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(_cube_stl_bytes())

    async def fake_download(url: str):
        return staged, "cube.stl"

    with (
        patch.object(ingest_module.importer, "validate_public_url", return_value=None),
        patch.object(
            ingest_module.import_resolvers, "classify_collection", return_value=None
        ),
        patch.object(
            ingest_module.import_resolvers,
            "list_model_files",
            AsyncMock(return_value=None),
        ),
        patch.object(
            ingest_module.import_resolvers,
            "resolve_page_url",
            AsyncMock(return_value=None),
        ),
        patch.object(ingest_module.importer, "download_to_staging", fake_download),
    ):
        response = client.post(
            "/api/v1/ingest/url",
            headers=auth_headers,
            json={"url": "https://cdn.test/cube.stl"},
        )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["state"] == "completed", job.json()


def test_ingest_archive_reports_inspect_import_error(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    with patch.object(
        ingest_module.importer,
        "inspect_archive",
        side_effect=ImportError_("archive_zip_bomb"),
    ):
        response = client.post(
            "/api/v1/ingest/archive",
            headers=auth_headers,
            files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
        )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "archive_zip_bomb"


def test_select_archive_entries_reports_extract_import_error(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    upload = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
    )
    archive_id = upload.json()["archive_id"]
    with patch.object(
        ingest_module.importer,
        "extract_selected",
        side_effect=ImportError_("archive_entry_unsafe"),
    ):
        response = client.post(
            f"/api/v1/ingest/archive/{archive_id}/select",
            headers=auth_headers,
            json={"names": ["cube.stl"]},
        )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "archive_entry_unsafe"


def test_select_archive_entries_reports_no_importable_files(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    upload = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", _zip_bytes(), "application/zip")},
    )
    archive_id = upload.json()["archive_id"]
    with patch.object(ingest_module.importer, "extract_selected", return_value=[]):
        response = client.post(
            f"/api/v1/ingest/archive/{archive_id}/select",
            headers=auth_headers,
            json={"names": ["cube.stl"]},
        )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "no_importable_files"


@pytest.mark.asyncio
async def test_run_file_selection_import_reports_unexpected_error(
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    job_id = registry.create(owner_user_id=1)
    with patch.object(
        import_resolvers,
        "resolve_selected_download",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await ingest_module._run_file_selection_import(
            job_id=job_id,
            page_url="https://www.printables.com/model/1",
            files=[],
            collection=None,
            tags=None,
            actor_user_id=1,
            session_factory=get_session_factory(),
        )
    status = registry.get(job_id)
    assert status is not None
    assert status.state == "failed"
    assert status.error == "boom"


def test_select_collection_members_rejects_unmatched_ids(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    token = ingest_module.pending_collections.add(
        ingest_module._PendingCollection(
            title="Cool",
            target_collection="Cool",
            owner_user_id=None,
            members=[
                import_resolvers.CollectionMember(
                    page_url="https://x", title="A", source_id="1"
                )
            ],
        )
    )
    response = client.post(
        f"/api/v1/ingest/collection/{token}/select",
        headers=auth_headers,
        json={"member_ids": ["does-not-exist"]},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "no_members_selected"
