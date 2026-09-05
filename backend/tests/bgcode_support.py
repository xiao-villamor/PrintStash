"""One official converter build for backend contracts and real-browser tests."""

import subprocess
import sys
from pathlib import Path

from tests.paths import BACKEND_DIR


def build_converter(destination: Path) -> Path:
    subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--target",
            "bgcode-binary",
            "--output",
            f"type=local,dest={destination}",
            "-f",
            str(BACKEND_DIR / "Dockerfile"),
            str(BACKEND_DIR),
        ],
        check=True,
        timeout=600,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return destination / "bgcode"


if __name__ == "__main__":
    print(build_converter(Path(sys.argv[1])))
