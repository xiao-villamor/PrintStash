"""Injected allocation faults exercise bounded retries, never hardware claims."""

import json
import time

import pytest

from app.runtime.compute.contracts import ComputeUnavailable, Reason
from app.runtime.compute.recovery import execute


class Worker:
    def __init__(self, fail_above=2):
        self.calls = []
        self.fail_above = fail_above

    def execute(self, payload):
        data = json.loads(payload)
        items = data["inputs"]
        self.calls.append(len(items))
        if len(items) > self.fail_above:
            raise MemoryError()
        return json.dumps(
            {
                "config_hash": data["config_hash"],
                "vectors": [[int(item["text"])] for item in items],
                "truncated": [False] * len(items),
            }
        ).encode()


def payload(count):
    return json.dumps(
        {
            "config_hash": "a" * 64,
            "inputs": [{"modality": "text", "text": str(i)} for i in range(count)],
        }
    ).encode()


class TestAllocationRecovery:
    def test_splits_once_with_source_order(self):
        worker = Worker()
        evictions = []

        result = execute(
            worker, payload(4), time.monotonic() + 1, lambda: evictions.append(True)
        )

        assert json.loads(result)["vectors"] == [[0], [1], [2], [3]]
        assert worker.calls == [4, 2, 2]
        assert evictions == [True]

    def test_stops_after_failed_split(self):
        worker = Worker(fail_above=0)

        with pytest.raises(ComputeUnavailable) as exc:
            execute(worker, payload(4), time.monotonic() + 1, lambda: None)

        assert exc.value.reason == Reason.CAPACITY
        assert worker.calls == [4, 2]

    def test_does_not_retry_expired_work(self):
        worker = Worker()

        with pytest.raises(ComputeUnavailable) as exc:
            execute(worker, payload(4), time.monotonic() - 1, lambda: None)

        assert exc.value.reason == Reason.DEADLINE
        assert worker.calls == [4]


class TestRecoveryBoundaries:
    def test_refuses_splitting_a_singleton(self):
        worker = Worker(fail_above=0)
        with pytest.raises(ComputeUnavailable) as exc:
            execute(
                worker,
                payload(1),
                time.monotonic() + 1,
                lambda: pytest.fail("evicted singleton"),
            )
        assert exc.value.reason is Reason.CAPACITY
        assert worker.calls == [1]

    def test_preserves_nonallocation_fault_after_split(self):
        from unittest.mock import Mock

        worker = Mock()
        worker.execute.side_effect = [MemoryError(), ValueError("invalid model")]
        with pytest.raises(ValueError, match="invalid model"):
            execute(worker, payload(4), time.monotonic() + 1, lambda: None)
        assert worker.execute.call_count == 2

    def test_rejects_changed_identity_after_split(self):
        from unittest.mock import Mock

        worker = Mock()
        output = json.dumps(
            {
                "config_hash": "b" * 64,
                "vectors": [[1], [2]],
                "truncated": [False, False],
            }
        ).encode()
        worker.execute.side_effect = [MemoryError(), output, output]
        with pytest.raises(ValueError, match="compute_batch_identity_mismatch"):
            execute(worker, payload(4), time.monotonic() + 1, lambda: None)

    def test_rejoins_sparse_results_after_allocation_recovery(self):
        from unittest.mock import Mock

        worker = Mock()
        worker.execute.side_effect = [
            MemoryError(),
            b'{"sparse_results":[{"term":"a"}]}',
            b'{"sparse_results":[{"term":"b"}]}',
        ]
        message = json.dumps(
            {"config_hash": "a" * 64, "sparse_texts": ["a", "b"]}
        ).encode()
        assert json.loads(
            execute(worker, message, time.monotonic() + 1, lambda: None)
        ) == {"sparse_results": [{"term": "a"}, {"term": "b"}]}
        assert [
            json.loads(call.args[0])["sparse_texts"]
            for call in worker.execute.call_args_list
        ] == [["a", "b"], ["a"], ["b"]]
