"""Disposable executable boundary for resource-limit tests, never decoding fixtures."""

import sys
from pathlib import Path


def converter_script(directory: Path, body: str) -> Path:
    executable = directory / "converter"
    executable.write_text(
        f"#!{sys.executable}\nimport sys, time\nfrom pathlib import Path\nsource = Path(sys.argv[1])\n{body}\n"
    )
    executable.chmod(0o700)
    return executable
