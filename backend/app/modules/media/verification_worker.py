"""One pairwise verification in a disposable process; see `verification_isolation`.

The parent supplies the whole request as one JSON argument and reads one framed
reply from stdout. Anything a native loader prints must not corrupt that frame,
so stdout is pointed at the null device once the real pipe has been duplicated.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from printstash_core.mesh.similarity import GeometryError

from app.modules.media import geometry_analysis
from app.modules.media.mesh_isolation import read_spec
from app.modules.media.verification_isolation import encode_error, encode_reply


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    spec = read_spec(argv)
    try:
        frame = encode_reply(
            geometry_analysis.verify_paths(
                Path(spec["first"]),
                Path(spec["second"]),
                first_type=spec["first_type"],
                second_type=spec["second_type"],
                first_component=spec["first_component"],
                second_component=spec["second_component"],
                sample_points=spec["sample_points"],
                triangle_cap=spec["triangle_cap"],
                verification_seconds=spec["verification_seconds"],
            )
        )
    except GeometryError as exc:
        frame = encode_error(exc)
    output.write(frame)
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
