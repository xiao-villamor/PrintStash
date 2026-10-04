"""A complete STL scan cannot certify partial or changing source bytes."""

from __future__ import annotations

import math
import struct
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.media import stl_reader as reader
from app.modules.media.stl_reader import (
    InvalidSTL,
    STLBudgetExceeded,
    STLReadLimits,
    STLSourceChanged,
    iter_stl_blocks,
    scan_stl,
)
from tests.factories import content
from tests.factories.content import ascii_stl_facets as _ascii_stl
from tests.factories.content import binary_stl_facets as _binary_stl

TRIANGLE = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
SECOND = ((0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0))


@pytest.fixture
def limits():
    def build(**overrides) -> STLReadLimits:
        return replace(
            STLReadLimits(
                max_triangles=1000,
                max_source_bytes=1 << 20,
                chunk_triangles=8,
                max_lines=10_000,
                max_line_bytes=1024,
                deadline=time.monotonic() + 30,
            ),
            **overrides,
        )

    return build


@pytest.fixture
def stl(tmp_path: Path):
    def write(data: bytes) -> Path:
        path = tmp_path / "part.stl"
        path.write_bytes(data)
        return path

    return write


class TestValidValue:
    @pytest.mark.parametrize(
        "value",
        [0.0, 1.5, -1.5, 3.4028234663852886e38],
        ids=["zero", "pos", "neg", "max"],
    )
    def test_accepts_a_coordinate_a_float32_can_hold(self, value: float) -> None:
        assert reader.valid_stl_value(value) is True

    @pytest.mark.parametrize(
        "value",
        [math.inf, -math.inf, math.nan, 1e39],
        ids=["inf", "-inf", "nan", "over-float32"],
    )
    def test_rejects_a_coordinate_a_float32_cannot_hold(self, value: float) -> None:
        assert reader.valid_stl_value(value) is False


class TestSourceIsBinary:
    def test_recognises_a_binary_stl_by_its_exact_length(self, stl) -> None:
        path = stl(_binary_stl([TRIANGLE, SECOND]))
        assert reader.binary_stl_info(path) == (2, path.stat().st_size)

    def test_rejects_a_file_too_short_to_hold_a_header(self, stl) -> None:
        assert reader.binary_stl_info(stl(b"short")) is None

    def test_rejects_a_declared_count_the_file_length_contradicts(self, stl) -> None:
        data = bytearray(_binary_stl([TRIANGLE]))
        struct.pack_into("<I", data, 80, 5)
        assert reader.binary_stl_info(stl(bytes(data))) is None

    def test_rejects_a_declared_count_of_zero(self, stl) -> None:
        assert reader.binary_stl_info(stl(_binary_stl([]))) is None

    def test_treats_ascii_as_not_binary(self, stl) -> None:
        assert reader.binary_stl_info(stl(_ascii_stl([TRIANGLE]))) is None

    def test_reports_a_file_that_is_not_there(self, tmp_path: Path) -> None:
        assert reader.binary_stl_info(tmp_path / "missing.stl") is None


