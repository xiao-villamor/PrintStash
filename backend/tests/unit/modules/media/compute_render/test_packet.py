"""Reaped-parser packets preserve bounded ownership and withdrawal through IPC."""

import gc
import json
import struct
import time
from unittest.mock import Mock

import pytest

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.modules.media import compute_render
from app.runtime.compute.contracts import ComputeUnavailable, Reason
from app.runtime.compute.protocol import Priority

WIDTH, HEIGHT = 320, 240


def packet(mode="canonical", **changes):
    header = {
        "vertices": 3,
        "faces": 1,
        "normals": 3,
        "width": WIDTH,
        "height": HEIGHT,
        "views": [None],
        "matte": False,
    }
    body = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    if mode == "canonical":
        body += struct.pack("<6q9d", 0, 1, 2, 0, 1, 2, *([0, 0, 1] * 3))
    else:
        header["normal_mode"] = mode
        body += struct.pack("<3I", 0, 1, 2)
        if mode == "gpu-angle-csr-v1":
            body += struct.pack("<3I", 0, 1, 2)
    header.update(changes)
    encoded = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack("!I", len(encoded)) + encoded + body


def reply(*, alpha=255):
    rgba = bytearray(WIDTH * HEIGHT * 4)
    offset = (WIDTH + 1) * 4
    rgba[offset : offset + 4] = bytes([190, 80, 40, alpha])
    return b"\x01" + struct.pack("!III", WIDTH, HEIGHT, 1) + rgba


@pytest.fixture
def gpu(monkeypatch):
    monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
    exchange = Mock(return_value=reply())
    monkeypatch.setattr(compute_render.client, "exchange_render", exchange)
    return exchange


def render(payload=None, *, deadline=None):
    return compute_render.render_packet(
        packet() if payload is None else payload,
        width=WIDTH,
        height=HEIGHT,
        deadline=time.monotonic() + 30 if deadline is None else deadline,
    )


class TestPacketDispatch:
    @pytest.mark.parametrize("mode", ["canonical", "gpu-angle-csr-v1", "gpu-visual-v2"])
    def test_retains_immutable_packet_through_exchange(self, mode, gpu):
        payload = packet(mode)
        deadline = time.monotonic() + 30

        result = render(payload, deadline=deadline)

        request, body = gpu.call_args.args
        assert body.obj is payload
        assert body.readonly
        assert request.geometry_bytes == len(body)
        assert request.priority is Priority.BACKGROUND
        assert request.deadline == deadline
        assert request.units == 1
        assert gpu.call_args.kwargs["response_limit"] == 14 + WIDTH * HEIGHT * 16
        assert result.rgba == reply()[13:]
        assert type(result.rgba) is bytes

    def test_respects_broker_deadline_ceiling(self, gpu, monkeypatch):
        monkeypatch.setattr(time, "monotonic", lambda: 100.0)

        render(deadline=1000.0)

        assert gpu.call_args.args[0].deadline == 400.0

    def test_avoids_copying_the_full_pixel_envelope(self, gpu):
        class UnsliceableReply(bytes):
            def __getitem__(self, key):
                if isinstance(key, slice):
                    raise AssertionError("full response envelope was sliced")
                return super().__getitem__(key)

        gpu.return_value = UnsliceableReply(reply())

        result = render()

        assert result.rgba == reply()[13:]

    def test_owns_pixels_after_the_response_owner_is_released(self, gpu):
        retired = []

        class ReplyOwner(bytes):
            def __del__(self):
                retired.append(True)

        gpu.side_effect = lambda *args, **kwargs: ReplyOwner(reply())

        result = render()
        gc.collect()

        assert retired == [True]
        assert type(result.rgba) is bytes
        assert result.rgba == reply()[13:]

    @pytest.mark.parametrize("alpha", [1, 128, 255])
    def test_accepts_one_visible_pixel(self, alpha, gpu):
        gpu.return_value = reply(alpha=alpha)

        assert render().rgba == reply(alpha=alpha)[13:]

    def test_routes_unavailable_compute_to_guarded_fallback(self, gpu, monkeypatch):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: False)

        assert render() is None
        gpu.assert_not_called()

    @pytest.mark.parametrize(
        "reason",
        [
            Reason.CAPACITY,
            Reason.DEVICE_FAILED,
            Reason.UNQUALIFIED,
            Reason.BROKER_UNAVAILABLE,
        ],
    )
    def test_routes_compute_refusal_to_guarded_fallback(self, reason, gpu):
        gpu.side_effect = ComputeUnavailable(reason)

        assert render() is None

    def test_treats_broker_input_rejection_as_terminal(self, gpu):
        gpu.side_effect = ComputeUnavailable(Reason.INVALID_INPUT)

        with pytest.raises(ValueError, match="compute_geometry_invalid"):
            render()

    @pytest.mark.parametrize(
        "response",
        [
            b"",
            b"\x02invalid",
            b"\x01short",
            reply()[:-1],
            reply() + b"extra",
            b"\x01" + struct.pack("!III", WIDTH, HEIGHT, 2) + bytes(WIDTH * HEIGHT * 8),
            b"\x01" + struct.pack("!III", HEIGHT, WIDTH, 1) + reply()[13:],
            reply(alpha=0),
            b"\x00"
            + struct.pack("!III", WIDTH * 2, HEIGHT * 2, 1)
            + bytes(WIDTH * HEIGHT * 16),
        ],
        ids=[
            "empty",
            "tag",
            "short-header",
            "short-pixels",
            "extra-pixels",
            "two-frames",
            "wrong-dimensions",
            "blank",
            "unfinalized",
        ],
    )
    def test_refuses_unusable_gpu_output(self, response, gpu):
        gpu.return_value = response

        assert render() is None

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"x",
            struct.pack("!I", 4097),
            struct.pack("!I", 4000) + b"{}",
            packet()[:-1],
            packet() + b"extra",
            packet(width=WIDTH + 1),
            packet(views=[None, None]),
            packet(views=[[[1, 0, 0], [0, 1, 0], [0, 0, 1]]]),
            packet(matte=True),
            packet(vertices=True),
            bytearray(packet()),
            memoryview(bytearray(packet())).toreadonly(),
        ],
        ids=[
            "empty",
            "prefix",
            "large-header",
            "truncated-header",
            "short-body",
            "extra-body",
            "dimensions",
            "two-views",
            "explicit-view",
            "matte",
            "invalid-shape",
            "mutable",
            "readonly-mutable",
        ],
    )
    def test_rejects_invalid_prepared_packet_before_dispatch(self, payload, gpu):
        with pytest.raises(ValueError):
            render(payload)
        gpu.assert_not_called()


