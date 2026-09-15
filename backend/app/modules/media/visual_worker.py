"""One bounded render pass; only raw RGB leaves the child over an anonymous pipe."""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

from printstash_core.mesh.similarity import GeometryError
from printstash_core.search.point_inputs import PointRecipe
from printstash_core.search.visual_inputs import VisualRecipe

from app.modules.media.geometry_analysis import visual_views

MAX_REPLY = 8 * 512 * 512 * 3 + 1024


def main() -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    # A native loader must never mix diagnostic output with the framed payload.
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    try:
        path, kind, recipe_json = sys.argv[1:]
        if len(recipe_json) > 1024:
            return 2
        values = json.loads(recipe_json)
        recipe = (
            PointRecipe if values.get("profile") == "point_cloud" else VisualRecipe
        )(**values)
        result = visual_views(Path(path), file_type=kind, recipe=recipe)
        if isinstance(recipe, PointRecipe):
            payload = b"PNT1" + (result.views[0].points or b"")
        else:
            assert result.thumbnail is not None
            frames = (result.thumbnail,) + result.views
            payload = (
                b"RGB1"
                + struct.pack("!HHB", recipe.image_size, recipe.image_size, len(frames))
                + b"".join(v.rgb or b"" for v in frames)
            )
        if len(payload) > MAX_REPLY:
            return 2
    except GeometryError as exc:
        payload = b"ERR1" + exc.code.encode("ascii")[:64]
    except Exception:
        payload = b"ERR1embedding_render_failed"
    output.write(struct.pack("!I", len(payload)) + payload)
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
