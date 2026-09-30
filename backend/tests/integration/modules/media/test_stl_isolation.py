"""Converting a mesh to STL in a worker gives the bytes the in-process code would.

The viewer converts on demand, in a request, from a file a user uploaded. That is
the one place a person can trigger mesh parsing at will, so it must not run in the
API process (#259). Running it elsewhere must not change what the viewer receives.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import trimesh

from app.core.config import _overlay
from app.modules.media import mesh_isolation, mesh_processing, stl_isolation
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.thumbnail_engine import ThumbnailFailureReason
from tests.factories.geometry import three_mf


@pytest.fixture
def cube_obj(tmp_path):
    path = tmp_path / "cube.obj"
    trimesh.creation.box(extents=[4, 4, 4]).export(path, file_type="obj")
    return path


class TestToStlBytes:
    def test_matches_the_in_process_conversion(self, cube_obj):
        isolated = stl_isolation.to_stl_bytes(cube_obj, file_type="obj")
        direct = mesh_processing.to_stl_bytes(cube_obj, file_type="obj")

        assert isolated == direct
        assert isolated is not None and len(isolated) > 84

    def test_passes_an_stl_through_without_a_worker(self, tmp_path, monkeypatch):
        raw = tmp_path / "cube.stl"
        raw.write_bytes(b"already stl")
        monkeypatch.setattr(
            mesh_isolation,
            "run_worker",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no worker for STL")),
        )

        assert stl_isolation.to_stl_bytes(raw, file_type="stl") == b"already stl"

    def test_a_3mf_over_the_budget_converts_to_nothing(self, tmp_path, monkeypatch):
        """#259: the worker's bounded loader refuses placements past the budget."""
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        placements = tuple((1, f"1 0 0 0 1 0 0 0 1 {i * 5} 0 0") for i in range(400))
        path = tmp_path / "plate.3mf"
        path.write_bytes(three_mf(build=placements))

        assert stl_isolation.to_stl_bytes(path, file_type="3mf") is None

    def test_finds_a_source_given_as_a_path_relative_to_the_caller(
        self, cube_obj, monkeypatch
    ):
        monkeypatch.chdir(cube_obj.parent)

        converted = stl_isolation.to_stl_bytes(Path("cube.obj"), file_type="obj")

        assert converted is not None and len(converted) > 84

    def test_the_converted_mesh_is_a_valid_stl(self, cube_obj):
        converted = stl_isolation.to_stl_bytes(cube_obj, file_type="obj")

        mesh = trimesh.load_mesh(io.BytesIO(converted), file_type="stl", process=False)
        assert len(mesh.faces) == 12

    def test_a_worker_over_its_memory_budget_raises_instead_of_dying_with_it(
        self, cube_obj, monkeypatch
    ):
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: 8 * 1024**2)

        with pytest.raises(MeshWorkerError) as raised:
            stl_isolation.to_stl_bytes(cube_obj, file_type="obj")

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT

    def test_leaves_no_scratch_files_behind(self, cube_obj, tmp_path, monkeypatch):
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        monkeypatch.setattr("tempfile.tempdir", str(scratch))

        stl_isolation.to_stl_bytes(cube_obj, file_type="obj")

        assert list(scratch.iterdir()) == []

    def test_refuses_a_file_the_worker_did_not_finish_writing(
        self, cube_obj, monkeypatch
    ):
        """The size in the reply is checked against the file, not trusted."""
        real = mesh_isolation.run_worker

        def lying(module, spec):
            real(module, spec)
            return stl_isolation.encode_reply(10**6)

        monkeypatch.setattr(mesh_isolation, "run_worker", lying)

        with pytest.raises(MeshWorkerError) as raised:
            stl_isolation.to_stl_bytes(cube_obj, file_type="obj")

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED
