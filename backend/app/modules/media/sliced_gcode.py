"""Validate sliced Revisions and resolve the toolpath selected for dispatch."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.db.models import DerivativeKind, DerivativeState, File, FileType
from app.modules.derivatives import records
from app.modules.media.three_mf_preview import (
    EmbeddedGcodeError,
    list_embedded_gcode_plates,
    read_embedded_gcode_path,
)
from app.modules.storage.artifact_content import ArtifactContentError, resolve
from app.schemas.plates import GcodePlateMetadataRead


def is_sliced_filename(filename: str) -> bool:
    return filename.lower().endswith(".gcode.3mf")


def is_container(file: File) -> bool:
    return file.file_type is FileType.THREE_MF or (
        file.file_type is FileType.GCODE and is_sliced_filename(file.original_filename)
    )


def selected_plate(file: File, plate_index: int | None) -> int | None:
    """Resolve a unique plate before recording a command or contacting a printer."""
    if not is_container(file):
        if plate_index is not None:
            raise OperationError("plate_not_supported", kind=ErrorKind.INVALID)
        return None
    try:
        with resolve(file).materialize() as path:
            plates = list_embedded_gcode_plates(path)
    except EmbeddedGcodeError as exc:
        raise OperationError(exc.code, kind=ErrorKind.INVALID) from exc
    except ArtifactContentError as exc:
        raise OperationError("file_blob_unavailable", kind=ErrorKind.GONE) from exc
    if plate_index is None:
        if len(plates) != 1:
            raise OperationError("plate_selection_required", kind=ErrorKind.INVALID)
        return plates[0]
    if plate_index not in plates:
        raise OperationError("embedded_gcode_not_found", kind=ErrorKind.INVALID)
    return plate_index


@contextmanager
def plate_toolpath(source: Path, plate_index: int) -> Iterator[Path]:
    """Expose bounded, validated plate bytes for the existing text G-code transport."""
    embedded = read_embedded_gcode_path(source, plate_index=plate_index)
    with tempfile.TemporaryDirectory(prefix="printstash-plate-") as directory:
        path = Path(directory) / embedded.filename
        path.write_bytes(embedded.content)
        yield path


def plate_metadata(session: Session, file: File) -> list[GcodePlateMetadataRead]:
    """Return plate estimates from the current published metadata derivative."""
    row = records.rows_for(session, file).get(DerivativeKind.METADATA)
    if row is None or row.state is not DerivativeState.READY:
        return []
    output = json.loads(row.output_json)
    # Pre-existing 3MF metadata producers may not publish sliced plate details.
    return [
        GcodePlateMetadataRead.model_validate(item)
        for item in output.get("plate_metadata", [])
    ]
