from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import User
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
