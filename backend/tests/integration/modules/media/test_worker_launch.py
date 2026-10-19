"""A supervised worker is forked from the warm template, or spawned.

A mesh worker on Linux is a fork of a process that has already imported the
application and its geometry libraries. The fork has to be the same worker a
fresh interpreter would have been: its own session, the job's environment,
output pipe and inherited descriptors, the hard memory ceiling, and an exit
status the supervisor can read. Whenever the template cannot serve a job, that
job is spawned instead and nothing else changes.

When this goes red, either every Artifact pays for an interpreter start again,
or a forked worker escapes part of the containment a spawned one has.
"""

from __future__ import annotations

import fcntl
import json
import os
import resource
import signal
import subprocess
import time
from pathlib import Path

import pytest

from app.modules.media import worker_launch
from app.modules.media.worker_bootstrap import RESOURCE_EXIT, command
from tests.paths import BACKEND_DIR

MB = 1024**2
PROBE = "tests.fakes.mesh_bootstrap_probe"
ROOMY = 4096 * MB


@pytest.fixture
def forkable_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the probe stand in for the mesh worker the template forks."""
    monkeypatch.setattr(worker_launch, "_FORKED_WORKERS", frozenset({PROBE}))


@pytest.fixture(autouse=True)
def fresh_template():
    worker_launch.stop_template()
    yield
    worker_launch.stop_template()


@pytest.fixture
def high_descriptor():
    """Keep a permit above the old template remapping boundary."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 2048), hard))
    read_end, write_end = os.pipe()
    descriptor = fcntl.fcntl(read_end, fcntl.F_DUPFD, 1100)
    os.close(read_end)
    os.write(write_end, b"permit")
    os.close(write_end)
    try:
        yield descriptor
    finally:
        os.close(descriptor)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


