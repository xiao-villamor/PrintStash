"""The optional diagnostic preserves transfer and admission behavior."""

import json
import socket
from types import SimpleNamespace

import pytest

from app.runtime import native_admission
from scripts import compute_pipeline_trace as trace


def records(directory):
    return [
        json.loads(line)
        for path in directory.glob("trace-*.jsonl")
        for line in path.read_text().splitlines()
    ]


class TestTransportTrace:
    def test_counts_partial_socket_transfers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trace, "_directory", tmp_path)

        class Connection:
            def recv(self, count):
                return b"abc"[:count]

            def send(self, payload):
                return min(2, len(payload))

        probe = trace.SocketProbe(Connection())

        assert probe.recv(2) == b"ab"
        assert probe.send(b"abcd") == 2
        assert probe.bytes == 4
        assert probe.calls == 2
        assert probe.timeouts == 0
        assert probe.call_ns > 0

    def test_preserves_socket_timeout(self):
        class Connection:
            def recv(self, count):
                raise socket.timeout("waiting for producer")

        probe = trace.SocketProbe(Connection())

        with pytest.raises(socket.timeout, match="waiting for producer"):
            probe.recv(1)
        assert probe.timeouts == 1
        assert probe.bytes == 0

    def test_emits_transfer_bytes_without_payload(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trace, "_directory", tmp_path)
        owner = SimpleNamespace(
            send_body=lambda connection, body: connection.send(body)
        )
        connection = SimpleNamespace(send=lambda body: len(body))
        trace.instrument_transport(owner, "send_body")

        assert owner.send_body(connection, b"private data") == 12

        [row] = records(tmp_path)
        assert row["bytes"] == 12
        assert row["calls"] == 1
        assert row["socket_ns"] <= row["duration_ns"]
        assert "private data" not in json.dumps(row)


class TestPoolTrace:
    def test_observes_real_admission_without_changing_custody(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(trace, "_directory", tmp_path)
        cls = native_admission.LocalResourcePool
        # Restore the originals after instrument_pool replaces the descriptors.
        for name in ("_coordinator", "reserve", "_available"):
            monkeypatch.setattr(cls, name, cls.__dict__[name])
        trace.instrument_pool(native_admission)
        pool = cls(tmp_path / "pool")
        amount = native_admission.Resources(1, 10)

        with pool.reserve(amount, amount, checkpoint=lambda: None):
            [row] = records(tmp_path)
            assert row["stage"] == "pool_admission"
            assert row["requested_bytes"] == 10
            assert row["lock_acquisitions"] >= 2
            assert row["polls"] == 1
            assert len(list(pool.directory.glob("*.ticket"))) == 1

        # Closed receipts are reclaimed on the next admission; no credit remains.
        with pool.reserve(amount, amount, checkpoint=lambda: None):
            assert len(list(pool.directory.glob("*.ticket"))) == 1
        lifetime = records(tmp_path)[-1]
        assert lifetime["stage"] == "pool_lifetime"
        assert lifetime["acquired_ns"] is not None
        assert lifetime["outcome"] == "returned"

    def test_preserves_cancellation_during_admission(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trace, "_directory", tmp_path)
        cls = native_admission.LocalResourcePool
        for name in ("_coordinator", "reserve", "_available"):
            monkeypatch.setattr(cls, name, cls.__dict__[name])
        trace.instrument_pool(native_admission)
        pool = cls(tmp_path / "pool")

        def withdrawn():
            raise RuntimeError("withdrawn")

        with (
            pytest.raises(RuntimeError, match="withdrawn"),
            pool.reserve(
                native_admission.Resources(1, 1),
                native_admission.Resources(1, 1),
                checkpoint=withdrawn,
            ),
        ):
            pytest.fail("withdrawn admission entered")
        [row] = records(tmp_path)
        assert row["stage"] == "pool_lifetime"
        assert row["acquired_ns"] is None
        assert row["outcome"] == "error"


class TestCancellationTrace:
    def test_omits_throttled_checkpoint_trace_writes(self, tmp_path, monkeypatch):
        from app.core import cancellation

        monkeypatch.setattr(trace, "_directory", tmp_path)
        monkeypatch.setattr(cancellation.time, "monotonic", lambda: 10.0)
        monkeypatch.setattr(cancellation._Probe, "check", cancellation._Probe.check)
        trace.timed(cancellation._Probe, "check", "cancellation_check")
        probe = cancellation._Probe(lambda: False)

        probe.check()
        probe.check()

        [row] = records(tmp_path)
        assert row["stage"] == "cancellation_check"
        assert row["outcome"] == "returned"

    def test_traces_cancellation_without_swallowing_it(self, tmp_path, monkeypatch):
        from app.core import cancellation

        monkeypatch.setattr(trace, "_directory", tmp_path)
        monkeypatch.setattr(cancellation._Probe, "check", cancellation._Probe.check)
        trace.timed(cancellation._Probe, "check", "cancellation_check")
        probe = cancellation._Probe(lambda: True)

        with pytest.raises(cancellation.OperationCancelled):
            probe.check()

        [row] = records(tmp_path)
        assert row["outcome"] == "error"
        assert row["failure"] == "OperationCancelled"
