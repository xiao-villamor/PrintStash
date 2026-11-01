"""Resin metadata and bounded embedded previews, independent of print layers."""

import struct

import pytest

from printstash_core.resin.reader import (
    ResinFormatError,
    extract_preview,
    read_metadata,
)
from tests.paths import FIXTURES_DIR

FIXTURES = FIXTURES_DIR / "resin"


@pytest.fixture
def changed(tmp_path):
    def write(extension="ctb", *, patches=(), limit=None):
        data = bytearray((FIXTURES / f"header.{extension}").read_bytes())
        for offset, value in patches:
            data[offset : offset + len(value)] = value
        path = tmp_path / f"changed.{extension}"
        path.write_bytes(data[:limit] if limit is not None else data)
        return path

    return write


class TestResinReader:
    @pytest.mark.parametrize(
        "extension,printer,slicer",
        [
            ("ctb", "Fixture CTB", None),
            ("goo", "Fixture GOO", "FixtureSlice"),
        ],
    )
    def test_reads_header_facts(self, extension, printer, slicer):
        meta = read_metadata(FIXTURES / f"header.{extension}")
        assert meta.layer_count == 120
        assert meta.layer_height_mm == pytest.approx(0.05)
        assert meta.exposure_time_s == 2.5
        assert meta.estimated_time_s == 600
        assert meta.resin_volume_ml == 2.5
        assert meta.printer_model == printer
        assert meta.slicer_name == slicer
        assert meta.slicer_version == ("1.2.3" if extension == "goo" else None)

    @pytest.mark.parametrize("extension", ["ctb", "goo"])
    def test_decodes_preview_colors(self, extension):
        image = extract_preview(FIXTURES / f"header.{extension}")
        assert image is not None
        if extension == "ctb":
            assert (image.width, image.height) == (2, 2)
            assert image.rgb == bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
        else:
            assert (image.width, image.height) == (290, 290)
            assert image.rgb == bytes([255, 0, 0]) * 290 * 290

    @pytest.mark.parametrize("version", [2, 3, 4, 5])
    def test_reads_compatible_ctb_headers(self, changed, version):
        path = changed(patches=[(4, struct.pack("<I", version))])
        assert read_metadata(path).layer_count == 120
        assert extract_preview(path).rgb[:3] == bytes([255, 0, 0])

    def test_reads_alternative_ctb_magic(self, changed):
        path = changed(patches=[(0, struct.pack("<I", 0x12FD0106))])
        assert read_metadata(path).estimated_time_s == 600

    @pytest.mark.parametrize(
        "extension,offset,value,error",
        [
            ("ctb", 0, b"nope", "unsupported_resin_format"),
            ("ctb", 4, struct.pack("<I", 99), "unsupported_ctb_version"),
            ("goo", 0, b"V4.0", "unsupported_goo_version"),
        ],
    )
    @pytest.mark.parametrize("reader", [read_metadata, extract_preview])
    def test_rejects_unknown_format_versions(
        self, changed, extension, offset, value, error, reader
    ):
        with pytest.raises(ResinFormatError, match=error):
            reader(changed(extension, patches=[(offset, value)]))

    @pytest.mark.parametrize(
        "extension,limit,reader",
        [
            ("ctb", 10, read_metadata),
            ("ctb", 100, read_metadata),
            ("ctb", 132, read_metadata),
            ("ctb", 195, extract_preview),
            ("goo", 100, read_metadata),
            ("goo", 195450, read_metadata),
            ("goo", 28000, extract_preview),
        ],
    )
    def test_rejects_truncated_records(self, changed, extension, limit, reader):
        with pytest.raises(ResinFormatError, match="truncated_resin_file"):
            reader(changed(extension, limit=limit))

    @pytest.mark.parametrize(
        "offset,value",
        [
            (32, struct.pack("<f", float("nan"))),
            (32, struct.pack("<f", 0)),
            (36, struct.pack("<f", -1)),
            (68, struct.pack("<I", 0)),
            (132, struct.pack("<f", float("inf"))),
        ],
    )
    def test_rejects_invalid_numeric_metadata(self, changed, offset, value):
        with pytest.raises(ResinFormatError, match="invalid_resin_metadata"):
            read_metadata(changed(patches=[(offset, value)]))

    @pytest.mark.parametrize(
        "patches,error",
        [
            ([(88, struct.pack("<I", 4))], "invalid_ctb_config"),
            ([(108, struct.pack("<I", 4))], "invalid_ctb_config"),
            ([(232, struct.pack("<I", 129))], "invalid_resin_text"),
            ([(240, b"\xff")], "invalid_resin_text"),
        ],
    )
    def test_rejects_invalid_optional_config(self, changed, patches, error):
        with pytest.raises(ResinFormatError, match=error):
            read_metadata(changed(patches=patches))

    def test_absent_optional_ctb_metadata_stays_null(self, changed):
        meta = read_metadata(changed(patches=[(84, bytes(8)), (104, bytes(8))]))
        assert meta.resin_volume_ml is None
        assert meta.printer_model is None

    def test_empty_printer_name_stays_null(self, changed):
        assert read_metadata(changed(patches=[(240, bytes(12))])).printer_model is None
        assert read_metadata(changed(patches=[(232, bytes(4))])).printer_model is None

    def test_missing_preview_is_optional(self, changed):
        assert extract_preview(changed(patches=[(60, bytes(4))])) is None

    def test_uses_small_preview_when_large_is_absent(self, changed):
        image = extract_preview(
            changed(patches=[(60, bytes(4)), (72, struct.pack("<I", 160))])
        )
        assert image is not None and image.width == 2

    def test_decodes_ctb_run_length(self, changed):
        path = changed(
            patches=[
                (172, struct.pack("<I", 4)),
                (192, struct.pack("<HH", 0xF820, 0x3003)),
            ]
        )
        assert extract_preview(path).rgb == bytes([255, 0, 0]) * 4

    @pytest.mark.parametrize(
        "patches,error",
        [
            ([(160, struct.pack("<I", 0))], "dimensions"),
            ([(160, struct.pack("<I", 5000000))], "dimensions"),
            ([(172, struct.pack("<I", 3))], "size"),
            ([(172, struct.pack("<I", 18))], "size"),
            (
                [(172, struct.pack("<I", 2)), (192, struct.pack("<H", 0xF820))],
                "truncated_resin_preview_run",
            ),
            ([(192, struct.pack("<HH", 0xF820, 0x2003))], "invalid_resin_preview_run"),
            ([(192, struct.pack("<HH", 0xF820, 0x3004))], "overflow"),
            ([(172, struct.pack("<I", 2))], "underflow"),
        ],
    )
    def test_refuses_invalid_preview_data(self, changed, patches, error):
        with pytest.raises(ResinFormatError, match=error):
            extract_preview(changed(patches=patches))