class TestPacketWithdrawal:
    @pytest.mark.parametrize(
        "reason,error",
        [(Reason.DEADLINE, TimeoutError), (Reason.CANCELLED, OperationCancelled)],
    )
    def test_propagates_broker_withdrawal(self, reason, error, gpu):
        gpu.side_effect = ComputeUnavailable(reason)

        with pytest.raises(error):
            render()

    def test_refuses_expired_work_before_dispatch(self, gpu):
        with pytest.raises(TimeoutError, match="thumbnail_deadline"):
            render(deadline=time.monotonic() - 1)
        gpu.assert_not_called()

    def test_refuses_cancelled_work_before_dispatch(self, gpu):
        with cancellation_scope(lambda: True):
            with pytest.raises(OperationCancelled):
                render()
        gpu.assert_not_called()

    def test_withdraws_pixels_completed_after_the_deadline(self, gpu, monkeypatch):
        clock = [10.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        def exchange(*args, **kwargs):
            clock[0] = 20.0
            return reply()

        gpu.side_effect = exchange
        with pytest.raises(TimeoutError, match="thumbnail_deadline"):
            render(deadline=15.0)

    @pytest.mark.parametrize("refused", [False, True], ids=["reply", "refusal"])
    def test_withdraws_pixels_after_cancellation_during_dispatch(self, gpu, refused):
        withdrawn = [False]

        def exchange(*args, **kwargs):
            withdrawn[0] = True
            if refused:
                raise ComputeUnavailable(Reason.DEVICE_FAILED)
            return reply()

        gpu.side_effect = exchange
        with cancellation_scope(lambda: withdrawn[0]):
            with pytest.raises(OperationCancelled):
                render()


class TestPacketPolling:
    def test_fragmented_transport_reuses_the_cancellation_probe(
        self, monkeypatch, tmp_path
    ):
        from contextlib import contextmanager

        from app.runtime.compute import client

        monkeypatch.setattr(time, "monotonic", lambda: 10.0)
        monkeypatch.setattr(client, "available", lambda **kwargs: True)
        monkeypatch.setattr(client, "directory", lambda: tmp_path)
        probes = []
        io_probes = []

        def withdrawn():
            probes.append(True)
            return False

        def frame(data):
            return struct.pack("!I", len(data)) + data

        incoming = bytearray(
            frame(b'{"ready":true}')
            + frame(b'{"geometry":"upload"}')
            + frame(b"\x00" + reply())
        )

        class Connection:
            def settimeout(self, seconds):
                pass

            def sendall(self, data):
                pass

            def send(self, data):
                io_probes.append(len(probes))
                return min(len(data), 1024)

            def recv(self, size):
                io_probes.append(len(probes))
                data = bytes(incoming[: min(size, 1024)])
                del incoming[: len(data)]
                return data

        @contextmanager
        def connect(*args, **kwargs):
            kwargs["checkpoint"]()
            yield Connection()

        monkeypatch.setattr(client, "_connect", connect)
        payload = packet("gpu-visual-v2", faces=20_000)[:-12] + bytes(20_000 * 12)
        with cancellation_scope(withdrawn):
            result = render(payload, deadline=11.0)

        assert result.rgba == reply()[13:]
        assert len(io_probes) > 500
        assert len(set(io_probes)) == 1
        assert len(probes) == io_probes[-1] + 1  # Forced result boundary.
        assert not incoming
