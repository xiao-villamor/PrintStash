"""Mesh loading, geometry extraction, thumbnail rendering, and STL export.

Trimesh is heavy, so it is lazy-imported inside each function that needs it.
Callers pass a `Path` and receive plain dicts / bytes — they never touch a
trimesh object directly.

The software thumbnail rasteriser lives in `mesh_render` and is re-exposed
here as `render_thumbnail` for backwards compatibility. Ingestion uses
`analyze_mesh`, which loads the mesh once for both geometry and thumbnail.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
import zipfile

from app.core.logging import get_logger
from app.services import mesh_render

logger = get_logger(__name__)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Slicer-generated 3MF archives usually embed a pre-rendered preview
# (Metadata/thumbnail.png per spec; plate_*.png from Orca/Bambu).
_3MF_THUMBNAIL_DIRS = ("metadata/", "3d/thumbnails/", "thumbnails/")


def _load_mesh(path: Path):
    """Return a single `trimesh.Trimesh` for *path*, or None on failure."""
    import trimesh

    try:
        loaded = trimesh.load(str(path), force="mesh")
    except Exception:
        logger.warning(
            "mesh_processing: trimesh.load failed for %s", path.name, exc_info=True
        )
        return None

    if isinstance(loaded, trimesh.Trimesh):
        return loaded

    if isinstance(loaded, trimesh.Scene):
        # Flatten all geometry in the scene into a single mesh.
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            return None
        if len(meshes) == 1:
            return meshes[0]
        return trimesh.util.concatenate(meshes)

    return None


def _geometry_from_mesh(mesh) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "bbox_x_mm": None,
        "bbox_y_mm": None,
        "bbox_z_mm": None,
        "volume_mm3": None,
        "triangle_count": None,
    }

    if mesh is None:
        return out

    if mesh.vertices.shape[0] > 0:
        extents = mesh.bounds[1] - mesh.bounds[0]
        out["bbox_x_mm"] = round(float(extents[0]), 2)
        out["bbox_y_mm"] = round(float(extents[1]), 2)
        out["bbox_z_mm"] = round(float(extents[2]), 2)

    if mesh.faces is not None and len(mesh.faces) > 0:
        out["triangle_count"] = len(mesh.faces)

    try:
        vol = mesh.volume
        if vol is not None and vol > 0:
            out["volume_mm3"] = round(float(vol), 2)
    except Exception:
        # Non-watertight meshes raise here; volume is best-effort only.
        pass

    return out


def extract_embedded_3mf_thumbnail(path: Path) -> Optional[bytes]:
    """Return the largest PNG preview embedded in a 3MF archive, or None.

    3MF files are ZIP archives; slicers store a rendered plate preview next to
    the mesh. Using it skips the software rasteriser entirely and matches what
    the user saw in the slicer.
    """
    if path.suffix.lower() != ".3mf":
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            candidates = [
                info
                for info in zf.infolist()
                if info.filename.lower().lstrip("/").startswith(_3MF_THUMBNAIL_DIRS)
                and info.filename.lower().endswith(".png")
                and info.file_size > 0
            ]
            if not candidates:
                return None
            best = max(candidates, key=lambda info: info.file_size)
            data = zf.read(best)
            if data.startswith(_PNG_MAGIC):
                logger.info(
                    "mesh_processing: using embedded 3MF thumbnail %s (%d bytes)",
                    best.filename,
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


def analyze_mesh(
    path: Path,
    *,
    width: int = 640,
    height: int = 480,
    report: Callable[[str], None] | None = None,
) -> Tuple[Dict[str, Optional[float]], Optional[bytes]]:
    """Extract geometry and render a thumbnail with a single mesh load.

    Returns ``(geometry_dict, png_bytes_or_None)``. *report* receives progress
    labels as the stages run (see ingestion progress hints).
    """

    def _report(label: str) -> None:
        if report is not None:
            report(label)

    _report("loading_mesh")
    mesh = _load_mesh(path)

    _report("extracting_geometry")
    geometry = _geometry_from_mesh(mesh)

    _report("rendering_thumbnail")
    thumb = extract_embedded_3mf_thumbnail(path)
    if thumb is None:
        thumb = mesh_render.render_mesh_thumbnail(
            mesh, path.name, width=width, height=height
        )
    return geometry, thumb


def extract_geometry(path: Path) -> Dict[str, Optional[float]]:
    """Extract bounding box, volume, and triangle count from a mesh file.

    The returned dict is shaped for direct use as **kwargs to the
    `Metadata` SQLModel constructor. Missing values are returned as None.
    """
    return _geometry_from_mesh(_load_mesh(path))


def render_thumbnail(
    path: Path, width: int = 640, height: int = 480
) -> Optional[bytes]:
    """Render a PNG thumbnail of *path*. Returns PNG bytes or None on failure."""
    thumb = extract_embedded_3mf_thumbnail(path)
    if thumb is not None:
        return thumb
    return mesh_render.render_thumbnail(_load_mesh, path, width=width, height=height)


def to_stl_bytes(path: Path) -> Optional[bytes]:
    """Convert any supported mesh file to binary STL bytes.

    If *path* is already an STL, its raw bytes are returned untouched.
    Returns None on conversion failure.
    """
    if path.suffix.lower() == ".stl":
        try:
            return path.read_bytes()
        except OSError:
            return None

    mesh = _load_mesh(path)
    if mesh is None:
        return None

    try:
        out = io.BytesIO()
        mesh.export(out, file_type="stl")
        return out.getvalue()
    except Exception:
        logger.warning(
            "mesh_processing: STL export failed for %s", path.name, exc_info=True
        )
        return None
