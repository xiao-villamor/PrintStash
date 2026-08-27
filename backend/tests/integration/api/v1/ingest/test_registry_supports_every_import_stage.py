"""Defends registry supports every import stage at the ingest API integration boundary.

A regression could publish an incomplete import or lose its durable job and artifact state.
"""

from __future__ import annotations

from ._import_progress_shared import (
    BackgroundJob,
    ExternalLibrary,
    ImportError_,
    IngestJobStatus,
    JobRegistry,
    Path,
    Session,
    TestClient,
    User,
    _configure_storage,
    _cube_stl_bytes,
    _overlay,
    _zip_bytes,
    import_resolvers,
    importer,
    ingest_module,
    io,
    json,
    patch,
    pytest,
    registry,
    runtime_config,
    safe_error,
    safe_item,
    timedelta,
    utcnow,
    zipfile,
)


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
    assert status.completion == "partial"
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
    assert status.completion is None
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


def test_reconnect_listing_scopes_before_status_deserialization(
    db_session: Session,
) -> None:
    db_session.add(
        BackgroundJob(
            id="other-corrupt",
            owner_user_id=8,
            visible=True,
            state="completed",
            status_json="not-json",
        )
    )
    db_session.add(
        BackgroundJob(
            id="mine-valid",
            owner_user_id=7,
            visible=True,
            state="running",
            status_json=json.dumps({"state": "running"}),
        )
    )
    db_session.commit()

    listed = JobRegistry().list_for_user(7)

    assert [job.job_id for job in listed] == ["mine-valid"]


def test_reconnect_listing_keeps_active_and_bounds_terminal_history(
    db_session: Session,
) -> None:
    now = utcnow()
    db_session.add(
        BackgroundJob(
            id="active",
            owner_user_id=7,
            visible=True,
            state="running",
            status_json=json.dumps({"state": "running"}),
            updated_at=now,
        )
    )
    for index in range(5):
        db_session.add(
            BackgroundJob(
                id=f"done-{index}",
                owner_user_id=7,
                visible=True,
                state="completed",
                status_json=json.dumps({"state": "completed"}),
                updated_at=now - timedelta(seconds=index + 1),
            )
        )
    db_session.commit()

    listed = JobRegistry().list_for_user(7, terminal_limit=2)

    assert {job.job_id for job in listed} == {"active", "done-0", "done-1"}


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
    assert status.headers["cache-control"] == "no-store"


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


def test_makerworld_cookie_is_ignored() -> None:
    _overlay["makerworld_cookie"] = ""
    assert ingest_module._makerworld_cookie("  session=abc  ") is None
    assert ingest_module._makerworld_cookie(None) is None
    assert ingest_module._makerworld_cookie("   ") is None

    _overlay["makerworld_cookie"] = "instance=cookie"
    assert ingest_module._makerworld_cookie(None) is None
    assert ingest_module._makerworld_cookie("override") is None


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
