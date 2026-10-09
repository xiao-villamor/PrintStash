"""Optional rendering accepts complete frames and preserves withdrawal semantics."""

import base64
import json
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
            "exchange",
            lambda request, **kwargs: json.dumps(
                {"result": base64.b64encode(payload).decode()}
            ).encode(),
        )

        frames = compute_render.render(prepared, 2, 2, [None, None], False)

        assert tuple(frame.rgba for frame in frames) == (red, blue)

    def test_refuses_incomplete_native_output(self, prepared, monkeypatch):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)
        payload = struct.pack("!III", 2, 2, 1) + b"short"
        monkeypatch.setattr(
            compute_render.client,
            "exchange",
            lambda request, **kwargs: json.dumps(
                {"result": base64.b64encode(payload).decode()}
            ).encode(),
        )

        assert compute_render.render(prepared, 2, 2, [None], False) is None

    def test_withdrawal_does_not_become_cpu_fallback(self, prepared, monkeypatch):
        monkeypatch.setattr(compute_render.client, "available", lambda **kwargs: True)

        with cancellation_scope(lambda **kwargs: True):
            with pytest.raises(OperationCancelled):
                compute_render.render(prepared, 2, 2, [None], False)
