"""Enumerate adapters and perform a bounded render/readback conformance check.

Run this diagnostic in a disposable process with GPU device access. Software
adapters require --allow-software and can never qualify GPU acceleration.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from scripts.render_backend import GpuError


def diagnose(*, selector: str | None, allow_software: bool) -> dict[str, object]:
    import numpy as np
    import wgpu

    from scripts.wgpu_render_backend import GpuContext, GpuFrame

    adapters = [dict(a.info) for a in wgpu.gpu.enumerate_adapters_sync()]
    context = GpuContext.create(selector=selector, allow_software=allow_software)
    try:
        frame = GpuFrame(context, 16, 16, 1, 2, allocation_limit_bytes=65536)
        try:
            image = np.zeros((16, 16, 3), dtype=np.uint8)
            depth = np.full((16, 16), np.inf)
            triangle = np.array([[[2, 2, 0], [14, 2, 0], [2, 14, 0]]], dtype=np.float32)
            normals = np.broadcast_to(np.array([0, 0, 1], dtype=np.float32), (1, 3, 3))
            frame(
                image,
                depth,
                triangle,
                normals,
                lambda n: np.ones_like(n),
                np.full(3, 255, dtype=np.float32),
                16,
                16,
            )
            frame.finish(image, depth)
            if not np.isfinite(depth).any() or not image.any():
                raise ValueError("empty_diagnostic_readback")
            return {
                "status": "passed",
                "adapters": adapters,
                "selected": context.info,
                "stats": asdict(frame.stats),
                "qualification": False,
            }
        finally:
            frame.close()
    finally:
        context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", dest="selector")
    parser.add_argument("--allow-software", action="store_true")
    args = parser.parse_args()
    try:
        result = diagnose(selector=args.selector, allow_software=args.allow_software)
    except (ImportError, GpuError, ValueError) as exc:
        print(
            json.dumps({"status": "failed", "reason": str(exc), "qualification": False})
        )
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
