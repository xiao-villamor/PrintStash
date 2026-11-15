"""TAR, 7z and RAR archives become the stored ZIP the review flow reads.

The review lists entry ids from that ZIP and the later selection rewrites the
archive again, so the output must be byte-identical every time. The rewrite is
also the only place these formats meet the archive limits: every refusal the
ZIP inspector makes for a ZIP has to be made here for the other formats.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest
from printstash_core.files.archives import ArchiveLimits, ArchivePolicyError

from app.modules.ingestion import archive_formats
from app.modules.ingestion.archive_formats import ArchiveFormat
from tests.factories.content import rar_bytes, seven_zip_bytes, tar_bytes, zip_bytes

LIMITS = ArchiveLimits(
    max_entries=10,
    max_entry_bytes=1000,
    max_total_bytes=1500,
    max_central_directory_bytes=1 << 20,
    max_path_bytes=64,
    max_depth=3,
)
ENTRIES = {"pack/a.stl": b"solid a", "pack/deep/b.3mf": b"PK-3mf", "notes.txt": b"n"}


def _keep(name: str) -> bool:
    return not name.endswith(".txt")


def _build(archive: ArchiveFormat, entries: dict[str, bytes]) -> bytes:
    if archive is ArchiveFormat.TAR:
        return tar_bytes(entries)
    if archive is ArchiveFormat.SEVEN_ZIP:
        return seven_zip_bytes(entries)
    return rar_bytes(entries)


def _transcode(tmp_path: Path, archive: ArchiveFormat, data: bytes, name="out.zip"):
    source = tmp_path / f"source.{archive.value}"
    source.write_bytes(data)
    target = tmp_path / name
    archive_formats.transcode_to_zip(
        source, archive, target, limits=LIMITS, keep=_keep, on_chunk=lambda: None
    )
    return target


FORMATS = [ArchiveFormat.TAR, ArchiveFormat.SEVEN_ZIP, ArchiveFormat.RAR]


class TestFormatNames:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("pack.zip", ArchiveFormat.ZIP),
            ("Pack.TAR.GZ", ArchiveFormat.TAR),
            ("pack.tgz", ArchiveFormat.TAR),
            ("pack.tar.xz", ArchiveFormat.TAR),
            ("pack.tar.bz2", ArchiveFormat.TAR),
            ("pack.tar", ArchiveFormat.TAR),
            ("pack.7z", ArchiveFormat.SEVEN_ZIP),
            ("pack.rar", ArchiveFormat.RAR),
            ("part.stl", None),
            ("part.stl.gz", None),
        ],
    )
    def test_names_the_archive_its_suffix_names(self, filename, expected) -> None:
        assert archive_formats.named_format(filename) is expected

    def test_keeps_the_whole_compound_suffix(self) -> None:
        assert archive_formats.archive_suffix("pack.tar.gz") == ".tar.gz"


class TestSniffing:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (zip_bytes({"a.stl": b"x"}), ArchiveFormat.ZIP),
            (tar_bytes({"a.stl": b"x"}), ArchiveFormat.TAR),
            (tar_bytes({"a.stl": b"x"}, compression=""), ArchiveFormat.TAR),
            (seven_zip_bytes({"a.stl": b"x"}), ArchiveFormat.SEVEN_ZIP),
            (rar_bytes({"a.stl": b"x"}), ArchiveFormat.RAR),
            (b"solid part\nendsolid\n", None),
        ],
        ids=["zip", "tar.gz", "tar", "7z", "rar", "stl"],
    )
    def test_recognizes_an_archive_by_its_bytes(self, tmp_path, data, expected) -> None:
        path = tmp_path / "download"
        path.write_bytes(data)

        assert archive_formats.sniff_format(path) is expected


@pytest.mark.parametrize("archive", FORMATS, ids=lambda archive: archive.value)
class TestTranscoding:
    def test_keeps_models_with_their_paths(self, tmp_path, archive) -> None:
        target = _transcode(tmp_path, archive, _build(archive, ENTRIES))

        with zipfile.ZipFile(target) as output:
            assert {info.filename: output.read(info) for info in output.infolist()} == {
                "pack/a.stl": b"solid a",
                "pack/deep/b.3mf": b"PK-3mf",
            }

    def test_stores_members_uncompressed(self, tmp_path, archive) -> None:
        target = _transcode(tmp_path, archive, _build(archive, ENTRIES))

        with zipfile.ZipFile(target) as output:
            assert {info.compress_type for info in output.infolist()} == {
                zipfile.ZIP_STORED
            }

    def test_writes_identical_bytes_on_every_run(self, tmp_path, archive) -> None:
        data = _build(archive, ENTRIES)
        first = _transcode(tmp_path, archive, data, "first.zip")
        second = _transcode(tmp_path, archive, data, "second.zip")

        assert (
            hashlib.sha256(first.read_bytes()).digest()
            == hashlib.sha256(second.read_bytes()).digest()
        )

    def test_plans_at_least_the_bytes_it_writes(self, tmp_path, archive) -> None:
        source = tmp_path / "source"
        source.write_bytes(_build(archive, ENTRIES))
        planned = archive_formats.planned_zip_bytes(
            source, archive, limits=LIMITS, keep=_keep, on_chunk=lambda: None
        )

        target = _transcode(tmp_path, archive, source.read_bytes())

        assert target.stat().st_size <= planned

    def test_refuses_an_entry_over_the_entry_limit(self, tmp_path, archive) -> None:
        data = _build(archive, {"big.stl": b"x" * 1001})

        with pytest.raises(ArchivePolicyError, match="archive_entry_too_large"):
            _transcode(tmp_path, archive, data)

    def test_refuses_kept_bytes_over_the_total_limit(self, tmp_path, archive) -> None:
        data = _build(archive, {"a.stl": b"x" * 800, "b.stl": b"y" * 800})

        with pytest.raises(ArchivePolicyError, match="archive_too_large"):
            _transcode(tmp_path, archive, data)

    def test_refuses_more_entries_than_the_limit(self, tmp_path, archive) -> None:
        data = _build(archive, {f"{index}.txt": b"" for index in range(11)})

        with pytest.raises(ArchivePolicyError, match="archive_too_many_entries"):
            _transcode(tmp_path, archive, data)

    def test_refuses_a_path_deeper_than_the_limit(self, tmp_path, archive) -> None:
        data = _build(archive, {"a/b/c/d/e.stl": b"x"})

        with pytest.raises(ArchivePolicyError, match="archive_path_too_deep"):
            _transcode(tmp_path, archive, data)

    def test_refuses_names_equal_after_case_folding(self, tmp_path, archive) -> None:
        data = _build(archive, {"Part.stl": b"x", "part.STL": b"y"})

        with pytest.raises(ArchivePolicyError, match="archive_duplicate_entry"):
            _transcode(tmp_path, archive, data)


class TestTranscodingPaths:
    # py7zr refuses to write a traversing name, so no 7z fixture can hold one.
    @pytest.mark.parametrize(
        "archive",
        [ArchiveFormat.TAR, ArchiveFormat.RAR],
        ids=lambda archive: archive.value,
    )
    def test_refuses_a_traversing_path(self, tmp_path, archive) -> None:
        data = _build(archive, {"../escape.stl": b"x"})

        with pytest.raises(ArchivePolicyError, match="archive_unsafe_entry"):
            _transcode(tmp_path, archive, data)


class TestTarMembers:
    def test_drops_symbolic_links(self, tmp_path) -> None:
        data = tar_bytes({"a.stl": b"x"}, links={"escape.stl": "/etc/passwd"})

        target = _transcode(tmp_path, ArchiveFormat.TAR, data)

        with zipfile.ZipFile(target) as output:
            assert output.namelist() == ["a.stl"]

    def test_refuses_a_truncated_archive(self, tmp_path) -> None:
        data = tar_bytes({"a.stl": b"x" * 600}, compression="")[:700]

        with pytest.raises(ArchivePolicyError, match="archive_invalid"):
            _transcode(tmp_path, ArchiveFormat.TAR, data)


def _hostile_tar(member_type: bytes, declared: int) -> bytes:
    """A bz2 TAR whose first member declares *declared* zero bytes of *member_type*.

    The shape of the audit's probes: a few hundred bytes on disk that
    ``tarfile`` would inflate in full while parsing it.
    """
    import bz2
    import tarfile

    info = tarfile.TarInfo("x")
    info.size = declared
    info.type = member_type
    compressor = bz2.BZ2Compressor(9)
    parts = [compressor.compress(info.tobuf(format=tarfile.GNU_FORMAT))]
    block = bytes(1 << 20)
    remaining = declared + (-declared % 512)
    while remaining:
        parts.append(compressor.compress(block[: min(remaining, len(block))]))
        remaining -= min(remaining, len(block))
    body = b"solid a"
    tail = tarfile.TarInfo("a.stl")
    tail.size = len(body)
    parts.append(compressor.compress(tail.tobuf(format=tarfile.GNU_FORMAT)))
    parts.append(compressor.compress(body + bytes(-len(body) % 512) + bytes(1024)))
    parts.append(compressor.flush())
    return b"".join(parts)


def _plan(tmp_path: Path, data: bytes, limits: ArchiveLimits = LIMITS, on_chunk=None):
    source = tmp_path / "source.tar"
    source.write_bytes(data)
    return archive_formats.planned_zip_bytes(
        source,
        ArchiveFormat.TAR,
        limits=limits,
        keep=_keep,
        on_chunk=on_chunk or (lambda: None),
    )


WIDE = ArchiveLimits(
    max_entries=10,
    max_entry_bytes=1000,
    max_total_bytes=1500,
    max_central_directory_bytes=1 << 20,
    max_path_bytes=1024,
    max_depth=32,
)


class TestHostileTarHeaders:
    """A tiny TAR must not drive memory or CPU before any limit applies (F1)."""

    def test_refuses_a_long_name_payload_over_the_path_limit(self, tmp_path) -> None:
        import tarfile
        import time

        data = _hostile_tar(tarfile.GNUTYPE_LONGNAME, 16 << 20)
        started = time.process_time()

        with pytest.raises(ArchivePolicyError, match="archive_path_too_deep"):
            _plan(tmp_path, data)

        assert time.process_time() - started < 0.5

    def test_refuses_an_unknown_member_type_without_inflating_it(
        self, tmp_path
    ) -> None:
        import time

        data = _hostile_tar(b"Z", 16 << 20)
        started = time.process_time()

        with pytest.raises(ArchivePolicyError, match="archive_invalid"):
            _plan(tmp_path, data)

        assert time.process_time() - started < 0.5

    def test_sniffs_a_hostile_archive_from_its_first_block(self, tmp_path) -> None:
        import tarfile
        import time

        source = tmp_path / "download"
        source.write_bytes(_hostile_tar(tarfile.GNUTYPE_LONGNAME, 16 << 20))
        started = time.process_time()

        assert archive_formats.sniff_format(source) is ArchiveFormat.TAR
        assert time.process_time() - started < 0.1

    def test_counts_links_and_directories_toward_the_entry_limit(
        self, tmp_path
    ) -> None:
        links = {f"link{i}.stl": "a.stl" for i in range(LIMITS.max_entries)}
        data = tar_bytes({"a.stl": b"x"}, links=links)

        with pytest.raises(ArchivePolicyError, match="archive_too_many_entries"):
            _plan(tmp_path, data)

    def test_refuses_a_stream_that_inflates_past_the_archive_limit(
        self, tmp_path
    ) -> None:
        limits = ArchiveLimits(
            max_entries=3,
            max_entry_bytes=100_000,
            max_total_bytes=100_000,
            max_central_directory_bytes=1 << 20,
            max_path_bytes=64,
            max_depth=3,
        )
        # Neither file is kept, so only the stream budget can refuse them.
        data = tar_bytes({"a.bin": bytes(100_000), "b.bin": bytes(100_000)})

        with pytest.raises(ArchivePolicyError, match="archive_too_large"):
            _plan(tmp_path, data, limits)

    def test_planning_stops_when_the_job_is_cancelled(self, tmp_path) -> None:
        class Cancelled(Exception):
            pass

        def cancelled() -> None:
            raise Cancelled

        with pytest.raises(Cancelled):
            _plan(tmp_path, tar_bytes(ENTRIES), on_chunk=cancelled)

    def test_refuses_a_header_with_a_wrong_checksum(self, tmp_path) -> None:
        data = bytearray(tar_bytes({"a.stl": b"x"}, compression=""))
        data[0] ^= 0x01

        with pytest.raises(ArchivePolicyError, match="archive_invalid"):
            _transcode(tmp_path, ArchiveFormat.TAR, bytes(data))

    @pytest.mark.parametrize("tar_format", ["gnu", "pax", "ustar"])
    def test_keeps_long_paths_in_every_tar_format(self, tmp_path, tar_format) -> None:
        import io
        import tarfile

        name = "/".join(["folder"] * 15) + "/model.stl"  # 116 bytes
        formats = {
            "gnu": tarfile.GNU_FORMAT,
            "pax": tarfile.PAX_FORMAT,
            "ustar": tarfile.USTAR_FORMAT,
        }
        buffer = io.BytesIO()
        with tarfile.open(
            fileobj=buffer, mode="w:xz", format=formats[tar_format]
        ) as archive:
            info = tarfile.TarInfo(name)
            info.size = 5
            archive.addfile(info, io.BytesIO(b"solid"))
            folder = tarfile.TarInfo("folder")
            folder.type = tarfile.DIRTYPE
            archive.addfile(folder)
        source = tmp_path / "source.tar.xz"
        source.write_bytes(buffer.getvalue())
        target = tmp_path / "out.zip"

        archive_formats.transcode_to_zip(
            source,
            ArchiveFormat.TAR,
            target,
            limits=WIDE,
            keep=_keep,
            on_chunk=lambda: None,
        )

        with zipfile.ZipFile(target) as output:
            assert {n: output.read(n) for n in output.namelist()} == {name: b"solid"}


class TestSevenZipMembers:
    def test_refuses_an_encrypted_archive(self, tmp_path) -> None:
        data = seven_zip_bytes({"a.stl": b"x"}, password="secret")

        with pytest.raises(ArchivePolicyError, match="archive_invalid"):
            _transcode(tmp_path, ArchiveFormat.SEVEN_ZIP, data)

    def test_refuses_bytes_that_are_not_a_7z(self, tmp_path) -> None:
        with pytest.raises(ArchivePolicyError, match="archive_invalid"):
            _transcode(tmp_path, ArchiveFormat.SEVEN_ZIP, b"7z\xbc\xaf\x27\x1cbroken")

    def test_transcodes_an_archive_with_several_folders(self, tmp_path) -> None:
        # Non-solid 7z holds one folder per file; py7zr extracts folders on
        # threads when given a path, which opened two ZIP write handles at once.
        import py7zr

        entries = {f"part{i}.stl": bytes([i]) * 200 for i in range(4)}
        target = tmp_path / "split.7z"
        # Each append session writes its own folder.
        for index, (name, body) in enumerate(entries.items()):
            with py7zr.SevenZipFile(target, mode="a" if index else "w") as archive:
                archive.writestr(body, name)
        with py7zr.SevenZipFile(target, mode="r") as archive:
            streams = archive.header.main_streams
            assert streams is not None and streams.unpackinfo is not None
            assert streams.unpackinfo.numfolders > 1

        out = _transcode(tmp_path, ArchiveFormat.SEVEN_ZIP, target.read_bytes())

        with zipfile.ZipFile(out) as result:
            assert {name: result.read(name) for name in result.namelist()} == entries


class TestRarMembers:
    def test_refuses_bytes_that_are_not_a_rar(self, tmp_path) -> None:
        with pytest.raises(ArchivePolicyError, match="archive_invalid"):
            _transcode(tmp_path, ArchiveFormat.RAR, b"Rar!\x1a\x07\x00broken")
