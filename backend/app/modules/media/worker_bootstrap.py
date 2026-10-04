"""Stdlib-only entry point: contain native allocation before importing a worker."""

from __future__ import annotations

import errno
import os
import runpy
import select
import shutil
import signal
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess

    from app.runtime.native_admission import NativePermit

RESOURCE_EXIT = 72
WORKER_MARKER = "PRINTSTASH_ISOLATED_MESH_WORKER"


class WorkerLifecycle(Enum):
    PROCESS_GROUP = "process_group"
    GUARDED = "guarded"


@dataclass(frozen=True)
class LaunchResources:
    descriptors: tuple[int, ...]
    environment: dict[str, str]


def launch_resources(permit: NativePermit) -> LaunchResources:
    """Native children retain source bytes as well as their compute allowance."""
    from app.runtime.native_runtime import PERMIT_ENV, PERMIT_PATH_ENV
    from app.runtime.preparation_runtime import PERMIT_ENV as PREPARATION_ENV
    from app.runtime.preparation_runtime import PERMIT_PATH_ENV as PREPARATION_PATH_ENV
    from app.runtime.preparation_runtime import current_permit

    descriptors = [permit.fileno]
    environment = {PERMIT_ENV: str(permit.fileno), PERMIT_PATH_ENV: str(permit.path)}
    prepared = current_permit()
    if prepared is not None:
        descriptors.append(prepared.fileno)
        environment[PREPARATION_ENV] = str(prepared.fileno)
        environment[PREPARATION_PATH_ENV] = str(prepared.path)
    return LaunchResources(tuple(descriptors), environment)


def terminate_worker(
    process: subprocess.Popen[bytes], lifecycle: WorkerLifecycle
) -> None:
    """Guarded roots leave their sentinel alive to drain native descendants.

    Native application code can only start after the bootstrap handshake. A root
    killed before that point has no native descendants; any forked sentinel
    observes the missing parent before announcing readiness.
    """
    if lifecycle is WorkerLifecycle.GUARDED and sys.platform == "linux":
        process.kill()
    elif lifecycle in (WorkerLifecycle.PROCESS_GROUP, WorkerLifecycle.GUARDED):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
    else:
        raise ValueError("invalid worker lifecycle")


def command(module: str, arguments: list[str], budget: int) -> list[str]:
    if budget <= 0:
        raise ValueError("worker budget must be positive")
    if sys.platform == "linux":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    return [
        sys.executable,
        "-m",
        "app.modules.media.worker_bootstrap",
        str(budget),
        str(os.getpid()),
        module,
        *arguments,
    ]


def reap_descendants(group: int) -> None:
    """Reap only descendants adopted from this admission, never another worker."""
    if sys.platform != "linux":
        return
    while True:
        adopted = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == os.getpid() and int(fields[2]) == group:
                    adopted.append(int(entry.name))
            except (OSError, ValueError, IndexError):
                continue
        if not adopted:
            return
        for pid in adopted:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


def _cleanup_owned_temp(path: Path | None, identity: tuple[int, int] | None) -> None:
    if path is None or identity is None:
        return
    quarantine = path.with_name(path.name + ".abandoned-" + os.urandom(8).hex())
    try:
        path.rename(quarantine)
        current = quarantine.lstat()
        if (current.st_dev, current.st_ino) != identity:
            if not path.exists():
                quarantine.rename(path)
            return
        shutil.rmtree(quarantine)
    except OSError:
        # Uncertain ownership or unavailable storage remains recoverable.
        return


def _pidfd_exited(fd: int) -> bool:
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    events = poll.poll(0)
    if any(event & select.POLLNVAL for _fd, event in events):
        raise OSError(errno.EBADF, "invalid process descriptor")
    return any(event & (select.POLLIN | select.POLLHUP) for _fd, event in events)


def _live_group_member(entry: Path, group: int) -> bool:
    fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[2]) == group and fields[0] not in {"Z", "X"}


def _drain_group(root: int, *, include_root: bool) -> None:
    """Retain resource credits until stable process identities have exited.

    Recheck membership after opening a pidfd: discovery may race PID reuse.
    Once owned, keep that descriptor until exit readiness, including kernel I/O
    waits. Unreadable process state or unavailable pidfds require another sweep;
    uncertainty never authorizes cleanup. Two empty sweeps cover descendants
    forked while their parents were disappearing from the first enumeration.
    """
    guardian = os.getpid()
    watched: dict[int, int] = {}
    empty_sweep = False
    while True:
        uncertain = False
        for pid, fd in list(watched.items()):
            try:
                if _pidfd_exited(fd):
                    os.close(fd)
                    del watched[pid]
            except (OSError, ValueError):
                uncertain = True
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                if pid == guardian or (pid == root and not include_root):
                    continue
                if pid in watched:
                    continue
                fd = None
                try:
                    if not _live_group_member(entry, root):
                        continue
                    fd = os.pidfd_open(pid)
                    if _pidfd_exited(fd):
                        continue
                    if not _live_group_member(entry, root):
                        continue
                    # The opened identity must still be alive after reading
                    # numeric /proc state; otherwise it could describe a reuse.
                    if _pidfd_exited(fd):
                        continue
                    watched[pid] = fd
                    fd = None
                except FileNotFoundError:
                    # The next enumeration must confirm this disappearance.
                    uncertain = True
                except ProcessLookupError:
                    pass
                except (OSError, ValueError, IndexError):
                    uncertain = True
                finally:
                    if fd is not None:
                        os.close(fd)
        except (OSError, ValueError):
            uncertain = True
        for fd in watched.values():
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                # Exit readiness, checked next sweep, is the release evidence.
                pass
            except OSError:
                uncertain = True
        if not watched and not uncertain:
            if empty_sweep:
                return
            empty_sweep = True
        else:
            empty_sweep = False
        time.sleep(0.01)


