"""The value guards every provenance write goes through.

Provenance is the only part of the library whose contents come from a page somebody else
publishes, read by an extension running on somebody else's machine. Every string in it is
therefore bounded, normalised, and refused rather than truncated: a title silently cut at
the limit is a title that no longer matches the source it claims to come from, and a
control character in one lands in a log line, a filename, and a CSV export. A line break
counts as a control character, and line endings are normalised *before* that check so
`\r\n`, `\r` and `\n` are all refused identically rather than one slipping through as a
different byte.

`canonicalize_url` is the identity function. Two spellings of one page — different case,
a trailing query, a tracking fragment — must reduce to one string, because that string is
what a re-capture deduplicates against. Credentials in the URL are refused outright rather
than stripped, because a URL that needed them is not a public page and storing the rest of
it would claim it was.

Unicode normalisation matters for the same reason: `é` written as one code point and as
`e` + a combining accent are the same title to a reader and two different rows to a
database.
"""

from __future__ import annotations

import pytest

from app.services import provenance


class TestBounded:
    def test_keeps_an_ordinary_value(self) -> None:
        assert provenance._bounded("Widget", 64, "title") == "Widget"

    def test_passes_an_absent_value_through(self) -> None:
        assert provenance._bounded(None, 64, "title") is None

    def test_normalises_unicode_to_one_composed_form(self) -> None:
        decomposed = "Café"

        # The same title written two ways must not become two rows.
        assert provenance._bounded(decomposed, 64, "title") == "Café"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("a\r\nb", id="crlf"),
            pytest.param("a\rb", id="cr"),
            pytest.param("a\nb", id="lf"),
        ],
    )
    def test_refuses_a_line_break_in_any_spelling(self, value: str) -> None:
        # A captured field is a single line by contract. Line endings are
        # normalised first, so all three spellings are refused identically
        # rather than one slipping through as a different byte.
        with pytest.raises(ValueError, match="invalid_notes"):
            provenance._bounded(value, 64, "notes")

    def test_refuses_a_value_past_its_limit(self) -> None:
        # Refused, not truncated: a cut title no longer matches its source.
        with pytest.raises(ValueError, match="invalid_title"):
            provenance._bounded("x" * 65, 64, "title")

    def test_refuses_a_value_that_is_only_whitespace_once_normalised(self) -> None:
        with pytest.raises(ValueError, match="invalid_title"):
            provenance._bounded("", 64, "title")

    def test_refuses_a_control_character(self) -> None:
        # One of these reaches a log line, a filename, and a CSV export.
        with pytest.raises(ValueError, match="invalid_title"):
            provenance._bounded("Wid\x07get", 64, "title")


class TestValidatedSha256:
    def test_accepts_a_hash(self) -> None:
        assert provenance._validated_sha256("A" * 64) == "a" * 64

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("a" * 63, id="too-short"),
            pytest.param("a" * 65, id="too-long"),
            pytest.param("g" * 64, id="not-hex"),
            pytest.param("", id="empty"),
        ],
    )
    def test_refuses_anything_that_is_not_one(self, value: str) -> None:
        with pytest.raises(ValueError, match="invalid_blob_sha256"):
            provenance._validated_sha256(value)


class TestCanonicalizeUrl:
    def test_keeps_a_page_url(self) -> None:
        assert (
            provenance.canonicalize_url("https://example.test/model/42")
            == "https://example.test/model/42"
        )

    def test_lowercases_the_authority(self) -> None:
        assert (
            provenance.canonicalize_url("HTTPS://Example.TEST/model/42")
            == "https://example.test/model/42"
        )

    def test_drops_everything_after_the_path(self) -> None:
        # A provider link carries tracking parameters that are not part of the
        # page's identity, and sometimes a signed credential that must not be
        # stored at all.
        assert (
            provenance.canonicalize_url("https://example.test/a?utm=x#section")
            == "https://example.test/a"
        )

    def test_keeps_a_non_default_port(self) -> None:
        assert (
            provenance.canonicalize_url("https://example.test:8443/a")
            == "https://example.test:8443/a"
        )

    def test_gives_a_path_less_url_a_root_path(self) -> None:
        assert provenance.canonicalize_url("https://example.test") == (
            "https://example.test/"
        )

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("ftp://example.test/a", id="scheme"),
            pytest.param("/model/42", id="relative"),
            pytest.param("https:///a", id="no-host"),
        ],
    )
    def test_refuses_something_that_is_not_an_http_page(self, value: str) -> None:
        with pytest.raises(ValueError, match="canonical_url_must_be_http_url"):
            provenance.canonicalize_url(value)

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("https://alice:pw@example.test/a", id="username-and-password"),
            pytest.param("https://alice@example.test/a", id="username-only"),
        ],
    )
    def test_refuses_a_url_carrying_credentials(self, value: str) -> None:
        # Refused rather than stripped: a URL that needed credentials is not a
        # public page, and keeping the rest of it would claim that it was.
        with pytest.raises(
            ValueError, match="canonical_url_must_not_contain_credentials"
        ):
            provenance.canonicalize_url(value)
