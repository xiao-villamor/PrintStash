"""HTTP delivery decisions never grant unsafe bearer capabilities or stale bytes."""

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.storage.artifact_delivery import (
    DeliveryRequest,
    byte_range,
    not_modified,
    range_matches,
    safe_browser_download,
)
from app.modules.storage.delivery_contracts import BrowserDownload, content_disposition

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestSafeBrowserDownload:
    def test_accepts_an_object_scoped_target(self):
        target = BrowserDownload(
            "https://storage.test/object?signature=test",
            NOW + timedelta(seconds=60),
            "key",
            cors_origin="https://app.test",
        )

        accepted = safe_browser_download(
            target, key="key", origin="https://app.test", now=NOW
        )

        assert accepted is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://storage.test/object",
            "https://user:password@storage.test/object",
            "https://storage.test/object#fragment",
            "https://storage.test/object\r\nheader",
            "//storage.test/object",
            "https:///object",
        ],
        ids=["http", "credentials", "fragment", "control", "relative", "host"],
    )
    def test_refuses_an_unsafe_url(self, url):
        target = BrowserDownload(url, NOW + timedelta(seconds=30), "key")

        accepted = safe_browser_download(target, key="key", origin=None, now=NOW)

        assert accepted is False

    @pytest.mark.parametrize(
        "seconds", [-1, 0, 61], ids=["expired", "expires_now", "too_long"]
    )
    def test_refuses_invalid_expiry(self, seconds):
        target = BrowserDownload(
            "https://storage.test/object", NOW + timedelta(seconds=seconds), "key"
        )

        accepted = safe_browser_download(target, key="key", origin=None, now=NOW)

        assert accepted is False

    def test_refuses_a_different_object(self):
        target = BrowserDownload(
            "https://storage.test/object", NOW + timedelta(seconds=30), "other"
        )

        accepted = safe_browser_download(target, key="key", origin=None, now=NOW)

        assert accepted is False

    def test_refuses_unproven_cors(self):
        target = BrowserDownload(
            "https://storage.test/object", NOW + timedelta(seconds=30), "key"
        )

        accepted = safe_browser_download(
            target, key="key", origin="https://app.test", now=NOW
        )

        assert accepted is False

    def test_refuses_required_headers(self):
        target = BrowserDownload(
            "https://storage.test/object",
            NOW + timedelta(seconds=30),
            "key",
            required_headers=("Authorization",),
        )

        accepted = safe_browser_download(target, key="key", origin=None, now=NOW)

        assert accepted is False

    def test_refuses_unparseable_url(self):
        target = BrowserDownload("https://[invalid", NOW + timedelta(seconds=30), "key")

        accepted = safe_browser_download(target, key="key", origin=None, now=NOW)

        assert accepted is False

    def test_redacts_target_representation(self):
        target = BrowserDownload(
            "https://storage.test/object?secret=private", NOW, "private-key"
        )

        representation = repr(target)

        assert "private" not in representation


class TestNotModified:
    @pytest.mark.parametrize(
        "value",
        ['"other", W/"hash"', "*", 'W/"hash"'],
        ids=["list", "wildcard", "weak"],
    )
    def test_matches_an_original_validator(self, value):
        request = DeliveryRequest("part.stl", if_none_match=value)

        result = not_modified(request, etag='"hash"', modified_at=NOW)

        assert result is True

    def test_ignores_invalid_date(self):
        request = DeliveryRequest("part.stl", if_modified_since="not a date")

        result = not_modified(request, etag='"hash"', modified_at=NOW)

        assert result is False


class TestByteRange:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("bytes=2-7", (2, 7)),
            ("bytes=-4", (6, 9)),
            ("bytes=2-", (2, 9)),
            ("bytes=2-99", (2, 9)),
        ],
        ids=["closed", "suffix", "open", "clipped"],
    )
    def test_selects_the_requested_bytes(self, value, expected):
        assert byte_range(value, 10) == expected

    @pytest.mark.parametrize(
        "value",
        ["bytes=10-", "bytes=7-2", "bytes=-0", "bytes=" + "9" * 100 + "-"],
        ids=["past_end", "reversed", "empty_suffix", "oversized"],
    )
    def test_rejects_unsatisfiable_range(self, value):
        with pytest.raises(ValueError, match="range_unsatisfiable"):
            byte_range(value, 10)

    @pytest.mark.parametrize(
        "value",
        [None, "bad", "bytes=1-2,4-5", "bytes=-"],
        ids=["absent", "malformed", "multiple", "empty"],
    )
    def test_ignores_unsupported_range(self, value):
        assert byte_range(value, 10) is None


class TestRangeMatches:
    def test_weak_validator_cannot_select_a_partial_response(self):
        request = DeliveryRequest("part.stl", if_range='W/"hash"')

        assert range_matches(request, etag='"hash"', modified_at=NOW) is False

    def test_matches_modification_time(self):
        request = DeliveryRequest("part.stl", if_range="Thu, 01 Jan 2026 00:00:00 GMT")

        assert range_matches(request, etag='"hash"', modified_at=NOW) is True


class TestContentDisposition:
    def test_encodes_unicode_display_name(self):
        assert "filename*=UTF-8''pi%C3%A8ce.stl" in content_disposition("pièce.stl")

    def test_removes_path_header_injection(self):
        header = content_disposition("../folder/piece\r\n.stl")

        assert (
            header == "attachment; filename=\"piece.stl\"; filename*=UTF-8''piece.stl"
        )


class TestStorageDeliveryContract:
    def test_removes_bare_url_contract(self):
        from app.modules.storage import artifact_content
        from app.modules.storage.storage_backend.contracts import StorageBackend

        assert not hasattr(StorageBackend, "presigned_download_url")
        assert not hasattr(artifact_content, "presigned_download_url")
