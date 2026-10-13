"""Direct STEP callers share guarded process ownership through native decoding."""

import json
import sys
from pathlib import Path

import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError

from app.core.config import _overlay
from app.modules.media import mesh_isolation, mesh_loading, mesh_policy, native_process
from app.modules.media.worker_bootstrap import command
from app.runtime.native_admission import Resources
from tests.paths import FIXTURES_DIR


@pytest.fixture
def step_fault_worker_factory(tmp_path, monkeypatch):
    budget = 160 * 1024**2
    monkeypatch.setattr(native_process, "native_capacity", lambda: Resources(1, budget))
    monkeypatch.setattr(native_process, "native_memory_budget_bytes", lambda: budget)
    monkeypatch.setitem(_overlay, "mesh_step_timeout_seconds", 1.5)
    original = mesh_isolation.subprocess.Popen
    pids = tmp_path / "pids.json"
    scripts = {
        "face-cap": "raise SystemExit(3)",
        "invalid": "raise SystemExit(4)",
        "unavailable": "raise SystemExit(7)",
    }

    def configure(case):
        def spawn(_argv, **kwargs):
            arguments = {
                "tree": ["tree", str(pids), "100"],
                "deadline": ["close_stdout_wait"],
            }
            argv = (
                command("tests.fakes.mesh_bootstrap_probe", arguments[case], budget)
                if case in arguments
                else [sys.executable, "-c", scripts[case]]
            )
            return original(argv, **kwargs)

        monkeypatch.setattr(mesh_isolation.subprocess, "Popen", spawn)
        return pids

    return configure


@pytest.fixture
def step_fault_worker(step_fault_worker_factory, request):
    return step_fault_worker_factory(request.param)


@pytest.fixture
def descendant_step_worker(step_fault_worker_factory):
    return step_fault_worker_factory("tree")


@pytest.fixture
def deadline_step_worker(step_fault_worker_factory):
    return step_fault_worker_factory("deadline")


class TestLoadStepMeshIsolated:
    def test_retains_credit_while_decoding(self, monkeypatch):
        from app.runtime.native_runtime import current_permit

        original = trimesh.load_mesh
        observed = []

        def decode(source, **kwargs):
            source = Path(source)
            permit = current_permit()
            assert permit is not None
            assert source.parent.name.startswith("printstash-mesh-")
            assert source.exists()
            observed.append(source.parent)
            return original(source, **kwargs)

        monkeypatch.setattr(trimesh, "load_mesh", decode)

        mesh = mesh_loading.load_step_mesh(
            FIXTURES_DIR / "cascadio_material.stp",
            strict_failures=True,
        )

        assert len(mesh.faces) == 12
        assert len(observed) == 1
        assert not observed[0].exists()
        assert current_permit() is None

    @pytest.mark.parametrize(
        "step_fault_worker, expected",
        [
            ("face-cap", "geometry_work_limit"),
            ("invalid", "invalid_step"),
            ("unavailable", "step_unavailable"),
        ],
        indirect=["step_fault_worker"],
        ids=["face-cap", "invalid", "unavailable"],
    )
    def test_preserves_domain_exit_causes(self, step_fault_worker, expected):
        with pytest.raises(GeometryError) as error:
            mesh_loading.load_step_mesh(
                FIXTURES_DIR / "cascadio_material.stp",
                strict_failures=True,
            )

        assert error.value.code == expected

    def test_refuses_descendant_memory(self, descendant_step_worker):
        with pytest.raises(GeometryError) as error:
            mesh_loading.load_step_mesh(
                FIXTURES_DIR / "cascadio_material.stp",
                strict_failures=True,
            )

        assert error.value.code == "worker_oom"
        assert all(
            not Path(f"/proc/{pid}").exists()
            for pid in json.loads(descendant_step_worker.read_text())
        )

    def test_deadline_remains_active_after_stdout_eof(self, deadline_step_worker):
        with pytest.raises(GeometryError) as error:
            mesh_loading.load_step_mesh(
                FIXTURES_DIR / "cascadio_material.stp",
                strict_failures=True,
            )

        assert error.value.code == "tessellation_timeout"

    def test_nested_work_consumes_existing_credit(self, monkeypatch):
        import os

        from app.modules.media.worker_bootstrap import WORKER_MARKER
        from app.runtime.native_runtime import current_permit

        def unexpected(*args, **kwargs):
            raise AssertionError("nested STEP must not launch another supervisor")

        monkeypatch.setattr(mesh_isolation, "supervise_result", unexpected)
        monkeypatch.setenv(WORKER_MARKER, str(os.getpid()))

        with mesh_policy.render_admission() as permit:
            mesh = mesh_loading.load_step_mesh(
                FIXTURES_DIR / "cascadio_material.stp",
                strict_failures=True,
            )
            assert current_permit() is permit

        assert len(mesh.faces) == 12
        assert current_permit() is None

    def test_cancellation_reaps_owned_descendants(self, descendant_step_worker):
        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.runtime.native_runtime import current_permit

        with (
            cancellation_scope(descendant_step_worker.exists),
            pytest.raises(OperationCancelled),
        ):
            mesh_loading.load_step_mesh(
                FIXTURES_DIR / "cascadio_material.stp",
                strict_failures=True,
            )

        assert current_permit() is None
        assert all(
            not Path(f"/proc/{pid}").exists()
            for pid in json.loads(descendant_step_worker.read_text())
        )
