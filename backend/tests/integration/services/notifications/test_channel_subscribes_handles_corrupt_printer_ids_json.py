"""Defends channel subscribes handles corrupt printer ids json at the services notifications integration boundary.

A regression could drop delivery attempts or persist an invalid channel state.
"""

from __future__ import annotations

from ._notifications_shared import (
    AsyncMock,
    MagicMock,
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEventType,
    NotificationTarget,
    Printer,
    PrinterStatus,
    UnsafeUrlError,
    _channel,
    _client_factory,
    _deliveries,
    _http_returning,
    asyncio,
    json,
    notifications,
    patch,
    pytest,
    set_notifications_enabled,
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


def test_offline_not_fired_from_unknown(db_session, hub):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.UNKNOWN)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])

    hub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)
    db_session.expire_all()
    assert _deliveries(db_session) == []
