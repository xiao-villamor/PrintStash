"""The executable diagnostic refuses absent optional GPU support explicitly."""

import json
import os
import subprocess
import sys

from tests.paths import BACKEND_DIR


class TestWebGpuDoctor:
    def test_reports_missing_optional_dependency(self, tmp_path):
        boundary = tmp_path / "optional-library-boundary"
        boundary.mkdir()
        (boundary / "wgpu.py").write_text("raise ImportError('optional_wgpu_absent')\n")
        completed = subprocess.run(
            [sys.executable, "-m", "scripts.webgpu_doctor"],
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join((str(boundary), str(BACKEND_DIR))),
            },
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 1
        assert json.loads(completed.stdout) == {
            "status": "failed",
            "reason": "optional_wgpu_absent",
            "qualification": False,
        }
