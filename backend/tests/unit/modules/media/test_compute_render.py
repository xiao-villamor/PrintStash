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

    def test_bounds_readback_to_requested_frames(self, prepared, monkeypatch):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        pixels = bytes([190, 80, 40, 255]) * 4
        response = b"\x01" + struct.pack("!III", 2, 2, 2) + pixels * 2
        limits = []

        def exchange(request, body, *, checkpoint, response_limit):
            limits.append(response_limit)
            return response

        monkeypatch.setattr(compute_render.client, "exchange_render", exchange)

        compute_render.render(prepared, 2, 2, [None, None], False)

        # Protocol tag, output tag/header and two 4x4 supersampled RGBA frames.
        assert limits == [14 + 2 * 4 * 4 * 4]

    @pytest.mark.parametrize("finalized,size", [(False, 4), (True, 2)])
    def test_refuses_empty_gpu_frames(self, prepared, monkeypatch, finalized, size):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        pixels = bytes([190, 80, 40, 0]) * (size * size)
        response = bytes([finalized]) + struct.pack("!III", size, size, 1) + pixels
        monkeypatch.setattr(
            compute_render.client,
            "exchange_render",
            lambda *args, **kwargs: response,
        )

        assert compute_render.render(prepared, 2, 2, [None], False) is None

    @pytest.mark.parametrize("alpha", [1, 128, 255])
    def test_preserves_nonzero_alpha(self, prepared, monkeypatch, alpha):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        # The last pixel alone proves visibility, even at minimum alpha.
        pixels = bytes(12) + bytes([190, 80, 40, alpha])
        response = b"\x01" + struct.pack("!III", 2, 2, 1) + pixels
        monkeypatch.setattr(
            compute_render.client,
            "exchange_render",
            lambda *args, **kwargs: response,
        )

        result = compute_render.render(prepared, 2, 2, [None], False)

        assert result[0].rgba == pixels

    @pytest.mark.parametrize("alphas", [(0, 255), (255, 0)])
    def test_requires_visibility_in_each_view(self, prepared, monkeypatch, alphas):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        first = bytes([190, 80, 40, alphas[0]]) * 4
        second = bytes([40, 80, 190, alphas[1]]) * 4
        response = b"\x01" + struct.pack("!III", 2, 2, 2) + first + second
        monkeypatch.setattr(
            compute_render.client,
            "exchange_render",
            lambda *args, **kwargs: response,
        )

        assert compute_render.render(prepared, 2, 2, [None, None], False) is None

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


class TestRenderRecipe:
    @pytest.mark.parametrize("views,matte", [(1, False), (2, True)])
    def test_dispatch_uses_shared_recipe_identity(
        self, prepared, monkeypatch, views, matte
    ):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        recipes = []

        def exchange(request, body, **kwargs):
            recipes.append(request.recipe)
            pixels = bytes([190, 80, 40, 255]) * 4 * views
            return b"\x01" + struct.pack("!III", 2, 2, views) + pixels

        monkeypatch.setattr(compute_render.client, "exchange_render", exchange)
        assert compute_render.render(prepared, 2, 2, [None] * views, matte) is not None
        assert recipes == [compute_render.render_recipe(2, 2, views, matte)]
        assert (
            recipes[0]
            == f"webgpu-native-raster-v4:qualified-portable-v6:2x2:{views}:{int(matte)}"
        )
