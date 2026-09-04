"""Printable-format classification binds filenames to the bytes being sent.

Routing can use the declared extension, but the final transfer must refuse a
binary container hidden behind a text suffix and a corrupt container advertised
as BGCODE.  These tests keep that safety boundary independent of any provider.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from printstash_core.gcode import (
    PrintArtifactFormatError,
    classify_print_artifact,
    content_type_for_format,
    declared_print_artifact_format,
)
from printstash_core.printers import PrintArtifactFormat


def _minimal_bgcode() -> bytes:
    header = b"GCDE" + struct.pack("<IH", 1, 0)
    gcode = struct.pack("<HHI", 1, 0, 4) + struct.pack("<H", 0) + b"G28\n"
    return header + gcode


class TestDeclaredPrintArtifactFormat:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            pytest.param("cube.gcode", PrintArtifactFormat.GCODE_TEXT, id="text"),
            pytest.param("cube.bgcode", PrintArtifactFormat.BGCODE_BINARY, id="binary"),
        ],
    )
    def test_classifies_the_visible_suffix(
        self, filename: str, expected: PrintArtifactFormat
    ) -> None:
        assert declared_print_artifact_format(filename) == expected


class TestClassifyPrintArtifact:
    def test_accepts_plain_text_gcode(self, tmp_path: Path) -> None:
        path = tmp_path / "cube.gcode"
        path.write_text("G28\n")

        assert classify_print_artifact(path) == PrintArtifactFormat.GCODE_TEXT

    def test_accepts_a_valid_binary_container(self, tmp_path: Path) -> None:
        path = tmp_path / "cube.bgcode"
        path.write_bytes(_minimal_bgcode())

        assert classify_print_artifact(path) == PrintArtifactFormat.BGCODE_BINARY

    def test_rejects_an_invalid_binary_container(self, tmp_path: Path) -> None:
        path = tmp_path / "cube.bgcode"
        path.write_bytes(b"not-bgcode")

        with pytest.raises(PrintArtifactFormatError) as error:
            classify_print_artifact(path)

        assert error.value.code == "invalid_binary_gcode"

    def test_rejects_binary_bytes_with_a_text_suffix(self, tmp_path: Path) -> None:
        path = tmp_path / "cube.gcode"
        path.write_bytes(_minimal_bgcode())

        with pytest.raises(PrintArtifactFormatError) as error:
            classify_print_artifact(path)

        assert error.value.code == "print_artifact_extension_mismatch"


class TestContentTypeForFormat:
    @pytest.mark.parametrize(
        ("artifact_format", "expected"),
        [
            pytest.param(PrintArtifactFormat.GCODE_TEXT, "text/x.gcode", id="text"),
            pytest.param(
                PrintArtifactFormat.BGCODE_BINARY,
                "application/octet-stream",
                id="binary",
            ),
        ],
    )
    def test_maps_the_wire_content_type(
        self, artifact_format: PrintArtifactFormat, expected: str
    ) -> None:
        assert content_type_for_format(artifact_format) == expected
