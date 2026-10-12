"""Thumbnail-only sources do not require the topology/analysis import stack."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.factories import content
from tests.factories.geometry import tetrahedron
from tests.paths import BACKEND_DIR


class TestGenerate:
    @pytest.mark.parametrize(
        ("suffix", "payload", "include_geometry"),
        [
            pytest.param(
                suffix,
                payload,
                include_geometry,
                id=name + ("-shared" if include_geometry else "-preview"),
            )
            for suffix, payload, name in [
                (".stl", content.binary_stl(), "binary-stl"),
                (".stl", tetrahedron().export(file_type="stl"), "closed-stl"),
                (".stl", content.ascii_stl(), "ascii-stl"),
                (".obj", b"v 0 0 0\nv 10 0 0\nv 0 10 0\nf 1 2 3\n", "obj"),
            ]
            for include_geometry in ([False, True] if suffix == ".stl" else [False])
        ],
    )
    def test_renders_without_analysis_dependencies(
        self, tmp_path, suffix, payload, include_geometry
    ):
        source = tmp_path / ("source" + suffix)
        source.write_bytes(payload)
        script = """
import importlib.abc, io, sys
from pathlib import Path
class BlockAnalysis(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] == 'trimesh' or (sys.argv[2] == '0' and fullname.split('.')[0] == 'scipy'):
            raise AssertionError('preview imported analysis: ' + fullname)
sys.meta_path.insert(0, BlockAnalysis())
from PIL import Image
from app.modules.media.mesh_contracts import ThumbnailRequest
from app.modules.media.thumbnail_engine import ThumbnailEngine
from app.runtime.native_runtime import bind_pool
from app.runtime.native_admission import LocalResourcePool
path = Path(sys.argv[1])
bind_pool(LocalResourcePool(path.parent / 'native'))
result = ThumbnailEngine().generate(ThumbnailRequest(
    path, include_geometry=(sys.argv[2] == '1'), width=128, height=96, output_format='WEBP'))
assert result.image is not None, result.failure_reason
image = Image.open(io.BytesIO(result.image)).convert('RGBA')
assert image.getbbox() is not None
assert image.size == (128, 96)
if sys.argv[2] == '1':
    assert result.geometry['triangle_count'] is not None, result.geometry
    if result.geometry['triangle_count'] == 4:
        assert result.geometry['volume_mm3'] == 1000, result.geometry
"""

        result = subprocess.run(
            [sys.executable, "-c", script, str(source), str(int(include_geometry))],
            cwd=BACKEND_DIR,
            env={
                **os.environ,
                "VAULT_DATA_DIR": str(tmp_path),
                "VAULT_COMPUTE_MODE": "cpu",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 0, result.stderr
