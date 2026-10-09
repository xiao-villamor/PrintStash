"""Invalid, stale or insufficient benchmark evidence never enables GPU routing."""

import pytest
from pydantic import ValidationError

from app.runtime.compute.contracts import DeviceInfo, Operation
from app.runtime.compute.qualification import Qualification, read_receipts


@pytest.fixture
def device():
    return DeviceInfo(
        identity="adapter",
        name="contract",
        vendor_id=1,
        device_id=2,
        driver="1",
        backend="Vulkan",
    )


@pytest.fixture
def receipt():
    return Qualification(
        device_identity="adapter",
        runtime_identity="runtime",
        operation=Operation.RENDER,
        recipe="v1",
        minimum_units=100,
        maximum_units=1000,
        quality_passed=True,
        source_to_publication_speedup=1.5,
        interactive_p95_ratio=1.1,
        peak_device_bytes=100,
        peak_host_bytes=100,
    )


class TestQualification:
    def test_accepts_the_measured_workload(self, device, receipt):
        assert receipt.accepts(device, "runtime", Operation.RENDER, "v1", 100)

    @pytest.mark.parametrize("units", [1, 99, 1001], ids=["tiny", "below", "above"])
    def test_refuses_unmeasured_workload_sizes(self, device, receipt, units):
        assert not receipt.accepts(device, "runtime", Operation.RENDER, "v1", units)

    def test_refuses_changed_runtime(self, device, receipt):
        assert not receipt.accepts(device, "different", Operation.RENDER, "v1", 100)

    def test_refuses_failed_quality(self, device, receipt):
        assert not receipt.model_copy(update={"quality_passed": False}).accepts(
            device, "runtime", Operation.RENDER, "v1", 100
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [("source_to_publication_speedup", 1.49), ("interactive_p95_ratio", 1.11)],
        ids=["speed", "latency"],
    )
    def test_rejects_insufficient_performance(self, receipt, field, value):
        with pytest.raises(ValidationError):
            Qualification.model_validate({**receipt.model_dump(), field: value})


class TestReadReceipts:
    def test_refuses_missing_evidence(self, tmp_path):
        assert read_receipts(tmp_path / "absent.json") == ()

    def test_refuses_malformed_evidence(self, tmp_path):
        target = tmp_path / "qualification.json"
        target.write_text("{broken")

        assert read_receipts(target) == ()
