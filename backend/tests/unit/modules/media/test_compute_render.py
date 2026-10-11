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


class TestRenderPriority:
    def test_direct_render_preserves_interactive_dispatch(self):
        from app.runtime.compute.protocol import Priority

        assert compute_render.render_priority() is Priority.INTERACTIVE

    def test_derivative_render_allows_background_coalescing(self):
        from app.runtime.compute.protocol import Priority

        with compute_render.background_render_scope():
            priority = compute_render.render_priority()

        assert priority is Priority.BACKGROUND

    def test_background_scope_restores_the_callers_priority(self):
        from app.runtime.compute.protocol import Priority

        with pytest.raises(ValueError, match="abandoned"):
            with compute_render.background_render_scope():
                raise ValueError("abandoned")

        assert compute_render.render_priority() is Priority.INTERACTIVE

    def test_backfill_never_raises_compute_priority(self):
        from app.core.work_priority import WorkPriority, priority_scope
        from app.runtime.compute.protocol import Priority

        with priority_scope(WorkPriority.BACKFILL):
            priority = compute_render.render_priority()

        assert priority is Priority.BACKGROUND


class TestStagedRender:
    def test_keeps_owned_geometry_through_dispatch(self, monkeypatch):
        import time

        from app.modules.media.compute_geometry_wire import ThumbnailWire
        from app.runtime.compute.protocol import Priority

        body = memoryview(b"owned geometry")
        wire = ThumbnailWire("a" * 64, "{}", body, 2, 2, 7)
        deadline = time.monotonic() + 5
        pixels = bytes([12, 24, 36, 255]) * 4
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)

        def exchange(request, data, **kwargs):
            assert data is body
            assert request.deadline == deadline
            assert request.priority is Priority.BACKGROUND
            assert request.units == 7
            return b"\x01" + struct.pack("!III", 2, 2, 1) + pixels

        monkeypatch.setattr(compute_render.client, "exchange_render", exchange)
        assert compute_render.render_staged(wire, deadline=deadline).rgba == pixels

    @pytest.mark.parametrize(
        "payload",
        [
            b"\x00" + struct.pack("!III", 4, 4, 1) + bytes(64),
            b"\x01" + struct.pack("!III", 2, 2, 1) + b"short",
        ],
        ids=["unprocessed", "truncated"],
    )
    def test_leaves_rejected_pixels_for_isolated_fallback(self, monkeypatch, payload):
        import time

        from app.modules.media.compute_geometry_wire import ThumbnailWire

        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        monkeypatch.setattr(
            compute_render.client, "exchange_render", lambda *args, **kwargs: payload
        )
        monkeypatch.setattr(
            compute_render,
            "postprocess_rgba",
            lambda *args: pytest.fail("supervisor imported image work"),
        )
        wire = ThumbnailWire("a" * 64, "{}", memoryview(b"owned"), 2, 2, 1)
        assert compute_render.render_staged(wire, deadline=time.monotonic() + 5) is None

    def test_propagates_staged_withdrawal(self, monkeypatch):
        import time

        from app.modules.media.compute_geometry_wire import ThumbnailWire

        wire = ThumbnailWire("a" * 64, "{}", memoryview(b"owned"), 2, 2, 1)
        with cancellation_scope(lambda **kwargs: True):
            with pytest.raises(OperationCancelled):
                compute_render.render_staged(wire, deadline=time.monotonic() + 5)