class TestReadBinary:
    def test_reads_every_triangle(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE, SECOND]))
        stats = reader.scan_stl(
            path, limits=limits(), source_format=reader.STLFormat.BINARY
        )
        assert stats.triangle_count == 2

    def test_reports_the_bounding_box_it_saw(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE, SECOND]))
        stats = reader.scan_stl(
            path, limits=limits(), source_format=reader.STLFormat.BINARY
        )
        assert stats.bounds_min == (0.0, 0.0, 0.0)
        assert stats.bounds_max == (1.0, 1.0, 1.0)

    def test_hands_each_chunk_to_the_caller(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE] * 20))
        chunks = [
            len(block)
            for block in reader.iter_stl_blocks(
                path, limits(chunk_triangles=8), source_format=reader.STLFormat.BINARY
            )
        ]
        assert chunks == [8, 8, 4]

    def test_refuses_a_file_that_is_not_exactly_a_binary_stl(self, stl, limits) -> None:
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(b"short"), limits=limits(), source_format=reader.STLFormat.BINARY
            )

    def test_refuses_more_triangles_than_the_budget_allows(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE] * 5))
        with pytest.raises(reader.STLBudgetExceeded):
            reader.scan_stl(
                path,
                limits=limits(max_triangles=2),
                source_format=reader.STLFormat.BINARY,
            )

    def test_refuses_a_source_larger_than_the_budget_allows(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE] * 5))
        with pytest.raises(reader.STLBudgetExceeded):
            reader.scan_stl(
                path,
                limits=limits(max_source_bytes=100),
                source_format=reader.STLFormat.BINARY,
            )

    def test_refuses_a_coordinate_that_is_not_finite(self, stl, limits) -> None:
        path = stl(
            _binary_stl([((math.inf, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))])
        )
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                path, limits=limits(), source_format=reader.STLFormat.BINARY
            )

    def test_refuses_to_read_past_the_deadline(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE]))
        with pytest.raises(reader.STLBudgetExceeded):
            reader.scan_stl(
                path,
                limits=limits(deadline=time.monotonic() - 1),
                source_format=reader.STLFormat.BINARY,
            )


class TestParseFloat:
    def test_reads_a_number(self) -> None:
        assert reader.parse_stl_float("1.5") == 1.5

    def test_refuses_something_that_is_not_a_number(self) -> None:
        with pytest.raises(reader.InvalidSTL):
            reader.parse_stl_float("not-a-number")

    def test_refuses_a_number_a_float32_cannot_hold(self) -> None:
        with pytest.raises(reader.InvalidSTL):
            reader.parse_stl_float("inf")


class TestReadAscii:
    @pytest.mark.parametrize("triangles", [1, 2], ids=["single", "repeated"])
    def test_reads_repeated_factory_facets(self, stl, limits, triangles) -> None:
        from tests.factories import content

        stats = reader.scan_stl(
            stl(content.ascii_stl(triangles=triangles)),
            limits=limits(),
            source_format=reader.STLFormat.ASCII,
        )
        assert stats.triangle_count == triangles
        assert stats.bounds_min == (0.0, 0.0, 0.0)
        assert stats.bounds_max == (1.0, 1.0, 0.0)

    def test_reads_every_facet(self, stl, limits) -> None:
        path = stl(_ascii_stl([TRIANGLE, SECOND]))
        stats = reader.scan_stl(
            path, limits=limits(), source_format=reader.STLFormat.ASCII
        )
        assert stats.triangle_count == 2

    def test_reports_the_bounding_box_it_saw(self, stl, limits) -> None:
        path = stl(_ascii_stl([TRIANGLE, SECOND]))
        stats = reader.scan_stl(
            path, limits=limits(), source_format=reader.STLFormat.ASCII
        )
        assert stats.bounds_min == (0.0, 0.0, 0.0)
        assert stats.bounds_max == (1.0, 1.0, 1.0)

    def test_ignores_lines_that_carry_no_geometry(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(
            b"solid test\n", b"solid test\n\n# a comment\n// another\n"
        )
        stats = reader.scan_stl(
            stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
        )
        assert stats.triangle_count == 1

    def test_accepts_a_file_that_ends_without_endsolid(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"endsolid test\n", b"")
        stats = reader.scan_stl(
            stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
        )
        assert stats.triangle_count == 1

    def test_refuses_a_file_that_ends_mid_facet(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"endfacet\nendsolid test\n", b"")
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
            )

    def test_refuses_a_file_with_no_facets_at_all(self, stl, limits) -> None:
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(b"solid test\nendsolid test\n"),
                limits=limits(),
                source_format=reader.STLFormat.ASCII,
            )

    def test_refuses_content_after_endsolid(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]) + b"facet normal 0 0 1\n"
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
            )

    @pytest.mark.parametrize(
        ("broken", "replacement"),
        [
            pytest.param(b"facet normal 0 0 1", b"facet normal 0 0", id="short-normal"),
            pytest.param(b"outer loop", b"inner loop", id="wrong-loop"),
            pytest.param(b"vertex 0.0 0.0 0.0", b"vertex 0.0 0.0", id="short-vertex"),
            pytest.param(b"endloop", b"endlop", id="misspelt-endloop"),
            pytest.param(b"endfacet", b"endfact", id="misspelt-endfacet"),
        ],
    )
    def test_refuses_a_malformed_facet(
        self, stl, limits, broken: bytes, replacement: bytes
    ) -> None:
        data = _ascii_stl([TRIANGLE]).replace(broken, replacement, 1)
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
            )

    def test_refuses_an_unknown_keyword(self, stl, limits) -> None:
        data = b"solid test\nsurprise\n" + _ascii_stl([TRIANGLE])
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
            )

    def test_refuses_a_line_longer_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE])
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(data),
                limits=limits(max_line_bytes=8),
                source_format=reader.STLFormat.ASCII,
            )

    def test_refuses_more_lines_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE, SECOND])
        with pytest.raises(reader.STLBudgetExceeded):
            reader.scan_stl(
                stl(data),
                limits=limits(max_lines=3),
                source_format=reader.STLFormat.ASCII,
            )

    def test_refuses_more_bytes_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE, SECOND])
        with pytest.raises(reader.STLBudgetExceeded):
            reader.scan_stl(
                stl(data),
                limits=limits(max_source_bytes=20),
                source_format=reader.STLFormat.ASCII,
            )

    def test_refuses_more_triangles_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE, SECOND])
        with pytest.raises(reader.STLBudgetExceeded):
            reader.scan_stl(
                stl(data),
                limits=limits(max_triangles=1),
                source_format=reader.STLFormat.ASCII,
            )

    def test_refuses_bytes_that_are_not_ascii(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"solid test", b"solid t\xffst")
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
            )

    def test_refuses_a_coordinate_that_is_not_a_number(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"vertex 0.0 0.0 0.0", b"vertex a b c", 1)
        with pytest.raises(reader.InvalidSTL):
            reader.scan_stl(
                stl(data), limits=limits(), source_format=reader.STLFormat.ASCII
            )


