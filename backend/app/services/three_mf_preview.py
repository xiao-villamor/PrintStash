"""Read embedded G-code from a Bambu 3MF project without extracting it.

The service deliberately works on a direct local path or a bounded temporary
archive supplied through the storage seam. It never publishes an extracted
copy and never trusts an archive member's path as a filesystem destination.
"""

from __future__ import annotations

import os
import re
import struct
import tempfile
import threading
import unicodedata
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.services.storage_backend import StorageBackend

_PLATE_PATH = re.compile(r"^Metadata/plate_[0-9]+\.gcode$")
_ZIP_EOCD = b"PK\x05\x06"
_ZIP64_EOCD_LOCATOR = b"PK\x06\x07"
_ZIP64_EOCD = b"PK\x06\x06"
_ZIP_EOCD_BYTES = 22
_ZIP_EOCD_SCAN_BYTES = 65_557
_STORAGE_CHUNK_BYTES = 1024 * 1024

_capacity_lock = threading.Lock()
_capacity_active = 0


class EmbeddedGcodeError(ValueError):
    """Stable, safe-to-expose failure from an embedded toolpath lookup."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EmbeddedGcode:
    """One bounded G-code member read from a 3MF archive."""

    filename: str
    content: bytes


@contextmanager
def preview_capacity() -> Iterator[None]:
    """Reserve one bounded preview slot before transfer or decompression."""

    global _capacity_active
    limit = max(int(settings.three_mf_preview_max_concurrent), 1)
    with _capacity_lock:
        if _capacity_active >= limit:
            raise EmbeddedGcodeError("embedded_gcode_busy")
        _capacity_active += 1
    try:
        yield
    finally:
        with _capacity_lock:
            _capacity_active -= 1


def _zip_footer_limits(
    archive_path: Path,
    archive_size: int,
    *,
    max_entries: int,
    max_central_directory_bytes: int,
) -> None:
    """Reject oversized ZIP metadata using its footer before ZipFile iterates."""

    if archive_size < _ZIP_EOCD_BYTES:
        raise EmbeddedGcodeError("embedded_gcode_malformed")
    with archive_path.open("rb") as source:
        source.seek(-min(archive_size, _ZIP_EOCD_SCAN_BYTES), 2)
        tail = source.read(_ZIP_EOCD_SCAN_BYTES)
    footer_at = tail.rfind(_ZIP_EOCD)
    if footer_at < 0 or footer_at + _ZIP_EOCD_BYTES > len(tail):
        # ZIP64 has a classic EOCD locator too; leave malformed/no-footer
        # classification to ZipFile when neither footer is discoverable.
        return
    _, _, _, _, entries, size_cd, offset_cd, comment_len = struct.unpack_from(
        "<4s4H2LH", tail, footer_at
    )
    del comment_len
    if entries == 0xFFFF or size_cd == 0xFFFFFFFF or offset_cd == 0xFFFFFFFF:
        locator_at = tail.rfind(_ZIP64_EOCD_LOCATOR, 0, footer_at)
        if locator_at >= 0 and locator_at + 20 <= len(tail):
            zip64_offset = struct.unpack_from("<Q", tail, locator_at + 8)[0]
            with archive_path.open("rb") as source:
                source.seek(zip64_offset)
                record = source.read(56)
            if len(record) >= 56 and record[:4] == _ZIP64_EOCD:
                entries = struct.unpack_from("<Q", record, 32)[0]
                size_cd = struct.unpack_from("<Q", record, 40)[0]
                offset_cd = struct.unpack_from("<Q", record, 48)[0]
    if entries > max_entries:
        raise EmbeddedGcodeError("embedded_gcode_too_many_entries")
    if size_cd > max_central_directory_bytes:
        raise EmbeddedGcodeError("embedded_gcode_central_directory_too_large")
    if offset_cd + size_cd > archive_size:
        raise EmbeddedGcodeError("embedded_gcode_malformed")


@contextmanager
def _bounded_archive(
    backend: StorageBackend,
    storage_key: str,
    *,
    max_archive_bytes: int,
) -> Iterator[Path]:
    """Yield a direct path or a bounded temporary copy of a remote archive."""

    archive_size = backend.stat_size(storage_key)
    if archive_size > max_archive_bytes:
        raise EmbeddedGcodeError("embedded_gcode_archive_too_large")
    direct = backend.direct_path(storage_key)
    if direct is not None:
        yield direct
        return

    fd, temporary_name = tempfile.mkstemp(suffix=".3mf")
    os.close(fd)
    temporary = Path(temporary_name)
    chunks = iter(backend.stream_chunks(storage_key, chunk_size=_STORAGE_CHUNK_BYTES))
    try:
        written = 0
        with temporary.open("wb") as destination:
            for chunk in chunks:
                written += len(chunk)
                if written > max_archive_bytes:
                    raise EmbeddedGcodeError("embedded_gcode_archive_too_large")
                destination.write(chunk)
        yield temporary
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
        temporary.unlink(missing_ok=True)


def _safe_member_name(name: str) -> bool:
    """Accept only canonical relative POSIX names; unsafe entries are ignored."""

    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or unicodedata.normalize("NFC", name) != name
    ):
        return False
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and str(path) == name
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _select_member(
    infos: list[zipfile.ZipInfo], plate_index: int | None
) -> zipfile.ZipInfo:
    safe_infos = [
        info
        for info in infos
        if not info.is_dir()
        and _safe_member_name(info.filename)
        and _PLATE_PATH.fullmatch(info.filename)
    ]
    if plate_index is not None:
        wanted = f"Metadata/plate_{plate_index}.gcode"
        matches = [info for info in safe_infos if info.filename == wanted]
    else:
        matches = safe_infos
    if not matches:
        raise EmbeddedGcodeError("embedded_gcode_not_found")
    if len(matches) != 1:
        raise EmbeddedGcodeError("embedded_gcode_ambiguous")
    return matches[0]


def extract_embedded_gcode(
    archive_path: Path,
    *,
    plate_index: int | None = None,
    max_uncompressed_bytes: int | None = None,
    max_compression_ratio: float | None = None,
    max_archive_bytes: int | None = None,
    max_entries: int | None = None,
    max_central_directory_bytes: int | None = None,
) -> EmbeddedGcode:
    """Read one embedded toolpath from *archive_path* with bounded memory use.

    A requested plate must have the exact ``Metadata/plate_<N>.gcode`` member.
    Without a plate index, exactly one canonical plate member is required.
    ``read(cap + 1)`` detects a stream that exceeds the configured cap without
    ever materialising an unbounded archive member.
    """

    if plate_index is not None and plate_index < 0:
        raise EmbeddedGcodeError("embedded_gcode_not_found")
    cap = (
        max_uncompressed_bytes
        if max_uncompressed_bytes is not None
        else settings.three_mf_preview_max_uncompressed_mb * 1024 * 1024
    )
    ratio_limit = (
        max_compression_ratio
        if max_compression_ratio is not None
        else settings.three_mf_preview_max_ratio
    )
    archive_cap = (
        max_archive_bytes
        if max_archive_bytes is not None
        else settings.three_mf_preview_max_archive_mb * 1024 * 1024
    )
    entry_cap = (
        max_entries
        if max_entries is not None
        else settings.three_mf_preview_max_entries
    )
    central_cap = (
        max_central_directory_bytes
        if max_central_directory_bytes is not None
        else settings.three_mf_preview_max_central_directory_mb * 1024 * 1024
    )
    if (
        cap <= 0
        or ratio_limit <= 0
        or archive_cap <= 0
        or entry_cap <= 0
        or central_cap <= 0
    ):
        raise ValueError("embedded_gcode_limits_invalid")

    try:
        archive_size = archive_path.stat().st_size
        if archive_size > archive_cap:
            raise EmbeddedGcodeError("embedded_gcode_archive_too_large")
        _zip_footer_limits(
            archive_path,
            archive_size,
            max_entries=entry_cap,
            max_central_directory_bytes=central_cap,
        )
        with zipfile.ZipFile(archive_path, "r") as archive:
            if getattr(archive, "size_cd", 0) > central_cap:
                raise EmbeddedGcodeError("embedded_gcode_central_directory_too_large")
            infos = archive.infolist()
            if len(infos) > entry_cap:
                raise EmbeddedGcodeError("embedded_gcode_too_many_entries")
            member = _select_member(infos, plate_index)
            if member.file_size > cap:
                raise EmbeddedGcodeError("embedded_gcode_too_large")
            if member.file_size and (
                member.compress_size == 0
                or member.file_size / member.compress_size > ratio_limit
            ):
                raise EmbeddedGcodeError("embedded_gcode_bomb")
            try:
                with archive.open(member, "r") as source:
                    content = source.read(cap + 1)
            except (
                EOFError,
                OSError,
                RuntimeError,
                NotImplementedError,
                ValueError,
                zlib.error,
                zipfile.BadZipFile,
            ) as exc:
                raise EmbeddedGcodeError("embedded_gcode_malformed") from exc
            if len(content) > cap:
                raise EmbeddedGcodeError("embedded_gcode_too_large")
            return EmbeddedGcode(
                filename=member.filename.rsplit("/", 1)[-1], content=content
            )
    except EmbeddedGcodeError:
        raise
    except FileNotFoundError:
        # Preserve the storage seam's missing-blob signal for the API's 410.
        raise
    except (
        EOFError,
        OSError,
        RuntimeError,
        NotImplementedError,
        ValueError,
        zlib.error,
        zipfile.BadZipFile,
    ) as exc:
        raise EmbeddedGcodeError("embedded_gcode_malformed") from exc


def read_embedded_gcode(
    backend: StorageBackend,
    storage_key: str,
    *,
    plate_index: int | None = None,
    max_uncompressed_bytes: int | None = None,
    max_compression_ratio: float | None = None,
    max_archive_bytes: int | None = None,
    max_entries: int | None = None,
    max_central_directory_bytes: int | None = None,
) -> EmbeddedGcode:
    """Read a bounded 3MF through storage metadata/chunks for local or S3 data."""

    archive_cap = (
        max_archive_bytes
        if max_archive_bytes is not None
        else settings.three_mf_preview_max_archive_mb * 1024 * 1024
    )
    with preview_capacity():
        try:
            with _bounded_archive(
                backend,
                storage_key,
                max_archive_bytes=archive_cap,
            ) as archive_path:
                return extract_embedded_gcode(
                    archive_path,
                    plate_index=plate_index,
                    max_uncompressed_bytes=max_uncompressed_bytes,
                    max_compression_ratio=max_compression_ratio,
                    max_archive_bytes=archive_cap,
                    max_entries=max_entries,
                    max_central_directory_bytes=max_central_directory_bytes,
                )
        except EmbeddedGcodeError:
            raise
        except FileNotFoundError:
            raise
        except (
            EOFError,
            OSError,
            RuntimeError,
            NotImplementedError,
            ValueError,
            zlib.error,
            zipfile.BadZipFile,
        ) as exc:
            raise EmbeddedGcodeError("embedded_gcode_malformed") from exc


def read_embedded_gcode_path(
    archive_path: Path,
    *,
    plate_index: int | None = None,
) -> EmbeddedGcode:
    """Read embedded G-code from an already resolved, stable Artifact path."""
    with preview_capacity():
        return extract_embedded_gcode(archive_path, plate_index=plate_index)
