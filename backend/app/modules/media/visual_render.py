"""Deadline/RSS containment for the shared mesh render capability."""

from __future__ import annotations

import os
import re
import selectors
import struct
import subprocess
import sys
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from printstash_core.search.point_inputs import PointRecipe
from printstash_core.search.visual_inputs import VisualRecipe

from app import __file__ as application_file
from app.core.config import settings
from app.modules.inference.worker_pool import pool
from app.modules.media import compute_slots, mesh_processing
from app.modules.media.geometry_analysis import VisualViews
from app.modules.media.stl_streaming import _terminate_process_group
from app.modules.media.visual_worker import MAX_REPLY


def _spawn(path: Path, file_type: str, recipe: VisualRecipe | PointRecipe):
    env = os.environ.copy()
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    env["VAULT_MESH_MAX_RENDER_TRIANGLES"] = str(
        min(MAX_ANALYSIS_FACES, settings.mesh_max_render_triangles)
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.modules.media.visual_worker",
            str(path),
            file_type,
            recipe.encode(),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
        cwd=Path(application_file).resolve().parent.parent,
    )


def render(
    path: Path,
    *,
    file_type: str,
    recipe: VisualRecipe | PointRecipe,
    context: InferenceContext,
) -> VisualViews:
    """Caller holds the durable compute permit; this shares media's local cap too."""
    context.remaining()
    with mesh_processing._render_semaphore():
        process = _spawn(path, file_type, recipe)
        try:
            assert process.stdout is not None
            os.set_blocking(process.stdout.fileno(), False)
            result = bytearray()
            expected = None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    context.remaining()
                    pool.enforce_memory_budget(
                        process,
                        compute_slots.native_memory_budget_bytes(),
                        compute_slots.native_process_rss_bytes,
                    )
                    for key, _ in selector.select(0.025):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            raise EmbeddingError("embedding_render_failed")
                        result.extend(chunk)
                        if len(result) >= 4 and expected is None:
                            expected = struct.unpack("!I", result[:4])[0]
                            if not 4 <= expected <= MAX_REPLY:
                                raise EmbeddingError("embedding_output_budget")
                        if expected is not None and len(result) >= expected + 4:
                            if len(result) != expected + 4:
                                raise EmbeddingError("embedding_output_invalid")
                            return decode_reply(bytes(result[4:]), recipe)
        finally:
            _terminate_process_group(process)
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def decode_reply(payload: bytes, recipe: VisualRecipe | PointRecipe) -> VisualViews:
    if payload.startswith(b"ERR1"):
        code = payload[4:].decode("ascii", errors="replace")
        raise EmbeddingError(
            code
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code)
            else "embedding_render_failed"
        )
    if isinstance(recipe, PointRecipe):
        if payload[:4] != b"PNT1" or len(payload) != 240004:
            raise EmbeddingError("embedding_output_invalid")
        points = EmbeddingInput("point_cloud", points=payload[4:])
        return VisualViews(None, (points,))
    if len(payload) < 9 or payload[:4] != b"RGB1":
        raise EmbeddingError("embedding_output_invalid")
    width, height, count = struct.unpack("!HHB", payload[4:9])
    size = width * height * 3
    if (
        width != recipe.image_size
        or height != width
        or count != recipe.view_count + 1
        or len(payload) != 9 + size * count
    ):
        raise EmbeddingError("embedding_output_invalid")
    views = tuple(
        EmbeddingInput(
            "image",
            rgb=payload[9 + i * size : 9 + (i + 1) * size],
            width=width,
            height=height,
        )
        for i in range(count)
    )
    return VisualViews(views[0], views[1:])
