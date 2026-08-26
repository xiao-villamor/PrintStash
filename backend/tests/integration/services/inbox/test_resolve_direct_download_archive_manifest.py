"""Defends resolve direct download archive manifest at the services inbox integration boundary.

A regression could cross an owner boundary or lose a captured import during a retry.
"""

from __future__ import annotations

from ._inbox_internals_shared import (
    HTTPException,
    InboxItem,
    InboxItemState,
    Path,
    Session,
    _make_item,
    _make_user,
    _overlay,
    get_session_factory,
    import_resolvers,
    importer,
    inbox,
    json,
    pytest,
    registry,
    settings,
)


@pytest.mark.asyncio
async def test_resolve_direct_download_archive_manifest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "resolve-archive")
    row = _make_item(db_session, owner)
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

    async def no_listing(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "list_model_files", no_listing)

    async def no_page_url(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "resolve_page_url", no_page_url)

    staged = tmp_path / "download.bin"
    staged.write_bytes(b"pk-zip-stub")

    async def fake_download(_url: str):
        return staged, "bundle.zip"

    monkeypatch.setattr(importer, "download_to_staging", fake_download)
    monkeypatch.setattr(
        importer,
        "inspect_archive",
        lambda _path: [
            importer.ArchiveEntry(
                entry_id="0:00000000:1",
                name="a.stl",
                size_bytes=1,
                file_type="stl",
                is_image=False,
            ),
            importer.ArchiveEntry(
                entry_id="1:00000000:1",
                name="readme.txt",
                size_bytes=1,
                file_type=None,
                is_image=False,
            ),
        ],
    )

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW
        manifest = json.loads(fresh.manifest_json)
        assert manifest["kind"] == "archive"
        assert [entry["id"] for entry in manifest["entries"]] == ["a.stl"]
        assert fresh.staging_key is not None
        assert Path(fresh.staging_key).exists()


@pytest.mark.asyncio
async def test_resolve_direct_download_non_archive_manifest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "resolve-direct")
    row = _make_item(db_session, owner)
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

    async def no_listing(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "list_model_files", no_listing)

    async def no_page_url(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "resolve_page_url", no_page_url)

    staged = tmp_path / "download.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download(_url: str):
        return staged, "model.stl"

    monkeypatch.setattr(importer, "download_to_staging", fake_download)

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW
        manifest = json.loads(fresh.manifest_json)
        assert manifest["kind"] == "direct"
    assert not staged.exists()


@pytest.mark.asyncio
async def test_resolve_unexpected_exception_marks_item_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "resolve-boom")
    row = _make_item(db_session, owner)

    def boom(_url: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(import_resolvers, "classify_collection", boom)

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_run_import_ignores_item_not_in_review(db_session: Session) -> None:
    owner = _make_user(db_session, "run-import-wrong-state")
    row = _make_item(db_session, owner, state=InboxItemState.CAPTURED)
    await inbox.run_import(row.id, [], get_session_factory())
    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.CAPTURED


@pytest.mark.asyncio
async def test_run_import_direct_completes_and_marks_model(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-direct")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps({"kind": "direct"}),
    )

    staged = tmp_path / "model.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "model.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=42)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 42
        assert fresh.completed_at is not None


@pytest.mark.asyncio
async def test_run_import_archive_selection_and_missing_staging(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "run-import-archive-missing")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {"kind": "archive", "entries": [{"id": "a.stl"}, {"id": "b.stl"}]}
        ),
        staging_key=None,
    )

    await inbox.run_import(row.id, ["a.stl"], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_run_import_archive_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-archive-ok")
    _overlay["staging_dir"] = tmp_path / "staging"
    settings.incoming_dir.mkdir(parents=True)
    staged_archive = settings.incoming_dir / "bundle.zip"
    staged_archive.write_bytes(b"pk-zip-stub")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {"kind": "archive", "entries": [{"id": "a.stl"}, {"id": "b.stl"}]}
        ),
        staging_key=str(staged_archive),
    )

    extracted = tmp_path / "a.stl"
    extracted.write_bytes(b"solid x endsolid")
    monkeypatch.setattr(
        importer,
        "extract_selected",
        lambda _path, names: [(extracted, "a.stl")] if "a.stl" in names else [],
    )

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=7)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, ["a.stl"], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 7
        assert fresh.staging_key is None
    assert not staged_archive.exists()


