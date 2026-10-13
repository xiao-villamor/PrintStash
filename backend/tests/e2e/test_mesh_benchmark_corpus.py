"""The corpus CLI produces portable evidence without running native parsers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tests.paths import BACKEND_DIR


class TestMeshBenchmarkCorpus:
    def test_generates_frozen_inputs_through_cli(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.mesh_benchmark_corpus",
                "--output-dir",
                str(tmp_path),
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        manifest = json.loads(result.stdout)
        assert manifest == json.loads((tmp_path / "manifest.json").read_text())
        assert manifest == json.loads(
            (BACKEND_DIR / "benchmarks/mesh/corpus-v1.json").read_text()
        )
        for fixture in manifest["fixtures"]:
            assert (
                hashlib.sha256(
                    (tmp_path / fixture["filename"]).read_bytes()
                ).hexdigest()
                == fixture["sha256"]
            )
