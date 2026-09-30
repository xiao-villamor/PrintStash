"""The embedding worker answers its parent with exactly one complete frame."""

from __future__ import annotations

import json

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import embedding_worker
from app.modules.media.embedding_isolation import decode_reply
from tests.factories.geometry import tetrahedron


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    source = tmp_path / "part.stl"
    source.write_bytes(tetrahedron().export(file_type="stl"))
    destination = tmp_path / "reply"

    def execute(*, image_size=64):
        spec = {
            "overrides": {},
            "path": str(source),
            "file_type": "stl",
            "component_index": 0,
            "image_size": image_size,
            "triangle_cap": 200_000,
        }
        # An owned descriptor stands in for stdout; main still duplicates and
        # redirects it itself, exactly as in the actual child process.
        with destination.open("w") as sink:
            with monkeypatch.context() as patch:
                patch.setattr(embedding_worker.sys, "stdout", sink)
                status = embedding_worker.main([json.dumps(spec)])
        return status, destination.read_bytes()

    return execute


class TestMain:
    def test_writes_one_frame_of_six_views_the_parent_can_decode(self, run_worker):
        status, reply = run_worker()

        assert status == 0
        views = decode_reply(reply)
        assert len(views) == 6
        assert all(view.width == view.height == 64 for view in views)

    def test_reports_a_geometry_failure_as_a_coded_frame(self, run_worker):
        status, reply = run_worker(image_size=8)

        assert status == 0
        with pytest.raises(GeometryError) as raised:
            decode_reply(reply)
        assert raised.value.code == "invalid_view_budget"
