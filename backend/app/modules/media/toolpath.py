"""Bounded toolpath text from pinned Artifact bytes.

ASCII G-code already *is* its toolpath: it is read, bounded, straight from the
Artifact. Binary G-code (``.bgcode``) needs a conversion, which is a
derivative: the ``derivatives.toolpath`` job runs ``convert`` once per recipe and
stores the result, so no request ever waits on a converter process. Originals
never enter the converter; it works on a temporary copy.
"""

from __future__ import annotations

import shutil
import struct
import subprocess  # nosec B404 - fixed interpreter and worker script, no shell
import sys
import tempfile
from pathlib import Path

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.db.models import File, FileType
from app.modules.storage.artifact_content import ArtifactContentError, resolve


def _copy_input(file: File, target: Path) -> None:
    maximum = settings.toolpath_input_max_mb * 1024 * 1024
    if file.size_bytes > maximum:
        raise OperationError("toolpath_input_too_large", kind=ErrorKind.TOO_LARGE)
    # Open the destination before acquiring a reader or a verified source copy.
    # A failed destination must not leave an unstarted iterator owning resources.
    with target.open("wb") as output:
        chunks = resolve(file).stream()
        try:
            total = 0
            for chunk in chunks:
                total += len(chunk)
                if total > maximum:
                    raise OperationError(
                        "toolpath_input_too_large", kind=ErrorKind.TOO_LARGE
                    )
                output.write(chunk)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()


def _read_output(path: Path) -> bytes:
    maximum = settings.toolpath_output_max_mb * 1024 * 1024
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise OperationError("toolpath_output_too_large", kind=ErrorKind.TOO_LARGE)
    return content


def _is_binary(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(10)


def is_binary_gcode(file: File) -> bool:
    return file.file_type == FileType.GCODE and file.original_filename.lower().endswith(
        (".bgcode", ".bgc")
    )


def read_ascii(file: File) -> bytes:
    """The toolpath of an ASCII G-code Artifact: its own bounded bytes."""
    if file.file_type != FileType.GCODE:
        raise OperationError("toolpath_not_gcode", kind=ErrorKind.NOT_FOUND)
    from app.modules.media.sliced_gcode import is_container

    if is_container(file):
        raise OperationError("plate_selection_required", kind=ErrorKind.CONFLICT)
    try:
        with tempfile.TemporaryDirectory(prefix="printstash-toolpath-") as directory:
            incoming = Path(directory) / "input.gcode"
            _copy_input(file, incoming)
            if _is_binary(incoming).startswith(b"GCDE"):
                raise OperationError(
                    "toolpath_requires_conversion", kind=ErrorKind.CONFLICT
                )
            return _read_output(incoming)
    except ArtifactContentError as error:
        raise OperationError("file_blob_unavailable", kind=ErrorKind.GONE) from error


def convert(file: File, directory: Path) -> Path:
    """Convert a binary G-code Artifact; returns the ASCII file in ``directory``."""
    if file.file_type != FileType.GCODE:
        raise OperationError("toolpath_not_gcode", kind=ErrorKind.NOT_FOUND)
    incoming = directory / "input.bgcode"
    try:
        _copy_input(file, incoming)
    except ArtifactContentError as error:
        raise OperationError("file_blob_unavailable", kind=ErrorKind.GONE) from error
    header = _is_binary(incoming)
    if not header.startswith(b"GCDE"):
        # Named .bgcode but plain text: the text is the toolpath.
        if file.original_filename.lower().endswith((".bgcode", ".bgc")):
            raise OperationError(
                "toolpath_invalid_bgcode", kind=ErrorKind.UNPROCESSABLE
            )
        _read_output(incoming)
        return incoming
    if len(header) != 10 or struct.unpack_from("<I", header, 4)[0] != 1:
        raise OperationError(
            "toolpath_unsupported_bgcode_version", kind=ErrorKind.UNPROCESSABLE
        )
    executable = shutil.which(settings.bgcode_executable)
    if executable is None:
        raise OperationError(
            "toolpath_converter_unavailable", kind=ErrorKind.UNAVAILABLE
        )
    worker = Path(__file__).with_name("bgcode_worker.py")
    try:
        completed = subprocess.run(  # nosec B603 - fixed interpreter, worker, args
            [
                sys.executable,
                str(worker),
                executable,
                str(incoming),
                str(settings.toolpath_memory_max_mb * 1024 * 1024),
                str(settings.toolpath_output_max_mb * 1024 * 1024),
                str(settings.toolpath_timeout_seconds),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=settings.toolpath_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise OperationError(
            "toolpath_conversion_timeout", kind=ErrorKind.TIMEOUT
        ) from error
    output = incoming.with_suffix(".gcode")
    if completed.returncode != 0 or not output.is_file():
        if (
            output.exists()
            and output.stat().st_size >= settings.toolpath_output_max_mb * 1024 * 1024
        ):
            raise OperationError("toolpath_output_too_large", kind=ErrorKind.TOO_LARGE)
        raise OperationError("toolpath_invalid_bgcode", kind=ErrorKind.UNPROCESSABLE)
    _read_output(output)
    return output
