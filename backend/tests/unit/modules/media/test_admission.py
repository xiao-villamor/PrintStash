"""Configuration changes must never create overlapping permit pools."""

import threading
from concurrent.futures import ThreadPoolExecutor

from app.core.config import _overlay
from app.modules.media import mesh_processing


def test_config_change_waits_for_old_admissions_to_settle(monkeypatch):
    monkeypatch.setattr(mesh_processing, "_RENDER_SEMAPHORE", None)
    monkeypatch.setitem(_overlay, "max_render_jobs", 1)
    old_gate = mesh_processing._render_semaphore()
    old_gate.__enter__()
    entered = threading.Event()
    waiting = threading.Event()
    monkeypatch.setitem(_overlay, "max_render_jobs", 2)

    def new_work():
        gate = mesh_processing._render_semaphore()
        waiting.set()
        with gate:
            entered.set()
            return gate

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(new_work)
            assert waiting.wait(2)
            assert not entered.wait(0.1)
            old_gate.__exit__(None, None, None)
            assert entered.wait(2)
            assert result.result() is old_gate
    finally:
        if not entered.is_set():
            old_gate.__exit__(None, None, None)


def test_disabling_estimates_keeps_divided_safety_budget(monkeypatch):
    from app.modules.media import mesh_isolation

    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    monkeypatch.setattr(mesh_processing, "_detect_memory_limit_bytes", lambda: 1024**3)
    assert mesh_isolation.memory_budget_bytes() == 256 * 1024**2
