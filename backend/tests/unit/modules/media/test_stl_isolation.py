"""The STL conversion worker's reply is a size or an honest "nothing".

The converted mesh itself travels through a file the parent owns, because an STL
of a large model is far bigger than any reply frame should be. The frame only says
how many bytes were written, and the parent checks the file against it: a worker
that dies half way through writing must not be served as a complete model.
"""

from __future__ import annotations

import pytest

from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.stl_isolation import decode_reply, encode_reply
from app.modules.media.thumbnail_engine import ThumbnailFailureReason


class TestReplyFrame:
    @pytest.mark.parametrize("size", [0, 84, 50 * 2_000_000 + 84, 2**40])
    def test_round_trips_a_size(self, size):
        assert decode_reply(encode_reply(size)) == size

    def test_round_trips_a_conversion_that_produced_nothing(self):
        assert decode_reply(encode_reply(None)) is None

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"STL1",
            b"STL1\x00\x00",
            b"NOPE" + bytes(8),
            b"NONEx",
            b"STL1" + bytes(9),
        ],
        ids=["empty", "no-size", "short-size", "wrong-magic", "trailing", "long-size"],
    )
    def test_treats_a_malformed_reply_as_a_worker_failure(self, payload):
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(payload)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestViewerResourceBounds:
    def test_refuses_oversized_stl_before_reading(self, tmp_path):
        from app.modules.media import stl_isolation

        source = tmp_path / "large.stl"
        with source.open("wb") as stream:
            stream.truncate(stl_isolation.MAX_STL_BYTES + 1)
        with pytest.raises(MeshWorkerError) as error:
            stl_isolation.to_stl_bytes(source)
        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert source.stat().st_size == stl_isolation.MAX_STL_BYTES + 1

    def test_reports_unavailable_original(self, tmp_path):
        from app.modules.media import stl_isolation

        assert stl_isolation.to_stl_bytes(tmp_path / "missing.stl") is None

    def test_refuses_missing_worker_output(self, tmp_path, monkeypatch):
        from app.modules.media import stl_isolation

        monkeypatch.setattr(
            stl_isolation.mesh_isolation,
            "run_worker",
            lambda *_args: encode_reply(84),
        )
        with pytest.raises(MeshWorkerError) as error:
            stl_isolation.to_stl_bytes(tmp_path / "mesh.obj")
        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED
