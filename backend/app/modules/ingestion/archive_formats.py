"""TAR, 7z and RAR archives become a ZIP the existing review flow already owns.

Archive review, selection and extraction are ZIP-only: entry ids come from the
ZIP directory and each selected entry is opened by name. Rather than teach that
flow a second random-access reader, a non-ZIP archive is read once, in its own
order, into an uncompressed ZIP under the same entry, size and path limits. The
output is deterministic, so a review and its later selection see the same ids.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import unicodedata
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import IO, BinaryIO, Protocol

from printstash_core.files.archives import (
    ArchiveLimits,
    ArchivePolicyError,
    safe_entry_name,
)

# Members are written with one fixed timestamp: the transcoded bytes, and so
# every entry id derived from them, depend only on the source archive.
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
_CHUNK = 1024 * 1024
# A stored ZIP64 member costs a local header, data descriptor and central
# record around its bytes, each repeating the name; 256 bytes covers the rest.
_MEMBER_OVERHEAD = 256
_DIRECTORY_OVERHEAD = 1024
_SEVEN_ZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
_RAR_MAGICS = (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")


class ArchiveFormat(StrEnum):
    ZIP = "zip"
    TAR = "tar"
    SEVEN_ZIP = "7z"
    RAR = "rar"


# Longest first: ``.tar.gz`` must not be read as a ``.gz`` file.
ARCHIVE_SUFFIXES: tuple[tuple[str, ArchiveFormat], ...] = (
    (".tar.gz", ArchiveFormat.TAR),
    (".tar.bz2", ArchiveFormat.TAR),
    (".tar.xz", ArchiveFormat.TAR),
    (".tgz", ArchiveFormat.TAR),
    (".tar", ArchiveFormat.TAR),
    (".zip", ArchiveFormat.ZIP),
    (".7z", ArchiveFormat.SEVEN_ZIP),
    (".rar", ArchiveFormat.RAR),
)


def archive_suffix(filename: str) -> str | None:
    """The archive suffix *filename* ends with, compound suffixes included."""
    lower = filename.lower()
    return next(
        (suffix for suffix, _ in ARCHIVE_SUFFIXES if lower.endswith(suffix)), None
    )


def named_format(filename: str) -> ArchiveFormat | None:
    """The archive format *filename* names, without reading any bytes."""
    lower = filename.lower()
    return next(
        (archive for suffix, archive in ARCHIVE_SUFFIXES if lower.endswith(suffix)),
        None,
    )


def sniff_format(path: Path) -> ArchiveFormat | None:
    """The archive format *path*'s bytes hold, for links that name no suffix."""
    with path.open("rb") as source:
        head = source.read(8)
    if head.startswith(_SEVEN_ZIP_MAGIC):
        return ArchiveFormat.SEVEN_ZIP
    if head.startswith(_RAR_MAGICS):
        return ArchiveFormat.RAR
    if zipfile.is_zipfile(path):
        return ArchiveFormat.ZIP
    return ArchiveFormat.TAR if _starts_with_tar_header(path) else None


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


@dataclass(frozen=True)
class _Member:
    name: str
    size: int


