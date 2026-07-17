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
from app.services.printer_hub import PrinterHub
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


def test_backoff_schedule_then_exhaustion():
    assert [notifications.next_retry_delay(a) for a in (1, 2, 3, 4)] == [
        30,
        120,
        600,
        1800,
    ]
    assert notifications.next_retry_delay(5) is None


# --------------------------------------------------------------------------- #
# enqueue (transactional outbox)
# --------------------------------------------------------------------------- #


def test_enqueue_noop_when_master_switch_off(db_session):
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    n = notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()
    assert n == 0
    assert _deliveries(db_session) == []


def test_enqueue_one_per_matching_channel(db_session):
    set_notifications_enabled(db_session, True)
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE], name="a")
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE], name="b")
    _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED], name="other")
    n = notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=7
    )
    db_session.commit()
    assert n == 2  # only the two offline-subscribed channels
    assert len(_deliveries(db_session)) == 2


def test_enqueue_skips_disabled_channel(db_session):
    set_notifications_enabled(db_session, True)
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE], enabled=False)
    n = notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()
    assert n == 0


def test_enqueue_respects_printer_scope(db_session):
    set_notifications_enabled(db_session, True)
    _channel(
        db_session,
        events=[NotificationEventType.PRINTER_OFFLINE],
        printer_ids=[5],
        name="scoped",
    )
    assert (
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=9
        )
        == 0
    )
    assert (
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=5
        )
        == 1
    )


