"""Stdlib-only malicious worker used to verify the production bootstrap."""

import os
import resource
import sys
import time
from pathlib import Path


def _publish_readiness(path: Path, content: str) -> None:
    """Expose a complete handshake only after closing its sibling write."""
    staged = path.with_name(path.name + ".part")
    staged.write_text(content, encoding="utf-8")
    staged.replace(path)


def main():
    case = sys.argv[1]
    if case == "priority":
        from app.core.work_priority import current_priority

        print(current_priority().value, flush=True)
    elif case == "close_stdout_wait":
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

            _publish_readiness(Path(sys.argv[2]), json.dumps([os.getpid(), child.pid]))
        _hold = bytearray(hold_mb * 1024**2)
        time.sleep(60)
    elif case in ("tree_wait", "leaves_child", "resource_reply"):
        import json
        import subprocess

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        _publish_readiness(Path(sys.argv[2]), json.dumps([os.getpid(), child.pid]))
        if case == "tree_wait":
            if len(sys.argv) > 3:
                temporary = Path(os.environ["TMPDIR"])
                (temporary / "partial.stl").write_bytes(b"partial native output")
                _publish_readiness(Path(sys.argv[3]), str(temporary))
            time.sleep(60)
        elif case == "resource_reply":
            raise MemoryError("resource refusal after withdrawal")
        else:
            print("reply", flush=True)
    elif case == "wait":
        _publish_readiness(Path(sys.argv[2]), str(os.getpid()))
        time.sleep(60)
    elif case == "identity":
        import json

        print(
            json.dumps(
                {"pid": os.getpid(), "parent": os.getppid(), "session": os.getsid(0)}
            ),
            flush=True,
        )
    elif case == "environment":
        import json
        import tempfile

        print(
            json.dumps(
                {"value": os.environ.get(sys.argv[2]), "temp": tempfile.gettempdir()}
            ),
            flush=True,
        )
    elif case == "descriptor":
        print(os.read(int(sys.argv[2]), 64).decode(), flush=True)
    elif case == "descriptors":
        import json

        print(json.dumps(sorted(int(name) for name in os.listdir("/proc/self/fd"))))
    elif case == "loaded":
        print(sys.argv[2] in sys.modules, flush=True)
    elif case == "unflushed":
        sys.stdout.write("buffered")
    elif case == "exit":
        raise SystemExit(int(sys.argv[2]))
    else:
        raise ValueError(case)


if __name__ == "__main__":
    main()
