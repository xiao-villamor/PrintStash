"""Compatible tickets retain ordered per-request results when coalesced."""

import base64
import json

import numpy as np
import pytest
from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE
from printstash_core.mesh.render_geometry import PreparedRender

from app.modules.media.compute_geometry import decode, encode, split
from app.modules.media.compute_render import RECIPE
from app.runtime.compute.batching import merge
from app.runtime.compute.protocol import BinaryRenderRequest, InferenceRequest, Priority


@pytest.fixture
def render_request():
    prepared = PreparedRender(
        np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
        1,
        lambda size: iter([np.asarray([[0, 1, 2]], dtype=np.int64)]),
        np.arange(3, dtype=np.int64),
        np.tile([0.0, 0.0, 1.0], (3, 1)),
    )
    payload = encode(prepared, 64, 48, [None] * 4, False)
    key, header, body = split(payload)
    request = BinaryRenderRequest(
        deadline=100,
        priority=Priority.BACKGROUND,
        request_id="render",
        recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:64x48:4:0",
        units=4,
        geometry_key=key,
        geometry_bytes=len(body),
        header=header,
    )
    request._decoded = decode(payload)
    return request


def request(text, *, model="same", deadline=100):
    payload = {"config_hash": "a" * 64, "inputs": [{"modality": "text", "text": text}]}
    return InferenceRequest(
        deadline=deadline,
        priority=Priority.BACKGROUND,
        request_id=text,
        directory="/models",
        model_key=model,
        threads=1,
        payload=base64.b64encode(json.dumps(payload).encode()).decode(),
    )


class TestMerge:
    def test_preserves_ticket_association(self):
        batch = merge([request("red"), request("blue")])
        assert batch is not None
        output = json.dumps(
            {
                "config_hash": "a" * 64,
                "vectors": [[1, 0], [0, 1]],
                "truncated": [False, True],
            }
        ).encode()

        results = [json.loads(item) for item in batch.split(output)]

        assert results == [
            {"config_hash": "a" * 64, "vectors": [[1.0, 0.0]], "truncated": [False]},
            {"config_hash": "a" * 64, "vectors": [[0.0, 1.0]], "truncated": [True]},
        ]

    def test_rejects_different_models(self):
        assert merge([request("red"), request("blue", model="different")]) is None

    def test_does_not_exceed_eight_inputs(self):
        assert merge([request(str(index)) for index in range(9)]) is None

    def test_keeps_healthy_members_deadline(self):
        batch = merge([request("red", deadline=50), request("blue", deadline=100)])

        assert batch.request.deadline == 100

    def test_preserves_sparse_member_association(self):
        red = request("red").model_copy(
            update={
                "payload": base64.b64encode(
                    json.dumps({"config_hash": "a" * 64, "sparse_text": "red"}).encode()
                ).decode()
            }
        )
        blue = request("blue").model_copy(
            update={
                "payload": base64.b64encode(
                    json.dumps(
                        {"config_hash": "a" * 64, "sparse_text": "blue"}
                    ).encode()
                ).decode()
            }
        )
        batch = merge([red, blue])

        output = batch.split(
            json.dumps(
                {
                    "sparse_results": [
                        {
                            "config_hash": "a" * 64,
                            "terms": [{"term": "red", "weight": 1}],
                            "truncated": False,
                        },
                        {
                            "config_hash": "a" * 64,
                            "terms": [{"term": "blue", "weight": 2}],
                            "truncated": True,
                        },
                    ]
                }
            ).encode()
        )

        assert json.loads(output[0])["terms"][0]["term"] == "red"
        assert json.loads(output[1])["truncated"] is True


