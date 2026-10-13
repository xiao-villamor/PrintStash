"""Real socket framing refuses excess readback before allocating its body."""

import json
import socket
import struct
import time
from threading import Thread

import pytest

from app.modules.media import compute_render

WIDTH, HEIGHT = 320, 240


class TestPacketReplyLimit:
    @pytest.mark.parametrize("excess", [0, 1], ids=["at-limit", "over-limit"])
    def test_bounds_the_supersampled_reply_before_reading(
        self, excess, monkeypatch, tmp_path
    ):
        from app.runtime.compute.protocol import send

        header = json.dumps(
            dict(
                vertices=3,
                faces=1,
                normals=3,
                width=WIDTH,
                height=HEIGHT,
                views=[None],
                matte=False,
                normal_mode="gpu-visual-v2",
            )
        ).encode()
        payload = (
            struct.pack("!I", len(header))
            + header
            + struct.pack("<9f3I", 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 2)
        )

        def render():
            return compute_render.render_packet(
                payload, width=WIDTH, height=HEIGHT, deadline=time.monotonic() + 30
            )

        # Exercise the actual client's length admission, using a socket peer
        # instead of a GPU owner. Over-limit payload bytes are never sent.
        limit = 14 + WIDTH * HEIGHT * 16
        raw = b"\x00" + struct.pack("!III", WIDTH * 2, HEIGHT * 2, 1)
        raw += bytes(WIDTH * HEIGHT * 16)
        wire = b"\x00" + raw
        assert len(wire) == limit
        connection, peer = socket.socketpair()
        failures = []

        def receive_exact(stream, length):
            chunks = []
            while length:
                chunk = stream.recv(length)
                if not chunk:
                    raise EOFError("client closed its request")
                chunks.append(chunk)
                length -= len(chunk)
            return b"".join(chunks)

        def serve():
            try:
                with peer:
                    peer.settimeout(5)
                    length = struct.unpack("!I", receive_exact(peer, 4))[0]
                    send(peer, b'{"ready":true}')
                    receive_exact(peer, length)
                    send(peer, b'{"geometry":"cached"}')
                    peer.sendall(struct.pack("!I", limit + excess))
                    if not excess:
                        peer.sendall(wire)
            except Exception as error:
                failures.append(error)

        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        monkeypatch.setattr(compute_render.client, "directory", lambda: tmp_path)
        monkeypatch.setattr(
            compute_render.client, "_connect", lambda *args, **kwargs: connection
        )
        worker = Thread(target=serve, daemon=True)
        worker.start()
        try:
            if excess:
                with pytest.raises(ValueError, match="compute_geometry_invalid"):
                    render()
            else:
                # Valid raw output represents unavailable GPU postprocessing;
                # it selects guarded fallback without processing pixels here.
                assert render() is None
        finally:
            connection.close()
            worker.join(5)
        assert not worker.is_alive()
        assert not failures
