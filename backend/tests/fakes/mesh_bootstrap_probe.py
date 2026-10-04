"""Stdlib-only malicious worker used to verify the production bootstrap."""

import os
import resource
import sys
import time


def main():
    case = sys.argv[1]
    if case == "close_stdout_wait":
        os.close(1)
        time.sleep(2)
    elif case == "burst":
        bytearray(8 * 1024**3)
    elif case == "native":
        import numpy as np
        import trimesh

        assert np.isfinite(trimesh.creation.box().volume)
        print(resource.getrlimit(resource.RLIMIT_AS)[0], flush=True)
    elif case == "tree":
        import subprocess

        hold_mb = int(sys.argv[3]) if len(sys.argv) > 3 else 100
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"import time; hold=bytearray({hold_mb}*1024**2); time.sleep(60)",
            ]
        )
        if len(sys.argv) > 2:
            import json
            from pathlib import Path

            Path(sys.argv[2]).write_text(json.dumps([os.getpid(), child.pid]))
        _hold = bytearray(hold_mb * 1024**2)
        time.sleep(60)
    elif case in ("tree_wait", "leaves_child", "resource_reply"):
        import json
        import subprocess
        from pathlib import Path

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        Path(sys.argv[2]).write_text(json.dumps([os.getpid(), child.pid]))
        if case == "tree_wait":
            if len(sys.argv) > 3:
                temporary = Path(os.environ["TMPDIR"])
                (temporary / "partial.stl").write_bytes(b"partial native output")
                Path(sys.argv[3]).write_text(str(temporary))
            time.sleep(60)
        elif case == "resource_reply":
            raise MemoryError("resource refusal after withdrawal")
        else:
            print("reply", flush=True)
    elif case == "wait":
        from pathlib import Path

        Path(sys.argv[2]).write_text(str(os.getpid()))
        time.sleep(60)
    else:
        raise ValueError(case)


if __name__ == "__main__":
    main()
