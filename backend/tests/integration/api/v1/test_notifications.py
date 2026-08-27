"""API coverage for notification channel management (superuser-only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import (
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEventType,
    NotificationTarget,
    User,
)
from app.services.auth import create_access_token, hash_password


def _create(client: TestClient, headers, **over):
    body = {
        "name": "My webhook",
        "target": "webhook",
        "config": {"url": "https://example.com/hook"},
        "events": ["print_completed", "print_failed"],
    }
    body.update(over)
    return client.post("/api/v1/notifications/channels", json=body, headers=headers)


def _regular_headers(db_session: Session, username: str) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def test_requires_superuser(client: TestClient):
    assert client.get("/api/v1/notifications").status_code == 401


def test_master_switch_roundtrip(client: TestClient, auth_headers):
    assert (
        client.get("/api/v1/notifications", headers=auth_headers).json()["enabled"]
        is False
    )
    client.put("/api/v1/notifications", json={"enabled": True}, headers=auth_headers)
    assert (
        client.get("/api/v1/notifications", headers=auth_headers).json()["enabled"]
        is True
    )


def test_create_masks_secret_url(client: TestClient, auth_headers):
    resp = _create(client, auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["config"]["url"] == "********"  # secret masked on read
    assert body["config_flags"]["has_url"] is True
    assert set(body["events"]) == {"print_completed", "print_failed"}


def test_update_preserves_secret_when_blank(client: TestClient, auth_headers):
    cid = _create(client, auth_headers).json()["id"]
    # Patch only the name; the secret URL must survive untouched.
    client.patch(
        f"/api/v1/notifications/channels/{cid}",
        json={"name": "Renamed"},
        headers=auth_headers,
    )
    # And re-sending the masked placeholder also preserves it.
    client.patch(
        f"/api/v1/notifications/channels/{cid}",
        json={"config": {"url": "********"}},
        headers=auth_headers,
    )
    # Verify by sending a test: a preserved URL renders/sends (mock the network).
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.core.url_safety import PinnedTarget

    resp = MagicMock(status_code=204, text="")
    fake = MagicMock(request=AsyncMock(return_value=resp))
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    target = PinnedTarget(
        url="https://hooks.example/x",
        host="hooks.example",
        port=443,
        ip="93.184.216.34",
    )
    with (
        patch("app.services.notifications._client_for", return_value=fake),
        patch("app.services.notifications.resolve_public_target", return_value=target),
    ):
        out = client.post(
            f"/api/v1/notifications/channels/{cid}/test", headers=auth_headers
        ).json()
    assert out["ok"] is True
    sent_url = fake.request.call_args.args[1]
    assert sent_url == "https://example.com/hook"  # original secret, not the mask


def test_update_events_and_printer_scope(client: TestClient, auth_headers):
    cid = _create(client, auth_headers).json()["id"]
    body = client.patch(
        f"/api/v1/notifications/channels/{cid}",
        json={"events": ["printer_offline"], "printer_ids": [3, 4]},
        headers=auth_headers,
    ).json()
    assert body["events"] == ["printer_offline"]
    assert body["printer_ids"] == [3, 4]


def test_invalid_events_are_dropped(client: TestClient, auth_headers):
    body = _create(
        client, auth_headers, events=["print_completed", "bogus", "print_completed"]
    ).json()
    assert body["events"] == ["print_completed"]  # invalid + dupes removed


def test_delete_channel(client: TestClient, auth_headers):
    cid = _create(client, auth_headers).json()["id"]
    assert (
        client.delete(
            f"/api/v1/notifications/channels/{cid}", headers=auth_headers
        ).status_code
        == 204
    )
    assert (
        client.get("/api/v1/notifications/channels", headers=auth_headers).json() == []
    )


def test_create_rejects_incomplete_config(client: TestClient, auth_headers):
    # Telegram channel missing chat_id is rejected at save time, not at send.
    resp = _create(
        client,
        auth_headers,
        target="telegram",
        config={"bot_token": "t"},
        events=["print_completed"],
    )
    assert resp.status_code == 400
    assert "chat_id" in resp.json()["detail"]


def test_create_rejects_empty_events(client: TestClient, auth_headers):
    resp = _create(client, auth_headers, events=[])
    assert resp.status_code == 400


def test_create_rejects_non_http_url(client: TestClient, auth_headers):
    resp = _create(client, auth_headers, config={"url": "ftp://example.com/x"})
    assert resp.status_code == 400


def test_reenable_resets_failure_count(client: TestClient, auth_headers):
    from app.db.models import NotificationChannel
    from app.db.session import get_session_factory

    cid = _create(client, auth_headers).json()["id"]
    # Simulate an auto-disabled channel.
    with get_session_factory().session() as s:
        ch = s.get(NotificationChannel, cid)
        ch.enabled = False
        ch.consecutive_failures = 10
        s.add(ch)
        s.commit()

    body = client.patch(
        f"/api/v1/notifications/channels/{cid}",
        json={"enabled": True},
        headers=auth_headers,
    ).json()
    assert body["enabled"] is True
    assert body["consecutive_failures"] == 0


def test_deliveries_endpoint_empty(client: TestClient, auth_headers):
    assert (
        client.get("/api/v1/notifications/deliveries", headers=auth_headers).json()
        == []
    )


class TestNotificationAuthorization:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            pytest.param("GET", "/api/v1/notifications", None, id="get-settings"),
            pytest.param(
                "PUT",
                "/api/v1/notifications",
                {"enabled": True},
                id="update-settings",
            ),
            pytest.param(
                "GET", "/api/v1/notifications/channels", None, id="list-channels"
            ),
            pytest.param(
                "POST",
                "/api/v1/notifications/channels",
                {
                    "name": "Denied",
                    "target": "webhook",
                    "config": {"url": "https://example.com/denied"},
                    "events": ["print_completed"],
                },
                id="create-channel",
            ),
            pytest.param(
                "PATCH",
                "/api/v1/notifications/channels/999999",
                {"name": "Denied"},
                id="update-channel",
            ),
            pytest.param(
                "DELETE",
                "/api/v1/notifications/channels/999999",
                None,
                id="delete-channel",
            ),
            pytest.param(
                "POST",
                "/api/v1/notifications/channels/999999/test",
                None,
                id="test-channel",
            ),
            pytest.param(
                "GET",
                "/api/v1/notifications/deliveries",
                None,
                id="list-deliveries",
            ),
        ],
    )
    def test_denies_every_notification_route_to_a_non_superuser(
        self,
        client: TestClient,
        db_session: Session,
        method: str,
        path: str,
        payload: dict[str, object] | None,
    ) -> None:
        headers = _regular_headers(db_session, f"notification-{method}-{path[-4:]}")

        response = client.request(method, path, json=payload, headers=headers)

        assert response.status_code == 403


class TestNotificationSettingsValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({}, id="missing-enabled"),
            pytest.param({"enabled": "definitely"}, id="non-boolean-enabled"),
            pytest.param({"enabled": True, "extra": True}, id="unknown-field"),
        ],
    )
    def test_rejects_malformed_settings_updates(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        payload: dict[str, object],
    ) -> None:
        response = client.put(
            "/api/v1/notifications", json=payload, headers=auth_headers
        )

        assert response.status_code == 422
        current = client.get("/api/v1/notifications", headers=auth_headers)
        assert current.json()["enabled"] is False


class TestNotificationTargetRegistry:
    @pytest.mark.parametrize(
        ("target", "config", "secret_key"),
        [
            pytest.param(
                target,
                {
                    NotificationTarget.WEBHOOK: {"url": "https://example.com/webhook"},
                    NotificationTarget.DISCORD: {
                        "url": "https://discord.example/webhook"
                    },
                    NotificationTarget.TELEGRAM: {
                        "bot_token": "123:FAKE",
                        "chat_id": "42",
                    },
                    NotificationTarget.NTFY: {
                        "topic": "prints",
                        "server_url": "https://ntfy.example",
                        "token": "fake-token",
                    },
                }[target],
                {
                    NotificationTarget.WEBHOOK: "url",
                    NotificationTarget.DISCORD: "url",
                    NotificationTarget.TELEGRAM: "bot_token",
                    NotificationTarget.NTFY: "token",
                }[target],
                id=target.value,
            )
            for target in NotificationTarget
        ],
    )
    def test_creates_every_registered_target_with_masked_secrets(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        target: NotificationTarget,
        config: dict[str, str],
        secret_key: str,
    ) -> None:
        response = client.post(
            "/api/v1/notifications/channels",
            json={
                "name": f"{target.value} channel",
                "target": target.value,
                "config": config,
                "events": [event.value for event in NotificationEventType],
            },
            headers=auth_headers,
        )

        assert response.status_code == 201, response.text
        assert response.json()["config"][secret_key] == "********"
        assert response.json()["config_flags"][f"has_{secret_key}"] is True
        channel = db_session.get(NotificationChannel, response.json()["id"])
        assert channel is not None
        assert channel.target == target

    def test_replaces_a_stored_secret_without_returning_it(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        channel_id = _create(client, auth_headers).json()["id"]

        response = client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"config": {"url": "https://example.com/replacement"}},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["config"]["url"] == "********"
        channel = db_session.get(NotificationChannel, channel_id)
        assert channel is not None
        assert json.loads(channel.config_json)["url"] == (
            "https://example.com/replacement"
        )


class TestUnknownNotificationChannel:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            pytest.param(
                "PATCH", "/api/v1/notifications/channels/999999", {}, id="update"
            ),
            pytest.param(
                "DELETE",
                "/api/v1/notifications/channels/999999",
                None,
                id="delete",
            ),
            pytest.param(
                "POST",
                "/api/v1/notifications/channels/999999/test",
                None,
                id="test-send",
            ),
        ],
    )
    def test_returns_not_found_for_an_unknown_channel(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        method: str,
        path: str,
        payload: dict[str, object] | None,
    ) -> None:
        response = client.request(method, path, json=payload, headers=auth_headers)

        assert response.status_code == 404


class TestNotificationDeliveries:
    def test_lists_recent_deliveries_newest_first(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        channel = NotificationChannel(
            name="Delivery history",
            target=NotificationTarget.WEBHOOK,
            config_json=json.dumps({"url": "https://example.com/hook"}),
            events_json=json.dumps([NotificationEventType.PRINT_COMPLETED.value]),
        )
        db_session.add(channel)
        db_session.commit()
        db_session.refresh(channel)
        instants = [datetime(2026, 1, day, tzinfo=timezone.utc) for day in (1, 2, 3)]
        for index, instant in enumerate(instants):
            db_session.add(
                NotificationDelivery(
                    channel_id=channel.id,
                    event_type=NotificationEventType.PRINT_COMPLETED,
                    status=NotificationDeliveryStatus.SENT,
                    attempts=index,
                    created_at=instant,
                )
            )
        db_session.commit()

        response = client.get("/api/v1/notifications/deliveries", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert [row["attempts"] for row in response.json()] == [2, 1, 0]

    def test_applies_the_requested_delivery_limit(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        channel = NotificationChannel(
            name="Limited delivery history",
            target=NotificationTarget.WEBHOOK,
            config_json=json.dumps({"url": "https://example.com/hook"}),
            events_json=json.dumps([NotificationEventType.PRINT_FAILED.value]),
        )
        db_session.add(channel)
        db_session.commit()
        db_session.refresh(channel)
        for day in (1, 2, 3):
            db_session.add(
                NotificationDelivery(
                    channel_id=channel.id,
                    event_type=NotificationEventType.PRINT_FAILED,
                    created_at=datetime(2026, 2, day, tzinfo=timezone.utc),
                )
            )
        db_session.commit()

        response = client.get(
            "/api/v1/notifications/deliveries?limit=2", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert len(response.json()) == 2
