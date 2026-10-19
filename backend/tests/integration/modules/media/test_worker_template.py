"""The template process itself: what it refuses, and how it ends.

It is the one process every mesh worker is forked from, so anything it holds
is inherited by all of them. It will not serve with a second thread alive, it
forks only for a well-formed request, and it does not outlive its owner.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys

import pytest

from app.modules.media import worker_template
from tests.paths import BACKEND_DIR


def _template(code: str | None = None):
    owner, inherited = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    argv = (
        [sys.executable, "-m", "app.modules.media.worker_template"]
        if code is None
        else [sys.executable, "-c", code]
    )
    process = subprocess.Popen(
        [*argv, str(inherited.fileno())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=(inherited.fileno(),),
        env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
        cwd=BACKEND_DIR,
    )
    inherited.close()
    return process, owner


class TestServe:
    def test_announces_its_virtual_size_when_ready(self) -> None:
        process, owner = _template()

        line = process.stdout.readline()

        owner.close()
        process.wait(timeout=30)
        process.stdout.close()
        assert (line.startswith(worker_template.READY), int(line.split()[1]) > 0) == (
            True,
            True,
        )

    def test_exits_when_its_owner_goes_away(self) -> None:
        process, owner = _template()
        process.stdout.readline()

        owner.close()

        assert process.wait(timeout=30) == 0
        process.stdout.close()

    def test_refuses_to_serve_with_a_second_thread_alive(self) -> None:
        process, owner = _template(
            "import sys\n"
            "import app.modules.media.worker_template as template\n"
            "template.PRELOAD = ('tests.fakes.thread_at_import',)\n"
            "raise SystemExit(template.serve(int(sys.argv[1])))\n"
        )

        code = process.wait(timeout=60)

        owner.close()
        announced = process.stdout.read()
        process.stdout.close()
        assert (code, announced) == (worker_template.REFUSED_THREADS, b"")

    def test_ignores_a_message_that_is_not_a_job(self) -> None:
        process, owner = _template()
        process.stdout.readline()

        owner.send(b"hey")
        owner.close()

        assert process.wait(timeout=30) == 0
        process.stdout.close()

    def test_forks_nothing_for_a_malformed_request(self) -> None:
        process, owner = _template()
        process.stdout.readline()
        job, handed = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        read_end, write_end = os.pipe()
        socket.send_fds(owner, [worker_template.JOB], [handed.fileno(), write_end])
        handed.close()
        os.close(write_end)

        job.sendall(worker_template.LENGTH.pack(2) + b"[]")

        refused = job.recv(worker_template.PID.size)
        output = os.read(read_end, 16)
        job.close()
        os.close(read_end)
        owner.close()
        process.wait(timeout=30)
        process.stdout.close()
        assert (refused, output) == (b"", b"")


class TestForkFailureExit:
    @pytest.mark.parametrize(
        "fault,expected",
        [
            ("none", 0),
            ("code", 7),
            ("text", 1),
            ("memory", None),
            ("early_memory", None),
            ("descriptors", 1),
        ],
    )
    def test_preserves_supervised_worker_exit(self, fault, expected):
        from app.modules.media.worker_bootstrap import RESOURCE_EXIT

        code = """
import os, sys
from app.modules.media import worker_template, worker_bootstrap
fault = sys.argv[1]
def execute(arguments):
    assert arguments[1] == str(os.getppid())
    if fault == 'memory': raise MemoryError()
    raise SystemExit(None if fault == 'none' else 7 if fault == 'code' else 'private failure')
worker_bootstrap.main = execute
if fault == 'early_memory':
    def unavailable(path): raise MemoryError()
    os.listdir = unavailable
request = {'descriptors': [], 'environment': dict(os.environ), 'arguments': ['4096', '0', 'fake']}
worker_template._become_worker(request, [1, 2] if fault == 'descriptors' else [1])
"""
        result = subprocess.run(
            [sys.executable, "-c", code, fault],
            cwd=BACKEND_DIR,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == (
            RESOURCE_EXIT if fault in {"memory", "early_memory"} else expected
        )
        assert result.stdout == b""
