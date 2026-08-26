"""Importer URL validation rejects malformed schemes and missing hosts."""

from __future__ import annotations

import pytest

from app.services import importer as imp


class TestValidatePublicUrl:
    @pytest.mark.parametrize(
        "url, code",
        [
            ("ftp://example.com/x", "url_scheme_not_allowed"),
            ("file:///etc/passwd", "url_scheme_not_allowed"),
            ("notaurl", "url_scheme_not_allowed"),
            ("http:///nohost", "url_host_missing"),
        ],
    )
    def test_rejects_bad_scheme_or_host(self, url: str, code: str) -> None:
        with pytest.raises(imp.ImportError_) as exc:
            imp.validate_public_url(url)
        assert str(exc.value) == code