class TestRenderBatchFits:
    def test_accepts_eight_frames(self, render_request):
        from app.runtime.compute.batching import render_batch_fits

        assert render_batch_fits([render_request] * 2, device_bytes=1024**3)

    def test_leaves_excess_frames_for_another_batch(self, render_request):
        from app.runtime.compute.batching import render_batch_fits

        assert not render_batch_fits([render_request] * 3, device_bytes=1024**3)

    def test_respects_device_capacity(self, render_request):
        from app.runtime.compute.batching import render_batch_fits

        assert not render_batch_fits([render_request] * 2, device_bytes=1)

    def test_leaves_invalid_identity_for_individual_execution(self, render_request):
        from app.runtime.compute.batching import render_batch_fits

        invalid = render_request.model_copy(update={"units": 100})

        assert not render_batch_fits([render_request, invalid], device_bytes=1024**3)

    def test_requires_independent_geometry_admission(self, render_request):
        from app.runtime.compute.batching import render_batch_fits

        unprepared = render_request.model_copy()
        unprepared._decoded = None

        assert not render_batch_fits([render_request, unprepared], device_bytes=1024**3)

    def test_preserves_legacy_request_admission(self):
        from app.runtime.compute.batching import render_batch_fits
        from app.runtime.compute.protocol import RenderRequest

        legacy = RenderRequest(
            deadline=100,
            priority=Priority.BACKGROUND,
            request_id="legacy",
            payload="",
            recipe="test",
            units=1,
        )

        assert render_batch_fits([legacy, legacy], device_bytes=1024**3)

    def test_reserves_gpu_packing_staging_once_per_geometry(self, render_request):
        from app.runtime.compute.batching import render_batch_fits
        from app.runtime.compute.render_policy import admission_decoded

        requests = [render_request] * 2
        workspace = admission_decoded(
            [request._decoded for request in requests],
            [request.geometry_bytes for request in requests],
            [request.recipe for request in requests],
            [request.units for request in requests],
            gpu_finalize=True,
        ).device_bytes
        # One resident triangle is 96 bytes. Its compact positions, indices,
        # weld IDs and three normals need another 96 bytes until the fence.
        assert render_batch_fits(requests, device_bytes=workspace + 96)
        assert not render_batch_fits(
            requests, device_bytes=workspace + 96, packing_min_faces=1
        )
        assert render_batch_fits(
            requests, device_bytes=workspace + 192, packing_min_faces=1
        )

    def test_small_cpu_packing_needs_no_gpu_staging(self, render_request):
        from app.runtime.compute.batching import render_batch_fits
        from app.runtime.compute.render_policy import admission_decoded

        requests = [render_request] * 2
        workspace = admission_decoded(
            [request._decoded for request in requests],
            [request.geometry_bytes for request in requests],
            [request.recipe for request in requests],
            [request.units for request in requests],
            gpu_finalize=True,
        ).device_bytes

        assert render_batch_fits(
            requests, device_bytes=workspace + 96, packing_min_faces=2
        )

    def test_reserves_deferred_normal_buffers_once_for_reused_geometry(
        self, render_request
    ):
        from printstash_core.mesh.render_geometry import PreparedRenderGeometry

        from app.modules.media.gpu_normals import defer, device_input_bytes
        from app.runtime.compute.batching import render_batch_fits
        from app.runtime.compute.render_policy import admission_decoded

        prepared, width, height, views, matte = render_request._decoded
        deferred = defer(
            PreparedRenderGeometry(
                prepared.vertices, prepared.face_count, prepared.face_chunks
            )
        )
        payload = encode(deferred, width, height, views, matte)
        key, header, body = split(payload)
        request = render_request.model_copy(
            update={
                "geometry_key": key,
                "header": header,
                "geometry_bytes": len(body),
            }
        )
        request._decoded = decode(payload)
        workspace = admission_decoded(
            [request._decoded] * 2,
            [len(body)] * 2,
            [request.recipe] * 2,
            [request.units] * 2,
            gpu_finalize=True,
        ).device_bytes
        bound = workspace + 96 + device_input_bytes(deferred)

        assert not render_batch_fits([request] * 2, device_bytes=bound - 1)
        assert render_batch_fits([request] * 2, device_bytes=bound)
