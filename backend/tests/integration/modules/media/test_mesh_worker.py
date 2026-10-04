"""The mesh worker writes requested basic outputs before its terminal frame."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image
from printstash_core.mesh.measurements import VolumeUnavailable, VolumeUnavailableCause

from app.core.config import _overlay
from app.modules.media import mesh_worker
from tests.factories import content


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    source = tmp_path / "cube.stl"
    source.write_bytes(content.binary_stl())
    destination = tmp_path / "reply"

    def execute(**overrides):
        spec = {
            "overrides": {},
            "path": str(source),
            "file_type": "stl",
            "width": None,
            "height": None,
            "include_geometry": True,
            "include_thumbnail": True,
            "include_fingerprint": False,
            "triangle_cap": 2_000_000,
            "output_format": "WEBP",
            "reason": "derivative",
        }
        spec.update(overrides)
        # An owned descriptor stands in for stdout; main still duplicates and
        # redirects it itself, exactly as in the actual child process.
        with destination.open("w") as output:
            with monkeypatch.context() as patch:
                patch.setattr(mesh_worker.sys, "stdout", output)
                status = mesh_worker.main([json.dumps(spec)])
        return status, destination.read_bytes()

    return execute


class TestMain:
    def test_writes_requested_basics_before_terminal_frame(self, run_worker):
        from pathlib import Path

        from app.modules.media.mesh_contracts import ThumbnailRequest
        from app.modules.media.mesh_protocol import (
            FingerprintFinal,
            FrameDecoder,
            GeometryOutput,
            ThumbnailOutput,
        )

        status, reply = run_worker()
        decoder = FrameDecoder(ThumbnailRequest(Path("source.stl")))
        frames = list(decoder.feed(reply))
        final = decoder.finish()

        assert status == 0
        assert tuple(type(frame) for frame in frames) == (
            GeometryOutput,
            ThumbnailOutput,
            FingerprintFinal,
        )
        assert frames[0].geometry["triangle_count"] == 12
        assert frames[0].volume == VolumeUnavailable(
            VolumeUnavailableCause.NOT_WATERTIGHT
        )
        assert frames[1].image is not None
        assert final is frames[2]
        assert final.fingerprint is None

    def test_adopts_the_parents_runtime_overrides_before_reading_a_setting(
        self, run_worker, monkeypatch
    ):
        # Registering the key first makes monkeypatch restore the shared overlay
        # after main() has written to it.
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 640)

        status, reply = run_worker(overrides={"model_thumbnail_width": 320})

        assert status == 0
        from pathlib import Path

        from app.modules.media.mesh_contracts import ThumbnailRequest
        from app.modules.media.mesh_protocol import FrameDecoder, ThumbnailOutput

        decoder = FrameDecoder(ThumbnailRequest(Path("source.stl"), width=320))
        frames = list(decoder.feed(reply))
        decoder.finish()
        image = next(
            frame.image for frame in frames if isinstance(frame, ThumbnailOutput)
        )
        assert image is not None
        with Image.open(io.BytesIO(image)) as decoded:
            assert decoded.size == (320, 240)
