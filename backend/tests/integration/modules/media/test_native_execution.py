"""Queue telemetry describes resource admission, even when work never starts."""

import json

import pytest
from printstash_core.inference import EmbeddingError

from app.core.cancellation import OperationCancelled
from app.modules.media import native_execution
from app.runtime import native_runtime
from app.runtime.native_admission import LocalResourcePool, Resources


def admission_records(caplog):
    return [
        json.loads(record.getMessage().removeprefix("mesh_admission "))
        for record in caplog.records
        if record.getMessage().startswith("mesh_admission ")
    ]


@pytest.fixture
def pool(tmp_path):
    owner = LocalResourcePool(tmp_path)
    prior = native_runtime.bind_pool(owner)
    try:
        yield owner
    finally:
        native_runtime.bind_pool(prior)


class TestAdmissionTelemetry:
    def test_excludes_work_time_from_queue_duration(self, pool, caplog, monkeypatch):
        moments = iter([100, 140])
        monkeypatch.setattr(native_execution, "monotonic_ns", lambda: next(moments))
        amount, capacity = Resources(1, 40), Resources(2, 100)

        with pytest.raises(ValueError, match="work failed"):
            with native_execution.admission(amount, capacity, checkpoint=lambda: None):
                (record,) = admission_records(caplog)
                assert record["elapsed_ns"] == 40
                assert record["requested_slots"] == 1
                assert record["requested_bytes"] == 40
                assert record["capacity_slots"] == 2
                assert record["capacity_bytes"] == 100
                assert record["outcome"] == "admitted"
                raise ValueError("work failed")

        assert len(admission_records(caplog)) == 1
        with pool.reserve(capacity, capacity, checkpoint=lambda: None) as permit:
            assert permit.resources == capacity

    @pytest.mark.parametrize(
        "error,outcome",
        [
            (OperationCancelled(), "cancelled"),
            (EmbeddingError("inference_cancelled"), "cancelled"),
            (EmbeddingError("inference_timeout"), "deadline"),
            (EmbeddingError("inference_invalid"), "failed"),
        ],
        ids=["job-cancelled", "inference-cancelled", "deadline", "failure"],
    )
    def test_records_withdrawal_before_execution(self, pool, caplog, error, outcome):
        amount = Resources(1, 100)
        calls = 0

        def withdraw():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise error

        with pool.reserve(amount, amount, checkpoint=lambda: None):
            with pytest.raises(type(error)) as observed:
                with native_execution.admission(amount, amount, checkpoint=withdraw):
                    pytest.fail("withdrawn work was started")
        assert observed.value is error
        (record,) = admission_records(caplog)
        assert record["outcome"] == outcome
        assert record["elapsed_ns"] > 0
        with pool.reserve(amount, amount, checkpoint=lambda: None) as permit:
            assert permit.resources == amount

    def test_nested_work_does_not_emit_another_queue_record(self, pool, caplog):
        amount = Resources(1, 100)
        with native_execution.admission(
            amount, amount, checkpoint=lambda: None
        ) as outer:
            with native_execution.admission(
                amount, amount, checkpoint=lambda: None
            ) as inner:
                assert inner is outer
        (record,) = admission_records(caplog)
        assert record["outcome"] == "admitted"

    def test_unbound_runtime_reports_failed_admission(self, pool, caplog):
        native_runtime.bind_pool(None)
        amount = Resources(1, 100)
        with pytest.raises(RuntimeError, match="not bound"):
            with native_execution.admission(amount, amount, checkpoint=lambda: None):
                pytest.fail("unbound work was started")
        (record,) = admission_records(caplog)
        assert record["outcome"] == "failed"
