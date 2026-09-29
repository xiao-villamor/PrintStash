"""One mesh derivative in a disposable process; see `mesh_isolation`.

The parent supplies the whole request as one JSON argument and reads one framed
reply from stdout. Anything a native loader prints must not corrupt that frame,
so stdout is pointed at the null device once the real pipe has been duplicated.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.core.config import _overlay
from app.modules.media.mesh_isolation import encode_reply
from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    (raw,) = argv
    spec = json.loads(raw)
    # Adopt the parent's runtime configuration before any setting is read.
    _overlay.update(spec["overrides"])
    request = ThumbnailRequest(
        path=Path(spec["path"]),
        file_type=spec["file_type"],
        width=spec["width"],
        height=spec["height"],
        include_geometry=spec["include_geometry"],
        include_thumbnail=spec["include_thumbnail"],
        include_fingerprint=spec["include_fingerprint"],
        triangle_cap=spec["triangle_cap"],
        output_format=spec["output_format"],
        reason=spec["reason"],
    )
    output.write(encode_reply(ThumbnailEngine().generate(request)))
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
