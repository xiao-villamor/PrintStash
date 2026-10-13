"""Cancellation is scoped, latched and cheap between durable checks."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import cancellation


class TestCancellationScope:
    def test_unscoped_work_continues(self):
        cancellation.checkpoint()

    def test_throttles_durable_checks(self, monkeypatch):
        now = [0.0]
        calls = []
        monkeypatch.setattr(cancellation.time, "monotonic", lambda: now[0])
        with cancellation.cancellation_scope(lambda: bool(calls.append(now[0]))):
            cancellation.checkpoint()
            now[0] = 0.4
            cancellation.checkpoint()
            now[0] = 0.5
            cancellation.checkpoint()
        assert calls == [0, 0.5]

    def test_a_forced_checkpoint_probes_inside_the_throttle(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cancellation.time, "monotonic", lambda: 0.0)
        with cancellation.cancellation_scope(lambda: bool(calls.append(1))):
            cancellation.checkpoint()
            cancellation.checkpoint(force=True)
        assert calls == [1, 1]

    def test_latches_withdrawal(self, monkeypatch):
        stopped = [True]
        monkeypatch.setattr(cancellation.time, "monotonic", lambda: 0)
        with cancellation.cancellation_scope(lambda: stopped[0]):
            with pytest.raises(cancellation.OperationCancelled):
                cancellation.checkpoint()
            stopped[0] = False
            with pytest.raises(cancellation.OperationCancelled):
                cancellation.checkpoint()
        cancellation.checkpoint()

    def test_restores_outer_scope_after_exception(self):
        with cancellation.cancellation_scope(lambda: True):
            with pytest.raises(ValueError):
                with cancellation.cancellation_scope(lambda: False):
                    cancellation.checkpoint()
                    raise ValueError("operation failed")
            with pytest.raises(cancellation.OperationCancelled):
                cancellation.checkpoint()
        cancellation.checkpoint()

    def test_isolates_concurrent_operations(self):
        barrier = threading.Barrier(2)

        def run(stopped):
            with cancellation.cancellation_scope(lambda: stopped):
                barrier.wait(timeout=2)
                try:
                    cancellation.checkpoint()
                    return False
                except cancellation.OperationCancelled:
                    return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, True)
            second = pool.submit(run, False)
            assert first.result(timeout=3)
            assert not second.result(timeout=3)
        cancellation.checkpoint()

    def test_forces_final_result_check(self, monkeypatch):
        stopped = [False]
        monkeypatch.setattr(cancellation.time, "monotonic", lambda: 0)
        with cancellation.cancellation_scope(lambda: stopped[0]):
            cancellation.checkpoint()
            stopped[0] = True
            with pytest.raises(cancellation.OperationCancelled):
                cancellation.checkpoint(force=True)
