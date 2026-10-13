"""Exercise the real private broker and its restart path in an isolated process."""

import json
import os
import signal
import socket
import struct
import sys
from pathlib import Path

from app.runtime.compute import client
from app.runtime.compute.protocol import StatusRequest


def main() -> None:
    root = Path(sys.argv[1])
    client.bind(root)
    address = root / "runtime" / "compute" / "broker.sock"
    processes = set()
    try:
        first = json.loads(client.exchange(StatusRequest()))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(address))
            pid, _, _ = struct.unpack(
                "3i",
                connection.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                ),
            )
        processes.add(pid)
        os.kill(pid, signal.SIGTERM)
        # Wait on the owner lock, which closes on process exit; no guessed sleeps.
        import fcntl

        with (address.parent / "owner.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
        second = json.loads(client.exchange(StatusRequest()))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(address))
            replacement, _, _ = struct.unpack(
                "3i",
                connection.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                ),
            )
        processes.add(replacement)
        print(
            json.dumps(
                {
                    "first": first,
                    "second": second,
                    "restarted": replacement != pid,
                    "socket_mode": address.stat().st_mode & 0o777,
                }
            )
        )
    finally:
        for owned in processes:
            try:
                os.kill(owned, signal.SIGTERM)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    main()
