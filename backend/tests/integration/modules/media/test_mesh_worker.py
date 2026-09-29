"""The mesh worker answers its parent with exactly one complete frame."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from app.core.config import _overlay
from app.modules.media import mesh_worker
from app.modules.media.mesh_isolation import decode_reply
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
    def test_writes_one_frame_the_parent_can_decode(self, run_worker):
        status, reply = run_worker()

        assert status == 0
        result = decode_reply(reply)
        assert result.image is not None
        assert result.geometry["triangle_count"] == 12

    def test_adopts_the_parents_runtime_overrides_before_reading_a_setting(
        self, run_worker, monkeypatch
    ):
        # Registering the key first makes monkeypatch restore the shared overlay
        # after main() has written to it.
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 640)

        status, reply = run_worker(overrides={"model_thumbnail_width": 320})

        assert status == 0
        image = decode_reply(reply).image
        assert image is not None
        with Image.open(io.BytesIO(image)) as decoded:
            assert decoded.size == (320, 240)
