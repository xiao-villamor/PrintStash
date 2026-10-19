"""A pre-imported mesh worker that is forked once per job.

Every mesh worker used to start a fresh interpreter and import the application
and its geometry libraries before reading a byte of its source: most of a
second of CPU per Artifact. This process pays that once. It imports what a mesh
worker imports, then waits; each request forks it, and the fork becomes that
job's worker.

The fork is the same worker a spawn would have produced. It starts its own
session, takes the job's environment, output pipe and resource permits, and
then runs ``worker_bootstrap.main``, which applies the address-space ceiling,
the parent-death signal and the guardian exactly as it does after an exec.

What the template must never hold, because every worker would inherit it:
threads (a fork copies only the calling one), a GPU device, an open database or
anything read from an untrusted source. It refuses to serve if importing left a
second thread behind.

One trade is deliberate. Workers forked from one template share its address
space layout, where spawned ones each got their own. The template is restarted
with the process that owns it.

Protocol. The owner holds one end of a control socket the template inherits;
no socket is ever bound to a path or connected to.

- per job, one control message (``JOB``) whose ancillary data is: one end of a
  socket pair made for that job, the job's output pipe, then its inherited
  descriptors;
- on the job's socket, the owner writes ``LENGTH`` and that many bytes of
  request JSON; the template answers with the worker's pid as ``PID`` and, when
  the worker exits, its wait status as ``STATUS`` (a ``Popen.returncode``).

A control socket that reaches end of file means the owner is gone: the
template exits, and its workers with it.
"""

from __future__ import annotations

import fcntl
import importlib
import json
import os
import selectors
import signal
import socket
import struct
import sys
import threading

# ``READY`` is followed by the template's virtual size in bytes and a newline:
# a fork starts at that size, so a job whose ceiling is not well above it is
# better served by a fresh interpreter.
READY = b"ready "
REFUSED_THREADS = 3
JOB = b"job"
LENGTH = struct.Struct("!I")
PID = struct.Struct("!q")
STATUS = struct.Struct("!q")
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_DESCRIPTORS = 8

# What a mesh worker imports on its way to its first byte of geometry. Kept to
# modules that load code only: nothing here may start a thread or open a device.
PRELOAD = (
    "app.modules.media.mesh_worker",
    "numpy",
    "PIL.Image",
    "trimesh",
    "app.modules.media.visual_loading",
    "app.modules.media.mesh_loading",
    "app.modules.media.mesh_render",
    "app.modules.media.stl_reader",
    "app.modules.media.three_mf_scene",
    "app.modules.media.fingerprints",
    "app.runtime.compute.client",
)


def _become_worker(request: dict, descriptors: list[int]) -> None:
    """In the fork: turn into the job's worker and never return."""
    code = 1
    try:
        os.setsid()
        for handler in (signal.SIGTERM, signal.SIGINT, signal.SIGCHLD):
            signal.signal(handler, signal.SIG_DFL)
        output, *inherited = descriptors
        numbers = [int(number) for number in request["descriptors"]]
        if len(numbers) != len(inherited) or any(number < 3 for number in numbers):
            raise ValueError("invalid worker descriptors")
        # Park above both the destination numbers and every template-owned fd.
        # A busy supervisor can hand us permits and control sockets above 1024.
        # Closing the whole lower range also removes other jobs' high pidfds.
        parked_from = max(numbers + [int(fd) for fd in os.listdir("/proc/self/fd")]) + 1
        parked = [
            fcntl.fcntl(descriptor, fcntl.F_DUPFD, parked_from)
            for descriptor in (output, *inherited)
        ]
        # Everything the template had open goes: its listening socket, other
        # jobs' connections, and the descriptors just parked.
        os.closerange(3, parked_from)
        os.dup2(parked[0], 1)
        for source, number in zip(parked[1:], numbers, strict=True):
            os.dup2(source, number, inheritable=True)
        for descriptor in parked:
            os.close(descriptor)
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 0)
        os.dup2(null, 2)
        os.close(null)
        os.environ.clear()
        os.environ.update(request["environment"])
        import tempfile

        # The job's own TMPDIR, not the directory the template resolved.
        tempfile.tempdir = None
        from app.modules.media import worker_bootstrap

        budget, _parent, *rest = request["arguments"]
        try:
            code = worker_bootstrap.main([budget, str(os.getppid()), *rest])
        except MemoryError:
            code = worker_bootstrap.RESOURCE_EXIT
        except SystemExit as exit_request:
            code = (
                exit_request.code
                if isinstance(exit_request.code, int)
                else 0
                if exit_request.code is None
                else 1
            )
    except MemoryError:
        from app.modules.media.worker_bootstrap import RESOURCE_EXIT

        code = RESOURCE_EXIT
    except BaseException:  # noqa: BLE001 - the fork must not unwind into the template
        code = 1
    finally:
        # ``os._exit`` skips the interpreter's own shutdown, which is what an
        # exec'd worker relied on to flush what it wrote.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001 - nothing left to report to
                pass
        os._exit(code)


