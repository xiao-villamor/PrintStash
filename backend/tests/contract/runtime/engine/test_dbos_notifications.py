"""Durable DBOS work progresses under long fallback intervals.

The cases need the patched SDK, which the shared environment never carries:
without it, one test patches a disposable copy of the installed SDK and runs
this module again in a subprocess with that copy first on PYTHONPATH. The real
database, queue dispatcher and workflow executor are used. A five-second
outcome deadline is well below the 30-second recovery scan, so missing wakeups
fail visibly.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections import defaultdict

import pytest
from dbos import DBOS, SetEnqueueOptions, SetWorkflowID

from tests.paths import BACKEND_DIR


def _sdk_patched() -> bool:
    from app.runtime.engine.dbos_notifications import verify_patch

    try:
        verify_patch()
    except RuntimeError:
        return False
    return True


PATCHED = _sdk_patched()


class TestDispatchUnderAPatchedSdk:
    """The shared environment never carries the patch: patch a disposable copy."""

    def test_runs_every_dispatch_case_on_a_disposable_patched_copy(self, tmp_path):
        import importlib.util
        import os
        import shutil
        from pathlib import Path

        spec = importlib.util.find_spec("dbos")
        assert spec is not None and spec.submodule_search_locations
        copy = tmp_path / "sdk" / "dbos"
        shutil.copytree(Path(next(iter(spec.submodule_search_locations))), copy)
        subprocess.run(
            [sys.executable, "scripts/experimental_dbos_notifications.py", str(copy)],
            cwd=BACKEND_DIR,
            check=True,
            capture_output=True,
            timeout=60,
        )
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(copy.parent), str(BACKEND_DIR)]),
        }
        result = subprocess.run(
            [sys.executable, "-m", "pytest", __file__, "-q", "-p", "no:cacheprovider"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]
        assert " passed" in result.stdout and " failed" not in result.stdout


class Runtime:
    def __init__(self, path):
        self.path = path
        self.started = defaultdict(threading.Event)
        self.gates = {}
        self.finished = []
        self.failures = set()
        self.launch()

    def launch(self, *, listen=True):
        DBOS.destroy(destroy_registry=True)
        from app.runtime.engine.dbos_notifications import signals, verify_patch

        verify_patch()
        signals.configure(enabled=True)
        DBOS(
            config={
                "name": "notify-contract",
                "system_database_url": f"sqlite:///{self.path}",
                "application_version": "notify-contract",
                "executor_id": "notify-contract",
                "log_level": "ERROR",
            }
        )

        @DBOS.workflow(name="notification.contract")
        def work(label):
            self.started[label].set()
            gate = self.gates.get(label)
            if gate is not None:
                assert gate.wait(15), "test gate never opened"
            if label in self.failures:
                raise ValueError("intentional workflow failure")
            self.finished.append(label)
            return label

        self.work = work
        DBOS.listen_queues(["notify-contract"] if listen else [])
        DBOS.launch()
        self.queue = DBOS.register_queue(
            "notify-contract",
            worker_concurrency=1,
            polling_interval_sec=0.05,
            on_conflict="always_update",
        )

    def enqueue(self, label):
        with SetWorkflowID("notify-" + label):
            return self.queue.enqueue(self.work, label)

    def prime(self):
        handle = self.enqueue("warmup")
        assert self.started["warmup"].wait(5)
        assert handle.get_result(polling_interval_sec=0.01) == "warmup"
        self.queue.set_polling_interval_sec(60)
        time.sleep(0.2)

    def close(self):
        for gate in self.gates.values():
            gate.set()
        DBOS.destroy(destroy_registry=True, workflow_completion_timeout_sec=5)
        from app.runtime.engine.dbos_notifications import signals

        signals.configure(enabled=False)


@pytest.fixture
def runtime(tmp_path):
    runtime = Runtime(tmp_path / "engine.sqlite")
    try:
        runtime.prime()
        yield runtime
    finally:
        runtime.close()


class TestNotificationDispatch:
    def test_committed_enqueue_wakes_the_dispatcher(self, runtime):
        handle = runtime.enqueue("ready")

        assert runtime.started["ready"].wait(5), "enqueue notification was missed"
        assert handle.get_result(polling_interval_sec=0.01) == "ready"

    def test_completion_releases_queued_work(self, runtime):
        gate = runtime.gates["first"] = threading.Event()
        runtime.enqueue("first")
        assert runtime.started["first"].wait(5)
        second = runtime.enqueue("second")
        assert not runtime.started["second"].wait(0.1)

        gate.set()

        assert runtime.started["second"].wait(5), "capacity release did not wake queue"
        assert second.get_result(polling_interval_sec=0.01) == "second"

    def test_failure_releases_queued_work(self, runtime):
        runtime.failures.add("fails")
        first = runtime.enqueue("fails")
        second = runtime.enqueue("survives")

        assert runtime.started["survives"].wait(5)
        assert second.get_result(polling_interval_sec=0.01) == "survives"
        with pytest.raises(ValueError, match="intentional"):
            first.get_result(polling_interval_sec=0.01)

    def test_delayed_work_wakes_after_its_deadline(self, runtime):
        with SetEnqueueOptions(delay_seconds=0.5):
            handle = runtime.enqueue("delayed")

        assert not runtime.started["delayed"].wait(0.2)
        assert runtime.started["delayed"].wait(5), "due transition did not wake queue"
        assert handle.get_result(polling_interval_sec=0.01) == "delayed"

    def test_capacity_change_uses_the_new_limit(self, runtime):
        runtime.gates["held"] = threading.Event()
        runtime.enqueue("held")
        assert runtime.started["held"].wait(5)
        second = runtime.enqueue("extra")
        assert not runtime.started["extra"].wait(0.1)

        runtime.queue.set_worker_concurrency(2)

        assert runtime.started["extra"].wait(5), "capacity change was stale after wake"
        assert second.get_result(polling_interval_sec=0.01) == "extra"

    def test_cancelled_queued_work_never_executes(self, runtime):
        gate = runtime.gates["held"] = threading.Event()
        runtime.enqueue("held")
        assert runtime.started["held"].wait(5)
        cancelled = runtime.enqueue("cancelled")
        last = runtime.enqueue("last")

        DBOS.cancel_workflow(cancelled.get_workflow_id())
        gate.set()

        assert runtime.started["last"].wait(5)
        assert last.get_result(polling_interval_sec=0.01) == "last"
        assert not runtime.started["cancelled"].is_set()
        assert (
            DBOS.get_workflow_status(cancelled.get_workflow_id()).status == "CANCELLED"
        )

    def test_duplicate_hints_do_not_duplicate_execution(self, runtime):
        from app.runtime.engine.dbos_notifications import signals

        handle = runtime.enqueue("once")
        signals.notify("notify-contract")
        signals.notify("notify-contract")

        assert runtime.started["once"].wait(5)
        assert handle.get_result(polling_interval_sec=0.01) == "once"
        assert runtime.finished.count("once") == 1

    def test_restart_discovers_persisted_work(self, runtime):
        runtime.close()
        runtime.launch(listen=False)
        runtime.enqueue("persisted")
        runtime.close()

        runtime.launch()

        assert runtime.started["persisted"].wait(5)
        assert (
            DBOS.retrieve_workflow("notify-persisted").get_result(
                polling_interval_sec=0.01
            )
            == "persisted"
        )

    def test_rate_limited_work_remains_bounded(self, runtime):
        runtime.queue.set_polling_interval_sec(0.05)
        runtime.queue.set_limiter({"limit": 1, "period": 1.0})
        # A dispatcher pass that reloaded its config just before the limiter
        # committed would claim the first workflow unlimited, outside the
        # window. Limited queues keep timed checks: let one load the limiter.
        time.sleep(0.2)
        runtime.enqueue("limited-first")
        assert runtime.started["limited-first"].wait(5)

        second = runtime.enqueue("limited-second")

        assert not runtime.started["limited-second"].wait(0.2)
        assert runtime.started["limited-second"].wait(5)
        assert second.get_result(polling_interval_sec=0.01) == "limited-second"

    def test_a_missed_cross_process_hint_is_recovered(self, runtime):
        script = """
import sys
from dbos import DBOSClient
client = DBOSClient(system_database_url=sys.argv[1], application_name="notify-contract")
client.enqueue({"workflow_name":"notification.contract", "queue_name":"notify-contract",
                "workflow_id":"notify-external", "app_version":"notify-contract"}, "external")
client.destroy()
"""

        completed = subprocess.run(
            [sys.executable, "-c", script, f"sqlite:///{runtime.path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert completed.returncode == 0, completed.stderr
        assert runtime.started["external"].wait(35), "recovery scan lost durable work"
        assert (
            DBOS.retrieve_workflow("notify-external").get_result(
                polling_interval_sec=0.01
            )
            == "external"
        )


# Generate only the cases that apply to this interpreter: the dispatch cases
# under the patched SDK, otherwise the one test that runs them under a copy.
if PATCHED:
    del TestDispatchUnderAPatchedSdk
else:
    del TestNotificationDispatch
