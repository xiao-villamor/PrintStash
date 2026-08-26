"""Download filename parsing remains portable across response headers and URLs."""

from __future__ import annotations

import pytest

from app.services import importer as imp


class _Resp:
    def __init__(self, cd: str) -> None:
        self.headers = {"content-disposition": cd}


class TestContentDispositionName:
    @pytest.mark.parametrize(
        "header, expected",
        [
            ('attachment; filename="benchy.stl"', "benchy.stl"),
            ("attachment; filename=benchy.stl", "benchy.stl"),
            ("attachment; filename=benchy.stl; size=10", "benchy.stl"),
            # Regression: a ';' inside the quoted value is part of the name,
            # not a parameter separator (used to truncate to "a").
            ('attachment; filename="a;b.stl"', "a;b.stl"),
            # A path in the filename is reduced to its basename.
            ('attachment; filename="/etc/passwd"', "passwd"),
            ("attachment; filename=a%20b.stl", "a b.stl"),
        ],
    )
    def test_parses(self, header: str, expected: str) -> None:
        assert imp._content_disposition_name(_Resp(header)) == expected

    @pytest.mark.parametrize("header", ["inline", "", "attachment"])
    def test_no_filename_is_none(self, header: str) -> None:
        assert imp._content_disposition_name(_Resp(header)) is None


class TestFilenameFromUrl:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://x.com/path/benchy.stl", "benchy.stl"),
            ("https://x.com/a%20b.stl", "a b.stl"),
            ("https://x.com/", "download"),  # falls back
            ("https://x.com", "download"),
        ],
    )
    def test_basename_or_fallback(self, url: str, expected: str) -> None:
        assert imp._filename_from_url(url) == expected
