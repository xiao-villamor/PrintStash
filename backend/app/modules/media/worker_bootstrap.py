"""Stdlib-only entry point: contain native allocation before importing a worker."""

from __future__ import annotations

import errno
import os
import runpy
import signal
import sys

RESOURCE_EXIT = 72
WORKER_MARKER = "PRINTSTASH_ISOLATED_MESH_WORKER"


def command(module: str, arguments: list[str], budget: int) -> list[str]:
    if budget <= 0:
        raise ValueError("worker budget must be positive")
    return [
        sys.executable,
        "-m",
        "app.modules.media.worker_bootstrap",
        str(budget),
        str(os.getpid()),
        module,
        *arguments,
    ]


def main(argv: list[str]) -> int:
    budget, parent, module, *arguments = argv
    # resource is Unix-only. Production containers run Linux; fail closed when
    # a platform cannot enforce the hard ceiling.
    import resource

    ceiling = int(budget)
    if ceiling <= 0:
        raise ValueError("worker budget must be positive")
    resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    if sys.platform == "linux":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG")
        if os.getppid() != int(parent):
            return RESOURCE_EXIT
    os.environ[WORKER_MARKER] = str(os.getpid())
    sys.argv = [module, *arguments]
    try:
        runpy.run_module(module, run_name="__main__")
    except MemoryError:
        os._exit(RESOURCE_EXIT)
    except OSError as exc:
        if exc.errno == errno.ENOMEM:
            os._exit(RESOURCE_EXIT)
        raise
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except MemoryError:
        os._exit(RESOURCE_EXIT)
