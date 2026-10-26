"""Queue hints coalesce without losing arrivals or blocking shutdown."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.runtime.engine.dbos_notifications import QueueSignals


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
