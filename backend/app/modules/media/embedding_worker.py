"""One embedding-view render in a disposable process; see `embedding_isolation`.

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
from app.modules.media.embedding_isolation import encode_reply
from app.modules.media.mesh_isolation import encode_error, read_spec


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    spec = read_spec(argv)
    try:
        frame = encode_reply(
            geometry_analysis.embedding_views(
                Path(spec["path"]),
                file_type=spec["file_type"],
                component_index=spec["component_index"],
                image_size=spec["image_size"],
                triangle_cap=spec["triangle_cap"],
            )
        )
    except GeometryError as exc:
        frame = encode_error(exc)
    output.write(frame)
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
