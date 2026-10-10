"""Optional rendering accepts complete frames and preserves withdrawal semantics."""

import struct

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import PreparedRender

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.modules.media import compute_render


@pytest.fixture
def prepared():
    return PreparedRender(
        np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
        1,
        lambda size: iter([np.asarray([[0, 1, 2]], dtype=np.int64)]),
        np.arange(3, dtype=np.int64),
        np.tile([0.0, 0.0, 1.0], (3, 1)),
    )


class TestOptionalRender:
    def test_returns_complete_associated_frames(self, prepared, monkeypatch):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        red = bytes([255, 0, 0, 255]) * 4
        blue = bytes([0, 0, 255, 255]) * 4
        payload = struct.pack("!III", 2, 2, 2) + red + blue
        monkeypatch.setattr(
            compute_render.client,
            "exchange_render",
            lambda request, body, **kwargs: b"\x01" + payload,
        )

        frames = compute_render.render(prepared, 2, 2, [None, None], False)

        assert tuple(frame.rgba for frame in frames) == (red, blue)

    def test_refuses_incomplete_native_output(self, prepared, monkeypatch):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        payload = struct.pack("!III", 2, 2, 1) + b"short"
        monkeypatch.setattr(
            compute_render.client,
            "exchange_render",
            lambda request, body, **kwargs: b"\x01" + payload,
        )

        assert compute_render.render(prepared, 2, 2, [None], False) is None

    def test_withdrawal_does_not_become_cpu_fallback(self, prepared, monkeypatch):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)

        with cancellation_scope(lambda **kwargs: True):
            with pytest.raises(OperationCancelled):
                compute_render.render(prepared, 2, 2, [None], False)


class TestCpuPostprocessingFallback:
    @pytest.mark.parametrize("width,factor", [(2, 2), (641, 1)])
    def test_finalizes_raw_pixels_in_the_caller(
        self, prepared, monkeypatch, width, factor
    ):
        from printstash_core.mesh.rasterizer import postprocess_rgba

        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        raw = bytes([190, 80, 40, 128]) * (width * 2 * factor * factor)
        wire = b"\x00" + struct.pack("!III", width * factor, 2 * factor, 1) + raw
        monkeypatch.setattr(
            compute_render.client, "exchange_render", lambda *args, **kwargs: wire
        )
        result = compute_render.render(prepared, width, 2, [None], False)
        assert result[0].rgba == postprocess_rgba(raw, width, 2).rgba

    @pytest.mark.parametrize(
        "wire", [b"", b"\x02bad", b"\x01" + struct.pack("!III", 3, 3, 1) + bytes(36)]
    )
    def test_refuses_an_invalid_pixel_envelope(self, prepared, monkeypatch, wire):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        monkeypatch.setattr(
            compute_render.client, "exchange_render", lambda *args, **kwargs: wire
        )
        assert compute_render.render(prepared, 2, 2, [None], False) is None
