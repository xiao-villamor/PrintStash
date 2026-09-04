"""Making a provider URL safe to write into a log line.

Every provider download URL PrintStash touches is signed: the credential *is* the URL.
So the moment one appears in a log, an error message, or a diagnostics page, it is a
credential someone else can use until it expires. This module exists to make that
impossible, and the rule it follows is stricter than masking — credential-bearing query
fields are **removed**, not replaced, because a masked field still tells an attacker which
parameter to go looking for.

The refusals matter as much as the redaction. Anything that is not a parseable absolute
URL returns a fixed marker rather than the input, because a value that cannot be parsed is
exactly the value most likely to slip past the query parser and land in a log verbatim.
"""

from __future__ import annotations

import pytest

from app.services.provider_redaction import (
    is_sensitive_query_key,
    normalize_query_key,
    redact_exception,
    redact_url,
)

SENSITIVE_URLS = [
    pytest.param(
        "https://alice:password@example.test/models/1"
        "?safe=ok&X-Amz-Credential=credential&x_amz_signature=signature"
        "&X-Amz-Security-Token=session#private",
        "https://example.test/models/1?safe=ok",
        id="userinfo-aws-and-fragment",
    ),
    pytest.param(
        "https://example.test/download?API-Key=key&x.amz.signature=sig&name=cube",
        "https://example.test/download?name=cube",
        id="punctuation-variants",
    ),
    pytest.param(
        "https://example.test/download?refresh_token=refresh&access-token=access&format=stl",
        "https://example.test/download?format=stl",
        id="oauth-tokens",
    ),
    pytest.param(
        "https://example.test/download?AWSAccessKeyId=access&X-Goog-Access-Id=google"
        "&key=value&name=cube",
        "https://example.test/download?name=cube",
        id="cloud-access-ids",
    ),
]


class TestNormalizeQueryKey:
    @pytest.mark.parametrize(
        "value",
        ["X-Amz-Signature", "x_amz_signature", "x.amz.signature", "XAmzSignature"],
        ids=["dashes", "underscores", "dots", "bare"],
    )
    def test_folds_every_punctuation_variant_to_one_name(self, value: str) -> None:
        # Providers spell the same parameter four ways; a set lookup only works
        # if they all normalize to the same string.
        assert normalize_query_key(value) == "xamzsignature"


class TestIsSensitiveQueryKey:
    @pytest.mark.parametrize(
        "value",
        ["X-Amz-Signature", "access_token", "api-key", "AWSAccessKeyId"],
        ids=["signature", "token", "api-key", "access-key"],
    )
    def test_recognises_a_credential_parameter(self, value: str) -> None:
        assert is_sensitive_query_key(value) is True

    @pytest.mark.parametrize("value", ["name", "format", "page"], ids=list("nfp"))
    def test_leaves_an_ordinary_parameter_alone(self, value: str) -> None:
        assert is_sensitive_query_key(value) is False


class TestRedactUrl:
    @pytest.mark.parametrize(("value", "expected"), SENSITIVE_URLS)
    def test_removes_the_credential_without_losing_the_rest(
        self, value: str, expected: str
    ) -> None:
        assert redact_url(value) == expected

    def test_lowercases_the_authority(self) -> None:
        assert redact_url("HTTPS://EXAMPLE.TEST/a") == "https://example.test/a"

    def test_keeps_a_non_default_port(self) -> None:
        assert (
            redact_url("https://example.test:8443/a") == "https://example.test:8443/a"
        )

    def test_brackets_an_ipv6_host(self) -> None:
        assert redact_url("https://[2001:db8::1]/a") == "https://[2001:db8::1]/a"

    def test_gives_a_path_less_url_a_root_path(self) -> None:
        assert redact_url("https://example.test") == "https://example.test/"

    def test_refuses_a_relative_value(self) -> None:
        assert redact_url("/download?token=never-log-this") == "[redacted-url]"

    def test_refuses_a_value_with_no_host(self) -> None:
        assert redact_url("https:///path?token=never-log-this") == "[redacted-url]"

    def test_refuses_a_host_that_is_not_valid_idna(self) -> None:
        # A hostname that cannot be encoded is the one most likely to smuggle
        # something past a naive parser.
        assert redact_url("https://" + "a" * 300 + ".test/x") == "[redacted-url]"

    def test_refuses_a_value_that_cannot_be_parsed_at_all(self) -> None:
        assert redact_url("https://example.test:not-a-port/x") == "[redacted-url]"

    def test_never_returns_the_input_when_it_refuses(self) -> None:
        assert "never-log-this" not in redact_url("/download?token=never-log-this")


class TestRedactException:
    def test_reports_only_the_exception_type(self) -> None:
        error = RuntimeError("https://example.test/file?token=never-log-this")

        assert redact_exception(error) == "RuntimeError"

    def test_never_includes_the_upstream_message(self) -> None:
        error = RuntimeError("https://example.test/file?token=never-log-this")

        # An httpx error message routinely contains the full signed URL.
        assert "never-log-this" not in redact_exception(error)
