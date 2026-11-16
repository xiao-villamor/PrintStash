"""Serve only the requested published image, without invoking any producer."""

import hashlib

from fastapi import HTTPException, Response

from app.db.models import ArtifactDerivative
from app.modules.media.preview_contracts import PreviewManifest, ViewId
from app.modules.storage.storage_backend.runtime import get_backend


def preview_response(
    row: ArtifactDerivative,
    manifest: PreviewManifest,
    view_id: ViewId,
    *,
    shared: bool = False,
) -> Response:
    view = next((view for view in manifest.views if view.id == view_id), None)
    if view is None or row.storage_key is None:
        raise HTTPException(404, "preview_not_found")
    backend = get_backend()
    try:
        if backend.supports_ranges:
            data = b"".join(
                backend.stream_range(
                    row.storage_key, view.offset, view.offset + view.size - 1
                )
            )
        else:
            # Streaming fallback for providers without range support. Memory is
            # bounded by one view; never expose or buffer the complete bundle.
            data_parts = []
            position = 0
            for chunk in backend.stream_chunks(row.storage_key):
                start = max(0, view.offset - position)
                end = min(len(chunk), view.offset + view.size - position)
                if end > start:
                    data_parts.append(chunk[start:end])
                position += len(chunk)
                if position >= view.offset + view.size:
                    break
            data = b"".join(data_parts)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(410, "preview_bytes_unavailable") from exc
    if len(data) != view.size or hashlib.sha256(data).hexdigest() != view.sha256:
        raise HTTPException(410, "preview_bytes_unavailable")
    return Response(
        data,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, no-store" if shared else "private, max-age=3600",
            "ETag": f'"{view.sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