class TestScanSTL:
    def test_preserves_ascii_precision(self, tmp_path: Path) -> None:
        source = tmp_path / "translated.stl"
        source.write_bytes(
            content.ascii_stl()
            .replace(b"vertex 0 0 0", b"vertex 1000000000 0 0")
            .replace(b"vertex 1 0 0", b"vertex 1000000000.125 0 0")
            .replace(b"vertex 0 1 0", b"vertex 1000000000 1 0")
        )

        result = scan_stl(source)

        assert result.bounds_max[0] - result.bounds_min[0] == 0.125
        assert result.scanned_bytes == source.stat().st_size

    @pytest.mark.parametrize(
        "budget",
        ["max_triangles", "max_source_bytes", "max_lines", "max_line_bytes"],
        ids=str,
    )
    def test_accepts_ascii_at_budget(self, tmp_path: Path, budget: str) -> None:
        body = content.ascii_stl()
        source = tmp_path / "at-budget.stl"
        source.write_bytes(body)
        exact = {
            "max_triangles": 1,
            "max_source_bytes": len(body),
            "max_lines": len(body.splitlines()),
            "max_line_bytes": max(len(line) for line in body.splitlines(keepends=True)),
        }[budget]

        result = scan_stl(source, limits=replace(STLReadLimits(), **{budget: exact}))

        assert result.triangle_count == 1
        assert result.scanned_bytes == len(body)

    @pytest.mark.parametrize(
        "budget", ["max_triangles", "max_source_bytes", "max_lines"], ids=str
    )
    def test_rejects_ascii_above_budget(self, tmp_path: Path, budget: str) -> None:
        body = content.ascii_stl(triangles=2)
        source = tmp_path / "over-budget.stl"
        source.write_bytes(body)
        exact = {
            "max_triangles": 2,
            "max_source_bytes": len(body),
            "max_lines": len(body.splitlines()),
        }[budget]

        with pytest.raises(STLBudgetExceeded):
            scan_stl(source, limits=replace(STLReadLimits(), **{budget: exact - 1}))

    def test_rejects_ascii_line_above_budget(self, tmp_path: Path) -> None:
        body = content.ascii_stl()
        source = tmp_path / "long-line.stl"
        source.write_bytes(body)
        longest = max(len(line) for line in body.splitlines(keepends=True))

        with pytest.raises(InvalidSTL, match="line too long"):
            scan_stl(source, limits=STLReadLimits(max_line_bytes=longest - 1))

    @pytest.mark.parametrize("encoding", ["ascii", "binary"], ids=str)
    def test_rejects_source_change_mid_scan(
        self, tmp_path: Path, encoding: str
    ) -> None:
        source = tmp_path / "changing.stl"
        source.write_bytes(
            content.ascii_stl(triangles=2)
            if encoding == "ascii"
            else content.binary_stl(triangles=2)
        )
        blocks = iter_stl_blocks(source, STLReadLimits(chunk_triangles=1))
        next(blocks)
        # Same-length overwrite still invalidates the opened source snapshot.
        with source.open("r+b") as stream:
            stream.write(b"S")

        with pytest.raises(STLSourceChanged):
            list(blocks)

    @pytest.mark.parametrize("encoding", ["ascii", "binary"], ids=str)
    def test_scan_is_independent_of_chunk_size(
        self, tmp_path: Path, encoding: str
    ) -> None:
        source = tmp_path / "stable.stl"
        source.write_bytes(
            content.ascii_stl(triangles=10)
            if encoding == "ascii"
            else content.binary_stl(triangles=10)
        )

        assert scan_stl(source, limits=STLReadLimits(chunk_triangles=1)) == scan_stl(
            source, limits=STLReadLimits(chunk_triangles=8)
        )

    def test_accepts_binary_header_starting_solid(self, tmp_path: Path) -> None:
        source = tmp_path / "solid-header.stl"
        body = content.binary_stl()
        source.write_bytes(b"solid" + body[5:])

        assert scan_stl(source).triangle_count == 12


