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
