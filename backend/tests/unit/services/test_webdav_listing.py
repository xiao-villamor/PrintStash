"""WebDAV metadata is conservative even when a server omits or corrupts fields."""

import pytest
from lxml import etree

from app.services.storage_backend import StorageConfigurationError
from app.services.webdav_listing import _entry


def _response(
    *,
    href="/dav/library/models/a.gcode",
    length="6",
    modified=None,
    directory=False,
    status="HTTP/1.1 200 OK",
):
    return etree.fromstring(
        f"""<d:response xmlns:d="DAV:"><d:href>{href}</d:href><d:propstat><d:status>{status}</d:status><d:prop><d:resourcetype>{"<d:collection/>" if directory else ""}</d:resourcetype>{"" if length is None else f"<d:getcontentlength>{length}</d:getcontentlength>"}{"" if modified is None else f"<d:getlastmodified>{modified}</d:getlastmodified>"}<d:getetag>"tag"</d:getetag></d:prop></d:propstat></d:response>"""
    )


class TestWebDAVEntry:
    @pytest.mark.parametrize("modified", [None, "invalid"])
    def test_unknown_timestamp_remains_unknown(self, modified):
        entry = _entry(_response(modified=modified), "/dav/library")
        assert entry.key == "models/a.gcode"
        assert entry.size == 6
        assert entry.modified_at is None
        assert entry.etag == '"tag"'

    def test_directory_needs_no_content_length(self):
        entry = _entry(_response(directory=True, length=None), "/dav/library")
        assert entry.is_dir is True
        assert entry.size == 0

    def test_root_response_is_not_a_child(self):
        assert (
            _entry(_response(href="/dav/library/", directory=True), "/dav/library")
            is None
        )

    @pytest.mark.parametrize(
        "href,reason",
        [
            ("/outside/a.gcode", "outside_root"),
            ("/dav/library/../a.gcode", "key_invalid"),
            ("/dav/library/a//b.gcode", "key_invalid"),
        ],
    )
    def test_listing_cannot_escape_the_enrolled_root(self, href, reason):
        with pytest.raises(StorageConfigurationError, match=reason):
            _entry(_response(href=href), "/dav/library")

    @pytest.mark.parametrize(
        "length,reason", [(None, "size_missing"), ("-1", "size_invalid")]
    )
    def test_file_size_requires_usable_evidence(self, length, reason):
        with pytest.raises(StorageConfigurationError, match=reason):
            _entry(_response(length=length), "/dav/library")

    def test_error_properties_cannot_supply_content_metadata(self):
        with pytest.raises(StorageConfigurationError, match="size_missing"):
            _entry(_response(status="HTTP/1.1 404 Not Found"), "/dav/library")

    def test_invalid_property_status_refuses_the_listing(self):
        with pytest.raises(StorageConfigurationError, match="status_invalid"):
            _entry(_response(status="malformed"), "/dav/library")

    def test_missing_href_refuses_the_listing(self):
        with pytest.raises(StorageConfigurationError, match="href_missing"):
            _entry(etree.fromstring('<d:response xmlns:d="DAV:"/>'), "/dav/library")


class TestStreamingResponse:
    @pytest.mark.parametrize(
        "exit_mode", ["exhaust", "early", "consumer_error", "malformed", "http_error"]
    )
    def test_response_closes_on_every_consumer_exit(self, monkeypatch, exit_mode):
        from contextlib import contextmanager
        from types import SimpleNamespace

        from app.services import webdav_listing

        closed = []
        payload = (
            b'<d:multistatus xmlns:d="DAV:">'
            + etree.tostring(_response(modified="Wed, 01 Jan 2025 00:00:00 GMT"))
            + b"</d:multistatus>"
        )
        if exit_mode == "malformed":
            payload = payload[:-10]

        @contextmanager
        def stream(*args, **kwargs):
            assert kwargs["headers"]["Depth"] == "1"
            assert kwargs["follow_redirects"] is False
            try:
                yield SimpleNamespace(
                    status_code=403 if exit_mode == "http_error" else 207,
                    iter_bytes=lambda **_: iter([payload]),
                )
            finally:
                closed.append(True)

        monkeypatch.setattr(webdav_listing.httpx, "stream", stream)

        def consume():
            with webdav_listing.iter_webdav_directory(
                "https://unit.test/dav/library/models",
                root_url="https://unit.test/dav/library",
                username="user",
                password="secret",
            ) as entries:
                if exit_mode == "early":
                    assert next(entries).key == "models/a.gcode"
                elif exit_mode == "consumer_error":
                    next(entries)
                    raise RuntimeError("consumer stopped")
                else:
                    values = list(entries)
                    assert len(values) == 1
                    assert values[0].modified_at.year == 2025

        if exit_mode in {"consumer_error", "malformed", "http_error"}:
            expected = {
                "consumer_error": RuntimeError,
                "malformed": etree.XMLSyntaxError,
                "http_error": StorageConfigurationError,
            }[exit_mode]
            with pytest.raises(expected):
                consume()
        else:
            consume()
        assert closed == [True]
