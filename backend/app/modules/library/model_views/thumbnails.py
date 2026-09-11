"""Stable thumbnail references without optional analysis dependencies."""

import hashlib
from pathlib import Path
from typing import Optional

from app.db.models import Model


def thumb_url(model: Model) -> Optional[str]:
    """Stable URL for the model's thumbnail, or None.

    Prefers ``thumbnail_file_id`` (current); falls back to parsing the legacy
    ``thumbnail_path`` for rows written before the file-id column existed.
    """
    if model.thumbnail_file_id:
        url = f"/api/v1/files/{model.thumbnail_file_id}/thumbnail"
        if model.thumbnail_path:
            stem = Path(model.thumbnail_path).stem
            if stem.startswith(f"{model.thumbnail_file_id}-"):
                version = hashlib.sha256(model.thumbnail_path.encode()).hexdigest()[:12]
                return f"{url}?v={version}"
        return url
    if model.thumbnail_path:
        stem = Path(model.thumbnail_path).stem
        if stem.isdigit():
            return f"/api/v1/files/{stem}/thumbnail"
    return None
