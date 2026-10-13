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
