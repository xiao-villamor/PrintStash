"""A real Cascadio STEP fixture crosses the disposable tessellation boundary.

This test protects the full-image dependency contract without mutating the
licensed fixture or trusting a mocked CAD loader.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import TEST_DATA_DIR


@pytest.mark.slow
def test_tessellates_a_real_step_file_into_a_glb(tmp_path: Path) -> None:
    source = TEST_DATA_DIR / "cascadio_material.stp"
    destination = tmp_path / "cascadio-material.glb"
    environment = os.environ.copy()
    environment["PRINTSTASH_STEP_TRIANGLE_LIMIT"] = "1000000"

    completed = subprocess.run(  # noqa: S603 - fixed interpreter/module invocation
        [
            sys.executable,
            "-m",
            "app.services.step_worker",
            str(source),
            str(destination),
        ],
        cwd=Path(__file__).resolve().parents[3],
        env=environment,
        check=False,
        capture_output=True,
        timeout=90,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert destination.read_bytes()[:4] == b"glTF"
