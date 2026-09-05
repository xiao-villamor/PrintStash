"""Bounded previews from pinned Artifact bytes; originals never enter a converter."""

from __future__ import annotations

import asyncio
import shutil
import struct
import sys
import tempfile
import threading
from pathlib import Path

import anyio
from fastapi import HTTPException

from app.core.config import settings
from app.db.models import File, FileType
from app.services.artifact_content import ArtifactContentError, resolve

_guard = threading.Lock()
_active = 0


def _copy_input(file: File, target: Path) -> None:
    maximum = settings.toolpath_input_max_mb * 1024 * 1024
    if file.size_bytes > maximum:
        raise HTTPException(413, "toolpath_input_too_large")
    chunks = resolve(file).stream()
    try:
        with target.open("wb") as output:
            total = 0
            for chunk in chunks:
                total += len(chunk)
                if total > maximum:
                    raise HTTPException(413, "toolpath_input_too_large")
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
        raise HTTPException(413, "toolpath_output_too_large")
    return content


async def render(file: File) -> bytes:
    """Validate/convert a temporary copy, holding one bounded conversion slot."""
    global _active
    if file.file_type != FileType.GCODE:
        raise HTTPException(404, "toolpath_not_gcode")
    with _guard:
        if _active >= settings.toolpath_max_jobs:
            raise HTTPException(429, "toolpath_busy", headers={"Retry-After": "2"})
        _active += 1
    try:
        with tempfile.TemporaryDirectory(prefix="printstash-toolpath-") as directory:
            incoming = Path(directory) / "input.bgcode"
            # Cancellation waits for the bounded storage read before deleting its
            # directory, so no abandoned writer can recreate temporary content.
            copying = asyncio.create_task(
                anyio.to_thread.run_sync(_copy_input, file, incoming)
            )
            try:
                await asyncio.shield(copying)
            except asyncio.CancelledError:
                # A plain asyncio cancellation must not abandon the writer;
                # AnyIO's default shielding only governs AnyIO cancel scopes.
                while not copying.done():
                    try:
                        await asyncio.shield(copying)
                    except asyncio.CancelledError:
                        continue
                # Retrieve any writer exception, retaining cancellation as the
                # caller's outcome once its temporary resources are released.
                if not copying.cancelled():
                    copying.exception()
                raise
            with incoming.open("rb") as stream:
                header = stream.read(10)
            binary = header.startswith(b"GCDE")
            if not binary:
                if file.original_filename.lower().endswith((".bgcode", ".bgc")):
                    raise HTTPException(422, "toolpath_invalid_bgcode")
                return await anyio.to_thread.run_sync(_read_output, incoming)
            if len(header) != 10 or struct.unpack_from("<I", header, 4)[0] != 1:
                raise HTTPException(422, "toolpath_unsupported_bgcode_version")
            executable = shutil.which(settings.bgcode_executable)
            if executable is None:
                raise HTTPException(503, "toolpath_converter_unavailable")
            worker = Path(__file__).with_name("bgcode_worker.py")
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(worker),
                executable,
                str(incoming),
                str(settings.toolpath_memory_max_mb * 1024 * 1024),
                str(settings.toolpath_output_max_mb * 1024 * 1024),
                str(settings.toolpath_timeout_seconds),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(
                    process.wait(), settings.toolpath_timeout_seconds
                )
            except TimeoutError as error:
                raise HTTPException(504, "toolpath_conversion_timeout") from error
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            output = incoming.with_suffix(".gcode")
            if process.returncode != 0 or not output.is_file():
                if (
                    output.exists()
                    and output.stat().st_size
                    >= settings.toolpath_output_max_mb * 1024 * 1024
                ):
                    raise HTTPException(413, "toolpath_output_too_large")
                raise HTTPException(422, "toolpath_invalid_bgcode")
            return await anyio.to_thread.run_sync(_read_output, output)
    except ArtifactContentError as error:
        raise HTTPException(410, "file_blob_unavailable") from error
    finally:
        with _guard:
            _active -= 1