def test_enqueue_empty_scope_means_all_printers(db_session):
    set_notifications_enabled(db_session, True)
    _channel(
        db_session,
        events=[NotificationEventType.PRINTER_OFFLINE],
        printer_ids=[],  # empty list == no restriction
    )
    assert (
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=123
        )
        == 1
    )


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dispatch_success_marks_sent_and_channel_status(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    with patch.object(
        notifications, "_client_for", new=_client_factory(_http_returning(204))
    ):
        attempted = await notifications.dispatch_due()
    assert attempted == 1

    db_session.expire_all()
    delivery = _deliveries(db_session, ch.id)[0]
    assert delivery.status == NotificationDeliveryStatus.SENT
    assert delivery.attempts == 1
    db_session.refresh(ch)
    assert ch.last_status == "sent"
    assert ch.last_delivered_at is not None


@pytest.mark.asyncio
async def test_dispatch_http_error_retries_with_backoff(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    with patch.object(
        notifications, "_client_for", new=_client_factory(_http_returning(500, "boom"))
    ):
        await notifications.dispatch_due()

    db_session.expire_all()
    delivery = _deliveries(db_session, ch.id)[0]
    assert delivery.status == NotificationDeliveryStatus.PENDING  # will retry
    assert delivery.attempts == 1
    assert "HTTP 500" in (delivery.last_error or "")
    assert delivery.next_retry_at > delivery.created_at  # backed off


@pytest.mark.asyncio
async def test_dispatch_marks_failed_after_exhausting_retries(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()
    delivery_id = _deliveries(db_session, ch.id)[0].id

    # Force the delivery to its last allowed attempt, then fail once more.
    delivery = db_session.get(NotificationDelivery, delivery_id)
    delivery.attempts = notifications._MAX_ATTEMPTS - 1
    db_session.add(delivery)
    db_session.commit()

    with patch.object(
        notifications, "_client_for", new=_client_factory(_http_returning(500))
    ):
        await notifications.dispatch_due()

    db_session.expire_all()
    delivery = db_session.get(NotificationDelivery, delivery_id)
    assert delivery.status == NotificationDeliveryStatus.FAILED


@pytest.mark.asyncio
async def test_dispatch_render_error_fails_without_network(db_session):
    set_notifications_enabled(db_session, True)
    # Telegram channel missing chat_id -> RenderError, no HTTP call.
    ch = _channel(
        db_session,
        events=[NotificationEventType.PRINTER_OFFLINE],
        target=NotificationTarget.TELEGRAM,
        config={"bot_token": "t"},
    )
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    client = _http_returning(200)
    with patch.object(notifications, "_client_for", new=_client_factory(client)):
        await notifications.dispatch_due()
    client.request.assert_not_called()

    db_session.expire_all()
    delivery = _deliveries(db_session, ch.id)[0]
    assert delivery.status == NotificationDeliveryStatus.FAILED


@pytest.mark.asyncio
async def test_dispatch_blocks_non_public_url(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    client = _http_returning(204)
    # Override the autouse allow-fixture: this URL is "not public".
    with (
        patch.object(notifications, "_client_for", new=_client_factory(client)),
        patch.object(
            notifications,
            "resolve_public_target",
            side_effect=UnsafeUrlError("url_target_not_public"),
        ),
    ):
        await notifications.dispatch_due()
    client.request.assert_not_called()  # never left the process

    db_session.expire_all()
    delivery = _deliveries(db_session, ch.id)[0]
    assert delivery.status == NotificationDeliveryStatus.FAILED  # permanent
    assert "not a public host" in (delivery.last_error or "")


@pytest.mark.asyncio
async def test_dispatch_honors_retry_after_without_spending_attempt(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    client = _http_returning(429, "slow down", headers={"Retry-After": "120"})
    with patch.object(notifications, "_client_for", new=_client_factory(client)):
        await notifications.dispatch_due()

    db_session.expire_all()
    d = _deliveries(db_session, ch.id)[0]
    assert d.status == NotificationDeliveryStatus.PENDING
    assert d.attempts == 0  # rate-limit did NOT consume the retry budget
    # Rescheduled roughly Retry-After seconds out.
    assert d.next_retry_at > d.created_at


@pytest.mark.asyncio
async def test_idempotency_key_header_sent(db_session):
    set_notifications_enabled(db_session, True)
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    client = _http_returning(204)
    with patch.object(notifications, "_client_for", new=_client_factory(client)):
        await notifications.dispatch_due()

    headers = client.request.call_args.kwargs["headers"]
    assert headers["Idempotency-Key"].startswith("printstash-delivery-")
    assert "X-PrintStash-Delivery-Id" in headers


@pytest.mark.asyncio
async def test_channel_auto_disabled_after_threshold(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    # One short of the threshold; a single terminal failure should trip it.
    ch.consecutive_failures = notifications._CIRCUIT_BREAKER_THRESHOLD - 1
    db_session.add(ch)
    db_session.commit()

    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()
    d = _deliveries(db_session, ch.id)[0]
    d.attempts = notifications._MAX_ATTEMPTS - 1  # next failure is terminal
    db_session.add(d)
    db_session.commit()

    client = _http_returning(500)
    with patch.object(notifications, "_client_for", new=_client_factory(client)):
        await notifications.dispatch_due()

    db_session.refresh(ch)
    assert ch.consecutive_failures >= notifications._CIRCUIT_BREAKER_THRESHOLD
    assert ch.enabled is False
    assert "auto-disabled" in (ch.last_error or "")


@pytest.mark.asyncio
async def test_success_resets_consecutive_failures(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    ch.consecutive_failures = 3
    db_session.add(ch)
    db_session.commit()
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    with patch.object(
        notifications, "_client_for", new=_client_factory(_http_returning(204))
    ):
        await notifications.dispatch_due()

    db_session.refresh(ch)
    assert ch.consecutive_failures == 0


@pytest.mark.asyncio
async def test_stuck_sending_is_reclaimed(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()
    d = _deliveries(db_session, ch.id)[0]
    # Simulate a dispatcher that died mid-send long ago.
    from datetime import timedelta

    from app.core.time import utcnow

    d.status = NotificationDeliveryStatus.SENDING
    d.updated_at = utcnow() - timedelta(
        seconds=notifications._STUCK_SENDING_SECONDS + 60
    )
    db_session.add(d)
    db_session.commit()

    with patch.object(
        notifications, "_client_for", new=_client_factory(_http_returning(204))
    ):
        attempted = await notifications.dispatch_due()

    assert attempted == 1  # reclaimed and delivered
    db_session.refresh(d)
    assert d.status == NotificationDeliveryStatus.SENT


def test_prune_deliveries_removes_old_terminal_rows(db_session):
    from datetime import timedelta

    from app.core.time import utcnow
    from app.db.models import NotificationDelivery

    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    old = utcnow() - timedelta(days=notifications._DELIVERY_RETENTION_DAYS + 1)
    # Old SENT (prune), old PENDING (keep), recent FAILED (keep).
    rows = [
        NotificationDelivery(
            channel_id=ch.id,
            event_type=NotificationEventType.PRINTER_OFFLINE,
            status=NotificationDeliveryStatus.SENT,
            created_at=old,
        ),
        NotificationDelivery(
            channel_id=ch.id,
            event_type=NotificationEventType.PRINTER_OFFLINE,
            status=NotificationDeliveryStatus.PENDING,
            created_at=old,
        ),
        NotificationDelivery(
            channel_id=ch.id,
            event_type=NotificationEventType.PRINTER_OFFLINE,
            status=NotificationDeliveryStatus.FAILED,
        ),
    ]
    for r in rows:
        db_session.add(r)
    db_session.commit()

    deleted = notifications.prune_deliveries()
    assert deleted == 1
    remaining = {d.status for d in _deliveries(db_session)}
    assert NotificationDeliveryStatus.SENT not in remaining
    assert NotificationDeliveryStatus.PENDING in remaining
    assert NotificationDeliveryStatus.FAILED in remaining


@pytest.mark.asyncio
async def test_run_dispatcher_loop_delivers_then_cancels(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    with (
        patch.object(notifications, "_POLL_INTERVAL_S", 0.01),
        patch.object(
            notifications, "_client_for", new=_client_factory(_http_returning(204))
        ),
    ):
        task = asyncio.create_task(notifications.run_dispatcher_loop())
        # Poll until the delivery is sent, then cancel the loop.
        for _ in range(200):
            await asyncio.sleep(0.01)
            db_session.expire_all()
            if (
                _deliveries(db_session, ch.id)[0].status
                == NotificationDeliveryStatus.SENT
            ):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    db_session.expire_all()
    assert _deliveries(db_session, ch.id)[0].status == NotificationDeliveryStatus.SENT


@pytest.mark.asyncio
async def test_run_dispatcher_loop_survives_tick_error(db_session):
    # A failing tick must not kill the loop. Wait on an actual tick count
    # rather than a wall-clock sleep — under load, a 0.1s sleep at a 0.01s
    # poll interval isn't reliably enough time for 2 ticks, and the loop
    # surviving is exactly what's under test, so the wait must not assume it.
    calls = {"n": 0}
    two_ticks = asyncio.Event()

    async def _boom():
        calls["n"] += 1
        if calls["n"] >= 2:
            two_ticks.set()
        raise RuntimeError("tick boom")

    with (
        patch.object(notifications, "_POLL_INTERVAL_S", 0.01),
        patch.object(notifications, "dispatch_due", side_effect=_boom),
    ):
        task = asyncio.create_task(notifications.run_dispatcher_loop())
        await asyncio.wait_for(two_ticks.wait(), timeout=5.0)
        assert not task.done()  # still running despite repeated errors
        assert calls["n"] >= 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --------------------------------------------------------------------------- #
# hub edge-triggers
# --------------------------------------------------------------------------- #


def test_offline_edge_fires_once_per_transition(db_session):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.READY)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])

    PrinterHub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)
    PrinterHub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)  # no re-fire
    db_session.expire_all()
    assert len(_deliveries(db_session)) == 1

    # Recover then drop again -> a second, distinct event.
    PrinterHub._mark_status_db(p.id, PrinterStatus.READY, None)
    PrinterHub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)
    db_session.expire_all()
    assert len(_deliveries(db_session)) == 2


def test_print_completed_fires_once_and_is_idempotent(db_session):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.PRINTING)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])

    stats = {"total_duration": 3600, "filament_used": 1000, "filename": "x.gcode"}
    hub = PrinterHub()
    hub._sync_active_job_db(p.id, "complete", "x.gcode", 1.0, stats)
    hub._sync_active_job_db(p.id, "complete", "x.gcode", 1.0, stats)  # idempotent
    db_session.expire_all()
    deliveries = _deliveries(db_session)
    assert len(deliveries) == 1
    assert deliveries[0].event_type == NotificationEventType.PRINT_COMPLETED
    assert deliveries[0].print_job_id is not None


