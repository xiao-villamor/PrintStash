"""Defends select collection members rejects empty selection at the ingest API integration boundary.

A regression could publish an incomplete import or lose its durable job and artifact state.
"""

from __future__ import annotations

from ._import_progress_shared import (
    AsyncMock,
    ImportError_,
    Path,
    TestClient,
    _configure_storage,
    _cube_stl_bytes,
    _overlay,
    _uuid,
    _zip_bytes,
    get_session_factory,
    import_resolvers,
    importer,
    ingest_module,
    io,
    patch,
    pytest,
    registry,
)


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
