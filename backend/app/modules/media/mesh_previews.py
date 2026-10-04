"""Bounded embedded 3MF preview extraction without loading mesh geometry."""

from __future__ import annotations

import io
import struct
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

from app.core.logging import get_logger
from app.modules.media import mesh_policy

logger = get_logger(__name__)
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_3MF_THUMBNAIL_BYTES = 32 * 1024 * 1024
_MAX_3MF_THUMBNAIL_CANDIDATES = 64
_MAX_3MF_THUMBNAIL_AGGREGATE_BYTES = 64 * 1024 * 1024
_MAX_3MF_ENTRIES = 4096
_MAX_3MF_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_3MF_COMPRESSION_RATIO = 200
_MAX_3MF_ENTRY_NAME_BYTES = 1024
_3MF_THUMBNAIL_DIRS = ("metadata/", "3d/thumbnails/", "thumbnails/")


def extract_embedded_3mf_thumbnail(
    path: Path, *, validate_image: bool = False, file_type: str | None = None
) -> Optional[bytes]:
    """Return one semantically unambiguous PNG preview from a 3MF, or None.

    3MF files are ZIP archives; slicers store a rendered plate preview next to
    the mesh. Using it skips the software rasteriser entirely and matches what
    the user saw in the slicer. ``validate_image`` additionally decodes the
    candidate with Pillow before it is selected for the early thumbnail path;
    the default remains permissive for callers that only need a bounded raw
    archive read, while persistence still validates through ``thumbnail.to_webp``.
    """
    if mesh_policy.canonical_suffix(path, file_type) != ".3mf":
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_3MF_ENTRIES:
                logger.warning("mesh_processing: 3MF entry limit exceeded")
                return None
            total_uncompressed = 0
            candidates = []
            for info in infos:
                name = info.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if (
                    len(name.encode("utf-8", errors="replace"))
                    > _MAX_3MF_ENTRY_NAME_BYTES
                    or name.startswith("/")
                    or ".." in parts
                ):
                    logger.warning("mesh_processing: unsafe 3MF member name")
                    return None
                total_uncompressed += info.file_size
                if total_uncompressed > _MAX_3MF_TOTAL_UNCOMPRESSED_BYTES:
                    logger.warning("mesh_processing: 3MF expanded size limit exceeded")
                    return None
                is_thumbnail_candidate = (
                    name.lower().startswith(_3MF_THUMBNAIL_DIRS)
                    and name.lower().endswith(".png")
                    and info.file_size > 0
                )
                if not is_thumbnail_candidate:
                    continue
                # Geometry members can legitimately compress extremely well and
                # are never inflated by this extractor. Their declared expanded
                # size still contributes to the archive-wide budget above, while
                # the ratio guard belongs on the image bytes we actually read.
                if (
                    info.file_size / max(info.compress_size, 1)
                    > _MAX_3MF_COMPRESSION_RATIO
                ):
                    logger.warning(
                        "mesh_processing: 3MF thumbnail compression ratio limit exceeded"
                    )
                    continue
                candidates.append(info)
                if len(candidates) > _MAX_3MF_THUMBNAIL_CANDIDATES:
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail candidate limit exceeded",
                        extra={"count": len(candidates)},
                    )
                    return None
            if not candidates:
                return None

            def semantic_rank(info: zipfile.ZipInfo) -> tuple[int, str]:
                name = info.filename.lower().replace("\\", "/")
                basename = PurePosixPath(name).name
                if name == "metadata/thumbnail.png":
                    return (0, name)
                if basename == "thumbnail.png":
                    return (1, name)
                if "plate_1" in basename or "plate_01" in basename:
                    return (2, name)
                return (3, name)

            candidates.sort(key=semantic_rank)
            best_rank = semantic_rank(candidates[0])[0]
            if best_rank == 3 and len(candidates) > 1:
                logger.warning(
                    "mesh_processing: ambiguous embedded 3MF thumbnails",
                    extra={"count": len(candidates)},
                )
                return None
            aggregate_bytes = 0
            for candidate in candidates:
                if candidate.file_size > _MAX_3MF_THUMBNAIL_BYTES:
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail exceeds limit",
                        extra={
                            "entry": candidate.filename,
                            "size": candidate.file_size,
                        },
                    )
                    continue
                remaining = _MAX_3MF_THUMBNAIL_AGGREGATE_BYTES - aggregate_bytes
                if candidate.file_size > remaining:
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail aggregate limit reached",
                        extra={"entry": candidate.filename},
                    )
                    continue
                try:
                    with zf.open(candidate) as source:
                        data = source.read(candidate.file_size + 1)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    logger.warning(
                        "mesh_processing: embedded 3MF thumbnail candidate is unreadable",
                        extra={"entry": candidate.filename},
                    )
                    continue
                aggregate_bytes += len(data)
                if len(data) != candidate.file_size or not data.startswith(_PNG_MAGIC):
                    continue
                if len(data) >= 24 and data[12:16] == b"IHDR":
                    try:
                        png_width, png_height = struct.unpack(">II", data[16:24])
                    except struct.error:
                        continue
                    if png_width * png_height > 25_000_000:
                        continue
                elif validate_image:
                    continue
                if validate_image:
                    try:
                        from PIL import Image

                        with warnings.catch_warnings():
                            warnings.simplefilter(
                                "error", Image.DecompressionBombWarning
                            )
                            with Image.open(io.BytesIO(data)) as preview:
                                if preview.format != "PNG":
                                    continue
                                preview.load()
                    except Exception:  # noqa: BLE001 - hostile image input
                        logger.warning(
                            "mesh_processing: embedded 3MF thumbnail is invalid",
                            extra={"entry": candidate.filename},
                        )
                        continue
                logger.info(
                    "mesh_processing: using embedded 3MF thumbnail %s (%d bytes)",
                    candidate.filename,
                    len(data),
                )
                return data
    except (zipfile.BadZipFile, OSError, KeyError):
        logger.warning(
            "mesh_processing: embedded 3MF thumbnail read failed for %s",
            path.name,
            exc_info=True,
        )
    return None
