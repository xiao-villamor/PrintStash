"""CPU fallback retains cancellation and the caller's original deadline."""

import json
from pathlib import Path

import pytest
from printstash_core.inference import EmbeddingError, InferenceContext

from app.runtime.compute import client


@pytest.fixture
def broker_startup(monkeypatch):
    from types import SimpleNamespace

    state = SimpleNamespace(now=10.0, sockets=[], launched=False, terminated=False)

    class Connection:
        closed = False

        def settimeout(self, seconds):
            pass

        def connect(self, address):
            raise ConnectionRefusedError()

        def close(self):
            self.closed = True

    class Child:
        def poll(self):
            return None

        def terminate(self):
            state.terminated = True

        def wait(self, timeout=None):
            pass

    def connection(*args):
        value = Connection()
        state.sockets.append(value)
        return value

    def launch(*args, **kwargs):
        state.launched = True
        return Child()

    def advance(seconds):
        state.now += seconds

    monkeypatch.setattr(client.socket, "socket", connection)
    monkeypatch.setattr(client.subprocess, "Popen", launch)
    monkeypatch.setattr(client.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(client.time, "sleep", advance)
    return state


class TestConnect:
    def test_withdraws_during_launch_lock_contention(
        self, broker_startup, tmp_path, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        def locked(*args):
            raise BlockingIOError()

        def checkpoint():
            if broker_startup.now > 10:
                raise OperationCancelled()

        monkeypatch.setattr(client.fcntl, "flock", locked)

        with pytest.raises(OperationCancelled):
            client._connect(tmp_path, 40, checkpoint=checkpoint)

        assert broker_startup.now < 10.1
        assert all(connection.closed for connection in broker_startup.sockets)
        assert not broker_startup.launched

    def test_withdraws_without_terminating_shared_owner(self, broker_startup, tmp_path):
        from app.core.cancellation import OperationCancelled

        def checkpoint():
            if broker_startup.launched:
                raise OperationCancelled()

        with pytest.raises(OperationCancelled):
            client._connect(tmp_path, 40, checkpoint=checkpoint)

        assert broker_startup.launched
        assert not broker_startup.terminated
        assert all(connection.closed for connection in broker_startup.sockets)
        assert broker_startup.now < 10.1

    def test_retains_original_bootstrap_deadline(
        self, broker_startup, tmp_path, monkeypatch
    ):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        def locked(*args):
            raise BlockingIOError()

        monkeypatch.setattr(client.fcntl, "flock", locked)

        with pytest.raises(ComputeUnavailable) as error:
            client._connect(tmp_path, 10.01)

        assert error.value.reason is Reason.DEADLINE
        assert broker_startup.now < 10.1
        assert not broker_startup.launched


class TestFallback:
    @pytest.mark.parametrize("reason", ["capacity", "unqualified", "device_failed"])
    def test_returns_valid_work_to_cpu(self, monkeypatch, reason):
        monkeypatch.setattr(client, "available", lambda: True)
        monkeypatch.setattr(
            client,
            "exchange",
            lambda request, **kwargs: json.dumps({"error": reason}).encode(),
        )
        context = InferenceContext.bounded(2)
        deadline = context.deadline

        assert client.infer(Path("/models"), "fixture", 1, b"{}", context) is None
        assert context.deadline == deadline
        assert context.remaining() > 0

    def test_never_turns_cancellation_into_cpu_work(self, monkeypatch):
        monkeypatch.setattr(client, "available", lambda: True)
        context = InferenceContext.bounded(2, cancelled=lambda: True)

        with pytest.raises(EmbeddingError, match="inference_cancelled"):
            client.infer(Path("/models"), "fixture", 1, b"{}", context)


class TestRenderPreview:
    @pytest.mark.parametrize(
        "policy,mode,render,expected",
        [
            ("preview", "auto", True, True),
            ("qualified", "auto", True, False),
            ("preview", "cpu", True, False),
            ("preview", "auto", False, False),
        ],
        ids=["preview", "unqualified", "cpu", "inference"],
    )
    def test_requires_explicit_render_evaluation(
        self, tmp_path, monkeypatch, policy, mode, render, expected
    ):
        from app.core.config import _overlay

        _overlay["compute_mode"] = mode
        _overlay["compute_render_policy"] = policy
        monkeypatch.setenv(client.ROOT_ENV, str(tmp_path))
        monkeypatch.setattr(client, "_disabled_until", 0)
        monkeypatch.setattr(client.importlib.util, "find_spec", lambda name: object())
        assert client.available(render=render) is expected


class TestWarmRender:
    def test_cools_failed_owner_startup(self, monkeypatch):
        from app.runtime.compute.contracts import Reason

        monkeypatch.setattr(client, "available", lambda **kwargs: True)
        monkeypatch.setattr(
            client, "status", lambda: client.cpu_status(Reason.DEVICE_FAILED)
        )
        monkeypatch.setattr(client, "_disabled_until", 0)
        client.warm_render()
        assert client._disabled_until > 0

    def test_cpu_deployment_does_not_start_an_owner(self, monkeypatch):
        monkeypatch.setattr(client, "available", lambda **kwargs: False)

        def forbidden():
            pytest.fail("CPU deployment started an optional broker")

        monkeypatch.setattr(client, "status", forbidden)
        assert client.warm_render() is None


class TestRenderResponseBudget:
    @pytest.mark.parametrize("stage", ["admission", "geometry", "pixels"], ids=str)
    def test_rejects_oversized_responses_before_reading_body(
        self, monkeypatch, tmp_path, stage
    ):
        import struct
        import time
        from contextlib import contextmanager

        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from app.runtime.compute.protocol import BinaryRenderRequest, Priority

        def frame(payload):
            return struct.pack("!I", len(payload)) + payload

        preceding = {
            "admission": b"",
            "geometry": frame(b'{"ready":true}'),
            "pixels": frame(b'{"ready":true}') + frame(b'{"geometry":"cached"}'),
        }[stage]
        payload = bytearray(preceding + struct.pack("!I", 4097))

        class Connection:
            def settimeout(self, seconds):
                pass

            def sendall(self, data):
                pass

            def send(self, data):
                return len(data)

            def recv(self, size):
                assert payload, "oversized body must not be read"
                result = bytes(payload[:size])
                del payload[:size]
                return result

        @contextmanager
        def connect(*args, **kwargs):
            yield Connection()

        monkeypatch.setattr(client, "directory", lambda: tmp_path)
        monkeypatch.setattr(client, "_connect", connect)
        request = BinaryRenderRequest(
            deadline=time.monotonic() + 5,
            priority=Priority.BACKGROUND,
            request_id="bounded",
            geometry_key="a" * 64,
            geometry_bytes=1,
            header="{}",
            recipe="test",
            units=1,
        )
        with pytest.raises(ComputeUnavailable) as error:
            client.exchange_render(request, b"x", response_limit=30)
        assert error.value.reason is Reason.INVALID_INPUT
        assert not payload


@pytest.fixture
def status_transport(monkeypatch, tmp_path):
    import select
    from contextlib import contextmanager
    from types import SimpleNamespace

    from app.core.config import _overlay
    from app.runtime.compute.contracts import Reason

    _overlay["compute_mode"] = "auto"
    state = SimpleNamespace(now=10.0, connections=[], sends=[], reads=[], timeouts=[])
    payload = client.cpu_status(Reason.READY).model_dump_json().encode()

    class Connection:
        def settimeout(self, seconds):
            state.timeouts.append(seconds)

    @contextmanager
    def connect(root, deadline, *, start, checkpoint):
        state.connections.append((deadline, start))
        checkpoint()
        yield Connection()

    def send(connection, payload, deadline, *, checkpoint):
        checkpoint()
        state.sends.append((json.loads(payload), deadline))

    def receive(connection, deadline, *, checkpoint):
        checkpoint()
        state.reads.append(deadline)
        return payload

    monkeypatch.setattr(client, "directory", lambda: tmp_path)
    monkeypatch.setattr(client, "_connect", connect)
    monkeypatch.setattr(client, "send_admitted", send)
    monkeypatch.setattr(client, "receive", receive)
    monkeypatch.setattr(client, "cooling", lambda root: False)
    monkeypatch.setattr(client.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(client.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(select, "select", lambda *args: ([object()], [], []))
    return state


class TestStatus:
    def test_nonstarting_probe_never_launches_owner(
        self, broker_startup, tmp_path, monkeypatch
    ):
        from app.core.config import _overlay
        from app.runtime.compute.contracts import Reason

        _overlay["compute_mode"] = "auto"
        monkeypatch.setattr(client, "directory", lambda: tmp_path)
        monkeypatch.setattr(client.importlib.util, "find_spec", lambda name: object())
        state = client.status(start=False, deadline=11)
        assert state.device is None
        assert all(
            capability.reason is Reason.BROKER_UNAVAILABLE
            for capability in state.capabilities
        )
        assert not broker_startup.launched
        assert not (tmp_path / "launch.lock").exists()
        assert all(connection.closed for connection in broker_startup.sockets)

    @pytest.mark.parametrize(
        "deadline,expected",
        [(None, 15), (10.01, 10.01), (100, 15)],
        ids=["default", "remaining", "five-second-cap"],
    )
    def test_status_uses_bounded_original_deadline(
        self, status_transport, deadline, expected
    ):
        client.status(start=False, deadline=deadline)
        assert status_transport.connections == [(expected, False)]
        assert status_transport.sends == [({"operation": "status"}, expected)]
        assert status_transport.reads == [expected]
        assert max(status_transport.timeouts) <= expected - 10

    def test_default_status_retains_startup_behavior(self, status_transport):
        client.status()
        assert status_transport.connections == [(15, True)]

    @pytest.mark.parametrize(
        "priority,expected", [("background", True), ("interactive", False)]
    )
    def test_exchange_retains_request_priority_startup(
        self, status_transport, priority, expected
    ):
        from app.runtime.compute.protocol import InferenceRequest, Priority

        request = InferenceRequest(
            deadline=80,
            priority=Priority(priority),
            request_id="compatibility",
            directory="/models",
            model_key="fixture",
            threads=1,
            payload="e30=",
        )
        client.exchange(request)
        assert status_transport.connections == [(40, expected)]
        assert status_transport.reads == [80]

    def test_expired_status_does_not_connect(self, status_transport):
        with pytest.raises(TimeoutError, match="compute_deadline"):
            client.status(start=False, deadline=10)
        assert status_transport.connections == []

    def test_deadline_during_receive_propagates(self, status_transport, monkeypatch):
        def receive(connection, deadline, *, checkpoint):
            status_transport.now = deadline
            checkpoint()
            pytest.fail("expired status read completed")

        monkeypatch.setattr(client, "receive", receive)
        with pytest.raises(TimeoutError, match="compute_deadline"):
            client.status(start=False, deadline=10.01)

    def test_completed_late_reply_is_withheld(self, status_transport, monkeypatch):
        from app.runtime.compute.contracts import Reason

        payload = client.cpu_status(Reason.READY).model_dump_json().encode()

        def receive(connection, deadline, *, checkpoint):
            status_transport.now = deadline
            return payload

        monkeypatch.setattr(client, "receive", receive)
        with pytest.raises(TimeoutError, match="compute_deadline"):
            client.status(start=False, deadline=10.01)

    def test_probe_timeout_does_not_extend_original_deadline(
        self, status_transport, monkeypatch
    ):
        from app.runtime.compute.contracts import ComputeUnavailable, Reason

        def receive(connection, deadline, *, checkpoint):
            status_transport.now = deadline
            raise ComputeUnavailable(Reason.DEADLINE)

        monkeypatch.setattr(client, "receive", receive)
        state = client.status(start=False, deadline=100)
        assert state.device is None
        assert status_transport.now == 15

    def test_cancellation_during_receive_propagates(
        self, status_transport, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        stopped = False

        def checkpoint():
            if stopped:
                raise OperationCancelled()

        def receive(connection, deadline, **kwargs):
            nonlocal stopped
            stopped = True
            kwargs["checkpoint"]()

        monkeypatch.setattr(client, "receive", receive)
        with pytest.raises(OperationCancelled):
            client.status(start=False, deadline=11, checkpoint=checkpoint)