class TestReadLimits:
    @pytest.mark.parametrize(
        "name",
        [
            "max_triangles",
            "max_source_bytes",
            "chunk_triangles",
            "max_lines",
            "max_line_bytes",
        ],
        ids=str,
    )
    @pytest.mark.parametrize(
        "value", [0, -1, True, 1.5], ids=["zero", "negative", "bool", "float"]
    )
    def test_rejects_invalid_integer_budget(self, name: str, value: object) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            STLReadLimits(**{name: value})

    def test_rejects_chunk_above_hard_cap(self) -> None:
        with pytest.raises(ValueError, match="hard cap"):
            STLReadLimits(chunk_triangles=8193)

    @pytest.mark.parametrize(
        "deadline", [math.inf, -math.inf, math.nan], ids=["inf", "negative-inf", "nan"]
    )
    def test_rejects_nonfinite_deadline(self, deadline: float) -> None:
        with pytest.raises(ValueError, match="finite"):
            STLReadLimits(deadline=deadline)


class TestCompletionBudget:
    def test_deadline_must_hold_when_final_block_is_consumed(
        self, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace

        from app.modules.media import stl_reader

        source = tmp_path / "completion.stl"
        source.write_bytes(_binary_stl([TRIANGLE]))
        now = [0.0]
        monkeypatch.setattr(
            stl_reader, "time", SimpleNamespace(monotonic=lambda: now[0])
        )
        blocks = stl_reader.iter_stl_blocks(
            source, stl_reader.STLReadLimits(deadline=100)
        )
        assert len(next(blocks)) == 1
        now[0] = 101.0

        with pytest.raises(stl_reader.STLBudgetExceeded, match="deadline"):
            next(blocks)
