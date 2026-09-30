"""Pairwise verification answers across the process boundary without losing detail.

`Verification` is the durable evidence behind a similar-model suggestion, and it
is built from nested tuples and floats that the worker must return unchanged: a
tuple that comes back as a list, or a float that drifts, would change what a
reviewer is shown. Expected geometry failures keep their code, so the run counts
them as it always has; everything else is a worker failure.
"""

from __future__ import annotations

import json

import pytest
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.verification import Verification

from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.thumbnail_engine import ThumbnailFailureReason
from app.modules.media.verification_isolation import (
    decode_reply,
    encode_error,
    encode_reply,
)


def _verification(**overrides) -> Verification:
    values = dict(
        evidence_class="rescaled_mirrored",
        confidence=0.8125,
        scale_factor=2.5,
        mirrored=True,
        mirror_ambiguous=False,
        exact_equivalence=False,
        transform=(
            (1.0, 0.0, 0.0, 0.5),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, -1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        sampled_hausdorff=0.0123,
        sampled_chamfer=0.0045,
        voxel_iou=None,
        convergence_error=1e-7,
        sample_points=256,
        unavailable=(("volume", "open_surface"),),
    )
    values.update(overrides)
    return Verification(**values)


class TestReplyFrame:
    def test_round_trips_the_evidence_exactly(self):
        evidence = _verification()

        assert decode_reply(encode_reply(evidence)) == evidence

    def test_round_trips_evidence_that_found_no_class(self):
        evidence = _verification(evidence_class=None, confidence=0.0, voxel_iou=0.25)

        assert decode_reply(encode_reply(evidence)) == evidence

    def test_keeps_nested_tuples_as_tuples(self):
        decoded = decode_reply(encode_reply(_verification()))

        assert type(decoded.transform) is tuple
        assert type(decoded.transform[0]) is tuple
        assert type(decoded.unavailable[0]) is tuple
        assert type(decoded.evaluation_seeds) is tuple

    def test_re_raises_a_geometry_failure_with_its_code(self):
        with pytest.raises(GeometryError) as raised:
            decode_reply(encode_error(GeometryError("verification_time_limit")))

        assert raised.value.code == "verification_time_limit"

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"VRF1",
            b"VRF1not json",
            b"NOPE{}",
            b"VRF1{}",
            b'VRF1{"evidence_class": null}',
            b"ERR1",
            b"ERR1Not A Code!",
            b"ERR1" + b"a" * 200,
        ],
        ids=[
            "empty",
            "no-body",
            "not-json",
            "wrong-magic",
            "missing-fields",
            "partial-fields",
            "empty-code",
            "unsafe-code",
            "overlong-code",
        ],
    )
    def test_treats_a_malformed_reply_as_a_worker_failure(self, payload):
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(payload)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_a_field_the_evidence_does_not_have(self):
        frame = encode_reply(_verification())
        body = json.loads(frame[4:])
        body["injected"] = 1

        with pytest.raises(MeshWorkerError):
            decode_reply(b"VRF1" + json.dumps(body).encode())
