"""Safety contracts for bootstrap startup failures and sentinel decisions."""

from __future__ import annotations

import ctypes
import errno
import io
import os
import resource
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.media import worker_bootstrap as bootstrap


class BootstrapExit(SystemExit):
    pass


class SentinelReady(Exception):
    pass


@pytest.fixture
def boot(monkeypatch):
    calls = SimpleNamespace(limits=[], modules=[], kills=[], waits=[])
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(bootstrap.sys, "argv", [])
    monkeypatch.setenv(bootstrap.WORKER_MARKER, "")
    monkeypatch.setattr(resource, "setrlimit", lambda *args: calls.limits.append(args))
    monkeypatch.setattr(
        bootstrap.runpy,
        "run_module",
        lambda *args, **kwargs: calls.modules.append((args, kwargs)),
    )
    monkeypatch.setattr(os, "kill", lambda *args: calls.kills.append(args))
    monkeypatch.setattr(bootstrap, "_arm_guardian", lambda _lib: 1234)

    def wait(pid, options):
        calls.waits.append((pid, options))
        if pid == -1:
            raise ChildProcessError()
        return pid, 0

    def exit_worker(code):
        raise BootstrapExit(code)

    monkeypatch.setattr(os, "waitpid", wait)
    monkeypatch.setattr(os, "_exit", exit_worker)
    calls.lib = SimpleNamespace(prctl=lambda *_args: 0)
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: calls.lib)
    original_open = open
    monkeypatch.setattr(
        "builtins.open",
        lambda name, *args, **kwargs: (
            io.StringIO("Name: worker\nVmSize: 4 kB\n")
            if name == "/proc/self/status"
            else original_open(name, *args, **kwargs)
        ),
    )
    calls.run = lambda: bootstrap.main(["1048576", "77", "worker", "argument"])
    return calls


@pytest.fixture
def sentinel(tmp_path, monkeypatch):
    calls = SimpleNamespace(
        handlers={}, closed=[], writes=[], kills=[], groups=[], waits=[]
    )
    owned = tmp_path / "printstash-mesh-owned"
    owned.mkdir()
    (owned / "partial").write_bytes(b"owned output")
    proc = tmp_path / "proc"
    proc.mkdir()
    for pid, group in [(42, 42), (43, 42), (44, 42), (45, 900), (46, 42)]:
        directory = proc / str(pid)
        directory.mkdir()
        (directory / "stat").write_text(f"{pid} (worker) S 1 {group}")
    (proc / "broken").mkdir()
    (proc / "47").mkdir()
    (proc / "47" / "stat").write_text("incomplete")
    for pid, children in [(42, "43 44"), (44, "46")]:
        directory = proc / str(pid) / "task" / str(pid)
        directory.mkdir(parents=True)
        (directory / "children").write_text(children)
    count = 0

    def pid():
        nonlocal count
        count += 1
        return 42 if count <= 2 else 43

    def pause():
        raise SentinelReady()

    def exit_worker(code):
        raise BootstrapExit(code)

    def kill(pid, sig):
        calls.kills.append((pid, sig))
        if pid == 46:
            raise ProcessLookupError()

    monkeypatch.setenv("TMPDIR", str(owned))
    monkeypatch.setattr(
        bootstrap,
        "Path",
        lambda value: (
            proc / str(value).removeprefix("/proc").lstrip("/")
            if str(value).startswith("/proc")
            else Path(value)
        ),
    )
    monkeypatch.setattr(os, "getpid", pid)
    monkeypatch.setattr(os, "getpgrp", lambda: 42)
    monkeypatch.setattr(os, "getppid", lambda: 42)
    monkeypatch.setattr(os, "setsid", lambda: calls.groups.append("setsid"))
    monkeypatch.setattr(os, "fork", lambda: 0)
    monkeypatch.setattr(os, "pipe", lambda: (70, 71))
    original_close = os.close

    def close(fd):
        calls.closed.append(fd)
        if fd not in {0, 1, 2, 70, 71}:
            original_close(fd)

    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(os, "read", lambda *_args: b"1")
    monkeypatch.setattr(os, "write", lambda *args: calls.writes.append(args))
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", lambda *args: calls.groups.append(args))
    monkeypatch.setattr(os, "_exit", exit_worker)
    monkeypatch.setattr(os, "waitpid", lambda *args: calls.waits.append(args))
    monkeypatch.setattr(
        signal, "signal", lambda sig, handler: calls.handlers.update({sig: handler})
    )
    monkeypatch.setattr(signal, "pause", pause)
    calls.lib = SimpleNamespace(prctl=lambda *_args: 0)
    calls.owned = owned
    return calls


