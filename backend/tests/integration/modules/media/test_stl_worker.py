"""The STL worker writes the mesh to the parent's file and answers with its size."""

from __future__ import annotations

import io
import json
import struct

import pytest
import trimesh

from app.core.config import _overlay
from app.modules.media import stl_worker
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.stl_isolation import decode_reply
from tests.factories.geometry import tetrahedron, three_mf
from tests.factories.three_mf_pilot import load_case


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    destination = tmp_path / "reply"

    def execute(source, *, file_type):
        output = tmp_path / "mesh.stl"
        spec = {
            "overrides": {},
            "path": str(source),
            "file_type": file_type,
            "output": str(output),
        }
        # An owned descriptor stands in for stdout; main still duplicates and
        # redirects it itself, exactly as in the actual child process.
        with destination.open("w") as sink:
            with monkeypatch.context() as patch:
                patch.setattr(stl_worker.sys, "stdout", sink)
                status = stl_worker.main([json.dumps(spec)])
        return status, decode_reply(destination.read_bytes()), output

    return execute


class TestMain:
    def test_reports_the_size_of_the_mesh_it_wrote(self, tmp_path, run_worker):
        source = tmp_path / "cube.obj"
        trimesh.creation.box(extents=[4, 4, 4]).export(source, file_type="obj")

        status, size, output = run_worker(source, file_type="obj")

        assert status == 0
        assert size == output.stat().st_size and size > 84

    def test_writes_the_complete_binary_stl_for_a_3mf(self, tmp_path, run_worker):
        source = tmp_path / "tetrahedron.3mf"
        payload = three_mf()
        source.write_bytes(payload)

        status, size, output = run_worker(source, file_type="3mf")

        converted = output.read_bytes()
        expected = tetrahedron().export(file_type="stl")
        assert status == 0
        assert size == len(converted) == 84 + 4 * 50
        assert struct.unpack_from("<I", converted, 80)[0] == 4
        assert converted == expected
        restored = trimesh.load_mesh(io.BytesIO(converted), file_type="stl")
        assert len(restored.faces) == 4
        assert restored.bounds.tolist() == [[0, 0, 0], [10, 20, 30]]
        assert source.read_bytes() == payload

    def test_reports_a_mesh_it_cannot_convert_as_nothing(self, tmp_path, run_worker):
        source = tmp_path / "garbage.obj"
        source.write_bytes(b"not a mesh \x00\x01")

        status, size, output = run_worker(source, file_type="obj")

        assert status == 0
        assert size is None
        assert not output.exists()

    def test_reports_required_extension_refusal_without_writing_stl(
        self, tmp_path, run_worker
    ):
        payload = load_case("unknown-required-extension").payload
        source = tmp_path / "unsupported.3mf"
        source.write_bytes(payload)

        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="3mf")

        assert raised.value.reason is ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
        assert not (tmp_path / "mesh.stl").exists()
        assert source.read_bytes() == payload

    def test_never_hands_a_3mf_to_trimesh_beyond_the_budget(
        self, tmp_path, run_worker, monkeypatch
    ):
        """#259 where it can be observed: inside the process that does the work.

        A recorded call, not a raised error, because the loader swallows errors.
        """
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        calls: list[str] = []
        monkeypatch.setattr(
            trimesh,
            "load_scene",
            lambda *a, **k: calls.append("called") or trimesh.Scene(),
        )
        placements = tuple((1, f"1 0 0 0 1 0 0 0 1 {i * 5} 0 0") for i in range(400))
        source = tmp_path / "plate.3mf"
        source.write_bytes(three_mf(build=placements))

        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="3mf")

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert calls == []
