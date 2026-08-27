"""Defends select archive entries owner mismatch hidden as not found at the ingest API integration boundary.

A regression could publish an incomplete import or lose its durable job and artifact state.
"""

from __future__ import annotations

from ._import_progress_shared import (
    AsyncMock,
    ImportError_,
    Path,
    Session,
    TestClient,
    _configure_storage,
    _cube_stl_bytes,
    _regular_user,
    _uuid,
    _zip_bytes,
    create_access_token,
    get_session_factory,
    import_resolvers,
    importer,
    ingest_module,
    patch,
    pytest,
    registry,
)


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
