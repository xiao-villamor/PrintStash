"""Short host-shared cooldown after device failure; never durable work state."""

import time
from pathlib import Path


def failed(root: Path) -> None:
    try:
        (root / "cooldown").write_text(str(time.time() + 30), encoding="ascii")
    except OSError:
        pass


def cooling(root: Path) -> bool:
    try:
        with (root / "cooldown").open("r", encoding="ascii") as source:
            deadline = float(source.read(64))
        remaining = deadline - time.time()
        return 0 < remaining <= 30
    except OSError, ValueError:
        return False
