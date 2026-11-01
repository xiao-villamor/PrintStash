"""Adapt bounded resin header/preview readers to artifact derivatives."""

from __future__ import annotations

import io
from dataclasses import asdict
from pathlib import Path
from typing import Any

from printstash_core.resin.reader import extract_preview, read_metadata


def parse(path: Path) -> dict[str, Any]:
    return {**asdict(read_metadata(path)), "material_type": "Resin"}


def extract(path: Path) -> bytes | None:
    preview = extract_preview(path)
    if preview is None:
        return None
    from PIL import Image

    with Image.frombytes("RGB", (preview.width, preview.height), preview.rgb) as image:
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
