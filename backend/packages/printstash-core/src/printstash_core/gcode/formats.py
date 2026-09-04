"""Classify stored print Artifacts without trusting their filename alone."""

from __future__ import annotations

from pathlib import Path

from printstash_core.printers.models import PrintArtifactFormat

from .bgcode import is_bgcode, is_valid_container


class PrintArtifactFormatError(ValueError):
    """A printable Artifact's filename and bytes do not form a safe format."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def declared_print_artifact_format(filename: str) -> PrintArtifactFormat:
    """Classify the operator-visible filename for capability routing."""
    if filename.lower().endswith(".bgcode"):
        return PrintArtifactFormat.BGCODE_BINARY
    return PrintArtifactFormat.GCODE_TEXT


def classify_print_artifact(
    path: Path, *, filename: str | None = None
) -> PrintArtifactFormat:
    """Return the upload format or raise a stable validation error."""
    display_name = filename or path.name
    declared_format = declared_print_artifact_format(display_name)
    declares_binary = declared_format == PrintArtifactFormat.BGCODE_BINARY
    has_binary_magic = is_bgcode(path)

    if declares_binary:
        if not has_binary_magic or not is_valid_container(path):
            raise PrintArtifactFormatError("invalid_binary_gcode")
        return PrintArtifactFormat.BGCODE_BINARY
    if has_binary_magic:
        raise PrintArtifactFormatError("print_artifact_extension_mismatch")
    return PrintArtifactFormat.GCODE_TEXT


def content_type_for_format(artifact_format: PrintArtifactFormat) -> str:
    if artifact_format == PrintArtifactFormat.BGCODE_BINARY:
        return "application/octet-stream"
    return "text/x.gcode"
