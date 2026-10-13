"""A real PID namespace holds inherited ledger credit until kernel exit."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.runtime.native_admission import NativePermit


def main(argv: list[str]) -> None:
    descriptor, ticket = argv
    with NativePermit.inherit(int(descriptor), Path(ticket)) as permit:
        # Leave only the validated inherited description. Its lifetime ends at
        # kernel process exit, rather than at a Python context-manager boundary.
        os.close(int(descriptor))
        print(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "pid_namespace": Path("/proc/self/ns/pid").stat().st_ino,
                    "identity": permit.identity,
                    "slots": permit.resources.slots,
                    "bytes": permit.resources.bytes,
                }
            ),
            flush=True,
        )
        if sys.stdin.readline() != "release\n":
            raise RuntimeError("namespace reservation was not released explicitly")
        os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
