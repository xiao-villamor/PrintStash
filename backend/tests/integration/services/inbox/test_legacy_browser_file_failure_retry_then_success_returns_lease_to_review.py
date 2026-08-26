"""Defends legacy browser file failure retry then success returns lease to review at the services inbox integration boundary.

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
    HTTPException,
    InboxItem,
    InboxItemResult,
    InboxItemState,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    Path,
    Session,
    StagingLease,
    StorageDeleteIntent,
    _make_item,
    _make_user,
    _overlay,
    base64,
    get_session_factory,
    hashlib,
    inbox,
    json,
    pytest,
    registry,
    select,
    settings,
    staging_leases,
    timedelta,
)


@pytest.mark.asyncio
async def test_legacy_browser_file_failure_retry_then_success_returns_lease_to_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "legacy-browser-retry")
    _overlay["staging_dir"] = tmp_path / "staging"
    staged = settings.incoming_dir / "legacy" / "widget.3mf"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"legacy-browser-file")
    row = _make_item(
        db_session,
        owner,
        source_kind=InboxSourceKind.BROWSER,
        state=InboxItemState.REVIEW,
        source_url="https://makerworld.com/en/models/1234-widget",
        manifest_json=json.dumps({"kind": "browser_file", "filename": "widget.3mf"}),
        staging_key=str(staged),
    )
    staging_leases.create_review_lease(
        db_session,
        inbox_item_id=row.id,
        owner_user_id=owner.id,
        path=staged,
        size_bytes=staged.stat().st_size,
        sha256=hashlib.sha256(staged.read_bytes()).hexdigest(),
    )
    db_session.commit()

    monkeypatch.setattr(
        inbox.importer,
        "import_assets",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("first import failed")),
    )
    await inbox.run_import(row.id, [], get_session_factory())

    db_session.expire_all()
    failed = db_session.get(InboxItem, row.id)
    assert failed is not None
    assert failed.state == InboxItemState.FAILED
    assert failed.background_job_id is not None
    job_id = failed.background_job_id
    lease = db_session.exec(
        select(StagingLease).where(StagingLease.background_job_id == job_id)
    ).one()
    assert lease.inbox_item_id is None
    retry_now = lease.expires_at - timedelta(minutes=1)
    monkeypatch.setattr(
        staging_leases,
        "utcnow",
        lambda: retry_now,
    )

    retried = inbox.retry(db_session, failed)
    assert retried.state == InboxItemState.REVIEW
    returned = db_session.exec(
        select(StagingLease).where(StagingLease.id == lease.id)
    ).one()
    assert returned.inbox_item_id == row.id
    assert returned.background_job_id is None
    # Release the read transaction before ``run_import`` opens its own
    # engine-bound session and writes the next job state.
    db_session.commit()

    def complete_import(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=23)

    monkeypatch.setattr(inbox.importer, "import_assets", complete_import)
    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh is not None
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 23


def test_dismiss_rejects_importing_item(db_session: Session) -> None:
    owner = _make_user(db_session, "dismiss-owner")
    row = _make_item(db_session, owner, state=InboxItemState.IMPORTING)
    with pytest.raises(HTTPException) as exc:
        inbox.dismiss(db_session, row)
    assert exc.value.status_code == 409


def test_dismiss_cleans_up_staging_directory(
    db_session: Session, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "dismiss-owner2")
    _overlay["staging_dir"] = tmp_path / "staging"
    staging_dir = settings.incoming_dir / "inbox-item"
    staging_dir.mkdir(parents=True)
    staged_file = staging_dir / "source.stl"
    staged_file.write_bytes(b"solid x endsolid")
    row = _make_item(
        db_session, owner, state=InboxItemState.REVIEW, staging_key=str(staged_file)
    )

    inbox.dismiss(db_session, row)

    assert row.state == InboxItemState.DISMISSED
    assert row.staging_key is None
    assert not staged_file.exists()
    assert not staging_dir.exists()


def test_reconcile_marks_resolving_items_failed(db_session: Session) -> None:
    owner = _make_user(db_session, "reconcile-resolving")
    row = _make_item(db_session, owner, state=InboxItemState.RESOLVING)
    count = inbox.reconcile_interrupted_items()
    assert count >= 1
    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "import_interrupted"


def test_reconcile_completes_importing_item_with_finished_job(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "reconcile-importing-ok")
    job_id = registry.create(owner_user_id=owner.id)
    registry.update(job_id, state="completed", model_id=5)
    row = _make_item(
        db_session, owner, state=InboxItemState.IMPORTING, background_job_id=job_id
    )

    inbox.reconcile_interrupted_items()

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 5


def test_reconcile_finished_capture_runs_normal_terminalization(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restarted job cleans slots after ownership moves to its origin lease.

    Upload publication has already removed each local spool by the time the
    import job is terminalized.  The cleanup seam must therefore use the
    transferred ``capture_upload_slot_origin_id`` lease, not try to look the
    slot up through its pre-import owner column.
    """
    owner = _make_user(db_session, "reconcile-capture-terminalization")
    file_bytes = b"captured-model"
    cover_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
        "AScY42YAAAAASUVORK5CYII="
    )
    payload = CaptureUploadSlotsCreate.model_validate(
        {
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": {
                "provider": "makerworld",
                "canonical_url": "https://makerworld.com/en/models/1234-widget",
                "source_item_id": "1234",
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
            "cover": {
                "id": "cover",
                "filename": "cover.png",
                "media_type": "image/png",
                "size_bytes": len(cover_bytes),
                "sha256": hashlib.sha256(cover_bytes).hexdigest(),
            },
        }
    )
    row, slots = inbox.create_capture_upload_slots(db_session, owner, payload)
    slot_ids = {slot.id for slot in slots}
    file_slot = next(slot for slot in slots if slot.role == "file")
    cover_slot = next(slot for slot in slots if slot.role == "cover")
    inbox.upload_capture_slot(
        db_session,
        file_slot,
        stream=BytesIO(file_bytes),
        media_type=file_slot.media_type,
    )
    inbox.upload_capture_slot(
        db_session,
        cover_slot,
        stream=BytesIO(cover_bytes),
        media_type=cover_slot.media_type,
    )
    inbox.finalize_capture_upload(db_session, owner, row.id)

    model = Model(name="Widget", slug="reconcile-widget", hash="f" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="makerworld",
        source_item_id="1234",
        canonical_url="https://makerworld.com/en/models/1234-widget",
        identity_key="reconcile-widget",
    )
    artifact = File(
        model_id=model.id,
        path="reconcile/widget.3mf",
        original_filename="widget.3mf",
        file_type=FileType.THREE_MF,
        size_bytes=len(file_bytes),
        sha256=hashlib.sha256(file_bytes).hexdigest(),
    )
    db_session.add_all([source, artifact])
    db_session.flush()
    assert source.id is not None
    assert artifact.id is not None
    db_session.add(
        ArtifactProvenanceLink(
            file_id=artifact.id,
            provenance_source_id=source.id,
            source_file_id="widget.3mf",
            source_filename="widget.3mf",
            blob_sha256=artifact.sha256,
            import_key="reconcile-widget-import",
        )
    )
    db_session.commit()

    attached_sources: list[int] = []
    monkeypatch.setattr(
        inbox.source_covers,
        "put",
        lambda _session, _backend, **kwargs: attached_sources.append(
            kwargs["provenance_source_id"]
        ),
    )
    job_id = registry.create(owner_user_id=owner.id)
    registry.update(
        job_id,
        state="completed",
        model_id=model.id,
        result={
            "items": [
                {
                    "source_selection_id": "widget.3mf",
                    "result_key": "self",
                    "name": "widget.3mf",
                    "model_id": model.id,
                    "file_id": artifact.id,
                }
            ]
        },
    )
    row = db_session.get(InboxItem, row.id)
    assert row is not None
    row.state = InboxItemState.IMPORTING
    row.background_job_id = job_id
    staging_leases.transfer_capture_slots_to_job(
        db_session, inbox_item_id=row.id, job_id=job_id
    )
    db_session.add(row)
    db_session.commit()

    for slot in slots:
        assert not staging_leases.capture_slot_staging_path(slot.id).exists()
    with get_session_factory().scoped_session() as session:
        transferred = session.exec(
            select(StagingLease).where(
                StagingLease.capture_upload_slot_origin_id.in_(slot_ids),
                StagingLease.background_job_id == job_id,
                StagingLease.capture_upload_slot_id.is_(None),
            )
        ).all()
        assert {
            lease.capture_upload_slot_origin_id for lease in transferred
        } == slot_ids

    inbox.reconcile_interrupted_items()

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh is not None
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == model.id
        assert fresh.retryable is False
        assert fresh.error_code is None
        result = session.exec(
            select(InboxItemResult).where(InboxItemResult.inbox_item_id == row.id)
        ).one()
        assert (
            result.source_selection_id,
            result.result_key,
            result.model_id,
            result.file_id,
            result.provenance_source_id,
            result.retryable,
        ) == ("widget.3mf", "self", model.id, artifact.id, source.id, False)
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
        intents = session.exec(select(StorageDeleteIntent)).all()
        assert {intent.resource_id for intent in intents} == slot_ids
    assert attached_sources == [source.id]