def _arm_guardian(libc) -> int:
    """A stdlib sentinel kills the group even when native parsing cannot handle signals."""
    if os.getpgrp() != os.getpid():
        os.setsid()
    root = os.getpid()
    # Probe every syscall before native code or a guardian can start. Unsupported
    # kernels must refuse admission rather than hang forever during cleanup.
    pidfd = os.pidfd_open(root)
    try:
        signal.pidfd_send_signal(pidfd, 0)
        _pidfd_exited(pidfd)
    finally:
        os.close(pidfd)
    temporary = Path(os.environ["TMPDIR"]) if os.environ.get("TMPDIR") else None
    identity = None
    if temporary is not None and temporary.name.startswith("printstash-mesh-"):
        stat = temporary.lstat()
        identity = (stat.st_dev, stat.st_ino)
    read_fd, write_fd = os.pipe()
    guardian = os.fork()
    if guardian == 0:
        os.close(read_fd)

        def abandon(_signal, _frame):
            _drain_group(root, include_root=True)
            _cleanup_owned_temp(temporary, identity)
            os._exit(0)

        def finish(_signal, _frame):
            # The root waits for this sentinel; only its remaining native work
            # must die before the sentinel may close inherited resource credits.
            _drain_group(root, include_root=False)
            os._exit(0)

        signal.signal(signal.SIGTERM, abandon)
        signal.signal(signal.SIGUSR1, finish)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            os._exit(RESOURCE_EXIT)
        if os.getppid() != root:
            abandon(None, None)
        os.write(write_fd, b"1")
        os.close(write_fd)
        for fd in (0, 1, 2):
            os.close(fd)
        while True:
            signal.pause()
    os.close(write_fd)
    try:
        if os.read(read_fd, 1) != b"1":
            os.waitpid(guardian, 0)
            raise RuntimeError("worker guardian could not start")
    finally:
        os.close(read_fd)
    return guardian


def _run(argv: list[str]) -> int:
    budget, parent, module, *arguments = argv
    # resource is Unix-only. Production containers run Linux; fail closed when
    # a platform cannot enforce the hard ceiling.
    import resource

    ceiling = int(budget)
    if ceiling <= 0:
        raise ValueError("worker budget must be positive")
    virtual_size = None
    if sys.platform == "linux":
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmSize:"):
                    virtual_size = int(line.split()[1]) * 1024
                    break
    resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    if virtual_size is not None and virtual_size >= ceiling:
        return RESOURCE_EXIT
    guardian = None
    if sys.platform == "linux":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG")
        if os.getppid() != int(parent):
            return RESOURCE_EXIT
        guardian = _arm_guardian(libc)
    os.environ[WORKER_MARKER] = str(os.getpid())
    sys.argv = [module, *arguments]
    try:
        runpy.run_module(module, run_name="__main__")
    except MemoryError:
        os._exit(RESOURCE_EXIT)
    except ImportError as exc:
        # CPython's dynamic loader reports ENOMEM as ImportError on Linux.
        # Missing dependencies remain worker failures, rather than refusals.
        if "failed to map segment" in str(exc) or "Cannot allocate memory" in str(exc):
            os._exit(RESOURCE_EXIT)
        raise
    except OSError as exc:
        if exc.errno == errno.ENOMEM:
            os._exit(RESOURCE_EXIT)
        raise
    finally:
        if guardian is not None:
            os.kill(guardian, signal.SIGUSR1)
            os.waitpid(guardian, 0)
            # Reap direct native children killed by the guardian as well.
            while True:
                try:
                    pid, _status = os.waitpid(-1, 0)
                    if pid == 0:
                        break
                except ChildProcessError:
                    break
    return 0


def main(argv: list[str]) -> int:
    from app.runtime.native_runtime import PERMIT_ENV, PERMIT_PATH_ENV, inherit

    inherited = os.environ.get(PERMIT_ENV)
    if inherited is None:
        return _run(argv)
    from app.runtime.native_admission import NativePermit
    from app.runtime.preparation_runtime import PERMIT_ENV as PREPARATION_ENV
    from app.runtime.preparation_runtime import PERMIT_PATH_ENV as PREPARATION_PATH_ENV

    with ExitStack() as credits:
        permit = credits.enter_context(
            inherit(int(inherited), Path(os.environ[PERMIT_PATH_ENV]))
        )
        if int(argv[0]) != permit.resources.bytes:
            raise ValueError("worker ceiling differs from its native resource permit")
        prepared = os.environ.get(PREPARATION_ENV)
        if prepared is not None:
            credits.enter_context(
                NativePermit.inherit(
                    int(prepared), Path(os.environ[PREPARATION_PATH_ENV])
                )
            )
        return _run(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except MemoryError:
        os._exit(RESOURCE_EXIT)
