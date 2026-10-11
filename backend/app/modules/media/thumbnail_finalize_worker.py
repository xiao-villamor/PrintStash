"""Encode owned pixels or render the same staged geometry in a disposable worker."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Literal, cast

from app.core.config import settings
from app.modules.media.mesh_isolation import MAX_REPLY_BYTES, read_spec
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
    spec = read_spec(argv)
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
            from app.modules.media.compute_geometry import decode
            from app.modules.media.gpu_normals import canonical

            payload = staging.read(StagedRender(spec["size"], spec["sha256"]))
            prepared, width, height, views, matte = decode(payload)
            if (
                (width, height) != (staging.width, staging.height)
                or views != [None]
                or matte
            ):
                raise ValueError("staged_thumbnail_camera")
            image = rasterizer.render_prepared_thumbnail(
                canonical(
                    prepared, face_chunk_size=settings.mesh_render_face_chunk_size
                ),
                "staged-thumbnail",
                width,
                height,
                output_format=output_format,
                face_chunk_size=settings.mesh_render_face_chunk_size,
            )
        if image is None or not 0 < len(image) <= MAX_REPLY_BYTES:
            raise ValueError("staged_thumbnail_output")
        output.write(image)
    finally:
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
