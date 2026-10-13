"""The STL worker writes the mesh to the parent's file and answers with its size."""

from __future__ import annotations

import hashlib
import io
import json
import struct

import pytest
import trimesh

from app.core.config import _overlay
from app.modules.media import stl_worker
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.stl_isolation import decode_manifest
from tests.factories.geometry import tetrahedron, three_mf
from tests.factories.three_mf_pilot import load_case


@pytest.fixture
def run_worker(tmp_path, monkeypatch):
    destination = tmp_path / "reply"

    def execute(source, *, file_type, expected_sha256=None):
        output = tmp_path / "mesh.stl"
        spec = {
            "overrides": {},
            "path": str(source),
            "file_type": file_type,
            "output_directory": str(tmp_path),
            "expected_sha256": expected_sha256
            if expected_sha256 is not None
            else hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        # An owned descriptor stands in for stdout; main still duplicates and
        # redirects it itself, exactly as in the actual child process.
        with destination.open("w") as sink:
            with monkeypatch.context() as patch:
                patch.setattr(stl_worker.sys, "stdout", sink)
                status = stl_worker.main([json.dumps(spec)])
        return status, decode_manifest(destination.read_bytes()), output

    return execute


class TestMain:
    def test_reports_the_size_of_the_mesh_it_wrote(self, tmp_path, run_worker):
        source = tmp_path / "cube.obj"
        trimesh.creation.box(extents=[4, 4, 4]).export(source, file_type="obj")

        status, manifest, output = run_worker(source, file_type="obj")

        assert status == 0
        assert manifest.size == output.stat().st_size and manifest.size > 84
        assert manifest.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()

    def test_writes_the_complete_binary_stl_for_a_3mf(self, tmp_path, run_worker):
        source = tmp_path / "tetrahedron.3mf"
        payload = three_mf()
        source.write_bytes(payload)

        status, manifest, output = run_worker(source, file_type="3mf")

        converted = output.read_bytes()
        expected = tetrahedron().export(file_type="stl")
        assert status == 0
        assert manifest.size == len(converted) == 84 + 4 * 50
        assert manifest.sha256 == hashlib.sha256(converted).hexdigest()
        assert struct.unpack_from("<I", converted, 80)[0] == 4
        assert converted == expected
        restored = trimesh.load_mesh(io.BytesIO(converted), file_type="stl")
        assert len(restored.faces) == 4
        assert restored.bounds.tolist() == [[0, 0, 0], [10, 20, 30]]
        assert source.read_bytes() == payload

    def test_refuses_translated_facets_that_collapse_in_float32(
        self, tmp_path, run_worker
    ):
        payload = three_mf(build=((1, "1 0 0 0 1 0 0 0 1 1e12 1e12 1e12"),))
        source = tmp_path / "translated.3mf"
        source.write_bytes(payload)

        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="3mf")

        assert raised.value.reason is ThumbnailFailureReason.INVALID_SOURCE
        assert not (tmp_path / "mesh.stl").exists()
        assert source.read_bytes() == payload

    def test_refuses_finite_coordinates_outside_float32_range(
        self, tmp_path, run_worker
    ):
        payload = three_mf(build=((1, "1e35 0 0 0 1e35 0 0 0 1e35 1e40 1e40 1e40"),))
        source = tmp_path / "overflow.3mf"
        source.write_bytes(payload)

        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="3mf")

        assert raised.value.reason is ThumbnailFailureReason.INVALID_SOURCE
        assert not (tmp_path / "mesh.stl").exists()
        assert source.read_bytes() == payload

    def test_preserves_modest_transformed_geometry(self, tmp_path, run_worker):
        payload = three_mf(build=((1, "1 0 0 0 1 0 0 0 1 7 11 13"),))
        source = tmp_path / "translated.3mf"
        source.write_bytes(payload)

        status, manifest, output = run_worker(source, file_type="3mf")

        converted = output.read_bytes()
        expected = tetrahedron()
        expected.apply_translation([7, 11, 13])
        assert status == 0
        assert converted == expected.export(file_type="stl")
        assert manifest.size == len(converted) == 84 + 4 * 50
        assert manifest.sha256 == hashlib.sha256(converted).hexdigest()
        restored = trimesh.load_mesh(io.BytesIO(converted), file_type="stl")
        assert len(restored.faces) == 4
        assert restored.bounds.tolist() == [[7, 11, 13], [17, 31, 43]]
        assert source.read_bytes() == payload

    def test_preserves_tiny_representable_facets(self, tmp_path, run_worker):
        import numpy as np

        payload = three_mf(build=((1, "1e-18 0 0 0 1e-18 0 0 0 1e-18 0 0 0"),))
        source = tmp_path / "tiny.3mf"
        source.write_bytes(payload)

        status, manifest, output = run_worker(source, file_type="3mf")

        converted = output.read_bytes()
        expected = tetrahedron()
        expected.apply_scale(1e-18)
        assert status == 0
        assert manifest.size == len(converted) == 284
        assert converted == expected.export(file_type="stl")
        restored = trimesh.load_mesh(
            io.BytesIO(converted), file_type="stl", process=False
        )
        np.testing.assert_allclose(restored.bounds, expected.bounds, rtol=1e-6, atol=0)
        assert source.read_bytes() == payload

    def test_reports_a_mesh_it_cannot_convert_as_invalid(self, tmp_path, run_worker):
        source = tmp_path / "garbage.obj"
        source.write_bytes(b"not a mesh \x00\x01")

        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="obj")

        assert raised.value.reason is ThumbnailFailureReason.INVALID_SOURCE
        assert not (tmp_path / "mesh.stl").exists()

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


