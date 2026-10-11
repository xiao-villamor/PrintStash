"""The render facade preserves the parent's numerical import boundary."""

import os
import subprocess
import sys
from pathlib import Path


def test_module_import_keeps_numpy_out_of_parent():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.modules.media.mesh_render; assert 'numpy' not in sys.modules",
        ],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
