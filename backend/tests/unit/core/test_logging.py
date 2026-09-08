"""Signed query credentials never survive application or access-log formatting."""

import logging

import pytest

from app.core.logging import SensitiveQueryFilter


@pytest.mark.parametrize(
    "message",
    [
        "redirect https://store.test/key?X-Amz-Signature=supersecret&token=other",
        "GET /api/v1/files/token?signature=supersecret HTTP/1.1",
        "SDK failure https://store.test/key?X-Amz-Credential=supersecret",
    ],
)
def test_redacts_signed_queries_in_log_records(message):
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 1, "%s", (message,), None
    )
    SensitiveQueryFilter().filter(record)

    rendered = logging.Formatter().format(record)
    assert "supersecret" not in rendered
    assert "[redacted]" in rendered


def test_redacts_exception_query_credentials():
    try:
        raise ValueError("https://store.test/key?signature=supersecret")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "app", logging.ERROR, "", 1, "request failed", (), sys.exc_info()
        )
    SensitiveQueryFilter().filter(record)

    assert "supersecret" not in logging.Formatter().format(record)


def test_preserves_uvicorn_access_formatter_arguments():
    from uvicorn.logging import AccessFormatter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/download?signature=supersecret", "1.1", 307),
        None,
    )
    SensitiveQueryFilter().filter(record)

    output = AccessFormatter(
        "%(request_line)s %(status_code)s", use_colors=False
    ).format(record)
    assert "supersecret" not in output
    assert "GET /download?[redacted] HTTP/1.1" in output


def test_preserves_nonsensitive_mapping_log_arguments():
    record = logging.LogRecord(
        "app",
        logging.INFO,
        "",
        1,
        "request %(path)s status %(status)d",
        ({"path": "/download?signature=secret", "status": 307},),
        None,
    )
    SensitiveQueryFilter().filter(record)

    assert (
        logging.Formatter().format(record) == "request /download?[redacted] status 307"
    )
