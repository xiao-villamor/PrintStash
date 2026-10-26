"""Queue hints coalesce without losing arrivals or blocking shutdown."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.runtime.engine.dbos_notifications import ClaimRetry, QueueSignals


class TestQueueSignals:
    def test_retains_a_notification_before_waiting(self):
        signals = QueueSignals()
        signals.notify("ready")

        with ThreadPoolExecutor(1) as worker:
            result = worker.submit(signals.wait, "ready", threading.Event(), 30)
            assert result.result(timeout=1) is False

    def test_wakes_an_already_waiting_dispatcher(self):
        signals = QueueSignals()
        with ThreadPoolExecutor(1) as worker:
            result = worker.submit(signals.wait, "ready", threading.Event(), 30)

            signals.notify("ready")

            assert result.result(timeout=1) is False

    def test_coalesces_repeated_notifications(self):
        signals = QueueSignals()
        signals.notify("ready")
        signals.notify("ready")
        signals.wait("ready", threading.Event(), 30)
        started = time.monotonic()

        stopped = signals.wait("ready", threading.Event(), 0.03)

        assert stopped is False
        assert time.monotonic() - started >= 0.025

    def test_missing_notifications_reach_the_recovery_deadline(self):
        signals = QueueSignals()
        started = time.monotonic()

        stopped = signals.wait("ready", threading.Event(), 0.03)

        assert stopped is False
        assert time.monotonic() - started >= 0.025

    def test_shutdown_interrupts_a_long_wait(self):
        signals = QueueSignals()
        stop = threading.Event()
        with ThreadPoolExecutor(1) as worker:
            result = worker.submit(signals.wait, "ready", stop, 30)

            stop.set()

            assert result.result(timeout=2) is True

    @pytest.mark.parametrize("seconds", [0, -1], ids=["zero", "negative"])
    def test_rejects_a_nonpositive_recovery_interval(self, seconds):
        with pytest.raises(ValueError, match="positive"):
            QueueSignals().wait("ready", threading.Event(), seconds)


class TestClaimRetry:
    def test_idle_has_no_failure_delay(self):
        assert ClaimRetry().delay is None

    def test_first_failure_uses_a_short_retry(self):
        retry = ClaimRetry()

        retry.failed()

        assert retry.delay == 0.25

    @pytest.mark.parametrize(
        "previous", [1.0, 2.0], ids=["reaches-cap", "stays-capped"]
    )
    def test_persistent_failures_have_a_bounded_delay(self, previous):
        retry = ClaimRetry()
        retry.delay = previous

        retry.failed()

        assert retry.delay == 2.0

    def test_success_resets_failure_backoff(self):
        retry = ClaimRetry()
        retry.failed()

        retry.succeeded()

        assert retry.delay is None


class TestNotificationMode:
    def test_disabled_mode_does_not_accumulate_hints(self):
        hints = QueueSignals(enabled=False)

        hints.notify("queue")

        assert hints.snapshot() == {}

    def test_restart_discards_old_hint_state(self):
        hints = QueueSignals()
        hints.notify("queue")

        hints.configure(enabled=False)

        assert hints.snapshot() == {}

    def test_counts_dispatch_failures_separately(self):
        hints = QueueSignals()

        hints.failed("queue")

        assert hints.snapshot()["queue"]["failed_passes"] == 1
