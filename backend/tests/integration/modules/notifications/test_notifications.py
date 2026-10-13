"""Coverage for the notification outbox: enqueue, dispatch, and hub edge-triggers.

Network is always mocked at ``notifications._client_for``; the in-memory test engine
(see conftest) backs both the ``db_session`` fixture and the dispatcher's own
sessions, so enqueue and delivery share one DB.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from app.core.url_safety import PinnedTarget, UnsafeUrlError
from app.db.models import (
    JobKind,
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationEventType,
    NotificationTarget,
    Printer,
    PrinterStatus,
)
from app.modules.administration.runtime_config import set_notifications_enabled
from app.modules.notifications import notifications
from tests.factories import build_printer, build_user

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


@pytest.fixture
def printer(db_session: Session) -> Printer:
    """The printer these notification events are about.

    `notification_deliveries.printer_id` is a foreign key, so a delivery for a
    printer that does not exist is refused here exactly as it is in production. The
    printer itself is incidental to what these tests assert, but it has to be real.
    """
    return build_printer(db_session, name="notify-printer")


class TestNextRetryDelay:
    """The backoff schedule, and the attempt at which a delivery gives up."""

    def test_backoff_schedule_then_exhaustion(self):
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


class TestEnqueueForEvent:
    """Which channels an event reaches, and which it must not."""

    def test_enqueue_one_per_matching_channel(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE], name="a")
        _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE], name="b")
        _channel(
            db_session, events=[NotificationEventType.PRINT_COMPLETED], name="other"
        )
        n = notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()
        assert n == 2  # only the two offline-subscribed channels
        assert len(_deliveries(db_session)) == 2

    def test_enqueue_noop_when_master_switch_off(self, printer: Printer, db_session):
        _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        n = notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()
        assert n == 0
        assert _deliveries(db_session) == []

    def test_enqueue_skips_disabled_channel(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        _channel(
            db_session, events=[NotificationEventType.PRINTER_OFFLINE], enabled=False
        )
        n = notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()
        assert n == 0

    def test_enqueue_skips_a_printer_outside_the_channels_scope(
        self, printer: Printer, db_session
    ) -> None:
        set_notifications_enabled(db_session, True)
        in_scope = build_printer(db_session, name="in-scope")
        _channel(
            db_session,
            events=[NotificationEventType.PRINTER_OFFLINE],
            printer_ids=[in_scope.id],
            name="scoped",
        )

        enqueued = notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )

        assert enqueued == 0

    def test_enqueue_delivers_for_a_printer_inside_the_channels_scope(
        self, db_session
    ) -> None:
        set_notifications_enabled(db_session, True)
        in_scope = build_printer(db_session, name="in-scope")
        _channel(
            db_session,
            events=[NotificationEventType.PRINTER_OFFLINE],
            printer_ids=[in_scope.id],
            name="scoped",
        )

        enqueued = notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=in_scope.id
        )

        assert enqueued == 1

    def test_enqueue_empty_scope_means_all_printers(
        self, printer: Printer, db_session
    ) -> None:
        set_notifications_enabled(db_session, True)
        _channel(
            db_session,
            events=[NotificationEventType.PRINTER_OFFLINE],
            printer_ids=[],  # empty list == no restriction
        )

        enqueued = notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )

        assert enqueued == 1


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# hub edge-triggers
# --------------------------------------------------------------------------- #


class TestEventEdges:
    """An event fires on the transition, not on every poll that still sees it."""

    def test_offline_edge_fires_once_per_transition(self, db_session, hub):
        set_notifications_enabled(db_session, True)
        p = build_printer(
            db_session,
            name="Ender",
            moonraker_url="http://x",
            status=PrinterStatus.READY,
        )
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

    def test_offline_not_fired_from_unknown(self, db_session, hub):
        set_notifications_enabled(db_session, True)
        p = build_printer(
            db_session,
            name="Ender",
            moonraker_url="http://x",
            status=PrinterStatus.UNKNOWN,
        )
        _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])

        hub._mark_status_db(p.id, PrinterStatus.OFFLINE, None)
        db_session.expire_all()
        assert _deliveries(db_session) == []

    def test_print_completed_enqueues_exactly_one_delivery(self, db_session, hub):
        set_notifications_enabled(db_session, True)
        p = build_printer(
            db_session,
            name="Ender",
            moonraker_url="http://x",
            status=PrinterStatus.PRINTING,
        )
        _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])

        stats = {"total_duration": 3600, "filament_used": 1000, "filename": "x.gcode"}
        hub._sync_active_job_db(p.id, "complete", "x.gcode", 1.0, stats)
        hub._sync_active_job_db(p.id, "complete", "x.gcode", 1.0, stats)  # idempotent
        db_session.expire_all()
        deliveries = _deliveries(db_session)
        assert len(deliveries) == 1
        assert deliveries[0].event_type == NotificationEventType.PRINT_COMPLETED
        assert deliveries[0].print_job_id is not None


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


class TestClientFor:
    @pytest.mark.asyncio
    async def test_client_for_returns_real_async_client(self):
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


class TestChannelSubscribes:
    def test_channel_subscribes_handles_corrupt_events_json(self):
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

    def test_channel_subscribes_handles_corrupt_printer_ids_json(self):
        ch = NotificationChannel(
            name="x",
            target=NotificationTarget.WEBHOOK,
            config_json="{}",
            events_json=json.dumps([NotificationEventType.PRINTER_OFFLINE.value]),
            printer_ids_json="not json",
        )
        # Corrupt scope parses to None (falsy) -> treated as unscoped, matches.
        assert (
            notifications._channel_subscribes(
                ch, NotificationEventType.PRINTER_OFFLINE, 42
            )
            is True
        )


class TestClaimDelivery:
    def test_survives_unparseable_stored_json(self, db_session):
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

        item = notifications._claim_delivery(delivery.id)

        assert item is not None
        assert (item["config"], item["context"]) == ({}, {})
        db_session.refresh(delivery)
        assert delivery.status == NotificationDeliveryStatus.SENDING

    def test_a_delivery_that_already_finished_is_not_claimed(self, db_session):
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        delivery = NotificationDelivery(
            channel_id=ch.id,
            event_type=NotificationEventType.PRINTER_OFFLINE,
            status=NotificationDeliveryStatus.SENT,
        )
        db_session.add(delivery)
        db_session.commit()

        # A resubmitted Job for a delivery another attempt already sent must
        # not send it twice.
        assert notifications._claim_delivery(delivery.id) is None
        assert notifications.deliver(delivery.id) is False

    def test_a_missing_delivery_is_not_claimed(self):
        assert notifications._claim_delivery(999_999_999) is None


class TestRecordResult:
    def test_record_result_noop_when_delivery_missing(self):
        # Must not raise even though the delivery id doesn't exist.
        notifications._record_result(999_999_999, 1, success=True, error=None)


class TestParseRetryAfter:
    def test_parse_retry_after_variants(self):
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
        naive_future = (
            utcnow() + __import__("datetime").timedelta(seconds=90)
        ).strftime("%a, %d %b %Y %H:%M:%S")
        assert notifications._parse_retry_after(naive_future) is not None


def _deliver_due() -> int:
    """Run every due delivery through its ``notifications.deliver`` Job; count the sends."""
    from sqlmodel import select

    from app.db.models import Job
    from app.db.session import get_session_factory
    from app.modules.work import nudge
    from tests.integration.api.v1._ingest_assertions import drain_work

    with get_session_factory().scoped_session() as session:
        before = set(session.exec(select(Job.id)).all())
    nudge(JobKind.NOTIFICATIONS_DELIVER)
    drain_work()
    with get_session_factory().scoped_session() as session:
        rows = session.exec(
            select(Job).where(Job.kind == JobKind.NOTIFICATIONS_DELIVER)
        ).all()
        return sum(
            1
            for row in rows
            if row.id not in before
            and json.loads(row.status_json or "{}").get("result", {}).get("sent")
        )


class TestSendOne:
    def test_send_one_records_network_exception(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        client = MagicMock()
        client.request = AsyncMock(side_effect=RuntimeError("connection reset"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            _deliver_due()

        db_session.expire_all()
        d = _deliveries(db_session, ch.id)[0]
        assert d.status == NotificationDeliveryStatus.PENDING  # will retry
        assert "connection reset" in (d.last_error or "")


class TestDeliveryJob:
    def test_nothing_due_sends_nothing(self, db_session):
        assert _deliver_due() == 0

    def test_an_enqueued_event_is_delivered_once_its_transaction_commits(
        self, printer: Printer, db_session
    ):
        # The headline: enqueue nudges the delivery source after the caller's
        # commit, so no poller is needed for the message to leave.
        from tests.integration.api.v1._ingest_assertions import drain_work

        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        with patch.object(
            notifications, "_client_for", new=_client_factory(_http_returning(204))
        ):
            drain_work()

        db_session.expire_all()
        assert (
            _deliveries(db_session, ch.id)[0].status == NotificationDeliveryStatus.SENT
        )

    def test_a_successful_dispatch_records_the_channel_as_healthy(
        self, printer: Printer, db_session
    ):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        with patch.object(
            notifications, "_client_for", new=_client_factory(_http_returning(204))
        ):
            attempted = _deliver_due()
        assert attempted == 1

        db_session.expire_all()
        delivery = _deliveries(db_session, ch.id)[0]
        assert delivery.status == NotificationDeliveryStatus.SENT
        assert delivery.attempts == 1
        db_session.refresh(ch)
        assert ch.last_status == "sent"
        assert ch.last_delivered_at is not None

    def test_idempotency_key_header_sent(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        client = _http_returning(204)
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            _deliver_due()

        headers = client.request.call_args.kwargs["headers"]
        assert headers["Idempotency-Key"].startswith("printstash-delivery-")
        assert "X-PrintStash-Delivery-Id" in headers

    def test_dispatch_http_error_retries_with_backoff(
        self, printer: Printer, db_session
    ):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        with patch.object(
            notifications,
            "_client_for",
            new=_client_factory(_http_returning(500, "boom")),
        ):
            _deliver_due()

        db_session.expire_all()
        delivery = _deliveries(db_session, ch.id)[0]
        assert delivery.status == NotificationDeliveryStatus.PENDING  # will retry
        assert delivery.attempts == 1
        assert "HTTP 500" in (delivery.last_error or "")
        assert delivery.next_retry_at > delivery.created_at  # backed off

    def test_a_backed_off_delivery_waits_for_its_retry_time(
        self, printer: Printer, db_session
    ):
        # The backoff is honoured by the source's due time, not by a poller:
        # draining again straight away sends nothing.
        set_notifications_enabled(db_session, True)
        _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()
        client = _http_returning(500, "boom")
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            _deliver_due()
            _deliver_due()

        assert client.request.await_count == 1

    def test_dispatch_honors_retry_after_without_spending_attempt(
        self, printer: Printer, db_session
    ):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        client = _http_returning(429, "slow down", headers={"Retry-After": "120"})
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            _deliver_due()

        db_session.expire_all()
        d = _deliveries(db_session, ch.id)[0]
        assert d.status == NotificationDeliveryStatus.PENDING
        assert d.attempts == 0  # rate-limit did NOT consume the retry budget
        # Rescheduled roughly Retry-After seconds out.
        assert d.next_retry_at > d.created_at

    def test_dispatch_marks_failed_after_exhausting_retries(
        self, printer: Printer, db_session
    ):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
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
            _deliver_due()

        db_session.expire_all()
        delivery = db_session.get(NotificationDelivery, delivery_id)
        assert delivery.status == NotificationDeliveryStatus.FAILED

    def test_dispatch_render_error_fails_without_network(
        self, printer: Printer, db_session
    ):
        set_notifications_enabled(db_session, True)
        # Telegram channel missing chat_id -> RenderError, no HTTP call.
        ch = _channel(
            db_session,
            events=[NotificationEventType.PRINTER_OFFLINE],
            target=NotificationTarget.TELEGRAM,
            config={"bot_token": "t"},
        )
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        client = _http_returning(200)
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            _deliver_due()
        client.request.assert_not_called()

        db_session.expire_all()
        delivery = _deliveries(db_session, ch.id)[0]
        assert delivery.status == NotificationDeliveryStatus.FAILED

    def test_dispatch_blocks_non_public_url(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
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
            _deliver_due()
        client.request.assert_not_called()  # never left the process

        db_session.expire_all()
        delivery = _deliveries(db_session, ch.id)[0]
        assert delivery.status == NotificationDeliveryStatus.FAILED  # permanent
        assert "not a public host" in (delivery.last_error or "")

    def test_success_resets_consecutive_failures(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        ch.consecutive_failures = 3
        db_session.add(ch)
        db_session.commit()
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        with patch.object(
            notifications, "_client_for", new=_client_factory(_http_returning(204))
        ):
            _deliver_due()

        db_session.refresh(ch)
        assert ch.consecutive_failures == 0

    def test_channel_auto_disabled_after_threshold(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        # One short of the threshold; a single terminal failure should trip it.
        ch.consecutive_failures = notifications._CIRCUIT_BREAKER_THRESHOLD - 1
        db_session.add(ch)
        db_session.commit()

        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()
        d = _deliveries(db_session, ch.id)[0]
        d.attempts = notifications._MAX_ATTEMPTS - 1  # next failure is terminal
        db_session.add(d)
        db_session.commit()

        client = _http_returning(500)
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            _deliver_due()

        db_session.refresh(ch)
        assert ch.consecutive_failures >= notifications._CIRCUIT_BREAKER_THRESHOLD
        assert ch.enabled is False
        assert "auto-disabled" in (ch.last_error or "")

    def test_stuck_sending_is_reclaimed(self, printer: Printer, db_session):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
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
            attempted = _deliver_due()

        assert attempted == 1  # reclaimed and delivered
        db_session.refresh(d)
        assert d.status == NotificationDeliveryStatus.SENT

    def test_cancelling_a_delivery_job_withdraws_the_delivery(
        self, printer: Printer, db_session
    ):
        # Cancel means intent is withdrawn: the reconciler must not find the
        # delivery pending again and resend it.
        from sqlmodel import select

        from app.db.models import Job
        from app.modules.work import service
        from app.modules.work.reconciler import run_pass

        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()
        run_pass(JobKind.NOTIFICATIONS_DELIVER)
        job = db_session.exec(
            select(Job).where(Job.kind == JobKind.NOTIFICATIONS_DELIVER)
        ).one()

        service.cancel(
            job.id, actor=build_user(db_session, "notify-admin", superuser=True)
        )
        client = _http_returning(204)
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            _deliver_due()

        client.request.assert_not_called()
        db_session.expire_all()
        delivery = _deliveries(db_session, ch.id)[0]
        assert (delivery.status, delivery.last_error) == (
            NotificationDeliveryStatus.FAILED,
            "cancelled",
        )


def _delivery(session, channel, **fields):
    row = NotificationDelivery(
        channel_id=channel.id,
        event_type=NotificationEventType.PRINTER_OFFLINE,
        **fields,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class TestDeliverySource:
    def _pending(self, session, *, now=None, limit=10):
        from app.core.time import utcnow

        return notifications.DeliverySource().pending(
            session, now=now or utcnow(), limit=limit
        )

    def test_a_due_delivery_is_pending_on_its_channels_partition(self, db_session):
        from app.core.time import utcnow

        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        row = _delivery(
            db_session,
            ch,
            status=NotificationDeliveryStatus.PENDING,
            next_retry_at=utcnow(),
        )

        (item,) = self._pending(db_session)

        assert item.subject_key == f"channel/{ch.id}/delivery/{row.id}"
        # One channel's rate limit and ordering never hold up another's.
        assert notifications._channel_partition(item.subject_key) == str(ch.id)

    def test_a_delivery_backing_off_is_not_yet_pending(self, db_session):
        from datetime import timedelta

        from app.core.time import utcnow

        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        _delivery(
            db_session,
            ch,
            status=NotificationDeliveryStatus.PENDING,
            next_retry_at=utcnow() + timedelta(minutes=5),
        )

        assert self._pending(db_session) == []

    def test_a_send_abandoned_mid_flight_is_pending_again(self, db_session):
        from datetime import timedelta

        from app.core.time import utcnow

        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        _delivery(
            db_session,
            ch,
            status=NotificationDeliveryStatus.SENDING,
            updated_at=utcnow()
            - timedelta(seconds=notifications._STUCK_SENDING_SECONDS + 1),
        )

        assert len(self._pending(db_session)) == 1

    def test_a_send_in_flight_is_not_pending(self, db_session):
        from app.core.time import utcnow

        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        _delivery(
            db_session,
            ch,
            status=NotificationDeliveryStatus.SENDING,
            updated_at=utcnow(),
        )

        assert self._pending(db_session) == []

    @pytest.mark.parametrize(
        "status",
        [NotificationDeliveryStatus.SENT, NotificationDeliveryStatus.FAILED],
    )
    def test_a_finished_delivery_is_not_pending(self, db_session, status):
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        _delivery(db_session, ch, status=status)

        assert self._pending(db_session) == []

    def test_is_bounded_by_the_lanes_room(self, db_session):
        from app.core.time import utcnow

        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        for _ in range(3):
            _delivery(
                db_session,
                ch,
                status=NotificationDeliveryStatus.PENDING,
                next_retry_at=utcnow(),
            )

        assert len(self._pending(db_session, limit=2)) == 2

    def test_next_due_is_the_earliest_retry(self, db_session):
        from datetime import timedelta

        from app.core.time import ensure_utc, utcnow

        now = utcnow()
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        for minutes in (10, 3):
            _delivery(
                db_session,
                ch,
                status=NotificationDeliveryStatus.PENDING,
                next_retry_at=now + timedelta(minutes=minutes),
            )

        due = notifications.DeliverySource().next_due(db_session, now=now)

        assert due is not None
        assert abs((ensure_utc(due) - (now + timedelta(minutes=3))).total_seconds()) < 1

    def test_nothing_backing_off_has_no_due_time(self, db_session):
        from app.core.time import utcnow

        assert notifications.DeliverySource().next_due(db_session, now=utcnow()) is None


class TestEnqueueStorageEvent:
    """Audit outcomes reach the channels subscribed to them, and leave promptly."""

    def _enqueue(self, session, event=NotificationEventType.STORAGE_REGRESSION, **kw):
        """How many deliveries it added (the rows it returns)."""
        added = notifications.enqueue_storage_event(
            session,
            event,
            run_id=7,
            mode="quick",
            summary={"new": 2, "bogus": 9},
            duration_s=-1,
            **kw,
        )
        assert all(row in session.new for row in added)
        return len(added)

    def test_a_storage_event_is_delivered_once_its_transaction_commits(
        self, db_session
    ):
        # Regression: the storage outbox recorded deliveries without nudging
        # delivery, so an alert waited for the next periodic reconcile.
        from tests.integration.api.v1._ingest_assertions import drain_work

        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.STORAGE_REGRESSION])

        assert self._enqueue(db_session) == 1
        db_session.commit()
        client = _http_returning(204)
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            drain_work()

        db_session.expire_all()
        (delivery,) = _deliveries(db_session, ch.id)
        assert delivery.status == NotificationDeliveryStatus.SENT
        context = json.loads(delivery.context_json)
        assert (context["audit_run_id"], context["audit_mode"]) == (7, "quick")
        # Only the known summary buckets, and never a negative duration.
        assert context["summary"]["new"] == 2
        assert "bogus" not in context["summary"]
        assert context["duration_s"] == 0

    def test_reaches_only_channels_subscribed_to_the_event(self, db_session):
        set_notifications_enabled(db_session, True)
        _channel(db_session, events=[NotificationEventType.STORAGE_RECOVERY])

        assert self._enqueue(db_session) == 0

    def test_can_be_limited_to_named_channels(self, db_session):
        set_notifications_enabled(db_session, True)
        wanted = _channel(
            db_session, events=[NotificationEventType.STORAGE_REGRESSION], name="a"
        )
        _channel(
            db_session, events=[NotificationEventType.STORAGE_REGRESSION], name="b"
        )

        assert self._enqueue(db_session, channel_ids=[wanted.id]) == 1
        db_session.commit()
        assert [d.channel_id for d in _deliveries(db_session)] == [wanted.id]

    def test_is_silent_while_notifications_are_off(self, db_session):
        _channel(db_session, events=[NotificationEventType.STORAGE_REGRESSION])

        assert self._enqueue(db_session) == 0

    def test_refuses_an_event_that_is_not_a_storage_event(self, db_session):
        with pytest.raises(ValueError, match="storage_event_required"):
            self._enqueue(db_session, NotificationEventType.PRINT_COMPLETED)


class TestPruneDeliveries:
    def test_prune_deliveries_removes_old_terminal_rows(self, db_session):
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


class TestSerializeChannel:
    def test_serialize_channel_handles_all_corrupt_json_fields(
        self, make_notification_channel
    ):
        ch = make_notification_channel(
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

    def test_serialize_channel_returns_nonsecret_config_plainly(
        self, make_notification_channel
    ):
        ch = make_notification_channel(
            name="x",
            target=NotificationTarget.NTFY,
            config_json=json.dumps({"topic": "my-topic", "token": "secret-tok"}),
            events_json=json.dumps([NotificationEventType.PRINTER_OFFLINE.value]),
        )
        out = notifications.serialize_channel(ch)
        assert out["config"]["topic"] == "my-topic"  # non-secret: passed through
        assert out["config"]["token"] == "********"  # secret: masked
        assert out["config_flags"]["has_token"] is True


class TestUpdateChannel:
    def test_update_channel_recovers_from_corrupt_stored_events_json(self, db_session):
        from app.modules.notifications.notifications import update_channel

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
        self,
        db_session,
    ):
        from app.modules.notifications.notifications import update_channel

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
        self,
        db_session,
    ):
        from app.modules.notifications.notifications import update_channel

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

    def test_update_channel_overwrites_nonsecret_config_key(self, db_session):
        from app.modules.notifications.notifications import update_channel

        ch = notifications.create_channel(
            db_session,
            name="x",
            target=NotificationTarget.NTFY,
            config={"topic": "old-topic"},
            events=["print_completed"],
        )
        updated = update_channel(db_session, ch, config={"topic": "new-topic"})
        assert json.loads(updated.config_json)["topic"] == "new-topic"


class TestListRecentDeliveries:
    def test_list_recent_deliveries_returns_serialized_rows(
        self, printer: Printer, db_session
    ):
        set_notifications_enabled(db_session, True)
        ch = _channel(db_session, events=[NotificationEventType.PRINTER_OFFLINE])
        notifications.enqueue_for_event(
            db_session, NotificationEventType.PRINTER_OFFLINE, printer_id=printer.id
        )
        db_session.commit()

        rows = notifications.list_recent_deliveries(db_session)

        assert len(rows) == 1
        assert rows[0]["channel_id"] == ch.id
        assert rows[0]["status"] == NotificationDeliveryStatus.PENDING.value


class TestSendTest:
    @pytest.mark.asyncio
    async def test_send_test_channel_not_found(self):
        result = await notifications.send_test(999_999_999)
        assert result == {"ok": False, "error": "channel not found"}

    @pytest.mark.asyncio
    async def test_send_test_recovers_from_corrupt_config_json(self, db_session):
        ch = _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])
        ch.config_json = "not json"
        db_session.add(ch)
        db_session.commit()

        # Corrupt config loads as {}, then webhook rendering fails for lack of a url.
        result = await notifications.send_test(ch.id)
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_send_test_render_error_missing_required_config(self, db_session):
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
    async def test_send_test_blocked_non_public_host(self, db_session):
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
    async def test_send_test_http_error_response(self, db_session):
        ch = _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])
        client = _http_returning(500, "server exploded")
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            result = await notifications.send_test(ch.id)
        assert result["ok"] is False
        assert "HTTP 500" in result["error"]

    @pytest.mark.asyncio
    async def test_send_test_network_exception(self, db_session):
        ch = _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])
        client = MagicMock()
        client.request = AsyncMock(side_effect=RuntimeError("dns failure"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(notifications, "_client_for", new=_client_factory(client)):
            result = await notifications.send_test(ch.id)
        assert result["ok"] is False
        assert "dns failure" in result["error"]


class TestRecordChannelTest:
    def test_record_channel_test_noop_when_channel_missing(self):
        # Must not raise even though the channel id doesn't exist.
        notifications._record_channel_test(999_999_999, True, None)


class TestEvent:
    def test_cancelled_emits_distinct_event(self, db_session, hub):
        set_notifications_enabled(db_session, True)
        p = build_printer(
            db_session,
            name="Ender",
            moonraker_url="http://x",
            status=PrinterStatus.PRINTING,
        )
        # Channel only wants completions, not cancellations -> nothing enqueued.
        _channel(db_session, events=[NotificationEventType.PRINT_COMPLETED])

        stats = {"filename": "y.gcode"}
        hub._sync_active_job_db(p.id, "cancelled", "y.gcode", 0.4, stats)
        db_session.expire_all()
        assert _deliveries(db_session) == []
