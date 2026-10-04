"""Observe real source I/O without replacing geometry parsing or rendering."""

from __future__ import annotations

import io
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest


@pytest.fixture
def count_source_reads(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[int]]:
    def observe(source: Path, *, fail_after_bytes: int | None = None) -> list[int]:
        reads: list[int] = []
        original_open = Path.open

        class MeteredReader(io.BufferedReader):
            def __init__(self, raw: io.RawIOBase) -> None:
                super().__init__(raw)
                self.index = len(reads)
                reads.append(0)

            def _check_failure(self) -> None:
                if (
                    fail_after_bytes is not None
                    and reads[self.index] >= fail_after_bytes
                ):
                    raise OSError("injected source read failure")

            def read(self, size: int = -1) -> bytes:
                self._check_failure()
                data = super().read(size)
                reads[self.index] += len(data)
                return data

            def readline(self, size: int = -1) -> bytes:
                self._check_failure()
                data = super().readline(size)
                reads[self.index] += len(data)
                return data

        def open_source(path: Path, mode: str = "r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path == source and mode == "rb":
                return MeteredReader(stream.detach())
            return stream

        monkeypatch.setattr(Path, "open", open_source)
        return reads

    return observe


@pytest.fixture
def encode_stl_facets(encoding):
    from tests.factories import content

    def solid_binary(facets):
        return b"solid" + content.binary_stl_facets(facets)[5:]

    return {
        "binary": content.binary_stl_facets,
        "solid-binary": solid_binary,
        "ascii": content.ascii_stl_facets,
    }[encoding]


@pytest.fixture
def encode_stl_count(encoding):
    from tests.factories import content

    return {"binary": content.binary_stl, "ascii": content.ascii_stl}[encoding]


@pytest.fixture
def expected_materialization_reads(encoding):
    return {
        "binary": lambda size: [84, size],
        "ascii": lambda size: [84, size + 84, size + 84],
    }[encoding]


@pytest.fixture
def canonical_stl_consumer(consumer):
    from app.modules.media import stl_fallback, stl_reader

    return {
        "scan": stl_reader.scan_stl,
        "sample": partial(stl_fallback.read_stl_sample, max_triangles=1),
        "materialize": stl_reader.materialize_stl,
    }[consumer]


@pytest.fixture
def malformed_stl_source(tmp_path, damage):
    from tests.factories import content

    facets = [
        ((0, 0, 0), (2, 0, 1), (0, 2, 1)),
        ((100, 3, 4), (101, 3, 4), (100, 4, 5)),
    ]
    binary = content.binary_stl_facets(facets)
    ascii_bytes = content.ascii_stl_facets(facets)
    bodies = {
        "truncated": binary[:-1],
        "count-mismatch": binary[:80] + (3).to_bytes(4, "little") + binary[84:],
        "trailing": binary + b"trailing",
        "unsampled-nonfinite": binary[:146] + b"\x00\x00\xc0\x7f" + binary[150:],
        "incomplete-ascii": ascii_bytes.replace(b"endfacet", b"", 1),
        "malformed-ascii": ascii_bytes.replace(b"outer loop", b"outer wrong", 1),
        "nonascii": ascii_bytes.replace(b"solid", b"sol\xffid", 1),
    }
    source = tmp_path / "damaged.stl"
    source.write_bytes(bodies[damage])
    return source


@pytest.fixture
def replace_source_after_eof(monkeypatch):
    def arm(source):
        original_stat = Path.stat
        observed = []

        def replace_after_stat(path, *args, **kwargs):
            current = original_stat(path, *args, **kwargs)
            if path == source:
                observed.append(current)
                if len(observed) == 3:
                    replacement = path.with_suffix(".replacement")
                    replacement.write_bytes(path.read_bytes())
                    replacement.replace(path)
            return current

        monkeypatch.setattr(Path, "stat", replace_after_stat)
        return observed

    return arm


@pytest.fixture
def canonical_stl_refusal(tmp_path, monkeypatch, failure, consumer):
    from app.modules.media import stl_fallback, stl_reader
    from tests.factories import content

    body = content.binary_stl(triangles=2)
    source = tmp_path / "canonical-refusal.stl"
    source.write_bytes(
        {"invalid": body + b"trailing", "budget": body, "changed": body}[failure]
    )
    limits = stl_reader.STLReadLimits(
        max_triangles={"invalid": 2, "budget": 1, "changed": 2}[failure]
    )
    expected = {
        "invalid": stl_reader.STLReadFailure.INVALID_SOURCE,
        "budget": stl_reader.STLReadFailure.RESOURCE_LIMIT,
        "changed": stl_reader.STLReadFailure.SOURCE_CHANGED,
    }[failure]
    if failure == "changed":
        original_snapshot = stl_reader.snapshot_stl

        def replace_after_snapshot(path):
            snapshot = original_snapshot(path)
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(body)
            replacement.replace(path)
            return snapshot

        owner = {"sample": stl_fallback, "materialize": stl_reader}[consumer]
        monkeypatch.setattr(owner, "snapshot_stl", replace_after_snapshot)
    return source, limits, expected