class TestConvert:
    def test_exports_directly_to_staging(self, tmp_path, monkeypatch):
        source = tmp_path / "cube.obj"
        destination = tmp_path / "mesh.stl"
        trimesh.creation.box().export(source, file_type="obj")
        export = trimesh.Trimesh.export

        def file_export(mesh, *, file_obj, file_type):
            assert isinstance(file_obj, io.IOBase), (
                "export requested an STL bytes buffer"
            )
            return export(mesh, file_obj=file_obj, file_type=file_type)

        monkeypatch.setattr(trimesh.Trimesh, "export", file_export)

        manifest = stl_worker.convert(source, "obj", destination)

        assert destination.stat().st_size == manifest.size == 684
        assert manifest.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()

    def test_refuses_output_expansion_before_export(self, tmp_path, monkeypatch):
        source = tmp_path / "cube.obj"
        destination = tmp_path / "mesh.stl"
        trimesh.creation.box().export(source, file_type="obj")
        monkeypatch.setattr(stl_worker, "MAX_STL_BYTES", 683)

        with pytest.raises(MeshWorkerError) as error:
            stl_worker.convert(source, "obj", destination)

        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert not destination.exists()


class TestWorkerBoundaries:
    @pytest.mark.parametrize(
        "coordinate", [float("nan"), float("inf")], ids=["nan", "infinity"]
    )
    def test_refuses_nonfinite_source_facets(self, coordinate):
        mesh = tetrahedron()
        mesh.vertices[0, 0] = coordinate
        before = mesh.vertices.tobytes(), mesh.faces.tobytes()
        with pytest.raises(MeshWorkerError) as raised:
            stl_worker._validate_float32_facets(mesh)
        assert raised.value.reason is ThumbnailFailureReason.INVALID_SOURCE
        assert (mesh.vertices.tobytes(), mesh.faces.tobytes()) == before

    def test_refuses_stream_write_above_real_ceiling(self, tmp_path):
        destination = tmp_path / "ceiling.bin"
        chunk = b"x" * (1024 * 1024)
        try:
            with destination.open("wb") as stream:
                output = stl_worker._CappedSTLOutput(stream)
                for _ in range(stl_worker.MAX_STL_BYTES // len(chunk)):
                    assert output.write(chunk) == len(chunk)
                output.flush()
                assert destination.stat().st_size == stl_worker.MAX_STL_BYTES
                with pytest.raises(MeshWorkerError) as raised:
                    output.write(b"x")
                assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
                assert output.written == stl_worker.MAX_STL_BYTES
                assert destination.stat().st_size == stl_worker.MAX_STL_BYTES
            output.flush()
        finally:
            destination.unlink(missing_ok=True)

    def test_refuses_changed_source_digest(self, tmp_path, run_worker):
        source = tmp_path / "source.obj"
        trimesh.creation.box().export(source, file_type="obj")
        original = source.read_bytes()
        wrong = "0" * 64
        assert hashlib.sha256(original).hexdigest() != wrong
        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="obj", expected_sha256=wrong)
        assert raised.value.reason is ThumbnailFailureReason.SOURCE_CHANGED
        assert source.read_bytes() == original
        assert not (tmp_path / "mesh.stl").exists()

    def test_reports_missing_source_storage(self, tmp_path, run_worker):
        source = tmp_path / "missing.obj"
        assert not source.exists()
        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="obj", expected_sha256="0" * 64)
        assert raised.value.reason is ThumbnailFailureReason.STORAGE
        assert not (tmp_path / "mesh.stl").exists()

    def test_reports_source_disappearance_after_export(
        self, tmp_path, run_worker, monkeypatch
    ):
        source = tmp_path / "source.obj"
        trimesh.creation.box().export(source, file_type="obj")
        export = trimesh.Trimesh.export

        def external_export(mesh, *, file_obj, file_type):
            result = export(mesh, file_obj=file_obj, file_type=file_type)
            source.unlink()
            return result

        monkeypatch.setattr(trimesh.Trimesh, "export", external_export)
        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="obj")
        assert raised.value.reason is ThumbnailFailureReason.STORAGE
        assert not source.exists()
        assert (tmp_path / "mesh.stl").stat().st_size == 684

    def test_refuses_source_mutation_after_export(
        self, tmp_path, run_worker, monkeypatch
    ):
        source = tmp_path / "source.obj"
        trimesh.creation.box().export(source, file_type="obj")
        original = source.read_bytes()
        export = trimesh.Trimesh.export

        def external_export(mesh, *, file_obj, file_type):
            result = export(mesh, file_obj=file_obj, file_type=file_type)
            source.write_bytes(original + b"\n# changed-source\n")
            return result

        monkeypatch.setattr(trimesh.Trimesh, "export", external_export)
        with pytest.raises(MeshWorkerError) as raised:
            run_worker(source, file_type="obj")
        assert raised.value.reason is ThumbnailFailureReason.SOURCE_CHANGED
        assert source.read_bytes() == original + b"\n# changed-source\n"
        assert (tmp_path / "mesh.stl").stat().st_size == 684
