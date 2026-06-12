from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.services.storage import ensure_unique_slug, slugify, stream_to_path


def test_slugify_normalises_unicode_punctuation_and_empty_names() -> None:
    assert slugify("  Café Racer — Bracket v2!! ") == "cafe-racer-bracket-v2"
    assert slugify("___") == "model"


def test_ensure_unique_slug_keeps_first_free_candidate() -> None:
    existing = {"gear", "gear-2", "gear-3"}

    slug = ensure_unique_slug("gear", existing.__contains__)

    assert slug == "gear-4"


def test_stream_to_path_creates_parent_dirs_and_returns_byte_count(tmp_path: Path) -> None:
    dest = tmp_path / "nested" / "upload.gcode"
    payload = b"G1 X0\n" * 3

    written = stream_to_path(BytesIO(payload), dest)

    assert written == len(payload)
    assert dest.read_bytes() == payload
