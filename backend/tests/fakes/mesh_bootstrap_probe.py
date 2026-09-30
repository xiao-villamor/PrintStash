"""Stdlib-only malicious worker used to verify the production bootstrap."""

import os
import resource
import sys
import time


def main():
    case = sys.argv[1]
    if case == "burst":
        bytearray(8 * 1024**3)
    elif case == "native":
        print(resource.getrlimit(resource.RLIMIT_AS)[0], flush=True)
    elif case == "tree":
        import subprocess

        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; hold=bytearray(100*1024**2); time.sleep(60)",
            ]
        )
        _hold = bytearray(100 * 1024**2)
        time.sleep(60)
    elif case == "wait":
        from pathlib import Path

        Path(sys.argv[2]).write_text(str(os.getpid()))
        time.sleep(60)
    else:
        raise ValueError(case)


if __name__ == "__main__":
    main()
