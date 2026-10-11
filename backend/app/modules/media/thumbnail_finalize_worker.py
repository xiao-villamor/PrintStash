"""Encode owned pixels or render the same staged geometry in a disposable worker."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Literal, cast

from app.modules.media.mesh_worker_limits import MAX_REPLY_BYTES
from app.modules.media.thumbnail_staging import (
    PIXELS_NAME,
    FinalizationMode,
    StagedRender,
    ThumbnailStaging,
)


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    (raw,) = argv
    spec = json.loads(raw)
    mode = FinalizationMode(spec["mode"])
    staging = ThumbnailStaging(Path(spec["directory"]), spec["width"], spec["height"])
    if spec["output_format"] not in ("PNG", "WEBP"):
        raise ValueError("staged_thumbnail_format")
    output_format = cast(Literal["PNG", "WEBP"], spec["output_format"])
    from printstash_core.mesh import rasterizer

    try:
        if mode is FinalizationMode.ENCODE:
            size = staging.width * staging.height * 4
            descriptor = os.open(
                staging.directory / PIXELS_NAME, os.O_RDONLY | os.O_NOFOLLOW
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                    raise ValueError("staged_thumbnail_pixels")
                pixels = stream.read(size + 1)
            if len(pixels) != size:
                raise ValueError("staged_thumbnail_pixels")
            image = rasterizer.encode_rendered_pixels(
                rasterizer.RenderedPixels(staging.width, staging.height, pixels),
                output_format=output_format,
            )
        else:
            face_chunk_size = spec["face_chunk_size"]
            if type(face_chunk_size) is not int or face_chunk_size <= 0:
                raise ValueError("staged_thumbnail_face_chunk_size")
            from app.core.config import _overlay
            from app.modules.media import mesh_render
            from app.modules.media.compute_geometry import decode

            _overlay["mesh_render_face_chunk_size"] = face_chunk_size

            payload = staging.read(StagedRender(spec["size"], spec["sha256"]))
            prepared, width, height, views, matte = decode(payload)
            if (
                (width, height) != (staging.width, staging.height)
                or views != [None]
                or matte
            ):
                raise ValueError("staged_thumbnail_camera")
            image = mesh_render.render_prepared_thumbnail(
                prepared,
                "staged-thumbnail",
                width,
                height,
                output_format=output_format,
                compute=False,
            )
        if image is None or not 0 < len(image) <= MAX_REPLY_BYTES:
            raise ValueError("staged_thumbnail_output")
        output.write(image)
    finally:
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
