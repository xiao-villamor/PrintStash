"""Residency credits follow child descriptors without parent thread bindings."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.runtime import inference_resources, native_runtime
from app.runtime.native_admission import LocalResourcePool, Resources


@pytest.fixture
def residency(tmp_path, monkeypatch):
    pool = LocalResourcePool(tmp_path / "inference-models")
    previous = inference_resources.bind_pool(pool)
    monkeypatch.setattr(inference_resources, "capacity", lambda: Resources(1, 1024**3))
    monkeypatch.setattr(
        inference_resources,
        "settings",
        type("Policy", (), {"embedding_worker_memory_mb": 512})(),
    )
    try:
        yield pool
    finally:
        inference_resources.bind_pool(previous)


class TestReserve:
    def test_does_not_bind_parent_native_execution(self, residency):
        before = native_runtime.current_permit()
        with inference_resources.reserve(checkpoint=lambda: None) as permit:
            assert permit.resources == Resources(1, 512 * 1024**2)
            assert native_runtime.current_permit() is before

    def test_inherited_descriptor_retains_residency_after_parent_close(self, residency):
        with inference_resources.reserve(checkpoint=lambda: None) as permit:
            inherited = os.dup(permit.fileno)
        with native_runtime.inherit(inherited, permit.path) as child:
            assert child.resources == Resources(1, 512 * 1024**2)
            assert not inference_resources.has_pressure()
        os.close(inherited)
        with inference_resources.reserve(checkpoint=lambda: None) as replacement:
            assert replacement.identity != permit.identity

    def test_requires_explicit_bootstrap_binding(self):
        previous = inference_resources.bind_pool(None)
        try:
            with pytest.raises(RuntimeError, match="not bound"):
                with inference_resources.reserve(checkpoint=lambda: None):
                    pytest.fail("unbound residency was admitted")
        finally:
            inference_resources.bind_pool(previous)


class TestPressure:
    def test_observes_queued_claim_through_inherited_namespace(self, residency):
        waiting = threading.Event()
        polls = 0

        def checkpoint():
            nonlocal polls
            polls += 1
            if polls >= 3:
                waiting.set()

        def contender():
            with inference_resources.reserve(checkpoint=checkpoint):
                return True

        with ThreadPoolExecutor(1) as executor:
            with inference_resources.reserve(checkpoint=lambda: None) as permit:
                future = executor.submit(contender)
                assert waiting.wait(5)
                with native_runtime.inherit(permit.fileno, permit.path):
                    assert inference_resources.has_pressure()
            assert future.result(timeout=5)


class TestLaunchResources:
    def test_inherits_only_model_residency(self, residency):
        with inference_resources.reserve(checkpoint=lambda: None) as permit:
            launch = inference_resources.launch_resources(permit)

            assert launch.descriptors == (permit.fileno,)
            assert launch.environment == {
                native_runtime.PERMIT_ENV: str(permit.fileno),
                native_runtime.PERMIT_PATH_ENV: str(permit.path),
            }


class TestWorkerMemoryBudget:
    def test_preserves_the_immutable_claimed_ceiling(self, residency):
        with inference_resources.reserve(checkpoint=lambda: None) as permit:
            inference_resources.settings.embedding_worker_memory_mb = 1024

            assert (
                inference_resources.worker_memory_budget_bytes(permit=permit)
                == 512 * 1024**2
            )
            assert inference_resources.worker_memory_budget_bytes() == 1024**3
