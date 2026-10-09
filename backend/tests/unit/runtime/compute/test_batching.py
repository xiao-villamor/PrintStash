"""Compatible tickets retain ordered per-request results when coalesced."""

import base64
import json

from app.runtime.compute.batching import merge
from app.runtime.compute.protocol import InferenceRequest, Priority


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