class _Writer:
    """Copy kept members into a stored ZIP while enforcing archive limits."""

    def __init__(
        self,
        archive: zipfile.ZipFile | None,
        *,
        limits: ArchiveLimits,
        keep: Callable[[str], bool],
        on_chunk: Callable[[], None],
    ) -> None:
        # Without an output ZIP the writer only plans: it admits every member
        # under the same limits and sums the bytes the ZIP will need.
        self._archive = archive
        self.planned_bytes = _DIRECTORY_OVERHEAD
        self._limits = limits
        self._keep = keep
        self._on_chunk = on_chunk
        self._entries = 0
        self._total = 0
        self._names: set[str] = set()
        self._handle: IO[bytes] | None = None
        # A streamed archive may decompress no more than the uncompressed
        # archive limit in all, plus room for every member's headers.
        self.stream_budget = limits.max_total_bytes + (limits.max_entries + 1) * (
            4 * _BLOCK + limits.max_path_bytes
        )
        self.max_path_bytes = limits.max_path_bytes
        self.on_chunk = on_chunk

    def admit(self, member: _Member) -> bool:
        """Check one regular file; True when its bytes belong in the output."""
        self._entries += 1
        if self._entries > self._limits.max_entries:
            raise ArchivePolicyError("archive_too_many_entries")
        normalized = unicodedata.normalize("NFC", member.name.replace("\\", "/"))
        if len(normalized.encode("utf-8")) > self._limits.max_path_bytes or (
            len(PurePosixPath(normalized).parts) > self._limits.max_depth + 1
        ):
            raise ArchivePolicyError("archive_path_too_deep")
        if not safe_entry_name(member.name):
            raise ArchivePolicyError("archive_unsafe_entry")
        folded = normalized.casefold()
        if folded in self._names:
            raise ArchivePolicyError("archive_duplicate_entry")
        self._names.add(folded)
        if member.size > self._limits.max_entry_bytes:
            raise ArchivePolicyError("archive_entry_too_large")
        if not self._keep(member.name):
            return False
        self._total += member.size
        if self._total > self._limits.max_total_bytes:
            raise ArchivePolicyError("archive_too_large")
        self.planned_bytes += (
            member.size + _MEMBER_OVERHEAD + 2 * len(member.name.encode("utf-8"))
        )
        return self._archive is not None

    def count(self) -> None:
        """Count a member that carries no file (a directory, link or device)."""
        self._entries += 1
        if self._entries > self._limits.max_entries:
            raise ArchivePolicyError("archive_too_many_entries")

    @contextmanager
    def member(self, member: _Member) -> Iterator[_BoundedTarget]:
        assert self._archive is not None
        info = zipfile.ZipInfo(member.name, date_time=_FIXED_TIME)
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        with self._archive.open(info, "w", force_zip64=True) as target:
            self._handle = target
            yield _BoundedTarget(target, member, self._on_chunk)
        self._handle = None

    def abort(self) -> None:
        """Release a member a failed reader left open, so the failure surfaces."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def copy(self, member: _Member, source: _Readable) -> None:
        with self.member(member) as target:
            while chunk := source.read(_CHUNK):
                target.write(chunk)
            target.close()


class _BoundedTarget:
    """A ZIP member that refuses more bytes than its archive declared."""

    def __init__(
        self, target: IO[bytes], member: _Member, on_chunk: Callable[[], None]
    ):
        self._target = target
        self._member = member
        self._on_chunk = on_chunk
        self._written = 0

    def write(self, chunk: bytes) -> int:
        self._on_chunk()
        self._written += len(chunk)
        if self._written > self._member.size:
            raise ArchivePolicyError("archive_entry_too_large")
        return self._target.write(chunk)

    def close(self) -> None:
        if self._written != self._member.size:
            raise ArchivePolicyError("archive_invalid")


def planned_zip_bytes(
    source: Path,
    archive: ArchiveFormat,
    *,
    limits: ArchiveLimits,
    keep: Callable[[str], bool],
    on_chunk: Callable[[], None],
) -> int:
    """Bytes the stored ZIP of *source* will take, refusing it as transcoding would.

    The plan reads only the archive's own directory, except for TAR, whose
    member headers are spread through the (possibly compressed) stream; that
    read calls *on_chunk* as it goes, so a cancelled Job stops it.
    """
    writer = _Writer(None, limits=limits, keep=keep, on_chunk=on_chunk)
    _READERS[archive](source, writer)
    return writer.planned_bytes


def transcode_to_zip(
    source: Path,
    archive: ArchiveFormat,
    destination: Path,
    *,
    limits: ArchiveLimits,
    keep: Callable[[str], bool],
    on_chunk: Callable[[], None],
) -> None:
    """Write *source*'s kept regular files to *destination* as a stored ZIP.

    Directories, links and device entries are dropped: they carry no model and
    a link could only point at something outside the archive. Every other entry
    counts toward the entry limit and must have a safe, unique path.
    """
    with zipfile.ZipFile(destination, "w", allowZip64=True) as output:
        writer = _Writer(output, limits=limits, keep=keep, on_chunk=on_chunk)
        try:
            _READERS[archive](source, writer)
        except BaseException:
            writer.abort()
            raise


_BLOCK = 512
_ZERO_BLOCK = bytes(_BLOCK)
# Member types that carry a file, and those that are counted and dropped
# (hard and symbolic links, character and block devices, directories, FIFOs).
_REGULAR_TYPES = frozenset({b"0", b"\0", b"7"})
_DROPPED_TYPES = frozenset({b"1", b"2", b"3", b"4", b"5", b"6"})
_GNU_LONG_NAME = b"L"
_GNU_LONG_LINK = b"K"
_PAX_MEMBER = b"x"
_PAX_GLOBAL = b"g"
# Real pax headers hold a path and a few timestamps: a few hundred bytes.
_PAX_HEADER_BYTES = 64 * 1024
_POSIX_MAGIC = b"ustar\0"
_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"


def _decompressed(
    source: Path,
) -> gzip.GzipFile | bz2.BZ2File | lzma.LZMAFile | BinaryIO:
    """*source* as its uncompressed TAR stream, by its compression magic."""
    with source.open("rb") as probe:
        head = probe.read(6)
    if head.startswith(_GZIP_MAGIC):
        return gzip.open(source, "rb")
    if head.startswith(_BZIP2_MAGIC):
        return bz2.open(source, "rb")
    if head.startswith(_XZ_MAGIC):
        return lzma.open(source, "rb")
    return source.open("rb")


# Every error a decompressor raises for damaged input.
_STREAM_ERRORS = (OSError, EOFError, lzma.LZMAError)


class _TarStream:
    """The uncompressed TAR stream, refused once it outgrows the archive limit.

    The budget is every byte decompressed (headers, skipped and kept payloads
    alike), so a tiny archive cannot make the reader inflate gigabytes it
    would only throw away.
    """

    def __init__(self, raw: _Readable, budget: int, on_chunk: Callable[[], None]):
        self._raw = raw
        self._left = budget
        self._on_chunk = on_chunk

    def block(self) -> bytes | None:
        """The next header block, or None at the end of the stream."""
        data = self._read(_BLOCK)
        if not data:
            return None
        if len(data) != _BLOCK:
            raise ArchivePolicyError("archive_invalid")
        return data

    def payload(self, size: int) -> bytes:
        """A small payload read whole, with its padding."""
        data = self._exact(size)
        self.skip(_padding(size))
        return data

    def skip(self, size: int) -> None:
        while size:
            size -= len(self._exact(min(size, _CHUNK)))

    def reader(self, size: int) -> _Readable:
        stream = self

        class _Payload:
            left = size
            padded = False

            def read(self, limit: int = -1, /) -> bytes:
                if not self.left:
                    if not self.padded:
                        self.padded = True
                        stream.skip(_padding(size))
                    return b""
                chunk = stream._exact(min(self.left, _CHUNK, limit if limit > 0 else _CHUNK))
                self.left -= len(chunk)
                return chunk

        return _Payload()

    def _exact(self, size: int) -> bytes:
        data = self._read(size)
        if len(data) != size:
            raise ArchivePolicyError("archive_invalid")
        return data

    def _read(self, size: int) -> bytes:
        self._left -= size
        if self._left < 0:
            raise ArchivePolicyError("archive_too_large")
        self._on_chunk()
        parts: list[bytes] = []
        while size and (chunk := self._raw.read(size)):
            parts.append(chunk)
            size -= len(chunk)
        return b"".join(parts)


def _padding(size: int) -> int:
    return -size % _BLOCK


def _number(field: bytes) -> int:
    """A TAR numeric field: octal text, or GNU base-256 when the high bit is set."""
    if field[0] & 0x80:
        if field[0] == 0xFF:
            raise ArchivePolicyError("archive_invalid")
        return int.from_bytes(bytes([field[0] & 0x7F]) + field[1:], "big")
    text = field.split(b"\0", 1)[0].strip(b" ")
    if not text:
        return 0
    try:
        return int(text, 8)
    except ValueError as exc:
        raise ArchivePolicyError("archive_invalid") from exc


def _text(field: bytes) -> str:
    try:
        return field.split(b"\0", 1)[0].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchivePolicyError("archive_invalid") from exc


@dataclass(frozen=True)
class _Header:
    name: str
    size: int
    kind: bytes


def _header(block: bytes) -> _Header:
    """Parse one 512-byte header block, refusing one whose checksum is wrong."""
    stored = _number(block[148:156])
    unsigned = sum(block[:148]) + 8 * 0x20 + sum(block[156:])
    # Some writers summed signed bytes; ``tarfile`` accepts either.
    signed = unsigned - 256 * sum(1 for byte in block[:148] + block[156:] if byte > 127)
    if stored not in (unsigned, signed):
        raise ArchivePolicyError("archive_invalid")
    name = _text(block[0:100])
    if block[257:263] == _POSIX_MAGIC and (prefix := _text(block[345:500])):
        name = f"{prefix}/{name}"
    return _Header(name=name, size=_number(block[124:136]), kind=block[156:157])


def _pax_records(payload: bytes) -> dict[str, str]:
    """The ``key=value`` records of a pax extended header."""
    records: dict[str, str] = {}
    position = 0
    while position < len(payload):
        space = payload.find(b" ", position)
        if space < 0:
            raise ArchivePolicyError("archive_invalid")
        try:
            length = int(payload[position:space])
        except ValueError as exc:
            raise ArchivePolicyError("archive_invalid") from exc
        record = payload[space + 1 : position + length]
        if length <= space - position or not record.endswith(b"\n") or b"=" not in record:
            raise ArchivePolicyError("archive_invalid")
        key, value = record[:-1].split(b"=", 1)
        try:
            records[key.decode("utf-8")] = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchivePolicyError("archive_invalid") from exc
        position += length
    return records


def _starts_with_tar_header(path: Path) -> bool:
    """Whether *path* opens with a valid TAR header, reading one block only."""
    try:
        with _decompressed(path) as raw:
            block = raw.read(_BLOCK)
    except _STREAM_ERRORS:
        return False
    if len(block) != _BLOCK or block == _ZERO_BLOCK:
        return False
    try:
        _header(block)
    except ArchivePolicyError:
        return False
    return True


def _copy_tar(source: Path, writer: _Writer) -> None:
    """Read TAR headers in order without ``tarfile``.

    ``tarfile`` loads a long-name or pax payload whole and inflates an unknown
    member to skip it, both before a caller sees the member. Here every header
    payload has a cap, every member counts toward the entry limit, an unknown
    member type is refused, and the stream is bounded by the archive size limit.
    """
    try:
        with _decompressed(source) as raw:
            _read_tar(_TarStream(raw, writer.stream_budget, writer.on_chunk), writer)
    except _STREAM_ERRORS as exc:
        raise ArchivePolicyError("archive_invalid") from exc


def _read_tar(stream: _TarStream, writer: _Writer) -> None:
    long_name: str | None = None
    pax: dict[str, str] = {}
    while (block := stream.block()) is not None and block != _ZERO_BLOCK:
        header = _header(block)
        if header.kind == _GNU_LONG_NAME:
            if header.size > writer.max_path_bytes + 1:
                raise ArchivePolicyError("archive_path_too_deep")
            long_name = _text(stream.payload(header.size))
            continue
        if header.kind == _GNU_LONG_LINK:
            if header.size > writer.max_path_bytes + 1:
                raise ArchivePolicyError("archive_path_too_deep")
            stream.payload(header.size)
            continue
        if header.kind in (_PAX_MEMBER, _PAX_GLOBAL):
            if header.size > _PAX_HEADER_BYTES:
                raise ArchivePolicyError("archive_invalid")
            records = _pax_records(stream.payload(header.size))
            if any(key.startswith("GNU.sparse.") for key in records):
                raise ArchivePolicyError("archive_invalid")
            # A global header's defaults never name a member: only its sparse
            # markers matter, and those were refused above.
            if header.kind == _PAX_MEMBER:
                pax = records
            continue
        name = pax.get("path", long_name if long_name is not None else header.name)
        size = _number_text(pax["size"]) if "size" in pax else header.size
        long_name, pax = None, {}
        if header.kind in _DROPPED_TYPES or (
            header.kind in _REGULAR_TYPES and name.endswith("/")
        ):
            writer.count()
            stream.skip(size + _padding(size))
            continue
        if header.kind not in _REGULAR_TYPES:
            raise ArchivePolicyError("archive_invalid")
        member = _Member(name, size)
        if writer.admit(member):
            writer.copy(member, stream.reader(size))
        else:
            stream.skip(size + _padding(size))


def _number_text(value: str) -> int:
    if not value.isdigit():
        raise ArchivePolicyError("archive_invalid")
    return int(value)


def _copy_seven_zip(source: Path, writer: _Writer) -> None:
    import py7zr
    from py7zr.exceptions import ArchiveError
    from py7zr.io import Py7zIO, WriterFactory

    class Member(Py7zIO):
        """Stream one decompressed member into its open ZIP entry."""

        def __init__(self, member: _Member) -> None:
            self._context = writer.member(member)
            self._target = self._context.__enter__()

        def write(self, s: bytes | bytearray) -> int:
            return self._target.write(bytes(s))

        def read(self, size: int | None = None) -> bytes:
            raise ArchivePolicyError("archive_invalid")

        def seek(self, offset: int, whence: int = 0) -> int:
            raise ArchivePolicyError("archive_invalid")

        def seekable(self) -> bool:
            # py7zr rewinds seekable products to read them back; a ZIP entry
            # being written cannot be.
            return False

        def flush(self) -> None:
            return None

        def size(self) -> int:
            raise ArchivePolicyError("archive_invalid")

        def close(self) -> None:
            self._target.close()
            self._context.__exit__(None, None, None)

    class Factory(WriterFactory):
        def __init__(self, sizes: dict[str, int]) -> None:
            self._sizes = sizes

        def create(self, filename: str) -> Py7zIO:
            return Member(_Member(filename, self._sizes[filename]))

    try:
        # A file object keeps py7zr's extraction on one thread: with a path it
        # decompresses folders in parallel, and the single output ZIP allows
        # only one open write handle.
        with source.open("rb") as handle, py7zr.SevenZipFile(handle, mode="r") as archive:
            if archive.needs_password():
                raise ArchivePolicyError("archive_invalid")
            sizes: dict[str, int] = {}
            for info in archive.list():
                if info.is_directory or info.is_symlink:
                    continue
                member = _Member(info.filename, int(info.uncompressed))
                if writer.admit(member):
                    sizes[info.filename] = member.size
            if sizes:
                archive.extract(targets=list(sizes), factory=Factory(sizes))
    except ArchivePolicyError:
        raise
    except (ArchiveError, py7zr.Bad7zFile, OSError, ValueError, KeyError) as exc:
        raise ArchivePolicyError("archive_invalid") from exc


def _copy_rar(source: Path, writer: _Writer) -> None:
    import rarfile

    try:
        # Strict parsing: a damaged header is a broken archive, not its end.
        with rarfile.RarFile(source, errors="strict") as archive:
            if archive.needs_password():
                raise ArchivePolicyError("archive_invalid")
            for info in archive.infolist():
                if info.is_dir() or info.is_symlink():
                    continue
                member = _Member(info.filename, int(info.file_size))
                if not writer.admit(member):
                    continue
                with archive.open(info) as extracted:
                    writer.copy(member, extracted)
    except rarfile.RarCannotExec as exc:
        raise ArchivePolicyError("archive_format_unavailable") from exc
    except (rarfile.Error, OSError) as exc:
        raise ArchivePolicyError("archive_invalid") from exc


_READERS: dict[ArchiveFormat, Callable[[Path, _Writer], None]] = {
    ArchiveFormat.TAR: _copy_tar,
    ArchiveFormat.SEVEN_ZIP: _copy_seven_zip,
    ArchiveFormat.RAR: _copy_rar,
}
