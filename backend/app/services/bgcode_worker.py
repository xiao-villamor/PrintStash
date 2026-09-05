"""Install Unix resource limits in an isolated process before executing libbgcode.

Using a launcher avoids preexec_fn in the API's multithreaded process. The CLI
validates the binary structure and checksums; the parent rejects partial output.
"""

from __future__ import annotations

import os
import resource
import sys


def main(arguments: list[str]) -> None:
    executable, source, memory, output, seconds = arguments
    for limit, value in (
        (resource.RLIMIT_AS, int(memory)),
        (resource.RLIMIT_FSIZE, int(output)),
        (resource.RLIMIT_CPU, int(seconds)),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(limit, (value, value))
    os.execv(executable, [executable, source])


if __name__ == "__main__":
    main(sys.argv[1:])
