"""Defends retry returns transferred capture slot leases to review at the services inbox integration boundary.

A regression could cross an owner boundary or lose a captured import during a retry.
"""

from __future__ import annotations

from ._inbox_api_shared import (
    BackgroundJob,
    BrowserDevice,
    BytesIO,
    CaptureUploadSlot,
    CaptureUploadSlotsCreate,
    CreationReceipt,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    Session,
    SourceCoverWrite,
    StagingLease,
    StorageDeleteIntent,
    TestClient,
    User,
    _capture_source,
    _headers,
    _make_item,
    _overlay,
    _slot_payload,
    _user,
    create_access_token,
    hash_password,
    hashlib,
    inbox,
    io,
    json,
    pytest,
    select,
    uuid,
)


def test_retry_returns_transferred_capture_slot_leases_to_review(
    db_session: Session,
) -> None:
    """A failed durable capture can be retried after its import job owned slots."""
    owner = _user(db_session, "slot-retry-after-transfer")
    row, slots = inbox.create_capture_upload_slots(db_session, owner, _slot_payload())
    inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    row.state = InboxItemState.FAILED
    row.retryable = True
    job = BackgroundJob(id="slot-retry-after-transfer-job", owner_user_id=owner.id)
    db_session.add(job)
    db_session.flush()
    row.background_job_id = job.id
    inbox.staging_leases.transfer_capture_slots_to_job(
        db_session, inbox_item_id=row.id, job_id=job.id
    )
    db_session.commit()

    retried = inbox.retry(db_session, row)

    assert retried.state == InboxItemState.REVIEW
    lease = db_session.exec(
        select(StagingLease).where(StagingLease.capture_upload_slot_id == slots[0].id)
    ).one()
    assert lease.background_job_id is None
    assert lease.capture_upload_slot_origin_id is None


