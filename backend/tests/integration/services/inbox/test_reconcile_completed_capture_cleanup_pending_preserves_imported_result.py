"""Defends reconcile completed capture cleanup pending preserves imported result at the services inbox integration boundary.

A regression could cross an owner boundary or lose a captured import during a retry.
"""

from __future__ import annotations

from ._inbox_internals_shared import (
    ArtifactProvenanceLink,
    BytesIO,
    CaptureUploadSlot,
    CaptureUploadSlotsCreate,
    File,
    FileType,
    InboxItem,
    InboxItemCompletion,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    MagicMock,
    Model,
    ModelProvenanceSource,
    Path,
    ResolvedAsset,
    Session,
    StagingLease,
    StorageBackend,
    StorageDeleteIntent,
    _capture_manifest,
    _make_item,
    _make_user,
    _overlay,
    get_session_factory,
    hashlib,
    import_resolvers,
    importer,
    inbox,
    json,
    nullcontext,
    pytest,
    registry,
    select,
    settings,
    staging_leases,
    utcnow,
)


def test_reconcile_completed_capture_cleanup_pending_preserves_imported_result(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup cleanup repairs a completed item without re-running ingestion."""
    owner = _make_user(db_session, "reconcile-completed-cleanup")
    file_bytes = b"already-imported-model"
    source_url = "https://makerworld.com/en/models/9876-widget"
    payload = CaptureUploadSlotsCreate.model_validate(
        {
            "source_url": source_url,
            "capture_source": {
                "provider": "makerworld",
                "canonical_url": source_url,
                "source_item_id": "9876",
                "adapter_version": "extension-v1",
                "fields": {},
                "tags": [],
            },
            "files": [
                {
                    "id": "widget.3mf",
                    "filename": "widget.3mf",
                    "media_type": "application/octet-stream",
                    "size_bytes": len(file_bytes),
                    "sha256": hashlib.sha256(file_bytes).hexdigest(),
                }
            ],
        }
    )
    row, slots = inbox.create_capture_upload_slots(db_session, owner, payload)
    slot = slots[0]
    inbox.upload_capture_slot(
        db_session, slot, stream=BytesIO(file_bytes), media_type=slot.media_type
    )
    assert slot.storage_key is not None
    slot_id = slot.id
    slot_key = slot.storage_key

    model = Model(
        name="Completed widget",
        slug="completed-widget",
        hash="c" * 64,
        source_url=source_url,
    )
    db_session.add(model)
    db_session.flush()
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="makerworld",
        source_item_id="9876",
        canonical_url=source_url,
        identity_key="completed-widget-source",
    )
    artifact = File(
        model_id=model.id,
        path="completed/widget.3mf",
        original_filename="widget.3mf",
        file_type=FileType.THREE_MF,
        size_bytes=len(file_bytes),
        sha256=hashlib.sha256(file_bytes).hexdigest(),
    )
    db_session.add_all([source, artifact])
    db_session.flush()
    link = ArtifactProvenanceLink(
        file_id=artifact.id,
        provenance_source_id=source.id,
        source_file_id="widget.3mf",
        source_filename="widget.3mf",
        blob_sha256=artifact.sha256,
        import_key="completed-widget-import",
    )
    result = InboxItemResult(
        inbox_item_id=row.id,
        source_selection_id="widget.3mf",
        result_key="self",
        original_filename="widget.3mf",
        state=InboxItemResultState.IMPORTED,
        model_id=model.id,
        file_id=artifact.id,
        provenance_source_id=source.id,
        retryable=False,
    )
    db_session.add_all([link, result])
    job_id = registry.create(owner_user_id=owner.id)
    row.state = InboxItemState.COMPLETED
    row.background_job_id = job_id
    row.resulting_model_id = model.id
    row.completion = InboxItemCompletion.COMPLETE
    row.retryable = True
    row.error_code = "capture_upload_cleanup_pending"
    row.completed_at = utcnow()
    staging_leases.transfer_capture_slots_to_job(
        db_session, inbox_item_id=row.id, job_id=job_id
    )
    db_session.add(row)
    db_session.commit()

    model_snapshot = (model.name, model.source_url, model.hash)
    artifact_snapshot = (
        artifact.path,
        artifact.original_filename,
        artifact.file_type,
        artifact.size_bytes,
        artifact.sha256,
    )
    result_snapshot = (
        result.source_selection_id,
        result.result_key,
        result.model_id,
        result.file_id,
        result.provenance_source_id,
        result.state,
    )
    monkeypatch.setattr(
        inbox,
        "_finish_import",
        lambda *_args: pytest.fail("completed cleanup must not re-run ingestion"),
    )
    monkeypatch.setattr(
        inbox,
        "_attach_capture_cover",
        lambda *_args: pytest.fail("completed cleanup must not re-attach cover"),
    )

    inbox.reconcile_interrupted_items()

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh is not None
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == model.id
        assert fresh.retryable is False
        assert fresh.error_code is None
        assert (
            session.exec(
                select(CaptureUploadSlot).where(
                    CaptureUploadSlot.inbox_item_id == row.id
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(StagingLease).where(StagingLease.background_job_id == job_id)
            ).all()
            == []
        )
        intent = session.exec(select(StorageDeleteIntent)).one()
        assert intent.key == slot_key
        assert intent.resource_id == slot_id
        preserved_model = session.get(Model, model.id)
        preserved_artifact = session.get(File, artifact.id)
        preserved_result = session.exec(
            select(InboxItemResult).where(InboxItemResult.inbox_item_id == row.id)
        ).one()
        assert preserved_model is not None
        assert preserved_artifact is not None
        assert (
            preserved_model.name,
            preserved_model.source_url,
            preserved_model.hash,
        ) == model_snapshot
        assert (
            preserved_artifact.path,
            preserved_artifact.original_filename,
            preserved_artifact.file_type,
            preserved_artifact.size_bytes,
            preserved_artifact.sha256,
        ) == artifact_snapshot
        assert (
            preserved_result.source_selection_id,
            preserved_result.result_key,
            preserved_result.model_id,
            preserved_result.file_id,
            preserved_result.provenance_source_id,
            preserved_result.state,
        ) == result_snapshot
    assert inbox.get_backend().exists(slot_key)


def test_reconcile_completed_v2_job_without_results_stays_retryable(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "reconcile-v2-no-results")
    job_id = registry.create(owner_user_id=owner.id)
    registry.update(job_id, state="completed", model_id=55)
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.IMPORTING,
        background_job_id=job_id,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": {},
                "files": [],
                "selected_ids": [],
            }
        ),
    )

    inbox.reconcile_interrupted_items()

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh is not None
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True
        assert fresh.resulting_model_id is None


def test_reconcile_fails_importing_item_without_finished_job(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "reconcile-importing-fail")
    row = _make_item(
        db_session, owner, state=InboxItemState.IMPORTING, background_job_id=None
    )

    inbox.reconcile_interrupted_items()

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "import_interrupted"


@pytest.mark.asyncio
async def test_download_assets_expands_importable_archive_and_removes_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"archive")
    extracted = tmp_path / "part.stl"

    async def download(_url: str) -> tuple[Path, str]:
        return archive, "bundle.zip"

    async def no_page_url(_url: str) -> None:
        return None

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(import_resolvers, "resolve_page_url", no_page_url)
    monkeypatch.setattr(inbox.asyncio, "to_thread", immediate)
    monkeypatch.setattr(importer, "download_to_staging", download)
    monkeypatch.setattr(
        importer,
        "inspect_archive",
        lambda _path: [
            importer.ArchiveEntry("mesh", "part.stl", 4, "stl", False),
            importer.ArchiveEntry("note", "readme.txt", 4, None, False),
        ],
    )

    def extract(_path: Path, selected: list[str]) -> list[tuple[Path, str]]:
        assert selected == ["part.stl"]
        extracted.write_bytes(b"mesh")
        return [(extracted, "part.stl")]

    monkeypatch.setattr(importer, "extract_selected", extract)

    assert await inbox._download_assets("https://example.test/page") == [
        (extracted, "part.stl")
    ]
    assert not archive.exists()


@pytest.mark.asyncio
async def test_download_resolved_zip_retains_selection_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _capture_manifest("bundle.zip")
    resolved = ResolvedAsset(
        manifest=manifest,
        source_selection_id="bundle.zip",
        source_file_id="bundle.zip",
        source_filename="bundle.zip",
        download_url="https://download.test/bundle.zip",
        source_item_id="42",
    )
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"archive")
    first = tmp_path / "one.stl"
    second = tmp_path / "two.3mf"

    async def download(_url: str) -> tuple[Path, str]:
        return archive, "bundle.zip"

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(inbox.asyncio, "to_thread", immediate)
    monkeypatch.setattr(importer, "download_to_staging", download)
    monkeypatch.setattr(
        importer,
        "inspect_archive",
        lambda _path: [
            importer.ArchiveEntry("one", "one.stl", 3, "stl", False),
            importer.ArchiveEntry("two", "two.3mf", 3, "3mf", False),
        ],
    )

    def extract(_path: Path, selected: list[str]) -> list[tuple[Path, str]]:
        assert selected == ["one.stl", "two.3mf"]
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        return [(first, "one.stl"), (second, "two.3mf")]

    monkeypatch.setattr(importer, "extract_selected", extract)

    assets = await inbox._download_resolved_asset(resolved)

    assert [asset.resolved for asset in assets] == [resolved, resolved]
    assert [asset.container_entry_path for asset in assets] == ["one.stl", "two.3mf"]
    assert len({asset.result_key for asset in assets}) == 2
    assert [asset.blob_sha256 for asset in assets] == [
        hashlib.sha256(b"one").hexdigest(),
        hashlib.sha256(b"two").hexdigest(),
    ]
    assert not archive.exists()


@pytest.mark.asyncio
async def test_browser_zip_staging_uses_manifest_identity_and_removes_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _capture_manifest("part.stl")
    archive = tmp_path / "capture.zip"
    archive.write_bytes(b"browser-owned")
    selected = tmp_path / "part.stl"
    unknown = tmp_path / "unknown.stl"

    def extract(_path: Path, wanted: list[str]) -> list[tuple[Path, str]]:
        assert wanted == ["part.stl"]
        selected.write_bytes(b"part")
        unknown.write_bytes(b"unknown")
        return [(selected, "part.stl"), (unknown, "unknown.stl")]

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(inbox.asyncio, "to_thread", immediate)
    monkeypatch.setattr(importer, "extract_selected", extract)
    assets = await inbox._stage_local_capture_assets(archive, manifest, ["part.stl"])

    assert len(assets) == 1
    assert assets[0].source_selection_id == "part.stl"
    assert assets[0].container_entry_path == "part.stl"
    assert assets[0].staged_path == selected
    assert archive.exists()
    assert not unknown.exists()


@pytest.mark.asyncio
async def test_browser_single_asset_is_copied_and_review_source_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(inbox.asyncio, "to_thread", immediate)
    _overlay["staging_dir"] = tmp_path / "staging"
    settings.incoming_dir.mkdir(parents=True)
    source = tmp_path / "part.stl"
    source.write_bytes(b"solid part")
    manifest = _capture_manifest("part.stl")

    assets = await inbox._stage_local_capture_assets(source, manifest, ["part.stl"])

    assert len(assets) == 1
    assert source.read_bytes() == b"solid part"
    assert assets[0].staged_path != source
    assert assets[0].staged_path.read_bytes() == b"solid part"
    assert assets[0].source_selection_id == "part.stl"
    assert assets[0].blob_sha256 == hashlib.sha256(b"solid part").hexdigest()


def test_capture_slot_staging_rejects_incomplete_selection() -> None:
    manifest = _capture_manifest("one.stl", "two.stl")

    with pytest.raises(importer.ImportError_, match="capture_upload_slots_incomplete"):
        inbox._stage_capture_upload_slot_assets(
            manifest, ["two.stl"], {"one.stl": "slot/one"}
        )


def test_capture_slot_staging_copies_exact_bytes_through_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    settings.incoming_dir.mkdir(parents=True)
    source = tmp_path / "durable.stl"
    source.write_bytes(b"durable bytes")
    backend = MagicMock(spec=StorageBackend)
    backend.local_path.return_value = nullcontext(source)
    monkeypatch.setattr(inbox, "get_backend", lambda: backend)
    manifest = _capture_manifest("part.stl")

    assets = inbox._stage_capture_upload_slot_assets(
        manifest, ["part.stl"], {"part.stl": "capture-slots/part"}
    )

    backend.local_path.assert_called_once_with("capture-slots/part")
    assert len(assets) == 1
    assert assets[0].staged_path.read_bytes() == b"durable bytes"
    assert assets[0].blob_sha256 == hashlib.sha256(b"durable bytes").hexdigest()
    assert assets[0].resolved.source_file_id == "part.stl"
    assert assets[0].resolved.source_item_id == "42"
