"""Starting a supervised worker: forked from the warm template, or spawned.

``launch`` returns what the supervisor needs from a child process, whichever
way it came to be. A mesh worker on Linux is forked from
``worker_template``; everything else, and any job the template cannot serve,
starts a fresh interpreter as before. The supervisor treats both alike: a pid
with its own session, an output pipe, an exit status.
"""

from __future__ import annotations

import atexit
import json
import os
import select
import signal
import socket
import subprocess  # nosec B404 - fixed interpreter/module invocation only
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Protocol

from app.core.logging import get_logger
from app.modules.media import worker_template

logger = get_logger(__name__)

_TEMPLATE_MODULE = "app.modules.media.worker_template"
_BOOTSTRAP_MODULE = "app.modules.media.worker_bootstrap"
_FORKED_WORKERS = frozenset({"app.modules.media.mesh_worker"})
_START_SECONDS = 30.0
_RETRY_SECONDS = 60.0
# A fork begins at the template's virtual size and the job's ceiling bounds
# virtual size, so the ceiling has to leave the job room above it. Below that
# the job starts a fresh interpreter, which begins small.
_CEILING_FACTOR = 1.5


class Worker(Protocol):
    """The part of ``subprocess.Popen`` the supervisor uses."""

    pid: int
    stdout: BinaryIO | None
    returncode: int | None

    def poll(self) -> int | None: ...

    def wait(self) -> int: ...

    def kill(self) -> None: ...


class ForkedWorker:
    """A worker forked from the template, known by pid and status connection.

    It is the template's child, not this process's, so its exit status comes
    over the connection the request was made on. A connection that closes
    without one means the template died, and its workers with it.
    """

    def __init__(self, pid: int, output: BinaryIO, connection: socket.socket) -> None:
        self.pid = pid
        self.stdout: BinaryIO | None = output
        self.returncode: int | None = None
        self._connection = connection
        self._received = b""

    def _read(self, *, block: bool) -> None:
        while self.returncode is None:
            if not block and not select.select([self._connection], [], [], 0)[0]:
                return
            chunk = self._connection.recv(
                worker_template.STATUS.size - len(self._received)
            )
            if not chunk:
                self.returncode = -signal.SIGKILL
            else:
                self._received += chunk
                if len(self._received) == worker_template.STATUS.size:
                    (self.returncode,) = worker_template.STATUS.unpack(self._received)
            if self.returncode is not None:
                self._connection.close()

    def poll(self) -> int | None:
        self._read(block=False)
        return self.returncode

    def wait(self) -> int:
        self._read(block=True)
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        if self.returncode is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


# What decides which code an interpreter imports. A fork runs the template's
# imports, so a job is only forked from a template started under the same ones.
_INTERPRETER_VARIABLES = ("PYTHONPATH", "PYTHONHOME", "PYTHONSAFEPATH")


def _interpreter(environment: dict[str, str]) -> tuple[str | None, ...]:
    return tuple(environment.get(name) for name in _INTERPRETER_VARIABLES)


@dataclass
class _Template:
    process: subprocess.Popen[bytes]
    control: socket.socket
    interpreter: tuple[str | None, ...]
    virtual_size: int


_lock = Lock()
_template: _Template | None = None
# The import paths a template could not start under, and until when that is
# taken as settled.
_refused: tuple[tuple[str | None, ...], float] | None = None


def _stop(template: _Template) -> None:
    template.control.close()
    template.process.kill()
    template.process.wait()
    if template.process.stdout is not None:
        template.process.stdout.close()


def stop_template() -> None:
    """Stop this process's template; the next mesh worker starts another."""
    global _template, _refused
    with _lock:
        if _template is not None:
            _stop(_template)
        _template = None
        _refused = None


atexit.register(stop_template)


