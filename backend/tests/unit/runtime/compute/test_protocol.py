"""Wire refusal and short I/O retain original cancellation and deadline reasons."""

import json
import socket
import struct
from unittest.mock import MagicMock, Mock

import pytest

from app.runtime.compute import protocol
from app.runtime.compute.contracts import ComputeUnavailable, Reason


class TestWireBoundaries:
    @pytest.mark.parametrize(
        "data,reason",
        [
            (b"", Reason.CANCELLED),
            (struct.pack("!I", protocol.MAX_FRAME + 1), Reason.INVALID_INPUT),
        ],
    )
    def test_rejects_invalid_input_frame(self, data, reason):
        connection = Mock()
        connection.recv.return_value = data
        with pytest.raises(ComputeUnavailable) as exc:
            protocol.receive(connection, float("inf"))
        assert exc.value.reason is reason

    def test_retries_short_reads_after_native_timeout(self):
        connection = Mock()
        connection.recv.side_effect = [socket.timeout(), b"a", b"b"]
        assert protocol._read(connection, 2, float("inf"), lambda: None) == b"ab"

    @pytest.mark.parametrize("method", ["send", "send_admitted"])
    def test_refuses_oversized_output_before_writing(self, monkeypatch, method):
        monkeypatch.setattr(protocol, "MAX_FRAME", 1)
        connection = Mock()
        with pytest.raises(ComputeUnavailable) as exc:
            getattr(protocol, method)(
                connection,
                b"xx",
                *([float("inf")] if method == "send_admitted" else []),
            )
        assert exc.value.reason is Reason.INVALID_INPUT
        connection.sendall.assert_not_called()

    @pytest.mark.parametrize("ack", [[], {}, {"ready": False}, {"error": "capacity"}])
    def test_requires_admitted_receiver_before_body(self, monkeypatch, ack):
        monkeypatch.setattr(
            protocol, "receive", lambda *args, **kwargs: json.dumps(ack).encode()
        )
        connection = Mock()
        with pytest.raises(ComputeUnavailable) as exc:
            protocol.send_admitted(connection, b"body", float("inf"))
        assert exc.value.reason is (
            Reason.CAPACITY
            if isinstance(ack, dict) and "error" in ack
            else Reason.INVALID_INPUT
        )
        connection.send.assert_not_called()

    def test_bounds_control_acknowledgement(self):
        with pytest.raises(ComputeUnavailable) as exc:
            protocol.admit_control_response(protocol.MAX_CONTROL_RESPONSE + 1)
        assert exc.value.reason is Reason.INVALID_INPUT

    @pytest.mark.parametrize(
        "deadline,writes,reason",
        [(0, [], Reason.DEADLINE), (float("inf"), [0], Reason.CANCELLED)],
    )
    def test_preserves_body_delivery_failure(self, deadline, writes, reason):
        connection = Mock()
        connection.send.side_effect = writes
        with pytest.raises(ComputeUnavailable) as exc:
            protocol.send_body(connection, b"body", deadline)
        assert exc.value.reason is reason

    def test_retries_short_writes_after_native_timeout(self):
        connection = Mock()
        connection.send.side_effect = [socket.timeout(), 1, 3]
        protocol.send_body(connection, b"body", float("inf"))
        assert [bytes(call.args[0]) for call in connection.send.call_args_list] == [
            b"body",
            b"body",
            b"ody",
        ]

    @pytest.mark.parametrize("error", [BlockingIOError, socket.timeout])
    def test_keeps_live_peer_after_readiness_race(self, monkeypatch, error):
        connection = Mock()
        connection.recv.side_effect = error
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [(Mock(), protocol.selectors.EVENT_READ)]
        monkeypatch.setattr(protocol.selectors, "DefaultSelector", lambda: selector)
        assert not protocol.peer_disconnected(connection)
        selector.select.assert_called_once_with(timeout=0)
        selector.__exit__.assert_called_once()