def test_capture_slot_cleanup_later_failure_preserves_all_slot_ownership(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup is all-or-nothing when a later durable intent cannot be made."""
    raw = _slot_payload().model_dump(mode="json")
    raw["files"].append(
        {
            "id": "second.stl",
            "filename": "second.stl",
            "media_type": "application/octet-stream",
            "size_bytes": 3,
            "sha256": hashlib.sha256(b"two").hexdigest(),
        }
    )
    owner = _user(db_session, "slot-cleanup-atomic")
    row, slots = inbox.create_capture_upload_slots(
        db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
    )
    inbox.upload_capture_slot(
        db_session,
        slots[0],
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    inbox.upload_capture_slot(
        db_session,
        slots[1],
        stream=BytesIO(b"two"),
        media_type="application/octet-stream",
    )
    original_enqueue = inbox.enqueue_creation_receipt
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("intent store unavailable")
        return original_enqueue(*args, **kwargs)

    monkeypatch.setattr(inbox, "enqueue_creation_receipt", fail_second)

    assert not inbox._cleanup_capture_slots(db_session, row)
    db_session.commit()
    db_session.expire_all()

    assert {slot.id for slot in db_session.exec(select(CaptureUploadSlot)).all()} >= {
        slot.id for slot in slots
    }
    assert len(db_session.exec(select(StagingLease)).all()) == 2
    assert db_session.exec(select(StorageDeleteIntent)).all() == []


def test_capture_cover_attaches_before_raw_slot_receipt_is_released(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    owner = _user(db_session, "slot-cover")
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(image, format="PNG")
    cover_bytes = image.getvalue()
    raw = _slot_payload().model_dump(mode="json")
    raw["cover"] = {
        "id": "cover",
        "filename": "cover.png",
        "media_type": "image/png",
        "size_bytes": len(cover_bytes),
        "sha256": hashlib.sha256(cover_bytes).hexdigest(),
    }
    row, slots = inbox.create_capture_upload_slots(
        db_session, owner, CaptureUploadSlotsCreate.model_validate(raw)
    )
    file_slot, cover_slot = slots
    inbox.upload_capture_slot(
        db_session,
        file_slot,
        stream=BytesIO(b"slot-owned"),
        media_type="application/octet-stream",
    )
    inbox.upload_capture_slot(
        db_session, cover_slot, stream=BytesIO(cover_bytes), media_type="image/png"
    )
    model = Model(
        name="Cover import",
        slug=f"cover-import-{uuid.uuid4().hex}",
        hash=uuid.uuid4().hex * 2,
    )
    db_session.add(model)
    db_session.flush()
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="makerworld",
        canonical_url="https://makerworld.com/en/models/1234-widget",
        source_item_id="1234",
        identity_key=uuid.uuid4().hex * 2,
    )
    db_session.add(source)
    row.resulting_model_id = model.id
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(
        inbox.source_covers,
        "put",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("cover publish failed")
        ),
    )
    with pytest.raises(RuntimeError, match="cover publish failed"):
        inbox._attach_capture_cover(db_session, row)
    assert db_session.get(type(cover_slot), cover_slot.id) is not None
    attached: list[int] = []
    monkeypatch.setattr(
        inbox.source_covers,
        "put",
        lambda _s, _b, **kwargs: attached.append(kwargs["provenance_source_id"]),
    )
    assert inbox._attach_capture_cover(db_session, row)
    assert attached == [source.id]
    assert inbox._cleanup_capture_slots(db_session, row)
    db_session.commit()
    assert db_session.get(type(cover_slot), cover_slot.id) is None


@pytest.mark.parametrize("created", [True, False])
def test_finished_capture_rolls_back_cover_write_when_commit_fails(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, created: bool
) -> None:
    owner = _user(db_session, f"cover-commit-failure-{created}")
    row = InboxItem(
        owner_user_id=owner.id,
        source_kind="browser",
        source_url="https://makerworld.com/en/models/1234-widget",
        source_hostname="makerworld.com",
        state=InboxItemState.IMPORTING,
    )
    db_session.add(row)
    db_session.commit()
    receipt = CreationReceipt(
        key=f"covers/{created}.webp",
        size=1,
        token="receipt",
        backend="fake",
        namespace="test",
    )
    write = SourceCoverWrite(
        cover=ModelSourceCover(provenance_source_id=1, storage_key=receipt.key),
        created=created,
        creation_receipt=receipt if created else None,
        replacement_receipt=None if created else receipt,
        replaced_bytes=None if created else b"old",
    )

    class _Factory:
        def scoped_session(self) -> object:
            class _Scope:
                def __enter__(self) -> Session:
                    return db_session

                def __exit__(self, *args: object) -> None:
                    return None

            return _Scope()

    job = type("Job", (), {"state": "completed", "model_id": 1, "result": None})()
    monkeypatch.setattr(inbox.registry, "get", lambda _job_id: job)
    monkeypatch.setattr(inbox, "_record_v2_results", lambda *_args: (True, 1, 0))
    monkeypatch.setattr(inbox, "_attach_capture_cover", lambda *_args: write)
    monkeypatch.setattr(inbox, "_cleanup_capture_slots", lambda *_args: True)
    rollback = pytest.MonkeyPatch()
    rollback.setattr(
        db_session,
        "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )
    seam_calls: list[SourceCoverWrite] = []
    monkeypatch.setattr(
        inbox.source_covers,
        "rollback_after_commit_failure",
        lambda _session, _backend, result: seam_calls.append(result),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        inbox._finish_import(row.id, "cover-commit-failure-job", _Factory())

    assert seam_calls == [write]
    rollback.undo()


def test_capture_is_durable_and_owner_scoped(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    owner = _headers(db_session, "capture-owner", admin=True)
    other = _headers(db_session, "capture-other", admin=True)
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def no_resolve(_item_id: int) -> None:
        return None

    monkeypatch.setattr(inbox, "resolve", no_resolve)
    created = client.post(
        "/api/v1/inbox",
        headers=owner,
        json={
            "url": "https://example.com/model?token=secret&view=files#fragment",
            "title": "Bracket",
        },
    )
    assert created.status_code == 202
    body = created.json()
    assert body["state"] == "captured"
    assert body["source_url"] == "https://example.com/model?view=files"
    assert client.get("/api/v1/inbox", headers=owner).json()[0]["id"] == body["id"]
    # Superusers may inspect all queues; ordinary users remain owner-scoped.
    ordinary = _headers(db_session, "capture-ordinary")
    assert client.get("/api/v1/inbox", headers=ordinary).json() == []
    assert (
        client.get(f"/api/v1/inbox/{body['id']}", headers=ordinary).status_code == 404
    )
    assert client.get(f"/api/v1/inbox/{body['id']}", headers=other).status_code == 200


@pytest.mark.parametrize(
    "source_kind",
    [pytest.param("browser", id="explicit-browser"), pytest.param(None, id="default")],
)
def test_rich_metadata_capture_requires_user_file_before_persistence(
    client: TestClient, db_session: Session, source_kind: str | None
) -> None:
    headers = _headers(db_session, "rich-browser-metadata", admin=True)
    payload = {
        "url": "https://makerworld.com/en/models/1234-widget",
        "capture_source": _capture_source(),
    }
    if source_kind is not None:
        payload["source_kind"] = source_kind

    response = client.post(
        "/api/v1/inbox",
        headers=headers,
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "user_file_required"
    assert db_session.exec(select(InboxItem)).all() == []
    assert db_session.exec(select(CaptureUploadSlot)).all() == []


def test_capture_rejects_url_credentials_at_boundary(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "capture-credentials", admin=True)
    response = client.post(
        "/api/v1/inbox",
        headers=headers,
        json={"url": "https://user:password@example.com/model"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "url_invalid"


def test_browser_upload_rich_source_is_staged_as_v2_manifest(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    headers = _headers(db_session, "browser-rich", admin=True)

    response = client.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": json.dumps(_capture_source()),
        },
        files={"file": ("widget.3mf", b"browser-owned", "application/octet-stream")},
    )

    assert response.status_code == 201, response.text
    manifest = response.json()["manifest"]
    assert manifest["schema_version"] == 2
    assert manifest["source"] == _capture_source()
    assert manifest["files"] == [
        {"id": "widget.3mf", "name": "widget.3mf", "file_type": "3mf", "size": 13}
    ]


@pytest.mark.parametrize(
    "source",
    [
        _capture_source(provider="MakerWorld"),
        _capture_source(
            canonical_url="https://makerworld.com/en/models/1234-widget?token=signed"
        ),
        {**_capture_source(), "signed_url": "https://cdn.example/file?sig=secret"},
    ],
)
def test_browser_upload_rejects_untrusted_source_before_staging(
    client: TestClient, db_session: Session, tmp_path, monkeypatch, source: dict
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    headers = _headers(db_session, f"browser-reject-{len(source)}", admin=True)

    response = client.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": json.dumps(source),
        },
        files={"file": ("widget.3mf", b"must-not-stage", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert db_session.exec(select(InboxItem)).all() == []
    assert not (tmp_path / "_incoming").exists()


def test_capture_routes_accept_only_active_paired_browser_credentials(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "staging_dir", tmp_path)
    owner = User(
        username="paired-browser-owner",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)
    credential = "opaque-browser-import-credential"
    device = BrowserDevice(
        user_id=owner.id,
        name="Firefox",
        credential_hash=hashlib.sha256(credential.encode()).hexdigest(),
    )
    db_session.add(device)
    db_session.commit()
    headers = {"Authorization": f"Bearer {credential}"}

    accepted = client.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={"source_url": "https://makerworld.com/en/models/1234-widget"},
        files={"file": ("widget.3mf", b"browser-owned", "application/octet-stream")},
    )
    assert accepted.status_code == 201, accepted.text

    device.revoked_at = inbox.utcnow()
    db_session.add(device)
    db_session.commit()
    rejected = client.post(
        "/api/v1/inbox",
        headers=headers,
        json={"url": "https://example.com/model"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "invalid_browser_credential"


def test_review_item_cannot_be_resolved_again(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "capture-review", admin=True)
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def no_resolve(_item_id: int) -> None:
        return None

    monkeypatch.setattr(inbox, "resolve", no_resolve)
    created = client.post(
        "/api/v1/inbox", headers=headers, json={"url": "https://example.com/model"}
    )
    item_id = created.json()["id"]
    row = db_session.get(inbox.InboxItem, item_id)
    assert row is not None
    row.state = inbox.InboxItemState.REVIEW
    db_session.add(row)
    db_session.commit()

    response = client.post(f"/api/v1/inbox/{item_id}/resolve", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "pending_import_not_resolvable"


def test_capture_maps_import_error_to_400(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "capture-import-error", admin=True)

    def _raise(_url: str) -> None:
        raise inbox.importer.ImportError_("private_address_blocked")

    monkeypatch.setattr(inbox.importer, "validate_public_url", _raise)

    response = client.post(
        "/api/v1/inbox", headers=headers, json={"url": "https://example.com/model"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "private_address_blocked"


def test_get_and_update_item(client: TestClient, db_session: Session) -> None:
    headers = _headers(db_session, "update-owner")
    owner = _user(db_session, "update-owner-user", admin=False)
    row = _make_item(db_session, owner)

    fetched = client.get(f"/api/v1/inbox/{row.id}", headers=headers)
    assert fetched.status_code == 404  # owner mismatch: caller is a different user

    own_headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }
    fetched_own = client.get(f"/api/v1/inbox/{row.id}", headers=own_headers)
    assert fetched_own.status_code == 200

    updated = client.patch(
        f"/api/v1/inbox/{row.id}",
        headers=own_headers,
        json={"title": "Renamed bracket"},
    )
    assert updated.status_code == 200
    assert updated.json()["display_title"] == "Renamed bracket"


def test_get_item_includes_durable_per_file_results(
    client: TestClient, db_session: Session
) -> None:
    owner = _user(db_session, "result-owner", admin=False)
    row = _make_item(db_session, owner)
    result = InboxItemResult(
        inbox_item_id=row.id,
        source_selection_id="remote-stl",
        result_key="self",
        original_filename="bracket.stl",
        state=InboxItemResultState.IMPORTED,
        model_id=42,
        file_id=99,
        retryable=False,
    )
    db_session.add(result)
    db_session.commit()
    headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }

    response = client.get(f"/api/v1/inbox/{row.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["results"][0]["state"] == "imported"
    assert response.json()["results"][0]["result_key"] == "self"