class TestBootstrapStartup:
    def test_rejects_nonpositive_budget(self, boot):
        with pytest.raises(ValueError, match="budget"):
            bootstrap.main(["0", "77", "worker"])
        assert boot.limits == boot.modules == []

    def test_starts_without_linux_process_controls(self, boot):
        assert boot.run() == 0
        assert boot.limits == [(resource.RLIMIT_AS, (1048576, 1048576))]
        assert boot.modules == [(("worker",), {"run_name": "__main__"})]
        assert bootstrap.sys.argv == ["worker", "argument"]
        assert os.environ[bootstrap.WORKER_MARKER] == str(os.getpid())

    def test_refuses_startup_above_the_ceiling(self, boot, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        monkeypatch.setattr(
            "builtins.open", lambda *_args, **_kwargs: io.StringIO("VmSize: 2048 kB\n")
        )
        assert boot.run() == bootstrap.RESOURCE_EXIT
        assert boot.modules == []

    def test_enforces_ceiling_when_virtual_size_is_absent(self, boot, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        monkeypatch.setattr(os, "getppid", lambda: 77)
        monkeypatch.setattr(
            "builtins.open", lambda *_args, **_kwargs: io.StringIO("Name: worker\n")
        )
        assert boot.run() == 0
        assert boot.limits == [(resource.RLIMIT_AS, (1048576, 1048576))]
        assert boot.kills == [(1234, signal.SIGUSR1)]
        assert boot.waits == [(1234, 0), (-1, 0)]

    def test_refuses_parent_identity_change(self, boot, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        monkeypatch.setattr(os, "getppid", lambda: 99)
        assert boot.run() == bootstrap.RESOURCE_EXIT
        assert boot.modules == []

    def test_fails_closed_when_death_signal_cannot_be_armed(self, boot, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        boot.lib.prctl = lambda *_args: -1
        with pytest.raises(OSError, match="PR_SET_PDEATHSIG"):
            boot.run()
        assert boot.modules == []

    @pytest.mark.parametrize(
        "failure",
        [
            MemoryError(),
            ImportError("failed to map segment"),
            ImportError("Cannot allocate memory"),
            OSError(errno.ENOMEM, "memory exhausted"),
        ],
    )
    def test_classifies_native_allocation_failure(self, boot, monkeypatch, failure):
        def exhausted(*_args, **_kwargs):
            raise failure

        monkeypatch.setattr(bootstrap.runpy, "run_module", exhausted)
        with pytest.raises(BootstrapExit) as error:
            boot.run()
        assert error.value.code == bootstrap.RESOURCE_EXIT

    @pytest.mark.parametrize(
        "failure", [ImportError("missing dependency"), OSError(errno.EACCES, "denied")]
    )
    def test_preserves_nonresource_failure(self, boot, monkeypatch, failure):
        def failed(*_args, **_kwargs):
            raise failure

        monkeypatch.setattr(bootstrap.runpy, "run_module", failed)
        with pytest.raises(type(failure)) as error:
            boot.run()
        assert error.value is failure

    def test_stops_reaping_when_no_child_is_available(self, boot, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        monkeypatch.setattr(os, "getppid", lambda: 77)
        monkeypatch.setattr(
            os, "waitpid", lambda pid, _options: (pid, 0) if pid != -1 else (0, 0)
        )
        assert boot.run() == 0
        assert boot.kills == [(1234, signal.SIGUSR1)]

    def test_rejects_invalid_admission(self):
        with pytest.raises(ValueError, match="budget"):
            bootstrap.command("worker", [], 0)

    def test_fails_closed_without_subreaper(self, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "linux")
        monkeypatch.setattr(
            ctypes,
            "CDLL",
            lambda *_args, **_kwargs: SimpleNamespace(prctl=lambda *_args: -1),
        )
        with pytest.raises(OSError, match="PR_SET_CHILD_SUBREAPER"):
            bootstrap.command("worker", [], 1024)

    def test_builds_nonlinux_command(self, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
        assert bootstrap.command("worker", ["arg"], 1024)[-2:] == ["worker", "arg"]

    def test_skips_linux_reaping_on_other_platforms(self, monkeypatch):
        monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
        bootstrap.reap_descendants(42)


class TestSentinel:
    def test_requires_successful_handshake(self, sentinel, monkeypatch):
        monkeypatch.setattr(os, "fork", lambda: 1234)
        assert bootstrap._arm_guardian(sentinel.lib) == 1234
        assert sentinel.closed == [71, 70]

    def test_rejects_failed_handshake(self, sentinel, monkeypatch):
        monkeypatch.setattr(os, "fork", lambda: 1234)
        monkeypatch.setattr(os, "read", lambda *_args: b"")
        with pytest.raises(RuntimeError, match="could not start"):
            bootstrap._arm_guardian(sentinel.lib)
        assert sentinel.waits == [(1234, 0)]
        assert sentinel.closed == [71, 70]

    def test_creates_its_own_process_group(self, sentinel, monkeypatch):
        monkeypatch.setattr(os, "getpgrp", lambda: 99)
        with pytest.raises(SentinelReady):
            bootstrap._arm_guardian(sentinel.lib)
        assert sentinel.groups == ["setsid"]
        assert sentinel.writes == [(71, b"1")]
        assert sentinel.closed == [70, 71, 0, 1, 2]

    def test_refuses_unavailable_death_notification(self, sentinel):
        sentinel.lib.prctl = lambda *_args: -1
        with pytest.raises(BootstrapExit) as error:
            bootstrap._arm_guardian(sentinel.lib)
        assert error.value.code == bootstrap.RESOURCE_EXIT
        assert sentinel.writes == []

    def test_abandonment_kills_only_owned_group(self, sentinel):
        with pytest.raises(SentinelReady):
            bootstrap._arm_guardian(sentinel.lib)
        with pytest.raises(BootstrapExit) as error:
            sentinel.handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert error.value.code == 0
        assert {pid for pid, _sig in sentinel.kills} == {42, 44, 46}
        assert sentinel.groups == [(42, signal.SIGKILL)]
        assert not sentinel.owned.exists()

    def test_parent_loss_during_initialization_abandons_work(
        self, sentinel, monkeypatch
    ):
        monkeypatch.setattr(os, "getppid", lambda: 99)
        with pytest.raises(BootstrapExit):
            bootstrap._arm_guardian(sentinel.lib)
        assert sentinel.writes == []
        assert not sentinel.owned.exists()

    def test_completion_terminates_nested_work(self, sentinel):
        with pytest.raises(SentinelReady):
            bootstrap._arm_guardian(sentinel.lib)
        with pytest.raises(BootstrapExit) as error:
            sentinel.handlers[signal.SIGUSR1](signal.SIGUSR1, None)
        assert error.value.code == 0
        assert sentinel.kills == [(46, signal.SIGKILL), (44, signal.SIGKILL)]
        assert sentinel.owned.exists()

    @pytest.mark.parametrize("temporary", [None, "unowned"])
    def test_abandonment_preserves_unowned_output(
        self, sentinel, monkeypatch, tmp_path, temporary
    ):
        if temporary is None:
            monkeypatch.delenv("TMPDIR")
        else:
            other = tmp_path / temporary
            other.mkdir()
            monkeypatch.setenv("TMPDIR", str(other))
        with pytest.raises(SentinelReady):
            bootstrap._arm_guardian(sentinel.lib)
        with pytest.raises(BootstrapExit):
            sentinel.handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert sentinel.owned.exists()


class TestOwnedTemporaryCleanup:
    def test_removes_proven_owned_directory(self, tmp_path):
        owned = tmp_path / "owned"
        owned.mkdir()
        stat = owned.stat()
        bootstrap._cleanup_owned_temp(owned, (stat.st_dev, stat.st_ino))
        assert list(tmp_path.iterdir()) == []

    def test_preserves_inaccessible_directory(self, tmp_path, monkeypatch):
        owned = tmp_path / "owned"
        owned.mkdir()
        stat = owned.stat()

        def unavailable(*_args):
            raise PermissionError()

        monkeypatch.setattr(Path, "rename", unavailable)
        bootstrap._cleanup_owned_temp(owned, (stat.st_dev, stat.st_ino))
        assert owned.exists()
