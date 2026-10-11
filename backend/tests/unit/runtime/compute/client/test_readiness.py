"""Parser handoff requires a live final-pixel owner for the measured workload."""

import json
from types import SimpleNamespace

import pytest

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.core.config import _overlay
from app.runtime.compute import client
from app.runtime.compute.contracts import (
    Capability,
    ComputeMode,
    DeviceInfo,
    Operation,
    Reason,
)
from app.runtime.compute.qualification import Qualification


@pytest.fixture
def owner(monkeypatch, tmp_path):
    _overlay["compute_mode"] = "auto"
    _overlay["compute_render_policy"] = "qualified"
    state = client.cpu_status(Reason.READY).model_copy(
        update={
            "device": DeviceInfo(
                identity="adapter",
                name="contract",
                vendor_id=1,
                device_id=2,
                driver="driver",
                backend="Vulkan",
            ),
            "runtime_identity": "runtime",
            "render_postprocess_gpu": True,
            "capabilities": [
                Capability(
                    operation=Operation.RENDER, available=True, reason=Reason.READY
                )
            ],
        }
    )
    receipt = Qualification(
        device_identity="adapter",
        runtime_identity="runtime",
        operation=Operation.RENDER,
        recipe="recipe",
        minimum_units=100,
        maximum_units=1000,
        quality_passed=True,
        source_to_publication_speedup=1.5,
        interactive_p95_ratio=1.1,
        peak_device_bytes=100,
        peak_host_bytes=100,
    )
    result = SimpleNamespace(state=state, calls=[], root=tmp_path, receipt=receipt)

    def status(**kwargs):
        result.calls.append(kwargs)
        kwargs["checkpoint"]()
        return result.state

    (tmp_path / "qualification.json").write_text(
        json.dumps([receipt.model_dump(mode="json")])
    )
    monkeypatch.setattr(client, "available", lambda **kwargs: True)
    monkeypatch.setattr(client, "directory", lambda: tmp_path)
    monkeypatch.setattr(client, "status", status)
    return result


class TestRenderReady:
    @pytest.mark.parametrize(
        "units", [None, 100, 1000], ids=["planning", "minimum", "maximum"]
    )
    def test_accepts_only_existing_qualified_owner(self, owner, units):
        assert client.render_ready(recipe="recipe", units=units)
        assert owner.calls[0]["start"] is False

    @pytest.mark.parametrize("units", [1, 99, 1001], ids=["tiny", "below", "above"])
    def test_retains_unmeasured_models_in_combined_worker(self, owner, units):
        assert not client.render_ready(recipe="recipe", units=units)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("recipe", "old-recipe"),
            ("device_identity", "old-adapter"),
            ("runtime_identity", "old-runtime"),
            ("operation", "dense"),
            ("quality_passed", False),
            ("minimum_units", 1001),
        ],
        ids=["recipe", "adapter", "runtime", "operation", "quality", "empty-interval"],
    )
    def test_rejects_stale_receipts(self, owner, field, value):
        data = {**owner.receipt.model_dump(mode="json"), field: value}
        (owner.root / "qualification.json").write_text(json.dumps([data]))
        assert not client.render_ready(recipe="recipe")

    @pytest.mark.parametrize(
        "payload", [None, "{broken", "[]"], ids=["missing", "corrupt", "empty"]
    )
    def test_requires_current_receipt_contents(self, owner, payload):
        target = owner.root / "qualification.json"
        if payload is None:
            target.unlink()
        else:
            target.write_text(payload)
        assert not client.render_ready(recipe="recipe", units=100)

    @pytest.mark.parametrize("policy", ["preview", "qualified"])
    @pytest.mark.parametrize(
        "change",
        [
            {"mode": ComputeMode.CPU},
            {"device": None},
            {"render_postprocess_gpu": False},
            {
                "capabilities": [
                    Capability(
                        operation=Operation.RENDER,
                        available=False,
                        reason=Reason.DEVICE_FAILED,
                    )
                ]
            },
            {
                "capabilities": [
                    Capability(
                        operation=Operation.DENSE, available=True, reason=Reason.READY
                    )
                ]
            },
        ],
        ids=[
            "cpu",
            "missing-device",
            "cold-postprocessor",
            "unavailable-render",
            "only-inference",
        ],
    )
    def test_requires_live_final_pixel_capability(self, owner, policy, change):
        _overlay["compute_render_policy"] = policy
        owner.state = owner.state.model_copy(update=change)
        assert not client.render_ready(recipe="recipe", units=100)

    def test_preview_requires_no_receipt(self, owner):
        _overlay["compute_render_policy"] = "preview"
        (owner.root / "qualification.json").unlink()
        assert client.render_ready(recipe="unmeasured-preview", units=1)

    def test_disabled_dispatch_skips_owner_probe(self, owner, monkeypatch):
        monkeypatch.setattr(client, "available", lambda **kwargs: False)
        assert not client.render_ready(recipe="recipe")
        assert owner.calls == []

    @pytest.mark.parametrize("units", [0, -1, True, 1.5])
    def test_rejects_invalid_units(self, owner, units):
        with pytest.raises(ValueError, match="compute_render_units"):
            client.render_ready(recipe="recipe", units=units)
        assert owner.calls == []

    def test_cancellation_never_becomes_combined_work(self, owner):
        with cancellation_scope(lambda: True), pytest.raises(OperationCancelled):
            client.render_ready(recipe="recipe")
        assert owner.calls == []

    def test_expired_deadline_never_probes(self, owner, monkeypatch):
        monkeypatch.setattr(client.time, "monotonic", lambda: 10)
        with pytest.raises(TimeoutError, match="compute_deadline"):
            client.render_ready(recipe="recipe", deadline=10)
        assert owner.calls == []

    def test_preserves_deadline_at_owner_probe(self, owner, monkeypatch):
        monkeypatch.setattr(client.time, "monotonic", lambda: 10)
        assert client.render_ready(recipe="recipe", deadline=10.01)
        assert owner.calls[0]["deadline"] == 10.01

    def test_withdrawal_during_receipt_read_propagates(self, owner, monkeypatch):
        from app.runtime.compute import qualification

        withdrawn = False

        def read_receipts(path):
            nonlocal withdrawn
            withdrawn = True
            return (owner.receipt,)

        monkeypatch.setattr(qualification, "read_receipts", read_receipts)
        with cancellation_scope(lambda: withdrawn), pytest.raises(OperationCancelled):
            client.render_ready(
                recipe="recipe",
                checkpoint=lambda: client.cancellation_checkpoint(force=True),
            )
