"""Model residency leaves explicit host headroom outside geometric work."""

from types import SimpleNamespace

import pytest
from printstash_core.inference import EmbeddingError

from app.runtime import inference_resources
from app.runtime.native_admission import Resources


@pytest.fixture
def memory_policy(monkeypatch):
    policy = SimpleNamespace(
        embedding_memory_budget_fraction=0.25,
        mesh_memory_budget_fraction=0.5,
        embedding_resident_workers=2,
        embedding_worker_memory_mb=1024,
    )
    monkeypatch.setattr(inference_resources, "settings", policy)
    monkeypatch.setattr(inference_resources, "memory_limit_bytes", lambda: 8 * 1024**3)
    return policy


class TestCapacity:
    def test_preserves_separate_model_residency_quota(self, memory_policy):
        assert inference_resources.capacity() == Resources(2, 2 * 1024**3)

    def test_refuses_undetected_physical_capacity(self, memory_policy, monkeypatch):
        monkeypatch.setattr(inference_resources, "memory_limit_bytes", lambda: None)
        with pytest.raises(
            EmbeddingError, match="embedding_memory_capacity_unavailable"
        ):
            inference_resources.capacity()

    @pytest.mark.parametrize(
        "geometry,models",
        [(0.75, 0.25), (0.8, 0.25), (0, 0.6)],
        ids=["no-headroom", "overcommit", "disabled-geometry-estimation"],
    )
    def test_refuses_combined_memory_overcommit(self, memory_policy, geometry, models):
        memory_policy.mesh_memory_budget_fraction = geometry
        memory_policy.embedding_memory_budget_fraction = models
        with pytest.raises(EmbeddingError, match="embedding_memory_budget_invalid"):
            inference_resources.capacity()


class TestPressure:
    def test_requires_an_inherited_residency_permit(self, monkeypatch):
        monkeypatch.setattr(inference_resources, "current_permit", lambda: None)
        with pytest.raises(RuntimeError, match="inherited"):
            inference_resources.has_pressure()


class TestWorkerMemoryBudget:
    def test_preserves_configured_per_model_ceiling(self, memory_policy):
        assert inference_resources.worker_memory_budget_bytes() == 1024**3

    def test_fits_the_physical_residency_partition(self, memory_policy, monkeypatch):
        monkeypatch.setattr(inference_resources, "memory_limit_bytes", lambda: 1024**3)
        assert inference_resources.worker_memory_budget_bytes() == 256 * 1024**2
