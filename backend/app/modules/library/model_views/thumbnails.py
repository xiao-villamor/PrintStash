"""Stable thumbnail references without optional analysis dependencies."""

import hashlib
from pathlib import Path
from typing import Optional

from app.db.models import Model


def thumbnail_version(file_id: int, thumbnail_path: str | None) -> Optional[str]:
    """Content version of a file's published thumbnail, or None when unversioned.

    Published thumbnails are immutable objects named ``<file_id>-<digest>``; a
    regenerated thumbnail gets a new name, so the name's hash versions the URL.
    """
    if not thumbnail_path or not Path(thumbnail_path).stem.startswith(f"{file_id}-"):
        return None
    return hashlib.sha256(thumbnail_path.encode()).hexdigest()[:12]


def thumb_url(model: Model) -> Optional[str]:
    """Stable URL for the model's thumbnail, or None.

    Prefers a chosen Model cover, then ``thumbnail_file_id``; falls back to the legacy
    ``thumbnail_path`` for rows written before the file-id column existed.
    """
    if model.cover_filename is not None:
        return f"/api/v1/models/{model.id}/cover/content?v={model.cover_filename}"
    if model.thumbnail_file_id:
        url = f"/api/v1/files/{model.thumbnail_file_id}/thumbnail"
        version = thumbnail_version(model.thumbnail_file_id, model.thumbnail_path)
        return f"{url}?v={version}" if version else url
    if model.thumbnail_path:
        stem = Path(model.thumbnail_path).stem
        if stem.isdigit():
            return f"/api/v1/files/{stem}/thumbnail"
    return None
