"""The views a similarity embedding is computed from survive the process boundary.

Six opaque RGB frames are what a learned embedding sees of a model, so a byte that
changes on the way back changes the vector that is stored and searched. Expected
geometry failures keep their code, so the run counts them as it always has;
anything else is a worker failure.
"""

from __future__ import annotations

import json

import pytest
from printstash_core.inference import EmbeddingInput
from printstash_core.mesh.similarity import GeometryError

from app.modules.media.embedding_isolation import decode_reply, encode_reply
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError, encode_error


def _view(seed: int, size: int = 32) -> EmbeddingInput:
    pixels = bytes((seed * 31 + index) % 256 for index in range(size * size * 3))
    return EmbeddingInput("image", rgb=pixels, width=size, height=size)


class TestReplyFrame:
    def test_round_trips_six_views_byte_for_byte(self):
        views = tuple(_view(seed) for seed in range(6))

        assert decode_reply(encode_reply(views)) == views

    def test_re_raises_a_geometry_failure_with_its_code(self):
        with pytest.raises(GeometryError) as raised:
            decode_reply(encode_error(GeometryError("embedding_view_failed")))

        assert raised.value.code == "embedding_view_failed"

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"EMB1",
            b"EMB1not json",
            b"NOPE[]",
            b"EMB1{}",
            b"EMB1[]",
            b"ERR1Not A Code!",
            b"ERR1",
        ],
        ids=[
            "empty",
            "no-body",
            "not-json",
            "wrong-magic",
            "not-a-list",
            "no-views",
            "unsafe-code",
            "empty-code",
        ],
    )
    def test_treats_a_malformed_reply_as_a_worker_failure(self, payload):
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(payload)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_a_view_the_input_type_itself_refuses(self):
        """`EmbeddingInput` validates its own bounds; the parent relies on that."""
        frame = encode_reply([_view(1)])
        body = json.loads(frame[4:])
        body[0]["width"] = 999  # pixels no longer fill width x height

        with pytest.raises(MeshWorkerError):
            decode_reply(b"EMB1" + json.dumps(body).encode())

    def test_rejects_more_views_than_a_render_pass_produces(self):
        frame = encode_reply([_view(seed) for seed in range(6)])
        body = json.loads(frame[4:]) * 3

        with pytest.raises(MeshWorkerError):
            decode_reply(b"EMB1" + json.dumps(body).encode())

    def test_rejects_a_view_that_is_not_an_image(self):
        frame = encode_reply([_view(1)])
        body = json.loads(frame[4:])
        body[0]["modality"] = "text"

        with pytest.raises(MeshWorkerError):
            decode_reply(b"EMB1" + json.dumps(body).encode())


class TestComponentFrame:
    def test_preserves_component_error_association(self):
        from printstash_core.inference import EmbeddingInput
        from printstash_core.mesh.similarity import GeometryError

        from app.modules.media.embedding_isolation import (
            decode_components,
            encode_components,
        )

        views = tuple(
            EmbeddingInput("image", width=1, height=1, rgb=b"abc") for _ in range(6)
        )

        results = decode_components(
            encode_components((views, GeometryError("component_unavailable"))), 2
        )

        assert results[0] == views
        assert isinstance(results[1], GeometryError)
        assert results[1].code == "component_unavailable"
