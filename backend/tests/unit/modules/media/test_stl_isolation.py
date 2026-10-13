"""The STL conversion worker's reply carries a verified size and digest.

The converted mesh itself travels through a file the parent owns, because an STL
of a large model is far bigger than any reply frame should be. The frame only says
how many bytes were written, and the parent checks the file against it: a worker
that dies half way through writing must not be served as a complete model.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.modules.media import mesh_isolation, stl_isolation
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.mesh_telemetry import (
    SupervisedReply,
    SupervisionStats,
    WorkerExitCause,
)
from app.modules.media.stl_isolation import (
    STLManifest,
    decode_manifest,
    encode_manifest,
)


@pytest.fixture
def reported_worker(monkeypatch):
    def install(manifest):
        payload = encode_manifest(manifest)
        reply = SupervisedReply(
            payload,
            SupervisionStats(
                "a" * 32, 0, None, len(payload), WorkerExitCause.EXITED_ZERO
            ),
        )

        @contextmanager
        def reported(_module, _spec, *, workspace, sources, work, reply_limit):
            yield reply, workspace

        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", reported)

    return install


class TestDecodeManifest:
    @pytest.mark.parametrize(
        "size",
        [0, 84, 50 * 2_000_000 + 84, 2**40],
        ids=["empty", "header", "analysis-cap", "large-report"],
    )
    def test_round_trips_a_size(self, size):
        manifest = STLManifest(size, "a" * 64)

        assert decode_manifest(encode_manifest(manifest)) == manifest

    @pytest.mark.parametrize("reason", list(ThumbnailFailureReason))
    def test_preserves_a_typed_failure(self, reason):
        from app.modules.media.stl_isolation import FAILURE_MAGIC

        with pytest.raises(MeshWorkerError) as raised:
            decode_manifest(FAILURE_MAGIC + reason.value.encode("ascii"))

        assert raised.value.reason is reason

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"FAILunknown",
            b"FAIL\xff",
            b"STL2",
            b"STL2\x00\x00",
            b"NOPE" + bytes(8),
            b"NONEx",
            b"STL2" + bytes(9),
        ],
        ids=[
            "empty",
            "unknown-failure",
            "invalid-ascii",
            "no-size",
            "short-size",
            "wrong-magic",
            "trailing",
            "long-size",
        ],
    )
    def test_treats_a_malformed_reply_as_a_worker_failure(self, payload):
        with pytest.raises(MeshWorkerError) as raised:
            decode_manifest(payload)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestPrepareStl:
    def test_refuses_oversized_stl_before_reading(self, tmp_path):
        from app.modules.media import stl_isolation

        source = tmp_path / "large.stl"
        with source.open("wb") as stream:
            stream.truncate(stl_isolation.MAX_STL_BYTES + 1)
        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                source, file_type="stl", expected_sha256="0" * 64, workspace=tmp_path
            ):
                pytest.fail("oversized STL yielded")
        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert source.stat().st_size == stl_isolation.MAX_STL_BYTES + 1

    def test_reports_unavailable_original(self, tmp_path):
        from app.modules.media import stl_isolation

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                tmp_path / "missing.stl",
                file_type="stl",
                expected_sha256="0" * 64,
                workspace=tmp_path,
            ):
                pytest.fail("missing source yielded")

        assert error.value.reason is ThumbnailFailureReason.STORAGE

    def test_refuses_missing_worker_output(self, tmp_path, reported_worker):
        source = tmp_path / "mesh.obj"
        source.write_bytes(b"source")
        reported_worker(STLManifest(84, "0" * 64))

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                source, file_type="obj", expected_sha256="0" * 64, workspace=tmp_path
            ):
                pytest.fail("missing output yielded")

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_refuses_oversized_worker_output(self, tmp_path, reported_worker):
        source = tmp_path / "mesh.obj"
        source.write_bytes(b"source")
        reported_worker(STLManifest(stl_isolation.MAX_STL_BYTES + 1, "0" * 64))

        with pytest.raises(MeshWorkerError) as error:
            with stl_isolation.prepare_stl(
                source, file_type="obj", expected_sha256="0" * 64, workspace=tmp_path
            ):
                pytest.fail("oversized output yielded")

        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT


class TestConvert:
    def test_does_not_hide_export_allocation_failure(self, tmp_path, monkeypatch):
        import trimesh

        from app.modules.media import stl_worker

        source = tmp_path / "cube.obj"
        trimesh.creation.box().export(source, file_type="obj")

        def exhausted(*args, **kwargs):
            raise MemoryError("export allocation")

        monkeypatch.setattr(trimesh.Trimesh, "export", exhausted)

        with pytest.raises(MemoryError):
            stl_worker.convert(source, "obj", tmp_path / "mesh.stl")
