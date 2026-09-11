"""Measure two real meshes plus a concurrent thumbnail in a bounded process.

Run this module in a 1 GiB cgroup; the JSON records the effective kernel limit.
No source files are modified. This is a manual resource probe, not a portability
check of the host running the ordinary test suite.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", choices=("benchy", "spatula"), default="benchy")
    args = parser.parse_args()
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    import psutil

    from app.core.config import _overlay
    from app.modules.media.geometry_analysis import _load, verify_paths
    from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest
    from app.modules.similarity.configuration import SimilaritySettings
    from tests.paths import TESTDATA_DIR

    _overlay["max_render_jobs"] = 1
    cgroup = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    directory = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
    memory_limit = int((directory / "memory.max").read_text())
    assert memory_limit == 1024**3, "run under a 1 GiB cgroup"
    source = TESTDATA_DIR / (
        "benchy/3dbenchy.stl"
        if args.source == "benchy"
        else "Spatula_Printables_IS.3mf"
    )
    file_type = source.suffix.lstrip(".")
    cube = TESTDATA_DIR / "Calibration Cube.stl"
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    with tempfile.TemporaryDirectory(prefix="similarity-resource-") as folder:
        prepared = _load(
            source, file_type, triangle_cap=SimilaritySettings().triangle_cap
        )
        second = Path(folder) / "spatula.stl"
        second.write_bytes(prepared.whole_mesh.export(file_type="stl"))
        del prepared
        tick = time.perf_counter()
        analyzed = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_fingerprint=True)
        )
        assert analyzed.fingerprint_result.state == "ready"
        assert analyzed.image
        assert analyzed.complete
        analysis_seconds = time.perf_counter() - tick
        peak = psutil.Process().memory_info().rss
        with ThreadPoolExecutor(max_workers=2) as pool:
            comparison = pool.submit(
                verify_paths,
                source,
                second,
                first_type=file_type,
                second_type="stl",
                sample_points=5000,
            )
            thumbnail = pool.submit(
                ThumbnailEngine().generate,
                ThumbnailRequest(cube, width=128, height=128),
            )
            futures = [comparison, thumbnail]
            while not all(job.done() for job in futures):
                peak = max(peak, psutil.Process().memory_info().rss)
                wait(futures, timeout=0.025)
        evidence = comparison.result()
        preview = thumbnail.result()
        assert evidence.exact_equivalence is True
        assert evidence.sample_points == 5000
        assert preview.image
        result = {
            "cgroup_memory_max": memory_limit,
            "cgroup_memory_peak": int((directory / "memory.peak").read_text()),
            "process_peak_rss_bytes": max(
                peak, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            ),
            "baseline_rss_bytes": baseline,
            "seconds": time.perf_counter() - tick,
            "analysis_seconds": analysis_seconds,
            "fingerprint_state": analyzed.fingerprint_result.state,
            "source_triangles": analyzed.geometry["triangle_count"],
            "sample_points": evidence.sample_points,
            "evidence_class": evidence.evidence_class,
            "thumbnail_bytes": len(preview.image),
            "max_render_jobs": 1,
            "sources": [source.name, cube.name],
            "temporary_directory_removed_on_exit": True,
        }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    assert result["process_peak_rss_bytes"] < memory_limit
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
