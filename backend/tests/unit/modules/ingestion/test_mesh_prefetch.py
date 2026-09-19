"""Parallel computation must retain ordered, bounded consumption."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.core.config import _overlay
from app.modules.ingestion.mesh_prefetch import PreparedImports
from app.modules.media import mesh_operations
from app.modules.media import render_budget as rb


@pytest.fixture
def parallel(monkeypatch, tmp_path):
    monkeypatch.setitem(_overlay, "import_workers", 2)
    monkeypatch.setattr(rb, "effective_cpus", lambda: 4)
    monkeypatch.setattr(rb, "memory_budget", lambda: 1024 * rb.MIB)
    monkeypatch.setattr(rb, "estimate_work", lambda *args: 128 * rb.MIB)
    monkeypatch.setattr(rb, "budget", rb.RenderBudget())
    paths = [
        (tmp_path / name, name) for name in ("first.stl", "second.stl", "third.stl")
    ]
    for path, _ in paths:
        path.write_bytes(b"mesh")
    return paths


class TestPreparedImports:
    def test_reports_activity_while_a_mesh_stage_has_not_changed(
        self, parallel, monkeypatch
    ):
        from app.modules.ingestion import mesh_prefetch

        reported = []
        heartbeat = Event()
        clock = iter((0.0, 1.1, 2.2, 3.3, 4.4))
        monkeypatch.setattr(mesh_prefetch, "monotonic", lambda: next(clock))

        def analyze(*args, **kwargs):
            assert heartbeat.wait(5), "unchanged mesh stage stopped reporting activity"
            return {}, b"preview"

        def observe(progress):
            reported.append(progress)
            if len(reported) >= 2:
                heartbeat.set()

        monkeypatch.setattr(mesh_operations, "analyze_mesh", analyze)
        with PreparedImports(parallel[:1], observe) as prepared:
            result = list(prepared)

        assert result[0][1].error is None
        assert reported[0] == reported[1]

    def test_computes_concurrently_in_input_order(self, parallel, monkeypatch):
        second_started = Event()

        def analyze(path, **kwargs):
            if path.name == "first.stl":
                assert second_started.wait(5), "second mesh did not run concurrently"
            else:
                second_started.set()
            return {"name": path.name}, b"thumbnail"

        monkeypatch.setattr(mesh_operations, "analyze_mesh", analyze)
        with PreparedImports(parallel, lambda p: None) as prepared:
            names = [
                analysis.process(path, lambda label: None)[0]["name"]
                for (path, _), analysis in prepared
            ]
        assert names == [name for _, name in parallel]

    def test_retains_reservations_until_consumed(self, parallel, monkeypatch):
        monkeypatch.setattr(
            mesh_operations, "analyze_mesh", lambda *a, **k: ({}, b"preview")
        )
        with PreparedImports(parallel, lambda p: None) as prepared:
            iterator = iter(prepared)
            next(iterator)
            assert rb.budget.used == 256 * rb.MIB
            assert rb.budget.jobs == 2
        assert rb.budget.used == 0

    def test_writer_failure_releases_admission(self, parallel, monkeypatch):
        monkeypatch.setattr(
            mesh_operations, "analyze_mesh", lambda *a, **k: ({}, b"preview")
        )
        with pytest.raises(RuntimeError, match="writer failed"):
            with PreparedImports(parallel, lambda p: None) as prepared:
                for _ in prepared:
                    raise RuntimeError("writer failed")
        assert rb.budget.used == 0
        assert rb.budget.jobs == 0

    def test_compute_failure_is_a_per_file_result(self, parallel, monkeypatch):
        def analyze(path, **kwargs):
            if path.name == "first.stl":
                raise ValueError("bad mesh")
            return {"name": path.name}, b"preview"

        monkeypatch.setattr(mesh_operations, "analyze_mesh", analyze)
        with PreparedImports(parallel, lambda p: None) as prepared:
            iterator = iter(prepared)
            (path, _), result = next(iterator)
            with pytest.raises(RuntimeError, match="bad mesh"):
                result.process(path, lambda label: None)
            remaining = [
                result.process(path, lambda label: None)[0]["name"]
                for (path, _), result in iterator
            ]
        assert remaining == ["second.stl", "third.stl"]

    def test_serial_setting_defers_to_normal_pipeline(self, parallel, monkeypatch):
        monkeypatch.setitem(_overlay, "import_workers", 1)
        with PreparedImports(parallel, lambda p: None) as prepared:
            results = list(prepared)
        assert [analysis for _, analysis in results] == [None] * 3

    def test_unsupported_files_keep_normal_pipeline(self, parallel):
        files = [(parallel[0][0], name) for name in ("readme.txt", "print.gcode")]
        with PreparedImports(files, lambda p: None) as prepared:
            assert [analysis for _, analysis in prepared] == [None, None]

    def test_large_reservation_waits_for_other_import(self, parallel, monkeypatch):
        monkeypatch.setattr(rb, "estimate_work", lambda *args: 1024 * rb.MIB)
        entered = Event()

        def analyze(*args, **kwargs):
            entered.set()
            return {}, b"preview"

        monkeypatch.setattr(mesh_operations, "analyze_mesh", analyze)
        lease = rb.budget.acquire(128 * rb.MIB, capacity=1024 * rb.MIB, jobs=2)
        with ThreadPoolExecutor(max_workers=1) as executor:

            def consume():
                with PreparedImports(parallel[:1], lambda p: None) as prepared:
                    return list(prepared)

            future = executor.submit(consume)
            assert not entered.wait(0.1)
            lease.release()
            assert future.result(timeout=5)[0][1].value == ({}, b"preview")

    def test_reports_current_file_during_computation(self, parallel, monkeypatch):
        names = []
        progress = []
        started = Event()

        def analyze(path, report, **kwargs):
            report("loading_mesh")
            report("extracting_geometry")
            assert started.wait(5)
            return {}, b"preview"

        monkeypatch.setattr(mesh_operations, "analyze_mesh", analyze)

        def observe(value):
            progress.append((names[-1], value))
            if value > 0:
                started.set()

        with PreparedImports(parallel[:1], observe, names.append) as prepared:
            results = list(prepared)
        assert results[0][1].error is None
        assert any(name == "first.stl" and 0 < value < 100 for name, value in progress)

    def test_missing_input_does_not_abort_remaining_files(self, parallel, monkeypatch):
        parallel[0][0].unlink()

        def analyze(path, **kwargs):
            return {"size": len(path.read_bytes())}, b"preview"

        monkeypatch.setattr(mesh_operations, "analyze_mesh", analyze)
        with PreparedImports(parallel, lambda p: None) as prepared:
            results = [analysis for _, analysis in prepared]
        assert results[0].error is not None
        assert results[1].value == ({"size": 4}, b"preview")

    def test_full_budget_files_are_consumed_without_deadlock(
        self, parallel, monkeypatch
    ):
        monkeypatch.setattr(rb, "estimate_work", lambda *args: 1024 * rb.MIB)
        monkeypatch.setattr(
            mesh_operations, "analyze_mesh", lambda *a, **k: ({}, b"preview")
        )
        with PreparedImports(parallel, lambda p: None) as prepared:
            names = [item[1] for item, _ in prepared]
        assert names == [name for _, name in parallel]
        assert rb.budget.used == 0

    def test_failed_submission_releases_reservation(self, parallel):
        with PreparedImports(parallel, lambda p: None) as prepared:
            prepared.executor.shutdown()
            with pytest.raises(RuntimeError, match="cannot schedule new futures"):
                next(iter(prepared))
            assert rb.budget.used == 0
            assert rb.budget.jobs == 0

    def test_unreadable_estimate_uses_conservative_admission(
        self, parallel, monkeypatch
    ):
        def unavailable(*args):
            raise OSError("unreadable estimate")

        monkeypatch.setattr(rb, "estimate_work", unavailable)
        monkeypatch.setattr(
            mesh_operations, "analyze_mesh", lambda *a, **k: ({}, b"preview")
        )
        with PreparedImports(parallel[:1], lambda p: None) as prepared:
            results = list(prepared)
        assert results[0][1].value == ({}, b"preview")

    def test_replays_bounded_progress_labels(self, parallel, monkeypatch):
        def analyze(path, report, **kwargs):
            for label in (
                "loading_mesh",
                "loading_mesh",
                "extracting_geometry",
                "rendering_thumbnail",
                "unexpected",
            ):
                report(label)
            return {}, b"preview"

        monkeypatch.setattr(mesh_operations, "analyze_mesh", analyze)
        with PreparedImports(parallel[:1], lambda p: None) as prepared:
            (path, _), analysis = next(iter(prepared))
            labels = []
            analysis.process(path, labels.append)
        assert labels == ["loading_mesh", "extracting_geometry", "rendering_thumbnail"]
