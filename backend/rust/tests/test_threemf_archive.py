"""Native member reads preserve geometry and reject incomplete ZIP input."""

import io
import struct
import zipfile
import zlib

import printstash_mesh_native as native
import pytest

XML = b'<model><resources><object id="1"><mesh><vertices><vertex x="1" y="2" z="3"/></vertices><triangles><triangle v1="0" v2="0" v3="0"/></triangles></mesh></object></resources><build><item objectid="1"/></build></model>'
PART = "3D/3dmodel.model"


@pytest.fixture
def package(tmp_path):
    def write(compression=zipfile.ZIP_DEFLATED):
        path = tmp_path / "native.3mf"
        with zipfile.ZipFile(path, "w", compression=compression) as archive:
            archive.writestr(PART, XML)
            archive.writestr("3D/part.model", XML.replace(b'x="1"', b'x="9"'))
        return path

    return write


class TestThreeMfArchive:
    @pytest.mark.parametrize(
        "compression", [0, 8, 12, 14], ids=["stored", "deflated", "bzip2", "lzma"]
    )
    def test_preserves_packed_model_bytes(self, package, compression):
        archive = native.ThreeMfArchive(package(compression))
        assert archive.read_part(PART, len(XML), zlib.crc32(XML), len(XML)) == (
            native.parse_3mf_xml(io.BytesIO(XML))
        )

    def test_reads_multiple_package_parts(self, package):
        archive = native.ThreeMfArchive(package())
        for name, xml in [
            (PART, XML),
            ("3D/part.model", XML.replace(b'x="1"', b'x="9"')),
            (PART, XML),
        ]:
            assert archive.read_part(name, len(xml), zlib.crc32(xml), len(xml)) == (
                native.parse_3mf_xml(io.BytesIO(xml))
            )

    def test_reads_across_buffer_boundaries(self, tmp_path):
        xml = XML + b"\n" * (2 * 65_536)
        path = tmp_path / "large.3mf"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as writer:
            writer.writestr(PART, xml)
        archive = native.ThreeMfArchive(path)
        assert archive.read_part(PART, len(xml), zlib.crc32(xml), len(xml)) == (
            native.parse_3mf_xml(io.BytesIO(xml))
        )

    def test_rejects_unsupported_compression(self, package):
        path = package(zipfile.ZIP_STORED)
        data = bytearray(path.read_bytes())
        struct.pack_into("<H", data, 8, 98)
        struct.pack_into("<H", data, data.index(b"PK\x01\x02") + 10, 98)
        path.write_bytes(data)
        archive = native.ThreeMfArchive(path)
        with pytest.raises(ValueError, match="[Cc]ompression"):
            archive.read_part(PART, len(XML), zlib.crc32(XML), len(XML))

    def test_enforces_decompressed_size_boundary(self, package):
        archive = native.ThreeMfArchive(package())
        with pytest.raises(ValueError, match="input limit"):
            archive.read_part(PART, len(XML), zlib.crc32(XML), len(XML) - 1)

    @pytest.mark.parametrize(
        "size,crc",
        [(len(XML) - 1, zlib.crc32(XML)), (len(XML), 0)],
        ids=["size", "crc"],
    )
    def test_rejects_changed_inventory(self, package, size, crc):
        archive = native.ThreeMfArchive(package())
        with pytest.raises(ValueError, match="member changed"):
            archive.read_part(PART, size, crc, 512 * 1024**2)

    def test_rejects_corrupted_crc(self, package):
        path = package(zipfile.ZIP_STORED)
        path.write_bytes(path.read_bytes().replace(b'x="1"', b'x="8"'))
        archive = native.ThreeMfArchive(path)
        with pytest.raises(ValueError, match="CRC|checksum"):
            archive.read_part(PART, len(XML), zlib.crc32(XML), len(XML))

    @pytest.mark.parametrize("delta", [-1, 1], ids=["understated", "overstated"])
    def test_rejects_false_uncompressed_size(self, package, delta):
        path = package()
        data = bytearray(path.read_bytes())
        central = data.index(b"PK\x01\x02")
        struct.pack_into("<I", data, 22, len(XML) + delta)
        struct.pack_into("<I", data, central + 24, len(XML) + delta)
        path.write_bytes(data)
        archive = native.ThreeMfArchive(path)
        with pytest.raises(ValueError, match="limit|size|length"):
            archive.read_part(PART, len(XML) + delta, zlib.crc32(XML), len(XML) + 1)

    def test_rejects_malformed_archives(self, tmp_path):
        path = tmp_path / "invalid.3mf"
        path.write_bytes(b"not a ZIP")
        with pytest.raises(ValueError):
            native.ThreeMfArchive(path)

    def test_rejects_missing_archive(self, tmp_path):
        with pytest.raises(ValueError, match="No such file"):
            native.ThreeMfArchive(tmp_path / "missing.3mf")

    def test_rejects_missing_part(self, package):
        archive = native.ThreeMfArchive(package())
        with pytest.raises(ValueError, match="not found"):
            archive.read_part("missing.model", 0, 0, 1024)

    def test_rejects_closed_archive(self, package):
        archive = native.ThreeMfArchive(package())
        archive.close()
        archive.close()
        with pytest.raises(ValueError, match="closed"):
            archive.read_part(PART, len(XML), zlib.crc32(XML), len(XML))
