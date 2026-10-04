"""Real process probe: send framed basics, then stall or finish abnormally."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    mode, frames, pid_path = sys.argv[1:]
    descendant = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(pid_path).write_text(
        json.dumps({"parent": os.getpid(), "child": descendant.pid})
    )
    payload = Path(frames).read_bytes()
    for fragment in (payload[:3], payload[3:17], payload[17:]):
        sys.stdout.buffer.write(fragment)
        sys.stdout.buffer.flush()
    if mode == "error":
        return 3
    if mode == "done":
        return 0
    if mode in {"final_stall", "grow"}:
        sys.stdout.close()
    if mode == "grow":
        time.sleep(0.2)
        resident = bytearray(96 * 1024 * 1024)
        resident[0] = 1
    time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
