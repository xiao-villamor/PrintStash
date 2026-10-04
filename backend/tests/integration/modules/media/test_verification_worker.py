"""The verification worker answers its parent with exactly one complete frame."""

from __future__ import annotations

import json

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import verification_worker
from app.modules.media.mesh_facts import FingerprintFailureCode
from app.modules.media.verification_isolation import decode_reply
from tests.factories.geometry import tetrahedron


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    source = tmp_path / "part.stl"
    source.write_bytes(tetrahedron().export(file_type="stl"))
    destination = tmp_path / "reply"

    def execute(*, first=None, second=None):
        spec = {
            "overrides": {},
            "first": str(first or source),
            "second": str(second or source),
            "first_type": "stl",
            "second_type": "stl",
            "first_component": 0,
            "second_component": 0,
            "sample_points": 256,
            "triangle_cap": 2_000_000,
            "verification_seconds": 60.0,
        }
        # An owned descriptor stands in for stdout; main still duplicates and
        # redirects it itself, exactly as in the actual child process.
        with destination.open("w") as output:
            with monkeypatch.context() as patch:
                patch.setattr(verification_worker.sys, "stdout", output)
                status = verification_worker.main([json.dumps(spec)])
        return status, destination.read_bytes()

    return execute


class TestMain:
    def test_writes_one_frame_the_parent_can_decode(self, run_worker):
        status, reply = run_worker()

        assert status == 0
        assert decode_reply(reply).evidence_class == "identical_geometry"

    def test_reports_a_geometry_failure_as_a_coded_frame(self, run_worker, tmp_path):
        broken = tmp_path / "broken.stl"
        broken.write_bytes(b"not a mesh")

        status, reply = run_worker(first=broken)

        assert status == 0
        with pytest.raises(GeometryError) as raised:
            decode_reply(reply)
        assert raised.value.code == FingerprintFailureCode.INVALID_SOURCE.value
