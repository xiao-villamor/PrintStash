"""The disposable CLI refuses bad input and preserves CPU evidence on GPU refusal.

The GPU fault is an external ModernGL import boundary in a fresh process; this
qualifies tooling containment, not a shipped product GPU feature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

import pytest
from PIL import Image

from scripts.mesh_benchmark_corpus import build_contract_corpus
from tests.paths import BACKEND_DIR


class TestMain:
    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param(["--trials", "0"], id="zero-trials"),
            pytest.param(["--chunk-size", "0"], id="zero-chunk"),
            pytest.param(["--frame-width", "0"], id="zero-width"),
            pytest.param(["--embedding-size", "31"], id="small-embedding"),
            pytest.param(["--views", "2"], id="preview-extra-views"),
            pytest.param(
                ["--flow", "multiview", "--output-format", "PNG"], id="multiview-png"
            ),
            pytest.param(
                ["--flow", "multiview", "--frame-height", "481"], id="multiview-height"
            ),
            pytest.param(["--flow", "unknown"], id="unknown-flow"),
            pytest.param(["--timeout-seconds", "0"], id="zero-deadline"),
            pytest.param(["--memory-budget-mb", "0"], id="zero-memory"),
        ],
    )
    def test_rejects_invalid_requests_before_creating_output(self, tmp_path, arguments):
        output = tmp_path / "never-created"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.gpu_render_pilot",
                "--output-dir",
                str(output),
                *arguments,
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        assert completed.returncode == 2, completed.stderr
        assert "error:" in completed.stderr
        assert completed.stdout == ""
        assert not output.exists()

    def test_retains_real_cpu_report_when_optional_gpu_is_unavailable(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        build_contract_corpus(corpus)
        source = corpus / "cube-mm.3mf"
        original = source.read_bytes()
        output = tmp_path / "pilot"
        boundary = tmp_path / "external-library-boundary"
        boundary.mkdir()
        (boundary / "moderngl.py").write_text(
            "raise ImportError('pilot_external_moderngl_refusal')\n"
        )
        user_vault = tmp_path / "user-vault"
        user_vault.mkdir()
        (user_vault / "original.stl").write_bytes(b"user-owned-model-must-remain")
        (user_vault / "library.sqlite").write_bytes(
            b"user-owned-database-must-not-open"
        )
        (user_vault / ".env").write_text("VAULT_DATA_DIR=must-not-be-used\n")
        snapshot = {path.name: path.read_bytes() for path in user_vault.iterdir()}
        scratch = tmp_path / "owned-child-scratch"
        scratch.mkdir()

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.gpu_render_pilot",
                "--source",
                str(source),
                "--output-dir",
                str(output),
                "--flow",
                "preview",
                "--frame-width",
                "64",
                "--frame-height",
                "48",
                "--trials",
                "1",
                "--timeout-seconds",
                "10",
                "--memory-budget-mb",
                "1024",
            ],
            cwd=user_vault,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join((str(boundary), str(BACKEND_DIR))),
                "PYTHONDONTWRITEBYTECODE": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "TMPDIR": str(scratch),
                "VAULT_DATA_DIR": str(user_vault),
                "VAULT_DATABASE_URL": "sqlite:///" + str(user_vault / "library.sqlite"),
            },
            capture_output=True,
            text=True,
            timeout=50,
            check=False,
        )

        assert completed.returncode == 1, completed.stderr
        summary = json.loads(completed.stdout)
        report = json.loads((output / "report.json").read_text())
        assert summary == {
            "report": str(output / "report.json"),
            "decision": "declined",
        }
        assert report["scope"] == "private_gpu_pilot_no_production_adoption"
        assert report["decision"] == "declined"
        cells = {cell["mode"]: cell for cell in report["cells"]}
        assert set(cells) == {"cpu", "cold", "reused"}
        cpu = cells["cpu"]
        assert cpu["status"] == "completed", cpu
        assert cpu["result"]["source_unchanged"] is True
        assert cpu["result"]["source_sha256"] == hashlib.sha256(original).hexdigest()
        assert cpu["result"]["triangle_count"] == 12
        assert cpu["result"]["observations"][0]["status"] == "completed"
        image = output / "cpu" / "cpu-view0.webp"
        with Image.open(image) as decoded:
            assert decoded.format == "WEBP"
            assert decoded.size == (64, 48)
            assert decoded.getchannel("A").getbbox() is not None
        assert [cells[mode]["status"] for mode in ("cold", "reused")] == [
            "failed",
            "failed",
        ]
        gpu_observations = [
            observation
            for mode in ("cold", "reused")
            for observation in cells[mode]["result"]["observations"]
            if observation["method"] == "gpu"
        ]
        assert [item["reason"] for item in gpu_observations] == [
            "dependency_unavailable"
        ] * 2
        assert all(
            "pilot_external_moderngl_refusal" in str(item["native_causes"])
            for item in gpu_observations
        )
        assert source.read_bytes() == original
        assert {
            path.name: path.read_bytes() for path in user_vault.iterdir()
        } == snapshot
        assert list(scratch.iterdir()) == []


class TestRun:
    def test_persists_cancelled_decline(self, tmp_path, monkeypatch):
        from app.modules.media import mesh_isolation
        from app.runtime.native_runtime import current_permit
        from scripts.gpu_render_measurement import Flow, OutputFormat
        from scripts.gpu_render_pilot import _run

        corpus = tmp_path / "source"
        corpus.mkdir()
        build_contract_corpus(corpus)
        source = corpus / "cube-mm.3mf"
        output = tmp_path / "report"
        output.mkdir()
        arguments = argparse.Namespace(
            source=source,
            case="sharp-cube",
            output_dir=output,
            flow=Flow.PREVIEW,
            output_format=OutputFormat.WEBP,
            embedding_size=224,
            frame_width=64,
            frame_height=48,
            views=1,
            memory_budget_mb=None,
            telemetry=False,
            trials=1,
            backend="egl",
            chunk_size=64000,
            allocation_limit=512 * 1024**2,
            timeout_seconds=10,
        )

        def interrupt_supervision(*args, **kwargs):
            assert current_permit() is kwargs["permit"]
            raise KeyboardInterrupt

        monkeypatch.setattr(mesh_isolation, "supervise_result", interrupt_supervision)

        report = _run(arguments, source)

        persisted = json.loads((output / "report.json").read_text())
        assert report["cancelled"] is True
        assert report["decision"] == "declined"
        assert persisted["cancelled"] is True
        assert persisted["decision"] == "declined"
        assert persisted == report
        assert not (output / "report.pending").exists()
        assert current_permit() is None