def test_cancelled_emits_distinct_event(db_session):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.PRINTING)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    # Channel only wants completions, not cancellations -> nothing enqueued.
    _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])

    stats = {"filename": "y.gcode"}
    PrinterHub()._sync_active_job_db(p.id, "cancelled", "y.gcode", 0.4, stats)
    db_session.expire_all()
    assert _deliveries(db_session) == []


# --------------------------------------------------------------------------- #
# _client_for, _channel_subscribes, _claim_due_deliveries, _record_result,
# _parse_retry_after — corrupt-JSON and edge-case branches
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_client_for_returns_real_async_client():
    import httpx

    target = PinnedTarget(
        url="https://hooks.example/x",
        host="hooks.example",
        port=443,
        ip="93.184.216.34",
    )
    client = notifications._client_for(target)
    try:
        assert isinstance(client, httpx.AsyncClient)
    finally:
        await client.aclose()


def test_channel_subscribes_handles_corrupt_events_json():
    ch = NotificationChannel(
        name="x",
        target=NotificationTarget.WEBHOOK,
        config_json="{}",
        events_json="not json",
        printer_ids_json=None,
    )
    assert (
        notifications._channel_subscribes(
            ch, NotificationEventType.PRINTER_OFFLINE, None
        )
        is False
    )


