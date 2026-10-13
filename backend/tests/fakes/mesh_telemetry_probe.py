"""A separate derivative executor with production logging and native supervision.

This probe exercises the mesh execution boundary in a worker-role process with
no HTTP server; it does not claim to bootstrap a complete DBOS deployment.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.modules.media.mesh_contracts import ThumbnailRequest
from app.modules.media.mesh_isolation import MeshWorkerError, generate, supervise_result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("metadata", "failure"))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    from app.bootstrap.native_resources import configure

    configure(args.source.parent)
    if args.mode == "metadata":
        generate(
            ThumbnailRequest(
                args.source, include_geometry=True, include_thumbnail=False
            )
        )
    else:
        try:
            supervise_result(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('partial'); sys.exit(3)",
                ],
                memory_budget=256 * 1024**2,
                timeout_seconds=30,
            )
        except MeshWorkerError:
            return 0
        raise RuntimeError("failure probe unexpectedly succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