@pytest.mark.asyncio
async def test_run_import_browser_file_uses_copy_and_releases_staging_on_success(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-browser-file")
    _overlay["staging_dir"] = tmp_path / "staging"
    settings.incoming_dir.mkdir(parents=True)
    staged = settings.incoming_dir / "inbox" / "1" / "source.3mf"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"browser-owned-package")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        source_url="https://makerworld.com/en/models/1234-widget",
        manifest_json=json.dumps({"kind": "browser_file", "filename": "widget.3mf"}),
        staging_key=str(staged),
    )

    def fake_import_assets(*, job_id: str, staged_files, **_kwargs) -> None:
        copied, name = staged_files[0]
        assert copied != staged
        assert copied.read_bytes() == b"browser-owned-package"
        assert name == "widget.3mf"
        copied.unlink()
        registry.update(job_id, state="completed", model_id=8)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 8
        assert fresh.staging_key is None
    assert not staged.exists()


@pytest.mark.asyncio
async def test_run_import_model_files_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-model-files")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "kind": "model_files",
                "files": [
                    {"id": "f1", "name": "bracket.stl", "file_type": "stl", "size": 10}
                ],
            }
        ),
    )

    async def fake_resolve_selected_download(_url, chosen):
        assert chosen[0].file_id == "f1"
        return ["https://example.com/download/f1"]

    monkeypatch.setattr(
        import_resolvers, "resolve_selected_download", fake_resolve_selected_download
    )

    staged = tmp_path / "bracket.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "bracket.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=9)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 9


@pytest.mark.asyncio
async def test_run_import_collection_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-collection")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "kind": "collection",
                "members": [{"id": "m1", "page_url": "https://example.com/model/1"}],
            }
        ),
    )

    staged = tmp_path / "member.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "member.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=11)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 11


@pytest.mark.asyncio
async def test_run_import_job_not_completed_marks_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-job-failed")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps({"kind": "direct"}),
    )

    staged = tmp_path / "model.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "model.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="failed", error="ingest_exploded")

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "ingest_exploded"
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_run_import_exception_marks_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "run-import-boom")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps({"kind": "direct"}),
    )

    async def boom(_url: str):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(inbox, "_download_assets", boom)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_run_import_requires_target_collection_access(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "run-import-no-access", admin=False)
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        target_collection_id=None,
        manifest_json=json.dumps({"kind": "direct"}),
    )
    with pytest.raises(HTTPException) as exc:
        await inbox.run_import(row.id, [], get_session_factory())
    assert exc.value.status_code == 403


def test_retry_requires_failed_and_retryable(db_session: Session) -> None:
    owner = _make_user(db_session, "retry-owner")
    row = _make_item(db_session, owner, state=InboxItemState.REVIEW)
    with pytest.raises(HTTPException) as exc:
        inbox.retry(db_session, row)
    assert exc.value.status_code == 409


def test_retry_returns_to_review_when_manifest_present(db_session: Session) -> None:
    owner = _make_user(db_session, "retry-owner2")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json=json.dumps({"kind": "direct"}),
    )
    updated = inbox.retry(db_session, row)
    assert updated.state == InboxItemState.REVIEW


def test_retry_returns_to_captured_without_manifest(db_session: Session) -> None:
    owner = _make_user(db_session, "retry-owner3")
    row = _make_item(db_session, owner, state=InboxItemState.FAILED, retryable=True)
    updated = inbox.retry(db_session, row)
    assert updated.state == InboxItemState.CAPTURED
