"""Converting a mesh to STL in a worker gives the bytes the in-process code would.

The viewer converts on demand, through a Job, from a file a user uploaded. That is
the one place a person can trigger mesh parsing at will, so it must not run in the
API process (#259). Running it elsewhere must not change what the viewer receives.
"""

from __future__ import annotations

import hashlib
import struct
import tracemalloc
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
import trimesh

from app.core.config import _overlay
from app.modules.media import mesh_isolation, mesh_loading, stl_isolation
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from tests.factories.geometry import three_mf
from tests.factories.three_mf_pilot import load_case


@pytest.fixture
def cube_obj(tmp_path):
    path = tmp_path / "cube.obj"
    trimesh.creation.box(extents=[4, 4, 4]).export(path, file_type="obj")
    return path


class TestPrepareStl:
    def test_prepares_converted_geometry_by_file(self, cube_obj, tmp_path):
        expected = mesh_loading.to_stl_bytes(cube_obj, file_type="obj")
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()

        with stl_isolation.prepare_stl(
            cube_obj, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            assert prepared.path.read_bytes() == expected
            assert prepared.size == len(expected)
            assert prepared.sha256 == hashlib.sha256(expected).hexdigest()

    def test_keeps_parent_allocation_bounded(self, tmp_path):
        source = tmp_path / "large.stl"
        with source.open("wb") as stream:
            stream.truncate(16 * 1024**2)
        with source.open("rb") as stream:
            source_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        tracemalloc.start()
        try:
            with stl_isolation.prepare_stl(
                source, file_type="stl", expected_sha256=source_sha, workspace=tmp_path
            ) as prepared:
                assert prepared.path != source
                assert prepared.size == source.stat().st_size
                assert prepared.sha256 == source_sha
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert peak < 4 * 1024**2
        assert source.exists()

    def test_keeps_conversion_parent_memory_bounded(self, tmp_path):
        source = tmp_path / "large.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\n" + b"f 1 2 3\n" * 120_000)
        with source.open("rb") as stream:
            source_sha = hashlib.file_digest(stream, "sha256").hexdigest()
        tracemalloc.start()
        try:
            with stl_isolation.prepare_stl(
                source, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
            ) as prepared:
                assert prepared.size == 84 + 50 * 120_000
                assert prepared.path.stat().st_size == prepared.size
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert peak < 4 * 1024**2

    def test_releases_successful_staging(self, cube_obj, tmp_path):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()

        with stl_isolation.prepare_stl(
            cube_obj, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            scratch = prepared.path.parent
            assert prepared.path.is_file()

        assert not scratch.exists()

    def test_releases_failed_staging(self, cube_obj, tmp_path):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()

        with pytest.raises(RuntimeError, match="caller failure"):
            with stl_isolation.prepare_stl(
                cube_obj,
                file_type="obj",
                expected_sha256=source_sha,
                workspace=tmp_path,
            ) as prepared:
                scratch = prepared.path.parent
                raise RuntimeError("caller failure")

        assert not scratch.exists()

    def test_refuses_mismatched_source_hash(self, cube_obj, tmp_path):
        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                cube_obj, file_type="obj", expected_sha256="0" * 64, workspace=tmp_path
            ):
                pytest.fail("mismatched source yielded output")

        assert error.value.reason is ThumbnailFailureReason.SOURCE_CHANGED

    def test_preserves_unsupported_capability(self, tmp_path):
        source = tmp_path / "required.3mf"
        payload = load_case("unknown-required-extension").payload
        source.write_bytes(payload)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                source,
                file_type="3mf",
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                workspace=tmp_path,
            ):
                pytest.fail("unsupported source yielded output")

        assert error.value.reason is ThumbnailFailureReason.UNSUPPORTED_CAPABILITY

    def test_refuses_corrupted_output(self, cube_obj, tmp_path, monkeypatch):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()
        worker = mesh_isolation.prepared_worker_result

        @contextmanager
        def corrupt(*args, **kwargs):
            with worker(*args, **kwargs) as (reply, directory):
                with (directory / "mesh.stl").open("r+b") as stream:
                    stream.write(b"changed!")
                yield reply, directory

        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", corrupt)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                cube_obj,
                file_type="obj",
                expected_sha256=source_sha,
                workspace=tmp_path,
            ):
                pytest.fail("corrupted output yielded")

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_refuses_output_symlink(self, cube_obj, tmp_path, monkeypatch):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()
        worker = mesh_isolation.prepared_worker_result

        @contextmanager
        def substituted(*args, **kwargs):
            with worker(*args, **kwargs) as (reply, directory):
                output = directory / "mesh.stl"
                output.unlink()
                output.symlink_to(cube_obj)
                yield reply, directory

        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", substituted)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                cube_obj,
                file_type="obj",
                expected_sha256=source_sha,
                workspace=tmp_path,
            ):
                pytest.fail("output symlink yielded")

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED
        assert cube_obj.is_file()

    def test_refuses_source_replacement(self, cube_obj, tmp_path, monkeypatch):
        payload = cube_obj.read_bytes()
        source_sha = hashlib.sha256(payload).hexdigest()
        worker = mesh_isolation.prepared_worker_result

        @contextmanager
        def replaced(*args, **kwargs):
            with worker(*args, **kwargs) as result:
                replacement = cube_obj.with_suffix(".replacement")
                replacement.write_bytes(payload)
                replacement.replace(cube_obj)
                yield result

        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", replaced)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                cube_obj,
                file_type="obj",
                expected_sha256=source_sha,
                workspace=tmp_path,
            ):
                pytest.fail("replaced source yielded")

        assert error.value.reason is ThumbnailFailureReason.SOURCE_CHANGED

    def test_refuses_post_stream_mutation(self, cube_obj, tmp_path):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()

        with stl_isolation.prepare_stl(
            cube_obj, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            prepared.stream.read()
            with prepared.path.open("r+b") as mutation:
                mutation.write(b"modified")

            with pytest.raises(MeshWorkerError) as error:
                prepared.verify()

            assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_passes_stl_through_without_worker(self, tmp_path, monkeypatch):
        source = tmp_path / "cube.stl"
        source.write_bytes(b"already stl")

        def no_worker(*_args, **_kwargs):
            raise AssertionError("STL passthrough launched native work")

        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", no_worker)

        with stl_isolation.prepare_stl(
            source,
            file_type="stl",
            expected_sha256=hashlib.sha256(b"already stl").hexdigest(),
            workspace=tmp_path,
        ) as prepared:
            assert prepared.stream.read() == b"already stl"

    def test_preserves_3mf_placement_refusal(self, tmp_path, monkeypatch):
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        placements = tuple((1, f"1 0 0 0 1 0 0 0 1 {i * 5} 0 0") for i in range(400))
        payload = three_mf(build=placements)
        source = tmp_path / "plate.3mf"
        source.write_bytes(payload)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                source,
                file_type="3mf",
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                workspace=tmp_path,
            ):
                pytest.fail("over-budget placements yielded")

        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT

    def test_preserves_step_triangle_refusal(self, tmp_path, monkeypatch):
        from tests.paths import FIXTURES_DIR

        source = FIXTURES_DIR / "cascadio_material.stp"
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                source,
                file_type="step",
                expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                workspace=tmp_path,
            ):
                pytest.fail("over-budget STEP yielded")

        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT

    def test_resolves_caller_relative_source(self, cube_obj, tmp_path, monkeypatch):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()
        monkeypatch.chdir(cube_obj.parent)

        with stl_isolation.prepare_stl(
            Path("cube.obj"),
            file_type="obj",
            expected_sha256=source_sha,
            workspace=tmp_path,
        ) as prepared:
            assert prepared.size == 684

    def test_converted_file_is_valid_stl(self, cube_obj, tmp_path):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()

        with stl_isolation.prepare_stl(
            cube_obj, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            mesh = trimesh.load_mesh(prepared.path, file_type="stl", process=False)

        assert len(mesh.faces) == 12

    def test_preserves_worker_memory_refusal(self, cube_obj, tmp_path, monkeypatch):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: 8 * 1024**2)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                cube_obj,
                file_type="obj",
                expected_sha256=source_sha,
                workspace=tmp_path,
            ):
                pytest.fail("over-memory worker yielded")

        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT

    def test_refuses_incomplete_output(self, cube_obj, tmp_path, monkeypatch):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()
        worker = mesh_isolation.prepared_worker_result

        @contextmanager
        def incomplete(*args, **kwargs):
            with worker(*args, **kwargs) as (reply, directory):
                actual = stl_isolation.decode_manifest(reply.payload)
                manifest = stl_isolation.STLManifest(actual.size + 1, actual.sha256)
                yield (
                    replace(reply, payload=stl_isolation.encode_manifest(manifest)),
                    directory,
                )

        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", incomplete)

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                cube_obj,
                file_type="obj",
                expected_sha256=source_sha,
                workspace=tmp_path,
            ):
                pytest.fail("incomplete output yielded")

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_keeps_verified_descriptor_after_output_path_replacement(
        self, cube_obj, tmp_path
    ):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()

        with stl_isolation.prepare_stl(
            cube_obj, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            prepared.path.unlink()
            prepared.path.write_bytes(b"replacement bytes")
            payload = prepared.stream.read()

        assert len(payload) == prepared.size
        assert hashlib.sha256(payload).hexdigest() == prepared.sha256

    @pytest.mark.parametrize("file_type", ["obj", "stl"])
    def test_preserves_replaced_workspace_directory(
        self, cube_obj, tmp_path, file_type
    ):
        source = cube_obj.with_suffix("." + file_type)
        trimesh.creation.box().export(source, file_type=file_type)
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()

        with stl_isolation.prepare_stl(
            source, file_type=file_type, expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            directory = prepared.path.parent
            directory.rename(directory.with_name(directory.name + "-retained"))
            directory.mkdir()
            foreign = directory / "foreign.txt"
            foreign.write_bytes(b"foreign ownership")

        assert foreign.read_bytes() == b"foreign ownership"

    @pytest.mark.parametrize(
        "requested_size", [-1, None], ids=["negative", "implicit-all"]
    )
    def test_preserves_unbounded_read_semantics(self, tmp_path, requested_size):
        source = tmp_path / "large.stl"
        with source.open("wb") as target:
            target.truncate(2 * 1024**2)
        with source.open("rb") as original:
            source_sha = hashlib.file_digest(original, "sha256").hexdigest()

        with stl_isolation.prepare_stl(
            source, file_type="stl", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            chunk = prepared.stream.read(requested_size)
            prepared.verify()

        assert chunk == bytes(2 * 1024**2)

    def test_checks_cancellation_before_publication_read(
        self, cube_obj, tmp_path, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        source = cube_obj.with_suffix(".stl")
        trimesh.creation.box().export(source, file_type="stl")
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()

        def cancelled(*args, **kwargs):
            raise OperationCancelled()

        with stl_isolation.prepare_stl(
            source, file_type="stl", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            scratch = prepared.path.parent
            monkeypatch.setattr(stl_isolation, "checkpoint", cancelled)
            with pytest.raises(OperationCancelled):
                prepared.stream.read(1024)

        assert prepared.stream.closed
        assert not scratch.exists()

    def test_preserves_requested_multipart_read_length(self, tmp_path):
        source = tmp_path / "multipart.stl"
        with source.open("wb") as target:
            target.truncate(6 * 1024**2)
        with source.open("rb") as original:
            source_sha = hashlib.file_digest(original, "sha256").hexdigest()

        with stl_isolation.prepare_stl(
            source, file_type="stl", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            first = prepared.stream.read(5 * 1024**2)
            final = prepared.stream.read(1024**2)
            prepared.verify()

        assert len(first) == 5 * 1024**2
        assert len(final) == 1024**2

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"not an STL",
            bytes(84),
            bytes(80) + struct.pack("<I", 1) + bytes(49),
        ],
        ids=["empty", "garbage", "zero-faces", "wrong-facet-length"],
    )
    def test_refuses_unframed_converted_output(
        self, cube_obj, tmp_path, monkeypatch, payload
    ):
        source_sha = hashlib.sha256(cube_obj.read_bytes()).hexdigest()
        worker = mesh_isolation.prepared_worker_result

        @contextmanager
        def malformed(*args, **kwargs):
            with worker(*args, **kwargs) as (reply, directory):
                (directory / "mesh.stl").write_bytes(payload)
                manifest = stl_isolation.STLManifest(
                    len(payload), hashlib.sha256(payload).hexdigest()
                )
                yield (
                    replace(reply, payload=stl_isolation.encode_manifest(manifest)),
                    directory,
                )

        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", malformed)

        with pytest.raises(MeshWorkerError) as raised:
            with stl_isolation.prepare_stl(
                cube_obj,
                file_type="obj",
                expected_sha256=source_sha,
                workspace=tmp_path,
            ):
                pytest.fail("unframed conversion yielded output")

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_preserves_ascii_stl_bytes(self, tmp_path):
        source = tmp_path / "ascii.stl"
        payload = trimesh.creation.box().export(file_type="stl_ascii").encode("ascii")
        source.write_bytes(payload)

        with stl_isolation.prepare_stl(
            source,
            file_type="stl",
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            workspace=tmp_path,
        ) as prepared:
            actual = prepared.stream.read()
            prepared.verify()

        assert actual == payload
        assert source.read_bytes() == payload
