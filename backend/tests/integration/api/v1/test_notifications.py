"""Notification channels: secrets in, masks out, and nothing undeliverable stored.

A channel holds the credential that reaches an operator — a Discord webhook URL, a
Telegram bot token. Two rules protect it. Reads mask the value and expose a
`has_<key>` flag instead, so the settings page can say "configured" without handing the
secret to anyone who can open it. And an update *preserves* what is stored when the
field is absent or comes back as the mask, because the UI re-sends what it rendered: a
mask written back verbatim would replace a working webhook with the literal `********`.

The other rule is that a channel is validated when it is saved, not when it is used.
Every target has required config and its URL fields must be http(s), so a channel that
could never deliver is refused at the API rather than discovered at 3am when a print
fails and nothing is sent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.url_safety import PinnedTarget
from app.db.models import NotificationChannel, NotificationTarget
from app.services.notifications import _REQUIRED_CONFIG_FIELDS
from tests.integration.conftest import UserHeaders

MASK = "********"
WEBHOOK_URL = "https://hooks.example.test/vault"
MAX_NAME = 128

# One valid config per target, derived from the same registry the API validates against.
TARGET_CONFIG: dict[NotificationTarget, dict[str, str]] = {
    NotificationTarget.WEBHOOK: {"url": WEBHOOK_URL},
    NotificationTarget.DISCORD: {
        "url": "https://discord.example.test/api/webhooks/1/x"
    },
    NotificationTarget.TELEGRAM: {"bot_token": "bot-token", "chat_id": "123456"},
    NotificationTarget.NTFY: {"topic": "printstash"},
}


def _create(client: TestClient, headers: dict[str, str], **overrides: Any):
    body: dict[str, Any] = {
        "name": "My webhook",
        "target": "webhook",
        "config": {"url": WEBHOOK_URL},
        "events": ["print_completed", "print_failed"],
    }
    body.update(overrides)
    return client.post("/api/v1/notifications/channels", json=body, headers=headers)


@pytest.fixture
def channel_id(client: TestClient, auth_headers: dict[str, str]) -> int:
    return _create(client, auth_headers).json()["id"]


@pytest.fixture
def delivery_target():
    """Stand in for the outbound webhook, recording the URL it was sent to."""
    response = MagicMock(status_code=204, text="")
    transport = MagicMock(request=AsyncMock(return_value=response))
    transport.__aenter__ = AsyncMock(return_value=transport)
    transport.__aexit__ = AsyncMock(return_value=False)
    pinned = PinnedTarget(
        url=WEBHOOK_URL, host="hooks.example.test", port=443, ip="93.184.216.34"
    )

    def send(client: TestClient, headers: dict[str, str], channel: int) -> str:
        with (
            patch("app.services.notifications._client_for", return_value=transport),
            patch(
                "app.services.notifications.resolve_public_target", return_value=pinned
            ),
        ):
            result = client.post(
                f"/api/v1/notifications/channels/{channel}/test", headers=headers
            )
        assert result.status_code == 200, result.text
        assert result.json()["ok"] is True
        return transport.request.call_args.args[1]

    return send


class TestGetSettings:
    def test_reports_the_master_switch(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/notifications", headers=auth_headers).json()

        assert body["enabled"] is False

    def test_lists_the_channels_alongside_it(
        self, client: TestClient, auth_headers: dict[str, str], channel_id: int
    ) -> None:
        body = client.get("/api/v1/notifications", headers=auth_headers).json()

        assert [row["id"] for row in body["channels"]] == [channel_id]

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/notifications").status_code == 401

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.get("/api/v1/notifications", headers=user_headers("operator"))

        assert response.status_code == 403, response.text


class TestUpdateSettings:
    def test_enables_notifications(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.put(
            "/api/v1/notifications", json={"enabled": True}, headers=auth_headers
        )

        body = client.get("/api/v1/notifications", headers=auth_headers).json()
        assert body["enabled"] is True

    def test_disables_notifications(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.put(
            "/api/v1/notifications", json={"enabled": True}, headers=auth_headers
        )

        client.put(
            "/api/v1/notifications", json={"enabled": False}, headers=auth_headers
        )

        body = client.get("/api/v1/notifications", headers=auth_headers).json()
        assert body["enabled"] is False

    def test_rejects_an_unknown_field(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.put(
            "/api/v1/notifications",
            json={"enabled": True, "unexpected": 1},
            headers=auth_headers,
        )

        assert response.status_code == 422, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.put(
            "/api/v1/notifications",
            json={"enabled": True},
            headers=user_headers("operator"),
        )

        assert response.status_code == 403, response.text


class TestListChannels:
    def test_lists_the_stored_channels(
        self, client: TestClient, auth_headers: dict[str, str], channel_id: int
    ) -> None:
        listed = client.get(
            "/api/v1/notifications/channels", headers=auth_headers
        ).json()

        assert [row["id"] for row in listed] == [channel_id]

    def test_returns_an_empty_list_with_no_channels(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert (
            client.get("/api/v1/notifications/channels", headers=auth_headers).json()
            == []
        )

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.get(
            "/api/v1/notifications/channels", headers=user_headers("operator")
        )

        assert response.status_code == 403, response.text


class TestCreateChannel:
    def test_returns_the_created_channel(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = _create(client, auth_headers)

        assert response.status_code == 201, response.text
        assert response.json()["name"] == "My webhook"

    def test_masks_a_secret_in_the_response(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(client, auth_headers).json()

        assert body["config"]["url"] == MASK
        assert WEBHOOK_URL not in str(body)

    def test_reports_secret_presence_as_a_flag(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(client, auth_headers).json()

        assert body["config_flags"]["has_url"] is True

    def test_returns_non_secret_config_as_is(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(
            client, auth_headers, target="ntfy", config={"topic": "printstash"}
        ).json()

        assert body["config"]["topic"] == "printstash", (
            "a topic is not a credential and stays readable"
        )

    def test_keeps_the_events_it_was_given(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(client, auth_headers).json()

        assert set(body["events"]) == {"print_completed", "print_failed"}

    def test_drops_every_event_it_cannot_use(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(
            client,
            auth_headers,
            events=["print_completed", "bogus", "print_completed"],
        ).json()

        assert body["events"] == ["print_completed"]

    def test_rejects_an_empty_event_list(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert _create(client, auth_headers, events=[]).status_code == 400

    @pytest.mark.parametrize(
        ("target", "missing"),
        [
            pytest.param(target, field, id=f"{target.value}-without-{field}")
            for target, fields in _REQUIRED_CONFIG_FIELDS.items()
            for field in fields
        ],
    )
    def test_requires_every_field_its_target_needs(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        target: NotificationTarget,
        missing: str,
    ) -> None:
        config = {k: v for k, v in TARGET_CONFIG[target].items() if k != missing}

        response = _create(client, auth_headers, target=target.value, config=config)

        assert response.status_code == 400, response.text
        assert missing in response.json()["detail"]

    def test_rejects_a_config_url_that_is_not_http(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = _create(
            client, auth_headers, config={"url": "ftp://example.test/hook"}
        )

        assert response.status_code == 400, response.text

    @pytest.mark.parametrize("target", list(NotificationTarget), ids=lambda t: t.value)
    def test_accepts_every_target_with_its_required_config(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        target: NotificationTarget,
    ) -> None:
        response = _create(
            client,
            auth_headers,
            name=f"{target.value} channel",
            target=target.value,
            config=TARGET_CONFIG[target],
        )

        assert response.status_code == 201, response.text

    def test_rejects_an_unknown_target(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = _create(client, auth_headers, target="carrier-pigeon")

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("x" * (MAX_NAME + 1), id="over-max"),
        ],
    )
    def test_rejects_a_name_outside_the_length_bounds(
        self, client: TestClient, auth_headers: dict[str, str], name: str
    ) -> None:
        assert _create(client, auth_headers, name=name).status_code == 422

    def test_rejects_an_unknown_field(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert _create(client, auth_headers, unexpected="ignored").status_code == 422

    def test_enables_a_new_channel_by_default(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(client, auth_headers).json()

        assert body["enabled"] is True

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = _create(client, user_headers("operator"))

        assert response.status_code == 403, response.text


class TestUpdateChannel:
    def test_renames_the_channel(
        self, client: TestClient, auth_headers: dict[str, str], channel_id: int
    ) -> None:
        response = client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Renamed"

    def test_keeps_the_stored_secret_when_it_is_not_sent(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        channel_id: int,
        delivery_target,
    ) -> None:
        client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )

        assert delivery_target(client, auth_headers, channel_id) == WEBHOOK_URL

    def test_keeps_the_stored_secret_when_the_mask_is_sent_back(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        channel_id: int,
        delivery_target,
    ) -> None:
        client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"config": {"url": MASK}},
            headers=auth_headers,
        )

        assert delivery_target(client, auth_headers, channel_id) == WEBHOOK_URL, (
            "the UI re-sends what it rendered; the mask must not overwrite the secret"
        )

    def test_replaces_the_event_list(
        self, client: TestClient, auth_headers: dict[str, str], channel_id: int
    ) -> None:
        body = client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"events": ["printer_offline"]},
            headers=auth_headers,
        ).json()

        assert body["events"] == ["printer_offline"]

    def test_sets_the_printer_scope(
        self, client: TestClient, auth_headers: dict[str, str], channel_id: int
    ) -> None:
        body = client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"printer_ids": [3, 4]},
            headers=auth_headers,
        ).json()

        assert body["printer_ids"] == [3, 4]

    def test_clears_the_printer_scope_when_sent_null(
        self, client: TestClient, auth_headers: dict[str, str], channel_id: int
    ) -> None:
        client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"printer_ids": [3, 4]},
            headers=auth_headers,
        )

        body = client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"printer_ids": None},
            headers=auth_headers,
        ).json()

        assert body["printer_ids"] is None, "an explicit null means every printer again"

    def test_resets_the_failure_count_when_re_enabled(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        channel_id: int,
    ) -> None:
        channel = db_session.get(NotificationChannel, channel_id)
        assert channel is not None
        channel.enabled = False
        channel.consecutive_failures = 10
        db_session.add(channel)
        db_session.commit()

        body = client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"enabled": True},
            headers=auth_headers,
        ).json()

        assert body["enabled"] is True
        # Without the reset it would trip its own auto-disable on the next failure.
        assert body["consecutive_failures"] == 0

    def test_keeps_the_stored_secret_when_it_is_sent_blank(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        channel_id: int,
        delivery_target,
    ) -> None:
        client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"config": {"url": ""}},
            headers=auth_headers,
        )

        assert delivery_target(client, auth_headers, channel_id) == WEBHOOK_URL, (
            "a blank secret means 'unchanged', the same as the mask"
        )

    def test_rejects_a_config_that_would_not_deliver(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # `topic` is not a secret, so clearing it really clears it — and an ntfy
        # channel without a topic can never deliver.
        ntfy = _create(
            client, auth_headers, target="ntfy", config={"topic": "printstash"}
        ).json()["id"]

        response = client.patch(
            f"/api/v1/notifications/channels/{ntfy}",
            json={"config": {"topic": ""}},
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text
        assert "topic" in response.json()["detail"]

    def test_reports_an_unknown_channel_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.patch(
            "/api/v1/notifications/channels/9999",
            json={"name": "Ghost"},
            headers=auth_headers,
        )

        assert response.status_code == 404, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders, channel_id: int
    ) -> None:
        response = client.patch(
            f"/api/v1/notifications/channels/{channel_id}",
            json={"name": "Hijacked"},
            headers=user_headers("operator"),
        )

        assert response.status_code == 403, response.text


class TestDeleteChannel:
    def test_deletes_the_channel(
        self, client: TestClient, auth_headers: dict[str, str], channel_id: int
    ) -> None:
        response = client.delete(
            f"/api/v1/notifications/channels/{channel_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        assert (
            client.get("/api/v1/notifications/channels", headers=auth_headers).json()
            == []
        )

    def test_reports_an_unknown_channel_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.delete(
            "/api/v1/notifications/channels/9999", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders, channel_id: int
    ) -> None:
        response = client.delete(
            f"/api/v1/notifications/channels/{channel_id}",
            headers=user_headers("operator"),
        )

        assert response.status_code == 403, response.text


class TestTestChannel:
    def test_sends_to_the_stored_url(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        channel_id: int,
        delivery_target,
    ) -> None:
        assert delivery_target(client, auth_headers, channel_id) == WEBHOOK_URL

    def test_reports_an_unknown_channel_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/notifications/channels/9999/test", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders, channel_id: int
    ) -> None:
        response = client.post(
            f"/api/v1/notifications/channels/{channel_id}/test",
            headers=user_headers("operator"),
        )

        assert response.status_code == 403, response.text


class TestListDeliveries:
    def test_returns_an_empty_list_with_no_deliveries(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert (
            client.get("/api/v1/notifications/deliveries", headers=auth_headers).json()
            == []
        )

    @pytest.mark.parametrize(
        "limit", [pytest.param(0, id="below-min"), pytest.param(500, id="above-max")]
    )
    def test_clamps_the_limit_into_range(
        self, client: TestClient, auth_headers: dict[str, str], limit: int
    ) -> None:
        response = client.get(
            f"/api/v1/notifications/deliveries?limit={limit}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert len(response.json()) <= 200

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.get(
            "/api/v1/notifications/deliveries", headers=user_headers("operator")
        )

        assert response.status_code == 403, response.text
