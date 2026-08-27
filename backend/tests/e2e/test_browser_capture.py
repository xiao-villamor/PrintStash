"""E2E: the Chrome extension's API contract persists a browser capture."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from app.db.models import (
    InboxItem,
    InboxItemState,
    User,
)
from app.services.auth import create_api_key

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_named_api_key_verifies_browser_extension_connection(
    api, superuser_headers, e2e_db
) -> None:
    del superuser_headers
    owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
    _record, raw_key = create_api_key(e2e_db, owner.id, "Browser connection")

    health = await api.get("/api/v1/health")
    assert health.status_code == 200
    health_body = health.json()
    assert health_body["status"] == "ok"
    assert health_body["name"] == "PrintStash"
    assert health_body["storage"]["provider"] == "local"
    assert health_body["storage"]["tier"] in {"verified", "guarded", "unguarded"}

    login = await api.post(
        "/api/v1/auth/login",
        json={"username": owner.username, "api_key": raw_key, "remember_me": False},
    )
    assert login.status_code == 200, login.text
    profile = await api.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["username"] == owner.username
    assert profile.json()["is_superuser"] is True


@pytest.mark.asyncio
async def test_named_api_key_stages_makerworld_package_from_browser(
    api, superuser_headers, e2e_db
) -> None:
    del superuser_headers
    owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
    _record, raw_key = create_api_key(e2e_db, owner.id, "Chrome MakerWorld importer")
    login = await api.post(
        "/api/v1/auth/login",
        json={"username": owner.username, "api_key": raw_key, "remember_me": False},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    page_url = "https://makerworld.com/en/models/1234-widget"

    captured = await api.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={"source_url": page_url, "title": "Widget"},
        files={
            "file": ("widget.3mf", b"browser-owned-package", "application/octet-stream")
        },
    )

    assert captured.status_code == 201, captured.text
    body = captured.json()
    assert body["state"] == "review"
    assert body["source_kind"] == "browser"
    assert body["source_url"] == page_url
    assert body["manifest"] == {
        "kind": "browser_file",
        "title": "widget.3mf",
        "filename": "widget.3mf",
        "size": len(b"browser-owned-package"),
    }
    e2e_db.expire_all()
    row = e2e_db.exec(select(InboxItem)).one()
    assert row.state == InboxItemState.REVIEW
    assert row.staging_key is not None
    staged = Path(row.staging_key)
    assert staged.read_bytes() == b"browser-owned-package"


@pytest.mark.asyncio
async def test_browser_upload_rejects_non_makerworld_source(
    api, superuser_headers, e2e_db
) -> None:
    del superuser_headers
    owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
    _record, raw_key = create_api_key(e2e_db, owner.id, "Chrome source check")
    login = await api.post(
        "/api/v1/auth/login",
        json={"username": owner.username, "api_key": raw_key, "remember_me": False},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    captured = await api.post(
        "/api/v1/inbox/browser-upload",
        headers=headers,
        data={"source_url": "https://www.printables.com/model/3161-benchy"},
        files={"file": ("benchy.3mf", b"not-staged", "application/octet-stream")},
    )

    assert captured.status_code == 400
    assert captured.json()["detail"] == "makerworld_model_page_required"
    assert e2e_db.exec(select(InboxItem)).all() == []
