"""Defends ``test_redact_url_removes_credentials_without_leaking_url_parts`` behavior for the ``services`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import pytest

from app.services.provider_redaction import redact_exception, redact_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://alice:password@example.test/models/1?safe=ok&X-Amz-Credential=credential&x_amz_signature=signature&X-Amz-Security-Token=session#private",
            "https://example.test/models/1?safe=ok",
        ),
        (
            "https://example.test/download?API-Key=key&x.amz.signature=sig&name=cube",
            "https://example.test/download?name=cube",
        ),
        (
            "https://example.test/download?refresh_token=refresh&access-token=access&format=stl",
            "https://example.test/download?format=stl",
        ),
        (
            "https://example.test/download?AWSAccessKeyId=access&X-Goog-Access-Id=google&key=value&name=cube",
            "https://example.test/download?name=cube",
        ),
    ],
)
def test_redact_url_removes_credentials_without_leaking_url_parts(
    value: str, expected: str
) -> None:
    assert redact_url(value) == expected


def test_redact_url_rejects_relative_values_instead_of_returning_raw_input() -> None:
    assert redact_url("/download?token=never-log-this") == "[redacted-url]"


def test_redact_exception_never_includes_upstream_message() -> None:
    error = RuntimeError("https://example.test/file?token=never-log-this")

    assert redact_exception(error) == "RuntimeError"
    assert "never-log-this" not in redact_exception(error)
