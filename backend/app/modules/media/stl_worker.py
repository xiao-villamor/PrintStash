"""One mesh-to-STL conversion in a disposable process; see `stl_isolation`.

The parent supplies the whole request as one JSON argument, including the file
the mesh is written to, and reads one small frame from stdout. Anything a native
loader prints must not corrupt that frame, so stdout is pointed at the null device
once the real pipe has been duplicated.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.modules.media import mesh_processing
from app.modules.media.mesh_isolation import read_spec
from app.modules.media.stl_isolation import encode_reply


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    spec = read_spec(argv)
    converted = mesh_processing.to_stl_bytes(
        Path(spec["path"]), file_type=spec["file_type"]
    )
    if converted is None:
        frame = encode_reply(None)
    else:
        Path(spec["output"]).write_bytes(converted)
        frame = encode_reply(len(converted))
    output.write(frame)
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
