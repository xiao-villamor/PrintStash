"""Serve only the requested published image, without invoking any producer."""

import hashlib

from fastapi import HTTPException, Response

from app.db.models import ArtifactDerivative
from app.modules.media.preview_contracts import PreviewManifest, PreviewSize, ViewId
from app.modules.storage.storage_backend.runtime import get_backend


def preview_response(
    row: ArtifactDerivative,
    manifest: PreviewManifest,
    view_id: ViewId,
    *,
    shared: bool = False,
    size: PreviewSize = PreviewSize.FULL,
) -> Response:
    view = next((view for view in manifest.views if view.id == view_id), None)
    if view is None or row.storage_key is None:
        raise HTTPException(404, "preview_not_found")
    image = view.thumbnail if size is PreviewSize.THUMBNAIL and view.thumbnail else view
    backend = get_backend()
    try:
        if backend.supports_ranges:
            data = b"".join(
                backend.stream_range(
                    row.storage_key, image.offset, image.offset + image.size - 1
                )
            )
        else:
            # Streaming fallback for providers without range support. Memory is
            # bounded by one view; never expose or buffer the complete bundle.
            data_parts = []
            position = 0
            for chunk in backend.stream_chunks(row.storage_key):
                start = max(0, image.offset - position)
                end = min(len(chunk), image.offset + image.size - position)
                if end > start:
                    data_parts.append(chunk[start:end])
                position += len(chunk)
                if position >= image.offset + image.size:
                    break
            data = b"".join(data_parts)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(410, "preview_bytes_unavailable") from exc
    if len(data) != image.size or hashlib.sha256(data).hexdigest() != image.sha256:
        raise HTTPException(410, "preview_bytes_unavailable")
    return Response(
        data,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, no-store" if shared else "private, max-age=3600",
            "ETag": f'"{image.sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
