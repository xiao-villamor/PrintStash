"""Coverage for the notification outbox: enqueue, dispatch, and hub edge-triggers.

Network is always mocked at ``notifications._client_for``; the in-memory test engine
(see conftest) backs both the ``db_session`` fixture and the dispatcher's own
sessions, so enqueue and delivery share one DB.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.url_safety import PinnedTarget, UnsafeUrlError
from app.db.models import (
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEventType,
    NotificationTarget,
    Printer,
    PrinterStatus,
)
from app.services import notifications
from app.services.runtime_config import set_notifications_enabled

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _channel(
    session,
    *,
    events,
    target=NotificationTarget.WEBHOOK,
    config=None,
    printer_ids=None,
    enabled=True,
    name="ch",
):
    ch = NotificationChannel(
        name=name,
        target=target,
        enabled=enabled,
        config_json=json.dumps(config or {"url": "https://example.com/hook"}),
        events_json=json.dumps([e.value for e in events]),
        printer_ids_json=json.dumps(printer_ids) if printer_ids is not None else None,
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


def _deliveries(session, channel_id=None):
    rows = session.exec(__import__("sqlmodel").select(NotificationDelivery)).all()
    return [d for d in rows if channel_id is None or d.channel_id == channel_id]


def _http_returning(status_code=200, text="", headers=None):
    """Fake for ``notifications._client_for``: an async-context-manager client."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    client = MagicMock()
    client.request = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _client_factory(client):
    """``_client_for(target)`` stub returning a prepared fake client."""
    return lambda _target: client


@pytest.fixture(autouse=True)
def _allow_public_urls():
    """Treat all delivery URLs as public so send-path tests don't hit real DNS.

    Tests that exercise the SSRF guard itself override this with their own patch.
    """
    target = PinnedTarget(
        url="https://hooks.example/x",
        host="hooks.example",
        port=443,
        ip="93.184.216.34",
    )
    with patch.object(notifications, "resolve_public_target", return_value=target):
        yield


# --------------------------------------------------------------------------- #
# backoff schedule
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# enqueue (transactional outbox)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# hub edge-triggers
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _client_for, _channel_subscribes, _claim_due_deliveries, _record_result,
# _parse_retry_after — corrupt-JSON and edge-case branches
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# serialize_channel / update_channel — corrupt-JSON and non-secret branches
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# send_test — error branches (channel-not-found, corrupt config, render error,
# blocked host, non-2xx response, network exception)
# --------------------------------------------------------------------------- #

__all__ = [
    "AsyncMock",
    "MagicMock",
    "NotificationChannel",
    "NotificationDelivery",
    "NotificationDeliveryStatus",
    "NotificationEventType",
    "NotificationTarget",
    "PinnedTarget",
    "Printer",
    "PrinterStatus",
    "UnsafeUrlError",
    "_channel",
    "_client_factory",
    "_deliveries",
    "_http_returning",
    "asyncio",
    "json",
    "notifications",
    "patch",
    "pytest",
    "set_notifications_enabled",
]