def test_channel_subscribes_handles_corrupt_printer_ids_json():
    ch = NotificationChannel(
        name="x",
        target=NotificationTarget.WEBHOOK,
        config_json="{}",
        events_json=json.dumps([NotificationEventType.PRINTER_OFFLINE.value]),
        printer_ids_json="not json",
    )
    # Corrupt scope parses to None (falsy) -> treated as unscoped, matches.
    assert (
        notifications._channel_subscribes(ch, NotificationEventType.PRINTER_OFFLINE, 42)
        is True
    )


def test_claim_due_deliveries_handles_corrupt_config_and_context_json(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    ch.config_json = "not json"
    db_session.add(ch)
    delivery = NotificationDelivery(
        channel_id=ch.id,
        event_type=NotificationEventType.PRINTER_OFFLINE,
        status=NotificationDeliveryStatus.PENDING,
        context_json="not json either",
    )
    db_session.add(delivery)
    db_session.commit()

    items = notifications._claim_due_deliveries()

    assert len(items) == 1
    assert items[0]["config"] == {}
    assert items[0]["context"] == {}


def test_record_result_noop_when_delivery_missing():
    # Must not raise even though the delivery id doesn't exist.
    notifications._record_result(999_999_999, 1, success=True, error=None)


def test_parse_retry_after_variants():
    assert notifications._parse_retry_after(None) is None
    assert notifications._parse_retry_after("") is None
    assert notifications._parse_retry_after("120") == 120
    assert notifications._parse_retry_after("not a date") is None
    from email.utils import format_datetime

    from app.core.time import utcnow

    future = utcnow() + __import__("datetime").timedelta(seconds=90)
    parsed = notifications._parse_retry_after(format_datetime(future))
    assert parsed is not None and parsed > 0

    # A timezone-less HTTP-date parses to a naive datetime, which must be
    # treated as UTC rather than raising.
    naive_future = (utcnow() + __import__("datetime").timedelta(seconds=90)).strftime(
        "%a, %d %b %Y %H:%M:%S"
    )
    assert notifications._parse_retry_after(naive_future) is not None


@pytest.mark.asyncio
async def test_send_one_records_network_exception(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    client = MagicMock()
    client.request = AsyncMock(side_effect=RuntimeError("connection reset"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(notifications, "_client_for", new=_client_factory(client)):
        await notifications.dispatch_due()

    db_session.expire_all()
    d = _deliveries(db_session, ch.id)[0]
    assert d.status == NotificationDeliveryStatus.PENDING  # will retry
    assert "connection reset" in (d.last_error or "")


@pytest.mark.asyncio
async def test_dispatch_due_returns_zero_when_nothing_claimed(db_session):
    assert await notifications.dispatch_due() == 0


@pytest.mark.asyncio
async def test_run_dispatcher_loop_reraises_cancelled_error_from_tick():
    with (
        patch.object(notifications, "_POLL_INTERVAL_S", 0.01),
        patch.object(notifications, "dispatch_due", side_effect=asyncio.CancelledError),
    ):
        task = asyncio.create_task(notifications.run_dispatcher_loop())
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)


# --------------------------------------------------------------------------- #
# serialize_channel / update_channel — corrupt-JSON and non-secret branches
# --------------------------------------------------------------------------- #


def test_serialize_channel_handles_all_corrupt_json_fields():
    ch = NotificationChannel(
        name="x",
        target=NotificationTarget.WEBHOOK,
        config_json="not json",
        events_json="not json",
        printer_ids_json="not json",
    )
    out = notifications.serialize_channel(ch)
    assert out["config"] == {}
    assert out["events"] == []
    assert out["printer_ids"] is None


def test_serialize_channel_returns_nonsecret_config_plainly():
    ch = NotificationChannel(
        name="x",
        target=NotificationTarget.NTFY,
        config_json=json.dumps({"topic": "my-topic", "token": "secret-tok"}),
        events_json=json.dumps([NotificationEventType.PRINTER_OFFLINE.value]),
    )
    out = notifications.serialize_channel(ch)
    assert out["config"]["topic"] == "my-topic"  # non-secret: passed through
    assert out["config"]["token"] == "********"  # secret: masked
    assert out["config_flags"]["has_token"] is True


def test_update_channel_recovers_from_corrupt_stored_events_json(db_session):
    from app.services.notifications import update_channel

    ch = notifications.create_channel(
        db_session,
        name="x",
        target=NotificationTarget.WEBHOOK,
        config={"url": "https://example.com/hook"},
        events=["print_completed"],
    )
    ch.events_json = "not json"
    db_session.add(ch)
    db_session.commit()

    # Corrupt events_json decodes to an empty list (the except branch), which
    # then fails validation the same as a genuinely-empty selection.
    with pytest.raises(notifications.NotificationConfigError):
        update_channel(db_session, ch, name="renamed")


def test_update_channel_recovers_from_corrupt_stored_config_json_no_config_arg(
    db_session,
):
    from app.services.notifications import update_channel

    ch = notifications.create_channel(
        db_session,
        name="x",
        target=NotificationTarget.WEBHOOK,
        config={"url": "https://example.com/hook"},
        events=["print_completed"],
    )
    ch.config_json = "not json"
    db_session.add(ch)
    db_session.commit()

    # config=None -> hits the "merged = json.loads(...)" except branch, then
    # validate_channel fails (no url) — this exercises the corrupt-read path.
    with pytest.raises(notifications.NotificationConfigError):
        update_channel(db_session, ch, name="renamed")


def test_update_channel_recovers_from_corrupt_stored_config_json_with_config_arg(
    db_session,
):
    from app.services.notifications import update_channel

    ch = notifications.create_channel(
        db_session,
        name="x",
        target=NotificationTarget.WEBHOOK,
        config={"url": "https://example.com/hook"},
        events=["print_completed"],
    )
    ch.config_json = "not json"
    db_session.add(ch)
    db_session.commit()

    updated = update_channel(
        db_session, ch, config={"url": "https://example.com/new-hook"}
    )
    assert json.loads(updated.config_json)["url"] == "https://example.com/new-hook"


def test_update_channel_overwrites_nonsecret_config_key(db_session):
    from app.services.notifications import update_channel

    ch = notifications.create_channel(
        db_session,
        name="x",
        target=NotificationTarget.NTFY,
        config={"topic": "old-topic"},
        events=["print_completed"],
    )
    updated = update_channel(db_session, ch, config={"topic": "new-topic"})
    assert json.loads(updated.config_json)["topic"] == "new-topic"


def test_list_recent_deliveries_returns_serialized_rows(db_session):
    set_notifications_enabled(db_session, True)
    ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
    notifications.enqueue_for_event(
        db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=1
    )
    db_session.commit()

    rows = notifications.list_recent_deliveries(db_session)

    assert len(rows) == 1
    assert rows[0]["channel_id"] == ch.id
    assert rows[0]["status"] == NotificationDeliveryStatus.PENDING.value


# --------------------------------------------------------------------------- #
# send_test — error branches (channel-not-found, corrupt config, render error,
# blocked host, non-2xx response, network exception)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_test_channel_not_found():
    result = await notifications.send_test(999_999_999)
    assert result == {"ok": False, "error": "channel not found"}


@pytest.mark.asyncio
async def test_send_test_recovers_from_corrupt_config_json(db_session):
    ch = _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])
    ch.config_json = "not json"
    db_session.add(ch)
    db_session.commit()

    # Corrupt config loads as {}, then webhook rendering fails for lack of a url.
    result = await notifications.send_test(ch.id)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_send_test_render_error_missing_required_config(db_session):
    ch = _channel(
        db_session,
        events=[NotificationEventType.PRINT_COMPLETED],
        target=NotificationTarget.TELEGRAM,
        config={"bot_token": "t"},  # missing chat_id
    )
    result = await notifications.send_test(ch.id)
    assert result["ok"] is False
    assert result["error"]


@pytest.mark.asyncio
async def test_send_test_blocked_non_public_host(db_session):
    ch = _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])
    with patch.object(
        notifications,
        "resolve_public_target",
        side_effect=UnsafeUrlError("url_target_not_public"),
    ):
        result = await notifications.send_test(ch.id)
    assert result["ok"] is False
    assert "not a public host" in result["error"]


@pytest.mark.asyncio
async def test_send_test_http_error_response(db_session):
    ch = _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])
    client = _http_returning(500, "server exploded")
    with patch.object(notifications, "_client_for", new=_client_factory(client)):
        result = await notifications.send_test(ch.id)
    assert result["ok"] is False
    assert "HTTP 500" in result["error"]


@pytest.mark.asyncio
async def test_send_test_network_exception(db_session):
    ch = _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])
    client = MagicMock()
    client.request = AsyncMock(side_effect=RuntimeError("dns failure"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch.object(notifications, "_client_for", new=_client_factory(client)):
        result = await notifications.send_test(ch.id)
    assert result["ok"] is False
    assert "dns failure" in result["error"]


def test_record_channel_test_noop_when_channel_missing():
    # Must not raise even though the channel id doesn't exist.
    notifications._record_channel_test(999_999_999, True, None)


def test_offline_not_fired_from_unknown(db_session):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.UNKNOWN)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])

    PrinterHub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)
    db_session.expire_all()
    assert _deliveries(db_session) == []
