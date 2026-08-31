from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

from PIL import Image

from app.services.thumbnail_engine import ThumbnailResult, ThumbnailStrategy


def _benchmark_module():
    repository = Path(__file__).resolve().parents[3]
    script = repository / "backend/scripts/bench_thumbnails.py"
    spec = importlib.util.spec_from_file_location("bench_thumbnails", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_benchmark_reports_cold_and_warm_cost(tmp_path: Path) -> None:
    module = _benchmark_module()
    source = tmp_path / "model.stl"
    source.write_bytes(b"model")
    calls = 0
    image = io.BytesIO()
    Image.new("RGBA", (80, 60), (80, 120, 200, 255)).save(image, format="PNG")

    class Engine:
        def generate(self, _request):
            nonlocal calls
            calls += 1
            return ThumbnailResult(
                image=image.getvalue(),
                geometry={
                    "bbox_x_mm": None,
                    "bbox_y_mm": None,
                    "bbox_z_mm": None,
                    "volume_mm3": None,
                    "triangle_count": None,
                },
                strategy=ThumbnailStrategy.FULL,
                complete=True,
                failure_reason=None,
                duration_ms=0,
                peak_rss_bytes=123,
            )

    result = module.benchmark_file(
        source,
        cold_runs=5,
        warm_runs=20,
        engine_factory=Engine,
    )

    assert calls == 5
    assert result.cold_render_calls == 5
    assert result.warm_render_calls == 0
    assert result.peak_rss_bytes == 123