def _start(cwd: Path, environment: dict[str, str]) -> _Template:
    # A pair made here and inherited by the template: nothing is bound to a
    # path, and nothing but this process can ever ask the template to fork.
    control, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        process = subprocess.Popen(  # nosec B603 - fixed argv; no shell
            [sys.executable, "-m", _TEMPLATE_MODULE, str(inherited.fileno())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(inherited.fileno(),),
            env={
                **environment,
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
            cwd=cwd,
        )
    except BaseException:
        control.close()
        raise
    finally:
        inherited.close()
    template = _Template(process, control, _interpreter(environment), 0)
    assert process.stdout is not None
    deadline = time.monotonic() + _START_SECONDS
    line = b""
    while not line.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
            break
        chunk = os.read(process.stdout.fileno(), 64)
        if not chunk:
            break
        line += chunk
    if not line.startswith(worker_template.READY) or not line.endswith(b"\n"):
        _stop(template)
        raise RuntimeError("worker template did not start")
    template.virtual_size = int(line[len(worker_template.READY) :])
    return template


def _current(cwd: Path, environment: dict[str, str]) -> _Template | None:
    """This process's live template for ``environment``, started on first use.

    A template that cannot start is not retried for a while: every job in
    between spawns, as it always could. One started under different import
    paths than this job's is replaced.
    """
    global _template, _refused
    wanted = _interpreter(environment)
    with _lock:
        if (
            _template is not None
            and _template.process.poll() is None
            and _template.interpreter == wanted
        ):
            return _template
        if _template is not None:
            _stop(_template)
            _template = None
        if (
            _refused is not None
            and _refused[0] == wanted
            and time.monotonic() < _refused[1]
        ):
            return None
        try:
            _template = _start(cwd, environment)
        except OSError, RuntimeError, ValueError:
            logger.exception("worker template failed to start")
            _refused = (wanted, time.monotonic() + _RETRY_SECONDS)
            return None
        return _template


def _fork(
    template: _Template,
    arguments: list[str],
    environment: dict[str, str],
    descriptors: tuple[int, ...],
) -> ForkedWorker:
    read_end, write_end = os.pipe()
    connection, handed = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            with _lock:
                socket.send_fds(
                    template.control,
                    [worker_template.JOB],
                    [handed.fileno(), write_end, *descriptors],
                )
        finally:
            handed.close()
            os.close(write_end)
        request = json.dumps(
            {
                "arguments": arguments,
                "environment": environment,
                "descriptors": list(descriptors),
            }
        ).encode()
        connection.sendall(worker_template.LENGTH.pack(len(request)) + request)
        header = b""
        while len(header) < worker_template.PID.size:
            chunk = connection.recv(worker_template.PID.size - len(header))
            if not chunk:
                raise OSError("worker template closed the request")
            header += chunk
        (pid,) = worker_template.PID.unpack(header)
    except BaseException:
        connection.close()
        os.close(read_end)
        raise
    return ForkedWorker(pid, os.fdopen(read_end, "rb", buffering=0), connection)


def forkable(command: list[str]) -> bool:
    """Whether ``command`` is a worker the template is built to become."""
    return (
        sys.platform == "linux"
        and len(command) >= 6
        and command[1:3] == ["-m", _BOOTSTRAP_MODULE]
        and command[5] in _FORKED_WORKERS
    )


def launch(
    command: list[str],
    *,
    environment: dict[str, str],
    descriptors: tuple[int, ...],
    cwd: Path,
    warm: bool,
) -> Worker:
    """Start ``command`` as a supervised worker in its own session.

    With ``warm``, a forkable worker whose ceiling leaves room above the
    template is forked from it. A template that is unavailable, or refuses the
    request, costs that job a spawn and nothing else.
    """
    if warm and forkable(command):
        template = _current(cwd, environment)
        if template is not None and int(command[3]) >= (
            template.virtual_size * _CEILING_FACTOR
        ):
            try:
                return _fork(template, command[3:], environment, descriptors)
            except OSError:
                logger.exception("worker template refused a job")
                stop_template()
    return subprocess.Popen(  # nosec B603 - fixed argv; no shell
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=descriptors,
        env=environment,
        cwd=cwd,
    )