@pytest.fixture
def descriptor_pressure():
    """Exercise real IPC descriptors beyond select's fixed-size fd set."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 2048), hard))
    opened = []
    try:
        while not opened or opened[-1] < 1100:
            opened.append(os.open(os.devnull, os.O_RDONLY))
        yield
    finally:
        for descriptor in opened:
            os.close(descriptor)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def _polled_exit(worker) -> int:
    deadline = time.monotonic() + 10
    while (result := worker.poll()) is None:
        if time.monotonic() >= deadline:
            worker.kill()
            worker.wait()
            raise AssertionError("worker did not report its exit status")
    return result


def _launch(*arguments: str, budget: int = ROOMY, warm: bool = True, **options):
    environment = {**os.environ, **options.pop("environment", {})}
    return worker_launch.launch(
        command(PROBE, list(arguments), budget),
        environment=environment,
        descriptors=options.pop("descriptors", ()),
        cwd=BACKEND_DIR,
        warm=warm,
    )


def _reply(worker) -> str:
    assert worker.stdout is not None
    reply = worker.stdout.read().decode()
    worker.wait()
    worker.stdout.close()
    return reply


def _template_pid() -> int:
    assert worker_launch._template is not None
    return worker_launch._template.process.pid


class TestForkable:
    def test_a_mesh_worker_command_is_forkable(self) -> None:
        assert worker_launch.forkable(
            command("app.modules.media.mesh_worker", ["{}"], ROOMY)
        )

    @pytest.mark.parametrize(
        "module",
        ["app.modules.media.stl_worker", "app.modules.media.step_worker", PROBE],
    )
    def test_any_other_worker_is_spawned(self, module: str) -> None:
        assert not worker_launch.forkable(command(module, ["{}"], ROOMY))

    def test_a_command_that_is_not_a_bootstrap_is_spawned(self) -> None:
        assert not worker_launch.forkable(["python", "-c", "pass"])


class TestLaunch:
    def test_a_forked_worker_returns_what_it_wrote(self, forkable_probe) -> None:
        worker = _launch("priority")

        assert (type(worker), _reply(worker)) == (
            worker_launch.ForkedWorker,
            "interactive\n",
        )

    def test_a_forked_worker_is_a_child_of_the_template(self, forkable_probe) -> None:
        identity = json.loads(_reply(_launch("identity")))

        assert identity["parent"] == _template_pid()

    def test_a_forked_worker_leads_its_own_session(self, forkable_probe) -> None:
        worker = _launch("identity")
        identity = json.loads(_reply(worker))

        assert (identity["pid"], identity["session"]) == (worker.pid, worker.pid)

    def test_a_forked_worker_starts_with_the_libraries_loaded(
        self, forkable_probe
    ) -> None:
        assert _reply(_launch("loaded", "trimesh")) == "True\n"

    def test_a_forked_worker_runs_under_its_hard_ceiling(self, forkable_probe) -> None:
        assert int(_reply(_launch("native", budget=2048 * MB))) == 2048 * MB

    def test_a_forked_worker_takes_the_jobs_environment(
        self, forkable_probe, tmp_path: Path
    ) -> None:
        reply = json.loads(
            _reply(
                _launch(
                    "environment",
                    "PRINTSTASH_PROBE",
                    environment={"PRINTSTASH_PROBE": "job", "TMPDIR": str(tmp_path)},
                )
            )
        )

        assert reply == {"value": "job", "temp": str(tmp_path)}

    def test_a_forked_worker_does_not_keep_the_templates_environment(
        self, forkable_probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRINTSTASH_PROBE", "template")
        _reply(_launch("priority"))
        monkeypatch.delenv("PRINTSTASH_PROBE")

        reply = json.loads(_reply(_launch("environment", "PRINTSTASH_PROBE")))

        assert reply["value"] is None

    def test_a_forked_worker_inherits_a_descriptor_at_its_own_number(
        self, forkable_probe
    ) -> None:
        read_end, write_end = os.pipe()
        os.write(write_end, b"permit")
        os.close(write_end)

        reply = _reply(_launch("descriptor", str(read_end), descriptors=(read_end,)))

        os.close(read_end)
        assert reply == "permit\n"

    def test_a_forked_worker_inherits_a_high_descriptor_at_its_own_number(
        self, forkable_probe, high_descriptor
    ) -> None:
        worker = _launch(
            "descriptor", str(high_descriptor), descriptors=(high_descriptor,)
        )

        assert _reply(worker) == "permit\n"

    def test_a_warm_launch_works_above_select_descriptor_limit(
        self, forkable_probe, descriptor_pressure
    ) -> None:
        worker = _launch("priority")

        assert (type(worker), _reply(worker)) == (
            worker_launch.ForkedWorker,
            "interactive\n",
        )

    def test_poll_reads_status_above_select_descriptor_limit(
        self, forkable_probe, descriptor_pressure
    ) -> None:
        worker = _launch("exit", "7")

        result = _polled_exit(worker)

        assert worker.stdout is not None
        worker.stdout.close()
        assert result == 7

    def test_a_forked_worker_closes_high_template_descriptors(
        self, forkable_probe, descriptor_pressure
    ) -> None:
        reply = json.loads(_reply(_launch("descriptors")))

        assert ({0, 1, 2} <= set(reply), len(set(reply) - {0, 1, 2})) == (True, 1)

    def test_a_forked_worker_holds_nothing_else_of_the_templates(
        self, forkable_probe
    ) -> None:
        read_end, write_end = os.pipe()

        reply = json.loads(_reply(_launch("descriptors", descriptors=(read_end,))))

        os.close(read_end)
        os.close(write_end)
        # Standard streams, the descriptor it was handed, and the one the
        # probe itself opened to list them: nothing of the template's.
        assert (
            {0, 1, 2, read_end} <= set(reply),
            len(set(reply) - {0, 1, 2, read_end}),
        ) == (True, 1)

    def test_what_a_forked_worker_left_unflushed_still_arrives(
        self, forkable_probe
    ) -> None:
        assert _reply(_launch("unflushed")) == "buffered"

    def test_a_forked_workers_exit_code_reaches_the_supervisor(
        self, forkable_probe
    ) -> None:
        worker = _launch("exit", "7")

        assert worker.wait() == 7

    def test_a_forked_worker_killed_by_a_signal_reports_the_signal(
        self, forkable_probe, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        worker = _launch("wait", str(ready))
        while not ready.exists():
            assert worker.poll() is None

        worker.kill()

        assert worker.wait() == -signal.SIGKILL

    def test_a_forked_worker_over_its_ceiling_reports_a_resource_refusal(
        self, forkable_probe
    ) -> None:
        worker = _launch("burst", budget=1024 * MB)

        assert worker.wait() == RESOURCE_EXIT

    def test_poll_is_none_while_a_forked_worker_runs(
        self, forkable_probe, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        worker = _launch("wait", str(ready))

        running = worker.poll()

        worker.kill()
        worker.wait()
        assert running is None

    def test_a_ceiling_too_close_to_the_template_is_spawned(
        self, forkable_probe
    ) -> None:
        worker = _launch("priority", budget=256 * MB)

        assert (type(worker), _reply(worker)) == (subprocess.Popen, "interactive\n")

    def test_a_cold_launch_is_spawned(self, forkable_probe) -> None:
        worker = _launch("priority", warm=False)

        assert (type(worker), _reply(worker)) == (subprocess.Popen, "interactive\n")

    def test_a_worker_the_template_is_not_built_for_is_spawned(self) -> None:
        worker = _launch("priority")

        assert (type(worker), _reply(worker)) == (subprocess.Popen, "interactive\n")

    def test_a_spawned_worker_leads_its_own_session(self) -> None:
        worker = _launch("identity")
        identity = json.loads(_reply(worker))

        assert identity["session"] == worker.pid


class TestTemplateLifetime:
    def test_one_template_serves_every_job(self, forkable_probe) -> None:
        _reply(_launch("priority"))
        first = _template_pid()

        _reply(_launch("priority"))

        assert _template_pid() == first

    def test_a_template_that_died_is_replaced(self, forkable_probe) -> None:
        _reply(_launch("priority"))
        first = _template_pid()
        os.kill(first, signal.SIGKILL)
        os.waitpid(first, 0)

        worker = _launch("priority")

        assert (type(worker), _reply(worker), _template_pid() != first) == (
            worker_launch.ForkedWorker,
            "interactive\n",
            True,
        )

    def test_a_worker_of_a_template_that_died_is_reported_killed(
        self, forkable_probe, tmp_path: Path
    ) -> None:
        ready = tmp_path / "ready"
        worker = _launch("wait", str(ready))
        while not ready.exists():
            assert worker.poll() is None

        os.kill(_template_pid(), signal.SIGKILL)

        assert worker.wait() == -signal.SIGKILL

    def test_a_template_that_cannot_start_costs_the_job_a_spawn(
        self, forkable_probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(worker_launch, "_TEMPLATE_MODULE", "tests.fakes.missing")

        worker = _launch("priority")

        assert (type(worker), _reply(worker)) == (subprocess.Popen, "interactive\n")

    def test_a_template_that_could_not_start_is_not_tried_for_every_job(
        self, forkable_probe, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        starts: list[Path] = []
        start = worker_launch._start

        def counted(cwd: Path, environment: dict[str, str]):
            starts.append(cwd)
            return start(cwd, environment)

        monkeypatch.setattr(worker_launch, "_TEMPLATE_MODULE", "tests.fakes.missing")
        monkeypatch.setattr(worker_launch, "_start", counted)
        _reply(_launch("priority"))

        _reply(_launch("priority"))

        assert len(starts) == 1

    def test_a_job_with_other_import_paths_gets_its_own_template(
        self, forkable_probe, tmp_path: Path
    ) -> None:
        # A fork runs what the template imported. A job told to import from
        # somewhere else must not be handed another job's modules.
        _reply(_launch("priority"))
        first = _template_pid()
        paths = os.pathsep.join((str(tmp_path), str(BACKEND_DIR)))

        worker = _launch("priority", environment={"PYTHONPATH": paths})

        assert (type(worker), _reply(worker), _template_pid() != first) == (
            worker_launch.ForkedWorker,
            "interactive\n",
            True,
        )

    def test_a_template_refused_for_one_import_path_is_tried_for_another(
        self, forkable_probe, tmp_path: Path
    ) -> None:
        (tmp_path / "trimesh.py").write_text("raise ImportError('blocked')\n")
        blocked = os.pathsep.join((str(tmp_path), str(BACKEND_DIR)))
        spawned = _launch("priority", environment={"PYTHONPATH": blocked})
        _reply(spawned)

        worker = _launch("priority")

        assert (type(spawned), type(worker), _reply(worker)) == (
            subprocess.Popen,
            worker_launch.ForkedWorker,
            "interactive\n",
        )

    def test_stopping_the_template_ends_its_process(self, forkable_probe) -> None:
        _reply(_launch("priority"))
        process = worker_launch._template.process

        worker_launch.stop_template()

        assert (process.poll(), worker_launch._template) == (-signal.SIGKILL, None)
