from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import Collection, InboxItem, InboxItemState, User
from app.services import inbox
from app.services.auth import create_access_token, hash_password


def _headers(session: Session, username: str, *, admin: bool = False) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=admin,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope='admin' if admin else 'write')}"
    }


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
    assert client.get(f"/api/v1/inbox/{body['id']}", headers=ordinary).status_code == 404
    assert client.get(f"/api/v1/inbox/{body['id']}", headers=other).status_code == 200


def test_capture_rejects_url_credentials(client: TestClient, db_session: Session) -> None:
    headers = _headers(db_session, "capture-credentials", admin=True)
    response = client.post(
        "/api/v1/inbox",
        headers=headers,
        json={"url": "https://user:password@example.com/model"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "url_credentials_not_allowed"


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


def _make_item(db_session: Session, owner: User, **overrides) -> InboxItem:
    defaults = dict(
        owner_user_id=owner.id,
        source_url="https://example.com/model",
        source_hostname="example.com",
        state=InboxItemState.CAPTURED,
    )
    defaults.update(overrides)
    row = InboxItem(**defaults)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _user(db_session: Session, username: str, *, admin: bool = True) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=admin,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


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


def test_resolve_item_success_schedules_background_resolve(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "resolve-success", admin=True)
    owner = _user(db_session, "resolve-success-owner")
    row = _make_item(db_session, owner, state=InboxItemState.FAILED)
    calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)

    response = client.post(f"/api/v1/inbox/{row.id}/resolve", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert calls == [row.id]


def test_import_item_requires_review_state(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "import-not-ready", admin=True)
    owner = _user(db_session, "import-not-ready-owner")
    row = _make_item(db_session, owner, state=InboxItemState.CAPTURED)

    response = client.post(
        f"/api/v1/inbox/{row.id}/import", headers=headers, json={"selected_ids": []}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "pending_import_not_ready"


def test_import_item_success_schedules_run_import(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "import-success", admin=True)
    owner = _user(db_session, "import-success-owner")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json='{"kind": "direct", "title": "x"}',
    )
    calls: list[tuple[int, list[str]]] = []

    async def fake_run_import(item_id: int, selected_ids: list[str], _session_factory) -> None:
        calls.append((item_id, selected_ids))

    monkeypatch.setattr(inbox, "run_import", fake_run_import)

    response = client.post(
        f"/api/v1/inbox/{row.id}/import",
        headers=headers,
        json={"selected_ids": ["a"]},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "review"
    assert calls == [(row.id, ["a"])]


def test_retry_item_schedules_resolve_when_returned_to_captured(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "retry-success", admin=True)
    owner = _user(db_session, "retry-success-owner")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json="",
    )
    calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)

    response = client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "captured"
    assert calls == [row.id]


def test_dismiss_item_returns_204(client: TestClient, db_session: Session) -> None:
    headers = _headers(db_session, "dismiss-owner", admin=True)
    owner = _user(db_session, "dismiss-owner-user")
    row = _make_item(db_session, owner)

    response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers)

    assert response.status_code == 204
    db_session.expire_all()
    refreshed = db_session.get(InboxItem, row.id)
    assert refreshed.state == InboxItemState.DISMISSED


def test_batch_actions_cover_every_branch(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "batch-owner", admin=True)
    owner = _user(db_session, "batch-owner-user")

    resolve_calls: list[int] = []
    import_calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        resolve_calls.append(item_id)

    async def fake_run_import(item_id: int, _selected_ids, _session_factory) -> None:
        import_calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)
    monkeypatch.setattr(inbox, "run_import", fake_run_import)

    collection = Collection(name="Batch target", slug="batch-target", path="batch-target")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    set_collection_item = _make_item(db_session, owner)
    tag_item = _make_item(db_session, owner, requested_tags_json='["existing"]')
    retry_item = _make_item(
        db_session, owner, state=InboxItemState.FAILED, retryable=True, manifest_json=""
    )
    review_item = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json='{"kind": "direct", "title": "x"}',
    )
    not_ready_item = _make_item(db_session, owner, state=InboxItemState.CAPTURED)
    dismiss_item = _make_item(db_session, owner)

    set_collection = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={
            "item_ids": [set_collection_item.id],
            "action": "set_collection",
            "collection_id": collection.id,
        },
    )
    assert set_collection.status_code == 200
    assert set_collection.json()[0]["target_collection_id"] == collection.id

    response = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={
            "item_ids": [tag_item.id, tag_item.id],  # dedup via dict.fromkeys
            "action": "add_tags",
            "tags": ["new"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0]["requested_tags"]) == {"existing", "new"}

    retried = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [retry_item.id], "action": "retry"},
    )
    assert retried.status_code == 200
    assert retried.json()[0]["state"] == "captured"
    assert resolve_calls == [retry_item.id]

    imported = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [review_item.id, not_ready_item.id], "action": "import"},
    )
    assert imported.status_code == 200
    # not_ready_item hits `continue` for action="import" (state != REVIEW) and is
    # dropped from the output entirely; only review_item is returned.
    assert {row["id"] for row in imported.json()} == {review_item.id}
    assert import_calls == [review_item.id]

    dismissed = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [dismiss_item.id], "action": "dismiss"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()[0]["state"] == "dismissed"
