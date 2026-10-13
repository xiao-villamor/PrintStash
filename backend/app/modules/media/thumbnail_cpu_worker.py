"""Guarded recovery from an owned geometry packet; never reload source files."""

import os
import sys
from pathlib import Path

from app.modules.media.mesh_isolation import read_spec
from app.modules.media.thumbnail_packet import PacketDescriptor, read_packet


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    spec = read_spec(argv)
    from app.modules.media.compute_geometry import decode
    from app.modules.media.mesh_render import render_prepared_thumbnail

    payload = read_packet(
        Path(spec["directory"]),
        PacketDescriptor(**spec["packet"]),
        spec["width"],
        spec["height"],
        directory_identity=tuple(spec["directory_identity"]),
    )
    prepared, width, height, _, _ = decode(payload)
    image = render_prepared_thumbnail(
        prepared, "thumbnail", width=width, height=height, output_format="WEBP"
    )
    try:
        if image is None:
            return 1
        output.write(image)
        return 0
    finally:
        output.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
