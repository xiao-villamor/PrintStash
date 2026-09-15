"""Delay native startup at the process boundary until the test releases it."""

import runpy
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    release = Path(sys.argv.pop())
    while not release.exists():
        time.sleep(0.02)
    sys.argv.append("--persistent")
    runpy.run_module("app.modules.inference.worker", run_name="__main__", alter_sys=True)
