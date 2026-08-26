"""Defends backoff schedule then exhaustion at the services notifications integration boundary.

A regression could drop delivery attempts or persist an invalid channel state.
"""

from __future__ import annotations

from ._notifications_shared import (
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEventType,
    NotificationTarget,
    PinnedTarget,
    Printer,
    PrinterStatus,
    UnsafeUrlError,
    _channel,
    _client_factory,
    _deliveries,
    _http_returning,
    asyncio,
    notifications,
    patch,
    pytest,
    set_notifications_enabled,
)


def test_backoff_schedule_then_exhaustion():
    assert [notifications.next_retry_delay(a) for a in (1, 2, 3, 4)] == [
        30,
        120,
        600,
        1800,
    ]
    assert notifications.next_retry_delay(5) is None


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


def test_offline_edge_fires_once_per_transition(db_session, hub):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.READY)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])

    hub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)
    hub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)  # no re-fire
    db_session.expire_all()
    assert len(_deliveries(db_session)) == 1

    # Recover then drop again -> a second, distinct event.
    hub._mark_status_db(p.id, PrinterStatus.READY, None)
    hub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)
    db_session.expire_all()
    assert len(_deliveries(db_session)) == 2


def test_print_completed_fires_once_and_is_idempotent(db_session, hub):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.PRINTING)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])

    stats = {"total_duration": 3600, "filament_used": 1000, "filename": "x.gcode"}
    hub._sync_active_job_db(p.id, "complete", "x.gcode", 1.0, stats)
    hub._sync_active_job_db(p.id, "complete", "x.gcode", 1.0, stats)  # idempotent
    db_session.expire_all()
    deliveries = _deliveries(db_session)
    assert len(deliveries) == 1
    assert deliveries[0].event_type == NotificationEventType.PRINT_COMPLETED
    assert deliveries[0].print_job_id is not None


def test_cancelled_emits_distinct_event(db_session, hub):
    set_notifications_enabled(db_session, True)
    p = Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.PRINTING)
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    # Channel only wants completions, not cancellations -> nothing enqueued.
    _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])

    stats = {"filename": "y.gcode"}
    hub._sync_active_job_db(p.id, "cancelled", "y.gcode", 0.4, stats)
    db_session.expire_all()
    assert _deliveries(db_session) == []


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