def _virtual_size() -> int:
    with open("/proc/self/status") as status:
        for line in status:
            if line.startswith("VmSize:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("worker template cannot read its virtual size")


def _exactly(connection: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ValueError("worker request ended early")
        data += chunk
    return data


def _request(connection: socket.socket) -> dict:
    (size,) = LENGTH.unpack(_exactly(connection, LENGTH.size))
    if not 0 < size <= MAX_REQUEST_BYTES:
        raise ValueError("invalid worker request size")
    request = json.loads(_exactly(connection, size))
    if (
        type(request) is not dict
        or type(request.get("arguments")) is not list
        or type(request.get("environment")) is not dict
        or type(request.get("descriptors")) is not list
    ):
        raise ValueError("invalid worker request")
    return request


def serve(control_descriptor: int) -> int:
    """Fork one worker per request until the owner goes away."""
    if sys.platform == "linux":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG")
        if os.getppid() == 1:
            return 0
    # Importing logs, and the application logs to standard output. The owner
    # reads one line from there, so keep that pipe aside and let everything
    # else the template ever prints go nowhere.
    announce = os.dup(1)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.close(null)
    for name in PRELOAD:
        importlib.import_module(name)
    if threading.active_count() != 1:
        os.close(announce)
        return REFUSED_THREADS
    control = socket.socket(fileno=control_descriptor)
    os.write(announce, READY + str(_virtual_size()).encode() + b"\n")
    os.close(announce)
    # pid descriptor -> (pid, the connection its status is owed on)
    running: dict[int, tuple[int, socket.socket]] = {}
    with selectors.DefaultSelector() as selector:
        selector.register(control, selectors.EVENT_READ)
        while True:
            for key, _events in selector.select():
                if key.fileobj is control:
                    message, descriptors, _flags, _address = socket.recv_fds(
                        control, len(JOB), MAX_DESCRIPTORS + 2
                    )
                    if not message:
                        # The owner is gone. Workers die with this process.
                        return 0
                    if message != JOB or len(descriptors) < 2:
                        for descriptor in descriptors:
                            os.close(descriptor)
                        continue
                    connection = socket.socket(fileno=descriptors[0])
                    handed = descriptors[1:]
                    try:
                        request = _request(connection)
                    except OSError, ValueError:
                        connection.close()
                        for descriptor in handed:
                            os.close(descriptor)
                        continue
                    pid = os.fork()
                    if pid == 0:
                        _become_worker(request, handed)
                    for descriptor in handed:
                        os.close(descriptor)
                    try:
                        connection.sendall(PID.pack(pid))
                        watched = os.pidfd_open(pid)
                    except OSError:
                        # Nobody is left to supervise it, or it cannot be
                        # watched: it must not outlive this refusal.
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        os.waitpid(pid, 0)
                        connection.close()
                        continue
                    running[watched] = (pid, connection)
                    selector.register(watched, selectors.EVENT_READ)
                    continue
                watched = key.fd
                pid, connection = running.pop(watched)
                selector.unregister(watched)
                os.close(watched)
                _pid, status = os.waitpid(pid, 0)
                try:
                    connection.sendall(STATUS.pack(os.waitstatus_to_exitcode(status)))
                except OSError:
                    pass
                connection.close()


def main(argv: list[str]) -> int:
    (control,) = argv
    return serve(int(control))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
